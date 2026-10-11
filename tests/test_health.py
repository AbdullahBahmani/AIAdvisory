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
        "version": "0.1.0",
        "mode": "rules-only",
    }


def test_openapi_exposes_health_contract() -> None:
    response = client.get("/openapi.json")
    assert response.status_code == 200
    assert "/health" in response.json()["paths"]
