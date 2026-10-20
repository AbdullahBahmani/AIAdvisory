"""Local/demo actor attribution: named, verified and organization-scoped reviewers.

These tests exercise the local/demo authentication path only: a named local
principal is resolved from a stored credential exactly like the production path,
so the audit actor, the review record and the tenant scope all come from the
server. The shared local reviewer token survives only as the explicitly
synthetic, permanently unverified fallback.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from app.db.models import ApiCredentialRow, AssessmentRow, Base, OrganizationRow
from app.db.session import get_db
from app.main import app
from app.security import digest_token, make_token

REVIEW_BODY = {
    "selected_category": "traditional_software",
    "rationale": "Deterministic pricing rules",
}


@pytest.fixture
def local_client(monkeypatch, tmp_path: Path):
    """Demo mode against an isolated database with operator-provisioned principals."""
    monkeypatch.setenv("ADVISORY_DEMO_MODE", "1")
    monkeypatch.setenv("ADVISORY_DEPLOYMENT_MODE", "local")
    monkeypatch.delenv("ADVISORY_REVIEW_TOKEN", raising=False)
    monkeypatch.delenv("ADVISORY_REQUIRE_APPROVAL_CHAIN", raising=False)
    engine = create_engine(
        f"sqlite:///{tmp_path / 'local.sqlite3'}", connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine, "connect")
    def enable_fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)

    def override_db():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = override_db

    def provision(
        organization: str,
        subject: str,
        role: str = "reviewer",
        *,
        expires_days: int = 2,
        revoked: bool = False,
    ):
        """Operator-side provisioning of a named local principal."""
        with Session(engine) as db:
            org = db.scalar(select(OrganizationRow).where(OrganizationRow.name == organization))
            if org is None:
                org = OrganizationRow(name=organization)
                db.add(org)
                db.flush()
            token = make_token()
            row = ApiCredentialRow(
                organization_id=org.id,
                subject=subject,
                role=role,
                token_hash=digest_token(token),
                expires_at=datetime.now(UTC) + timedelta(days=expires_days),
                revoked_at=datetime.now(UTC) if revoked else None,
            )
            db.add(row)
            db.commit()
            return {"Authorization": f"Bearer {token}"}, org.id, token

    with TestClient(app) as client:
        yield client, provision, engine
    app.dependency_overrides.clear()
    engine.dispose()


def _analyzed_assessment(client, headers, title="Local assessment") -> str:
    """Create and analyze an assessment owned by the calling principal."""
    created = client.post("/api/v1/assessments", json={"title": title}, headers=headers)
    assert created.status_code == 201, created.text
    aid = created.json()["id"]
    workflow = client.post(
        f"/api/v1/assessments/{aid}/workflows", json={"name": "Workflow"}, headers=headers
    )
    assert workflow.status_code == 201, workflow.text
    step = client.post(
        f"/api/v1/workflows/{workflow.json()['id']}/steps",
        json={"name": "Calculate", "order": 0},
        headers=headers,
    )
    assert step.status_code == 201, step.text
    task = client.post(
        f"/api/v1/workflow-steps/{step.json()['id']}/tasks",
        json={"title": "Price", "signals": {"exact_math": True, "deterministic_rules": True}},
        headers=headers,
    )
    assert task.status_code == 201, task.text
    analyzed = client.post(f"/api/v1/assessments/{aid}/analyze", headers=headers)
    assert analyzed.status_code == 200, analyzed.text
    return aid


def test_local_credential_resolves_a_named_organization_scoped_principal(local_client):
    client, provision, _ = local_client
    alice, org_a, _ = provision("Acme", "alice", "reviewer")
    bob, org_b, _ = provision("Globex", "bob", "admin")
    aid = _analyzed_assessment(client, alice)
    # The synthetic demo identity may not be used to reach a tenant-owned record.
    assert client.get(f"/api/v1/assessments/{aid}", headers=bob).status_code == 404
    assert client.get("/api/v1/assessments", headers=bob).json() == []
    assert client.get("/api/v1/assessments", headers=alice).json()[0]["id"] == aid
    with Session(local_client[2]) as db:
        row = db.get(AssessmentRow, aid)
        assert row.organization_id == org_a
        assert row.audit_events[0].actor == "alice"
        assert row.audit_events[0].actor_organization_id == org_a
        assert row.audit_events[0].actor_verified is True


def test_two_named_local_principals_produce_two_different_audit_actors(local_client):
    client, provision, _ = local_client
    alice, _, _ = provision("Acme", "alice", "reviewer")
    bob, _, _ = provision("Acme", "bob", "reviewer")
    aid = _analyzed_assessment(client, alice)
    review = client.post(
        f"/api/v1/assessments/{aid}/fde-review",
        json={**REVIEW_BODY, "reviewer_id": "alice"},
        headers=alice,
    )
    assert review.status_code == 201, review.text
    assert review.json()["identity_unverified"] is False
    final = client.post(f"/api/v1/assessments/{aid}/final-decision", headers=alice)
    assert final.status_code == 200, final.text
    assert final.json()["identity_unverified"] is False
    comment = client.post(
        f"/api/v1/assessments/{aid}/comments", json={"body": "Checked the rules"}, headers=bob
    )
    assert comment.status_code == 201, comment.text
    events = client.get(f"/api/v1/assessments/{aid}/audit", headers=alice).json()
    actors = {event["actor"] for event in events}
    assert actors == {"alice", "bob"}
    recorded = [event for event in events if event["event_type"] == "fde.review_recorded"][0]
    assert recorded["details"]["identity_unverified"] is False


def test_reviewer_cannot_record_a_review_under_another_reviewers_name(local_client):
    client, provision, _ = local_client
    alice, _, _ = provision("Acme", "alice", "reviewer")
    bob, _, _ = provision("Acme", "bob", "reviewer")
    aid = _analyzed_assessment(client, alice)
    impersonated = client.post(
        f"/api/v1/assessments/{aid}/fde-review",
        json={**REVIEW_BODY, "reviewer_id": "bob"},
        headers=alice,
    )
    assert impersonated.status_code == 403
    assert (
        client.post(
            f"/api/v1/assessments/{aid}/fde-review",
            json={**REVIEW_BODY, "reviewer_id": "alice"},
            headers=alice,
        ).status_code
        == 201
    )
    events = client.get(f"/api/v1/assessments/{aid}/audit", headers=alice).json()
    assert all(event["actor"] != "bob" for event in events)
    with Session(local_client[2]) as db:
        reviews = db.get(AssessmentRow, aid).reviews
        assert [r.reviewer_id for r in reviews] == ["alice"]
        assert all(r.identity_verified is True for r in reviews)


def test_credential_from_another_organization_is_rejected(local_client):
    client, provision, _ = local_client
    alice, _, _ = provision("Org A", "alice", "reviewer")
    outsider, _, _ = provision("Org B", "outsider", "admin")
    aid = _analyzed_assessment(client, alice)
    for method, path, payload in [
        ("get", f"/api/v1/assessments/{aid}", None),
        ("get", f"/api/v1/assessments/{aid}/audit", None),
        ("get", f"/api/v1/assessments/{aid}/report", None),
        (
            "post",
            f"/api/v1/assessments/{aid}/fde-review",
            {**REVIEW_BODY, "reviewer_id": "outsider"},
        ),
        ("post", f"/api/v1/assessments/{aid}/final-decision", None),
        ("post", f"/api/v1/assessments/{aid}/comments", {"body": "cross tenant"}),
    ]:
        response = (
            client.get(path, headers=outsider)
            if method == "get"
            else client.post(path, json=payload, headers=outsider)
        )
        assert response.status_code == 404, (path, response.text)
    assert client.get("/api/v1/assessments", headers=outsider).json() == []
    assert client.get("/api/v1/assessments", headers=alice).json()[0]["id"] == aid


def test_revoked_expired_and_malformed_credentials_are_rejected(local_client):
    client, provision, _ = local_client
    valid, _, _ = provision("Org", "valid", "viewer")
    expired, _, _ = provision("Org", "expired", "viewer", expires_days=-1)
    revoked, _, _ = provision("Org", "revoked", "viewer", revoked=True)
    for headers in [
        expired,
        revoked,
        {"Authorization": f"Bearer aia_{'x' * 40}"},
        {"Authorization": "Basic dXNlcjpwYXNz"},
        {"Authorization": valid["Authorization"] + "garbage"},
    ]:
        assert client.get("/api/v1/assessments", headers=headers).status_code == 401
    assert client.get("/api/v1/assessments", headers=valid).status_code == 200


def test_shared_token_fallback_still_works_and_stays_unverified(local_client, monkeypatch):
    client, _, _ = local_client
    monkeypatch.setenv("ADVISORY_REVIEW_TOKEN", "0123456789abcdef01234567")
    created = client.post("/api/v1/assessments", json={"title": "Synthetic demo"})
    assert created.status_code == 201, created.text
    aid = created.json()["id"]
    workflow = client.post(f"/api/v1/assessments/{aid}/workflows", json={"name": "Workflow"})
    step = client.post(
        f"/api/v1/workflows/{workflow.json()['id']}/steps", json={"name": "Calculate", "order": 0}
    )
    client.post(
        f"/api/v1/workflow-steps/{step.json()['id']}/tasks",
        json={"title": "Price", "signals": {"exact_math": True, "deterministic_rules": True}},
    )
    assert client.post(f"/api/v1/assessments/{aid}/analyze").status_code == 200
    review = client.post(
        f"/api/v1/assessments/{aid}/fde-review",
        json={**REVIEW_BODY, "reviewer_id": "Typed Name"},
    )
    assert review.status_code == 201, review.text
    assert review.json()["identity_unverified"] is True
    assert client.post(f"/api/v1/assessments/{aid}/final-decision").status_code == 403
    assert (
        client.post(
            f"/api/v1/assessments/{aid}/final-decision",
            headers={"X-Reviewer-Token": "wrong-token-value-here"},
        ).status_code
        == 403
    )
    final = client.post(
        f"/api/v1/assessments/{aid}/final-decision",
        headers={"X-Reviewer-Token": "0123456789abcdef01234567"},
    )
    assert final.status_code == 200, final.text
    assert final.json()["identity_unverified"] is True
    events = client.get(f"/api/v1/assessments/{aid}/audit").json()
    # The typed label is never promoted to an audit actor.
    assert all(event["actor"] == "local_demo" for event in events)
    assert all(event["details"]["identity_unverified"] is True for event in events)
    finalized = [event for event in events if event["event_type"] == "fde.finalized"][0]
    assert finalized["actor"] == "local_demo"
    recorded = [event for event in events if event["event_type"] == "fde.review_recorded"][0]
    # The typed label is kept only as an explicitly unverified claim.
    assert recorded["details"]["claimed_actor"] == "Typed Name"
    assert recorded["details"]["identity_unverified"] is True
    with Session(local_client[2]) as db:
        row = db.get(AssessmentRow, aid)
        assert all(event.actor_verified is False for event in row.audit_events)
        assert all(event.actor_organization_id is None for event in row.audit_events)
        assert all(review.reviewer_organization_id is None for review in row.reviews)
        assert all(review.identity_verified is False for review in row.reviews)


def test_approval_chain_cannot_collapse_onto_a_single_local_actor(local_client, monkeypatch):
    client, provision, _ = local_client
    monkeypatch.setenv("ADVISORY_REQUIRE_APPROVAL_CHAIN", "1")
    tech, _, _ = provision("Approvals", "tech", "reviewer")
    security, _, _ = provision("Approvals", "sec", "reviewer")
    sponsor, _, _ = provision("Approvals", "sponsor", "admin")
    aid = _analyzed_assessment(client, tech)
    url = f"/api/v1/assessments/{aid}/approvals"
    assert (
        client.post(
            url,
            json={"stage": "security", "decision": "approved", "rationale": "Out of order"},
            headers=tech,
        ).status_code
        == 409
    )
    assert (
        client.post(
            url,
            json={"stage": "technical", "decision": "approved", "rationale": "Checked"},
            headers=tech,
        ).status_code
        == 201
    )
    # The same actor may not take a second stage.
    assert (
        client.post(
            url,
            json={"stage": "security", "decision": "approved", "rationale": "Same person"},
            headers=tech,
        ).status_code
        == 409
    )
    assert (
        client.post(
            url,
            json={"stage": "security", "decision": "approved", "rationale": "Checked"},
            headers=security,
        ).status_code
        == 201
    )
    assert (
        client.post(
            url,
            json={"stage": "business", "decision": "approved", "rationale": "Accepted"},
            headers=sponsor,
        ).status_code
        == 201
    )
    # The synthetic demo identity can never satisfy the chain.
    assert (
        client.post(
            url, json={"stage": "business", "decision": "approved", "rationale": "Shared token"}
        ).status_code
        == 403
    )
    review = client.post(
        f"/api/v1/assessments/{aid}/fde-review",
        json={**REVIEW_BODY, "reviewer_id": "tech"},
        headers=tech,
    )
    assert review.status_code == 201, review.text
    final = client.post(f"/api/v1/assessments/{aid}/final-decision", headers=tech)
    assert final.status_code == 200, final.text
    chain = client.get(url, headers=tech).json()
    assert chain["complete"] is True
    assert len({vote["actor"] for vote in chain["history"]}) == 3


def test_assigned_reviewer_is_binding_for_local_principals(local_client):
    client, provision, _ = local_client
    admin, _, _ = provision("Org", "admin", "admin")
    primary, _, _ = provision("Org", "primary", "reviewer")
    other, _, _ = provision("Org", "other", "reviewer")
    aid = _analyzed_assessment(client, admin)
    assigned = client.put(
        f"/api/v1/assessments/{aid}/reviewer", json={"reviewer_subject": "primary"}, headers=admin
    )
    assert assigned.status_code == 200, assigned.text
    rejected = client.post(
        f"/api/v1/assessments/{aid}/fde-review",
        json={**REVIEW_BODY, "reviewer_id": "other"},
        headers=other,
    )
    assert rejected.status_code == 403
    accepted = client.post(
        f"/api/v1/assessments/{aid}/fde-review",
        json={**REVIEW_BODY, "reviewer_id": "primary"},
        headers=primary,
    )
    assert accepted.status_code == 201, accepted.text


def test_local_principal_without_reviewer_role_cannot_review(local_client):
    client, provision, _ = local_client
    analyst, _, _ = provision("Org", "analyst", "analyst")
    aid = _analyzed_assessment(client, analyst)
    response = client.post(
        f"/api/v1/assessments/{aid}/fde-review",
        json={**REVIEW_BODY, "reviewer_id": "analyst"},
        headers=analyst,
    )
    assert response.status_code == 403


def test_demo_mode_does_not_relax_the_oidc_only_rule(local_client, monkeypatch):
    client, provision, _ = local_client
    alice, _, _ = provision("Org", "alice", "reviewer")
    monkeypatch.setenv("ADVISORY_OIDC_REQUIRED", "1")
    # The demo switch is never a fallback after a credential that the deployment
    # refuses to accept.
    assert client.get("/api/v1/assessments", headers=alice).status_code == 401
