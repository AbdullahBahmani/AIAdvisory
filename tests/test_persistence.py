"""Integration tests use real database transactions and no hosted runner/AI provider."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.main import app
from tests.fixtures.cases import AI_SUPPORT, EXACT_PRICING, HYBRID_REFUND


@pytest.fixture
def client(db_engine):
    """A test client bound to an isolated database on the selected backend."""

    def override_db():
        with Session(db_engine) as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def create_assessment(client):
    response = client.post(
        "/api/v1/assessments",
        json={
            "title": "Customer workflow",
            "organization": "Example Co",
            "owner": "FDE",
            "business_problem": "Reduce manual work",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def add_workflow_task(client, assessment_id, *, name="Workflow", position=0, **task):
    wf = client.post(f"/api/v1/assessments/{assessment_id}/workflows", json={"name": name})
    assert wf.status_code == 201, wf.text
    workflow_id = wf.json()["id"]
    step = client.post(
        f"/api/v1/workflows/{workflow_id}/steps", json={"name": "Handle case", "order": position}
    )
    assert step.status_code == 201, step.text
    step_id = step.json()["id"]
    created = client.post(
        f"/api/v1/workflow-steps/{step_id}/tasks",
        json={
            "title": task.get("title", "Task"),
            "signals": task.get("signals", {}),
            "actions": task.get("actions", []),
            "risks": task.get("risks", []),
        },
    )
    assert created.status_code == 201, created.text
    return workflow_id, step_id, created.json()["id"]


def test_create_list_and_save_after_http_client_restart(client):
    row = create_assessment(client)
    rid = row["id"]
    assert row["revision"] == 1
    assert client.get(f"/api/v1/assessments/{rid}").json()["title"] == "Customer workflow"
    assert any(x["id"] == rid for x in client.get("/api/v1/assessments").json())
    updated = client.patch(f"/api/v1/assessments/{rid}", json={"title": "Revised title"})
    assert updated.status_code == 200
    assert updated.json()["revision"] == 2
    assert client.get(f"/api/v1/assessments/{rid}").json()["title"] == "Revised title"


def test_workflow_task_analysis_and_stale_invalidation(client):
    row = create_assessment(client)
    rid = row["id"]
    wf_id, step_id, task_id = add_workflow_task(
        client,
        rid,
        title="Fixed math",
        signals={"deterministic_rules": True, "exact_math": True, "output_verifiable": True},
    )
    assert client.get(f"/api/v1/workflows/{wf_id}").json()["steps"][0]["tasks"][0]["id"] == task_id
    analyzed = client.post(f"/api/v1/assessments/{rid}/analyze")
    assert analyzed.status_code == 200, analyzed.text
    assert analyzed.json()["tasks"][0]["recommendation"]["category"] == "traditional_software"
    assert client.get(f"/api/v1/assessments/{rid}/scores").status_code == 200
    assert client.get(f"/api/v1/assessments/{rid}/recommendations").status_code == 200
    assert client.get(f"/api/v1/assessments/{rid}/risks").status_code == 200
    previous_rev = client.get(f"/api/v1/assessments/{rid}").json()["revision"]
    changed = client.patch(f"/api/v1/tasks/{task_id}", json={"signals": {"semantic_search": True}})
    assert changed.status_code == 200, changed.text
    state = client.get(f"/api/v1/assessments/{rid}").json()
    assert state["revision"] == previous_rev + 1
    assert state["analysis"] is None
    assert client.get(f"/api/v1/assessments/{rid}/scores").status_code == 409
    assert client.post(f"/api/v1/assessments/{rid}/analyze").status_code == 200
    kinds = [a["event_type"] for a in client.get(f"/api/v1/assessments/{rid}/audit").json()]
    assert "assessment.analyzed" in kinds and "task.updated" in kinds


def test_discovery_answers_provenance_and_conditional_questions(client):
    rid = create_assessment(client)["id"]
    question = client.get(f"/api/v1/assessments/{rid}/next-question")
    assert question.json()["next_question"]["key"] == "business_problem"
    for value in ("yes", "no"):
        response = client.post(
            f"/api/v1/assessments/{rid}/answers",
            json={
                "key": "business_problem",
                "value": value,
                "source": "stakeholder",
                "confidence": 0.8,
            },
        )
        assert response.status_code == 201
    discovery = client.get(f"/api/v1/assessments/{rid}/missing-information").json()
    assert discovery["next_question"]["key"] == "business_problem"
    assert any(f["kind"] == "contradiction" for f in discovery["findings"])
    assert (
        client.post(
            f"/api/v1/assessments/{rid}/answers", json={"key": "external_ai_use", "value": True}
        ).status_code
        == 201
    )
    latest = client.get(f"/api/v1/assessments/{rid}/next-question").json()
    assert "provider_permission" in latest["missing_question_keys"]
    assert len(client.get(f"/api/v1/assessments/{rid}").json()["answers"]) == 3


def test_cannot_analyze_empty_or_incomplete_workflow(client):
    rid = create_assessment(client)["id"]
    assert client.post(f"/api/v1/assessments/{rid}/analyze").status_code == 409
    wf = client.post(f"/api/v1/assessments/{rid}/workflows", json={"name": "Incomplete"}).json()[
        "id"
    ]
    assert (
        client.post(f"/api/v1/workflows/{wf}/steps", json={"name": "Empty", "order": 0}).status_code
        == 201
    )
    assert client.post(f"/api/v1/assessments/{rid}/analyze").status_code == 409


def test_review_requires_analysis_and_override_reason(client, monkeypatch):
    rid = create_assessment(client)["id"]
    body = {
        "reviewer_id": "Reviewer",
        "selected_category": "strong_ai_candidate",
        "rationale": "Business rationale",
    }
    assert client.post(f"/api/v1/assessments/{rid}/fde-review", json=body).status_code == 409
    add_workflow_task(client, rid, signals={"exact_math": True, "deterministic_rules": True})
    assert client.post(f"/api/v1/assessments/{rid}/analyze").status_code == 200
    assert client.post(f"/api/v1/assessments/{rid}/fde-review", json=body).status_code == 422
    assert (
        client.post(
            f"/api/v1/assessments/{rid}/fde-review",
            json={**body, "override_reason": "Manual exception"},
        ).status_code
        == 201
    )
    assert client.post(f"/api/v1/assessments/{rid}/final-decision").status_code == 403
    monkeypatch.setenv("ADVISORY_REVIEW_TOKEN", "0123456789abcdef01234567")
    assert (
        client.post(
            f"/api/v1/assessments/{rid}/final-decision", headers={"X-Reviewer-Token": "wrong"}
        ).status_code
        == 403
    )
    final = client.post(
        f"/api/v1/assessments/{rid}/final-decision",
        headers={"X-Reviewer-Token": "0123456789abcdef01234567"},
    )
    assert final.status_code == 200, final.text
    assert final.json()["is_final"] is True and final.json()["identity_unverified"] is True
    assert client.patch(f"/api/v1/assessments/{rid}", json={"title": "No"}).status_code == 409
    assert client.post(f"/api/v1/assessments/{rid}/analyze").status_code == 409
    assert (
        client.post(
            f"/api/v1/assessments/{rid}/reopen",
            headers={"X-Reviewer-Token": "0123456789abcdef01234567"},
        ).status_code
        == 200
    )
    assert client.get(f"/api/v1/assessments/{rid}").json()["analysis"] is None


def test_multi_workflow_positions_do_not_conflict_and_cleanup(client):
    rid = create_assessment(client)["id"]
    _, step1, task1 = add_workflow_task(client, rid, name="One", position=0)
    _, step2, task2 = add_workflow_task(client, rid, name="Two", position=0)
    assert client.post(f"/api/v1/assessments/{rid}/analyze").status_code == 200
    assert len(client.get(f"/api/v1/assessments/{rid}/recommendations").json()["tasks"]) == 2
    assert client.delete(f"/api/v1/tasks/{task1}").status_code == 204
    assert client.delete(f"/api/v1/workflow-steps/{step1}").status_code == 204
    assert client.post(f"/api/v1/assessments/{rid}/analyze").status_code == 200


def test_request_validation_and_missing_resources(client):
    rid = create_assessment(client)["id"]
    assert client.post("/api/v1/assessments", json={"title": ""}).status_code == 422
    assert client.patch(f"/api/v1/assessments/{rid}", json={"title": None}).status_code == 422
    assert (
        client.patch(f"/api/v1/assessments/{rid}", json={"status": "finalized"}).status_code == 422
    )
    assert client.get("/api/v1/assessments/notfound").status_code == 404
    assert client.post(f"/api/v1/assessments/{rid}/workflows", json={"name": ""}).status_code == 422
    wf = client.post(f"/api/v1/assessments/{rid}/workflows", json={"name": "Workflow"}).json()["id"]
    assert (
        client.post(f"/api/v1/workflows/{wf}/steps", json={"name": "A", "order": 0}).status_code
        == 201
    )
    assert (
        client.post(f"/api/v1/workflows/{wf}/steps", json={"name": "B", "order": 0}).status_code
        == 409
    )
    assert (
        client.get(f"/api/v1/workflows/{wf}").json()["steps"]
        and len(client.get(f"/api/v1/workflows/{wf}").json()["steps"]) == 1
    )


def test_risky_action_remains_advisory_and_requires_approval(client):
    rid = create_assessment(client)["id"]
    add_workflow_task(
        client,
        rid,
        actions=["issue_refund"],
        signals={
            "classification_or_extraction": True,
            "unstructured_content": True,
            "output_verifiable": True,
            "data_available": True,
            "high_consequence": True,
            "human_review_available": True,
        },
    )
    result = client.post(f"/api/v1/assessments/{rid}/analyze")
    assert result.status_code == 200
    rec = result.json()["tasks"][0]["recommendation"]
    assert rec["category"] == "ai_human_approval"
    assert rec["required_controls"]
    assert result.json()["advisory_only"] is True


def test_assessment_soft_delete_keeps_audit_history(client, monkeypatch):
    rid = create_assessment(client)["id"]
    monkeypatch.setenv("ADVISORY_REVIEW_TOKEN", "abcdefghijklmnopqrstuvwxyz123456")
    response = client.delete(
        f"/api/v1/assessments/{rid}",
        headers={"X-Reviewer-Token": "abcdefghijklmnopqrstuvwxyz123456"},
    )
    assert response.status_code == 204
    assert client.get(f"/api/v1/assessments/{rid}").status_code == 404
    assert all(a["id"] != rid for a in client.get("/api/v1/assessments").json())
    # The record and append-only audit trail survive deletion for future retention policy.
    from app.db.models import AssessmentRow

    db_override = app.dependency_overrides[get_db]
    session = next(db_override())
    try:
        record = session.get(AssessmentRow, rid)
        assert record.status == "deleted"
        assert any(event.event_type == "assessment.soft_deleted" for event in record.audit_events)
    finally:
        session.close()


def test_rescore_existing_fixture_cases_using_database(client):
    for fixture, expected in [
        (AI_SUPPORT, "strong_ai_candidate"),
        (EXACT_PRICING, "traditional_software"),
        (HYBRID_REFUND, "ai_human_approval"),
    ]:
        rid = client.post("/api/v1/assessments", json={"title": fixture["title"]}).json()["id"]
        source = fixture["steps"][0]["tasks"][0]
        add_workflow_task(
            client,
            rid,
            title=source["title"],
            signals=source.get("signals", {}),
            actions=source.get("actions", []),
            risks=source.get("risks", []),
        )
        response = client.post(f"/api/v1/assessments/{rid}/analyze")
        assert response.status_code == 200, response.text
        assert response.json()["tasks"][0]["recommendation"]["category"] == expected


def test_provider_privacy_gates_are_not_overridable_for_processing(client):
    rid = create_assessment(client)["id"]
    add_workflow_task(
        client,
        rid,
        signals={
            "unstructured_content": True,
            "sensitive_data": True,
            "provider_data_permitted": False,
            "data_available": True,
            "output_verifiable": True,
        },
    )
    response = client.post(f"/api/v1/assessments/{rid}/analyze")
    assert response.status_code == 200
    result = response.json()["tasks"][0]
    assert result["recommendation"]["category"] == "more_discovery"
    assert any(hit["severity"] == "block_provider_data" for hit in result["rule_hits"])
    # Reviewer can disagree in the advisory log but cannot turn on an external model.
    assert (
        client.post(
            f"/api/v1/assessments/{rid}/fde-review",
            json={
                "reviewer_id": "FDE",
                "rationale": "Need exception",
                "selected_category": "strong_ai_candidate",
                "override_reason": "Review external governance",
            },
        ).status_code
        == 201
    )
    assert (
        client.get(f"/api/v1/assessments/{rid}/recommendations").json()["tasks"][0][
            "recommendation"
        ]["category"]
        == "more_discovery"
    )
