"""Phase 9 decision provenance ledger: capture, drift, tenant isolation and export.

Synthetic local SQLite only. No external provider, no model output, no network.
"""

from __future__ import annotations

from tests.test_persistence import add_workflow_task, client, create_assessment  # noqa: F401
from tests.test_security import secure_client  # noqa: F401 - shared tenant fixture

REVIEW_TOKEN = {"X-Reviewer-Token": "0123456789abcdef01234567"}


def _scenario(name: str = "Recorded scenario") -> dict:
    return {
        "name": name,
        "currency": "NOK",
        "period": "month",
        "cases_per_period": "100",
        "baseline_minutes": "30",
        "target_minutes": "10",
        "hourly_cost": "600",
        "recurring_cost": "5000",
        "initial_cost": "15000",
        "assumption_source": "Synthetic discovery interview",
    }


def _finalized(client, monkeypatch, *, with_evidence=True, with_scenario=True) -> str:
    """Run a full advisory path and finalize it as a human reviewer would."""
    monkeypatch.setenv("ADVISORY_REVIEW_TOKEN", REVIEW_TOKEN["X-Reviewer-Token"])
    aid = create_assessment(client)["id"]
    root = f"/api/v1/assessments/{aid}"
    add_workflow_task(
        client,
        aid,
        title="Determine refund amount",
        signals={"deterministic_rules": True, "exact_math": True, "output_verifiable": True},
    )
    analysis = client.post(root + "/analyze")
    assert analysis.status_code == 200, analysis.text
    category = analysis.json()["tasks"][0]["recommendation"]["category"]
    if with_evidence:
        evidence = client.post(
            root + "/evidence",
            json={
                "kind": "baseline",
                "description": "Synthetic baseline observation",
                "source_reference": "Pilot worksheet 2026-01",
            },
        )
        assert evidence.status_code == 201, evidence.text
        assert client.post(root + f"/evidence/{evidence.json()['id']}/verify").status_code == 200
    if with_scenario:
        scenario = client.post(root + "/value-scenarios", json=_scenario())
        assert scenario.status_code == 201, scenario.text
    review = client.post(
        root + "/fde-review",
        json={
            "reviewer_id": "Example FDE",
            "selected_category": category,
            "rationale": "A deterministic calculation must stay in tested code.",
        },
    )
    assert review.status_code == 201, review.text
    final = client.post(root + "/final-decision", headers=REVIEW_TOKEN)
    assert final.status_code == 200, final.text
    return aid


def test_finalization_records_the_full_provenance_set(client, monkeypatch):
    aid = _finalized(client, monkeypatch)
    root = f"/api/v1/assessments/{aid}"
    ledger = client.get(root + "/provenance")
    assert ledger.status_code == 200, ledger.text
    record = ledger.json()
    assert record["assessment_id"] == aid
    assert record["schema_version"] == "provenance-v1"
    assert record["policy_version"] == "rules-v2"
    assert record["scoring_weight_version"].startswith("rules-v2-weights-")
    assert record["analysis_revision"] == client.get(root).json()["revision"]
    assert record["assessment_revision"] == record["analysis_revision"]
    assert len(record["analysis_digest"]) == 16
    assert len(record["evidence_ids"]) == 1
    assert record["verified_evidence_ids"] == record["evidence_ids"]
    assert record["value_scenario_id"]
    assert record["value_scenario_ids"] == [record["value_scenario_id"]]
    assert record["ai_contributed"] is False
    assert record["ai_proposals"] == []
    assert record["recorded_at"]
    assert record["stale"] is False
    assert record["stale_reasons"] == []
    assert record["advisory_only"] is True
    # The ledger is a record of facts, never an approval or a validation.
    assert "not proof" in record["note"].lower()
    assert "approval" in record["note"].lower()


def test_provenance_matches_the_analysis_actually_used(client, monkeypatch):
    aid = _finalized(client, monkeypatch)
    root = f"/api/v1/assessments/{aid}"
    analysis = client.get(root + "/scores")
    assert analysis.status_code == 200
    record = client.get(root + "/provenance").json()
    assert record["policy_version"] == "rules-v2"
    assert record["analysis_revision"] == analysis.json()["revision"]
    # The finalization gate is untouched: the ledger never decides anything.
    assert client.get(root).json()["status"] == "finalized"
    audit = client.get(root + "/audit").json()
    assert any(event["event_type"] == "fde.finalized" for event in audit)
    assert any(event["event_type"] == "provenance.recorded" for event in audit)


