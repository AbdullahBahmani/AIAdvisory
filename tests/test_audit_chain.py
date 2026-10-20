"""Tamper-evident audit chaining and rollback detection.

A local failure-recovery drill restored a ``pg_dump`` backup over a live database and found
that the ``fde.finalized`` audit event was silently lost: the assessment status reverted, the
API still answered 200, and nothing said a decision record had disappeared.

These tests cover the two mechanisms that make that failure visible:

* the per-assessment hash chain, which detects a row altered in place;
* the out-of-database high-water mark, which detects a rollback (rows removed).

They also cover the limits: a legacy row without a chain position is reported as a gap
rather than being quietly treated as intact.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, select

from app.db.models import AuditRow
from app.services.audit_chain import (
    GENESIS_HASH,
    HighWaterMark,
    assess_integrity,
    canonical_payload,
    chain_state,
    compute_chain_hash,
    verify_chain,
)
from tests.test_release_hardening import session  # noqa: F401 - shared pytest fixture
from tests.test_security import secure_client  # noqa: F401 - shared pytest fixture


@pytest.fixture(autouse=True)
def isolated_mark(tmp_path, monkeypatch):
    """Keep the high-water mark out of the repository and out of other tests."""
    path = tmp_path / "audit-high-water.json"
    monkeypatch.setenv("ADVISORY_AUDIT_HIGH_WATER_PATH", str(path))
    return path


def _rows(db, assessment_id) -> list[AuditRow]:
    return list(
        db.scalars(
            select(AuditRow)
            .where(AuditRow.assessment_id == assessment_id)
            .order_by(AuditRow.sequence)
        ).all()
    )


# --------------------------------------------------------------------------------------
# The chain itself
# --------------------------------------------------------------------------------------


def test_canonical_payload_is_stable_across_key_order():
    a = canonical_payload(
        event_id="e1",
        sequence=1,
        assessment_id="a1",
        event_type="assessment.created",
        actor="alice",
        actor_verified=True,
        actor_organization_id="o1",
        details={"b": 2, "a": 1},
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    b = canonical_payload(
        event_id="e1",
        sequence=1,
        assessment_id="a1",
        event_type="assessment.created",
        actor="alice",
        actor_verified=True,
        actor_organization_id="o1",
        details={"a": 1, "b": 2},
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert a == b


def test_chain_hash_depends_on_the_previous_hash():
    payload = b"content"
    assert compute_chain_hash(GENESIS_HASH, payload) != compute_chain_hash("f" * 64, payload)


def test_chain_hash_is_deterministic():
    payload = b"content"
    assert compute_chain_hash(GENESIS_HASH, payload) == compute_chain_hash(GENESIS_HASH, payload)


def test_chain_state_of_no_rows_is_genesis():
    state = chain_state([])
    assert state.sequence == 0
    assert state.chain_hash == GENESIS_HASH
    assert state.is_genesis


def test_verify_chain_reports_a_gap_for_legacy_rows():
    """Rows that predate the chain must be reported, not treated as intact."""

    class Row:
        id = "legacy"
        sequence = 0
        chain_hash = ""
        assessment_id = "a1"
        event_type = "assessment.created"
        actor = "alice"
        actor_verified = False
        actor_organization_id = None
        details = {}
        created_at = datetime(2026, 1, 1)

    ok, problems = verify_chain([Row()])
    assert not ok
    assert any("sequence gap" in problem for problem in problems)


# --------------------------------------------------------------------------------------
# The high-water mark
# --------------------------------------------------------------------------------------


def test_mark_records_and_reads_back(isolated_mark):
    mark = HighWaterMark()
    mark.record("a1", 3, "abc")
    assert mark.get("a1") == {"sequence": 3, "chain_hash": "abc"}


def test_mark_never_moves_backwards(isolated_mark):
    mark = HighWaterMark()
    mark.record("a1", 5, "abc")
    mark.record("a1", 2, "xyz")
    assert mark.get("a1")["sequence"] == 5


def test_mark_file_is_owner_only(isolated_mark):
    HighWaterMark().record("a1", 1, "abc")
    mode = isolated_mark.stat().st_mode & 0o777
    assert mode == 0o600, f"the mark file must not be group or world readable, got {oct(mode)}"


def test_missing_mark_is_not_a_rollback():
    """A first run has no mark; that is not evidence of a rollback."""
    verdict = assess_integrity([])
    assert verdict.rollback_detected is False
    assert verdict.marked_sequence is None


def test_corrupt_mark_is_ignored_rather_than_crashing(isolated_mark):
    isolated_mark.parent.mkdir(parents=True, exist_ok=True)
    isolated_mark.write_text("{not json", encoding="utf-8")
    assert HighWaterMark().load() == {}


# --------------------------------------------------------------------------------------
# Through the application
# --------------------------------------------------------------------------------------


def test_audit_events_are_chained_and_sequenced(secure_client):
    client, provision, _ = secure_client
    headers, _, _ = provision("Chain Org", "alice", "analyst")
    created = client.post("/api/v1/assessments", json={"title": "Chained"}, headers=headers)
    assert created.status_code == 201, created.text
    aid = created.json()["id"]

    events = client.get(f"/api/v1/assessments/{aid}/audit", headers=headers).json()
    assert events, "creating an assessment must write an audit event"
    sequences = [event["sequence"] for event in events]
    assert sequences == sorted(sequences)
    assert sequences[0] == 1, "the first audit event of an assessment is position 1"
    assert sequences == list(range(1, len(sequences) + 1))


def test_audit_history_reports_an_intact_chain(secure_client):
    client, provision, _ = secure_client
    headers, _, _ = provision("Intact Org", "alice", "analyst")
    aid = client.post("/api/v1/assessments", json={"title": "Intact"}, headers=headers).json()["id"]
    response = client.get(f"/api/v1/assessments/{aid}/audit", headers=headers)
    assert response.headers["X-Audit-Chain-Intact"] == "true"
    assert response.headers["X-Audit-Rollback-Detected"] == "false"


def test_integrity_endpoint_reports_a_clean_trail(secure_client):
    client, provision, _ = secure_client
    headers, _, _ = provision("Clean Org", "alice", "analyst")
    aid = client.post("/api/v1/assessments", json={"title": "Clean"}, headers=headers).json()["id"]
    body = client.get(f"/api/v1/assessments/{aid}/audit/integrity", headers=headers).json()
    assert body["rollback_detected"] is False
    assert body["chain_intact"] is True
    assert body["advisory_only"] is True


def test_deleting_an_audit_event_is_detected_as_a_rollback(secure_client, session):
    """The drill's finding: a restore drops a finalization record silently."""
    client, provision, _ = secure_client
    headers, _, _ = provision("Rollback Org", "alice", "analyst")
    aid = client.post("/api/v1/assessments", json={"title": "Rolled back"}, headers=headers).json()[
        "id"
    ]
    before = client.get(f"/api/v1/assessments/{aid}/audit", headers=headers).json()
    assert len(before) >= 1

    # Simulate what a restore does: the database loses its most recent audit events.
    rows = _rows(session, aid)
    victim = rows[-1]
    session.execute(delete(AuditRow).where(AuditRow.id == victim.id))
    session.commit()

    body = client.get(f"/api/v1/assessments/{aid}/audit/integrity", headers=headers).json()
    assert body["rollback_detected"] is True
    assert any("rollback detected" in problem for problem in body["problems"])

    response = client.get(f"/api/v1/assessments/{aid}/audit", headers=headers)
    assert response.headers["X-Audit-Rollback-Detected"] == "true"


