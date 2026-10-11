"""Offline API contract smoke tests."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_is_public_and_rules_only() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "ai-opportunity-advisor-api",
        "version": "0.4.0",
        "mode": "rules-only",
    }


def test_openapi_exposes_health_contract() -> None:
    response = client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/health" in paths
    assert "/api/v1/analysis/preview" in paths
    assert "/api/v1/discovery/next-question" in paths