def test_changed_evidence_and_scenario_mark_the_provenance_stale(client, monkeypatch):
    aid = _finalized(client, monkeypatch)
    root = f"/api/v1/assessments/{aid}"
    assert client.get(root + "/provenance").json()["stale"] is False
    reopened = client.post(root + "/reopen", headers=REVIEW_TOKEN)
    assert reopened.status_code == 200, reopened.text
    evidence = client.post(
        root + "/evidence",
        json={
            "kind": "technical",
            "description": "Newly observed evidence after the decision",
            "source_reference": "Later worksheet 2026-02",
        },
    )
    assert evidence.status_code == 201, evidence.text
    scenario = client.post(root + "/value-scenarios", json=_scenario(name="Later scenario"))
    assert scenario.status_code == 201, scenario.text
    record = client.get(root + "/provenance").json()
    assert record["stale"] is True
    assert "analysis_revision_changed" in record["stale_reasons"]
    assert "evidence_changed" in record["stale_reasons"]
    assert "evidence_added" in record["stale_reasons"]
    assert "value_scenario_added" in record["stale_reasons"]
    # The ledger still reports the basis the decision was actually taken against.
    assert record["analysis_revision"] != client.get(root).json()["revision"]


def test_report_contains_the_provenance_and_labels_a_stale_basis(client, monkeypatch):
    aid = _finalized(client, monkeypatch)
    root = f"/api/v1/assessments/{aid}"
    markdown = client.get(root + "/report?format=markdown").text
    html = client.get(root + "/report?format=html").text
    for text in (markdown.replace("\\", ""), html):
        assert "rules-v2" in text
        assert "Basis stale" in text
        assert "Decision inputs recorded" in text
        assert "neither an approval nor a validation" in text
    assert "Stale basis" not in markdown.replace("\\", "")
    assert client.post(root + "/reopen", headers=REVIEW_TOKEN).status_code == 200
    assert client.post(root + "/analyze").status_code == 200
    stale_markdown = client.get(root + "/report?format=markdown").text.replace("\\", "")
    stale_html = client.get(root + "/report?format=html").text
    assert "Stale basis" in stale_markdown
    assert "Stale basis" in stale_html
    assert "Decision basis stale" in stale_markdown
    assert "Decision basis stale" in stale_html


def test_provenance_is_tenant_scoped_and_requires_authentication(secure_client):
    client, provision, _ = secure_client
    reviewer, _, _ = provision("Org A", "reviewer-a", "reviewer")
    outsider, _, _ = provision("Org B", "admin-b", "admin")
    aid = client.post("/api/v1/assessments", json={"title": "Scoped"}, headers=reviewer).json()[
        "id"
    ]
    root = f"/api/v1/assessments/{aid}"
    client.post(root + "/workflows", json={"name": "Pricing"}, headers=reviewer)
    workflow = client.get(root, headers=reviewer).json()
    step = client.post(
        f"/api/v1/workflows/{workflow['workflows'][0]['id']}/steps",
        json={"name": "Calculate", "order": 0},
        headers=reviewer,
    ).json()["id"]
    client.post(
        f"/api/v1/workflow-steps/{step}/tasks",
        json={"title": "Pricing", "signals": {"exact_math": True}},
        headers=reviewer,
    )
    assert client.post(root + "/analyze", headers=reviewer).status_code == 200
    assert client.get(root + "/provenance", headers=reviewer).json() is None
    category = client.get(root + "/recommendations", headers=reviewer).json()["tasks"][0][
        "recommendation"
    ]["category"]
    review = client.post(
        root + "/fde-review",
        json={
            "reviewer_id": "reviewer-a",
            "selected_category": category,
            "rationale": "Exact arithmetic belongs in tested code.",
        },
        headers=reviewer,
    )
    assert review.status_code == 201, review.text
    assert client.post(root + "/final-decision", headers=reviewer).status_code == 200
    own = client.get(root + "/provenance", headers=reviewer)
    assert own.status_code == 200, own.text
    record = own.json()
    assert record["reviewer_subject"] == "reviewer-a"
    assert record["reviewer_identity_verified"] is True
    assert record["decision_maker_subject"] == "reviewer-a"
    assert record["decision_maker_identity_verified"] is True
    # Another organization must not see another organization's ledger.
    assert client.get(root + "/provenance", headers=outsider).status_code == 404
    assert client.get(root + "/provenance").status_code == 401
    assert client.get("/api/v1/assessments/unknown/provenance", headers=reviewer).status_code == 404