def test_wiping_the_whole_trail_is_still_detected(secure_client, session):
    """The most complete rollback there is: every event gone.

    This case cannot be detected from the rows alone - there are none - so the
    assessment id has to be carried into the check explicitly.
    """
    client, provision, _ = secure_client
    headers, _, _ = provision("Wiped Org", "alice", "analyst")
    aid = client.post("/api/v1/assessments", json={"title": "Wiped"}, headers=headers).json()["id"]
    assert _rows(session, aid), "the assessment must have an audit event to lose"

    session.execute(delete(AuditRow).where(AuditRow.assessment_id == aid))
    session.commit()

    body = client.get(f"/api/v1/assessments/{aid}/audit/integrity", headers=headers).json()
    assert body["rollback_detected"] is True
    assert body["actual_sequence"] == 0
    assert body["marked_sequence"] and body["marked_sequence"] >= 1


def test_altering_an_audit_event_in_place_is_detected(secure_client, session):
    """A row edited after the fact must not still verify."""
    client, provision, _ = secure_client
    headers, _, _ = provision("Alter Org", "alice", "analyst")
    aid = client.post("/api/v1/assessments", json={"title": "Altered"}, headers=headers).json()[
        "id"
    ]

    rows = _rows(session, aid)
    victim = rows[-1]
    victim.details = {**victim.details, "tampered": True}
    session.commit()

    body = client.get(f"/api/v1/assessments/{aid}/audit/integrity", headers=headers).json()
    assert body["chain_intact"] is False
    assert any("does not match its recorded chain hash" in p for p in body["problems"])


def test_the_mark_is_written_at_commit_time(secure_client, isolated_mark):
    """The mark exists even before anything reads the audit history."""
    client, provision, _ = secure_client
    headers, _, _ = provision("Mark Org", "alice", "analyst")
    aid = client.post("/api/v1/assessments", json={"title": "Marked"}, headers=headers).json()["id"]
    stored = json.loads(isolated_mark.read_text(encoding="utf-8"))
    assert aid in stored
    assert stored[aid]["sequence"] >= 1
    assert stored[aid]["chain_hash"]


def test_audit_chain_is_per_assessment(secure_client):
    """Two assessments each start at position 1; the chain is not global."""
    client, provision, _ = secure_client
    headers, _, _ = provision("Per Org", "alice", "analyst")
    first = client.post("/api/v1/assessments", json={"title": "First"}, headers=headers).json()[
        "id"
    ]
    second = client.post("/api/v1/assessments", json={"title": "Second"}, headers=headers).json()[
        "id"
    ]
    for aid in (first, second):
        events = client.get(f"/api/v1/assessments/{aid}/audit", headers=headers).json()
        assert events[0]["sequence"] == 1


def test_integrity_endpoint_requires_authentication(secure_client):
    client, provision, _ = secure_client
    headers, _, _ = provision("Anon Org", "alice", "analyst")
    aid = client.post("/api/v1/assessments", json={"title": "Anonymous"}, headers=headers).json()[
        "id"
    ]
    assert client.get(f"/api/v1/assessments/{aid}/audit/integrity").status_code == 401


def test_integrity_endpoint_is_tenant_scoped(secure_client):
    client, provision, _ = secure_client
    alice, _, _ = provision("Org A", "alice", "analyst")
    bob, _, _ = provision("Org B", "bob", "analyst")
    aid = client.post("/api/v1/assessments", json={"title": "Owned"}, headers=alice).json()["id"]
    assert client.get(f"/api/v1/assessments/{aid}/audit/integrity", headers=bob).status_code == 404
