"""REST preview routes validate contracts without calling providers or databases."""

from fastapi.testclient import TestClient

from app.main import app
from tests.fixtures.cases import AI_SUPPORT, EXACT_PRICING, HYBRID_REFUND

client = TestClient(app)


def test_cases_return_distinct_per_task_verdicts():
    expected = ["strong_ai_candidate", "traditional_software", "ai_human_approval"]
    for payload, category in zip((AI_SUPPORT, EXACT_PRICING, HYBRID_REFUND), expected, strict=True):
        response = client.post("/api/v1/analysis/preview", json=payload)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["advisory_only"] is True
        assert body["tasks"][0]["recommendation"]["category"] == category
        assert body["policy_version"] == "rules-v1"


def test_rejects_malformed_workflow():
    response = client.post("/api/v1/analysis/preview", json={"title": "Bad", "steps": []})
    assert response.status_code == 422


def test_rejects_unexpected_fields():
    response = client.post("/api/v1/analysis/preview", json={**AI_SUPPORT, "authorize_rollout": True})
    assert response.status_code == 422


def test_discovery_api_is_stateless():
    response = client.post("/api/v1/discovery/next-question", json={"answers": []})
    assert response.status_code == 200
    assert response.json()["next_question"]["key"] == "business_problem"
    questions = client.get("/api/v1/discovery/questions")
    assert questions.status_code == 200
    assert len(questions.json()) >= 8
