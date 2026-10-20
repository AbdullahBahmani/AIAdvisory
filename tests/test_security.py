"""Negative tenant/auth controls with an isolated real database."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ApiCredentialRow, AssessmentRow, OrganizationRow
from app.db.session import get_db
from app.main import app
from app.security import digest_token, make_token


@pytest.fixture
def secure_client(monkeypatch, db_engine):
    """A test client bound to an isolated database on the selected backend."""
    monkeypatch.delenv("ADVISORY_DEMO_MODE", raising=False)
    monkeypatch.delenv("ADVISORY_REVIEW_TOKEN", raising=False)
    engine = db_engine

    def override_db():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client:

        def provision(name, subject, role="analyst", *, expires_days=2, revoked=False):
            with Session(engine) as db:
                org = db.scalar(select(OrganizationRow).where(OrganizationRow.name == name))
                if not org:
                    org = OrganizationRow(name=name)
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
                return {"Authorization": f"Bearer {token}"}, org.id, row.id

        yield client, provision, engine
    app.dependency_overrides.clear()


def test_fail_closed_all_routes(secure_client):
    client, _, _ = secure_client
    for method, path in [
        ("get", "/api/v1/assessments"),
        ("post", "/api/v1/assessments"),
        ("post", "/api/v1/analysis/preview"),
        ("get", "/api/v1/discovery/questions"),
        ("get", "/api/v1/assessments/guess/report"),
        ("get", "/api/v1/assessments/guess/ai-runs"),
    ]:
        assert getattr(client, method)(path).status_code == 401, (method, path)
    assert client.get("/health").status_code == 200


def test_tenant_isolation_all_nested_resources_reports_and_audit(secure_client):
    client, provision, _ = secure_client
    alice, _, _ = provision("Org A", "alice", "reviewer")
    bob, _, _ = provision("Org B", "bob", "admin")
    assessment = client.post("/api/v1/assessments", json={"title": "A-only"}, headers=alice)
    assert assessment.status_code == 201, assessment.text
    aid = assessment.json()["id"]
    wf = client.post(
        f"/api/v1/assessments/{aid}/workflows", json={"name": "Workflow"}, headers=alice
    ).json()["id"]
    step = client.post(
        f"/api/v1/workflows/{wf}/steps", json={"name": "Step", "order": 0}, headers=alice
    ).json()["id"]
    task = client.post(
        f"/api/v1/workflow-steps/{step}/tasks",
        json={"title": "Compute", "signals": {"exact_math": True}},
        headers=alice,
    ).json()["id"]
    assert client.post(f"/api/v1/assessments/{aid}/analyze", headers=alice).status_code == 200
    assert [x["id"] for x in client.get("/api/v1/assessments", headers=bob).json()] == []
    for path in [
        f"/api/v1/assessments/{aid}",
        f"/api/v1/workflows/{wf}",
        f"/api/v1/assessments/{aid}/report",
        f"/api/v1/assessments/{aid}/scores",
        f"/api/v1/assessments/{aid}/audit",
        f"/api/v1/assessments/{aid}/ai-runs",
    ]:
        assert client.get(path, headers=bob).status_code == 404, path
    for method, path, payload in [
        ("patch", f"/api/v1/tasks/{task}", {"title": "Steal"}),
        ("delete", f"/api/v1/workflow-steps/{step}", None),
        (
            "post",
            f"/api/v1/assessments/{aid}/fde-review",
            {"reviewer_id": "bob", "selected_category": "traditional_software", "rationale": "x"},
        ),
    ]:
        resp = (
            getattr(client, method)(path, json=payload, headers=bob)
            if payload is not None
            else getattr(client, method)(path, headers=bob)
        )
        assert resp.status_code == 404, (path, resp.text)
    assert client.get(f"/api/v1/assessments/{aid}/report", headers=alice).status_code == 200


def test_role_matrix_and_verified_review(secure_client):
    client, provision, _ = secure_client
    admin, _, _ = provision("Org", "administrator", "admin")
    viewer, _, _ = provision("Org", "viewer", "viewer")
    analyst, _, _ = provision("Org", "analyst", "analyst")
    reviewer, _, _ = provision("Org", "fde", "reviewer")
    aid = client.post("/api/v1/assessments", json={"title": "Pricing"}, headers=admin).json()["id"]
    assert client.get(f"/api/v1/assessments/{aid}", headers=viewer).status_code == 200
    assert (
        client.patch(f"/api/v1/assessments/{aid}", json={"title": "No"}, headers=viewer).status_code
        == 403
    )
    wf = client.post(
        f"/api/v1/assessments/{aid}/workflows", json={"name": "Math"}, headers=analyst
    ).json()["id"]
    sid = client.post(
        f"/api/v1/workflows/{wf}/steps", json={"name": "Calculate", "order": 0}, headers=analyst
    ).json()["id"]
    client.post(
        f"/api/v1/workflow-steps/{sid}/tasks",
        json={
            "title": "Apply exact rules",
            "signals": {"exact_math": True, "deterministic_rules": True},
        },
        headers=analyst,
    )
    assert client.post(f"/api/v1/assessments/{aid}/analyze", headers=analyst).status_code == 200
    payload = {
        "reviewer_id": "fde",
        "selected_category": "traditional_software",
        "rationale": "Rule based",
    }
    assert (
        client.post(
            f"/api/v1/assessments/{aid}/fde-review", json=payload, headers=analyst
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/api/v1/assessments/{aid}/fde-review",
            json={**payload, "reviewer_id": "someone-else"},
            headers=reviewer,
        ).status_code
        == 403
    )
    saved = client.post(f"/api/v1/assessments/{aid}/fde-review", json=payload, headers=reviewer)
    assert saved.status_code == 201, saved.text
    assert saved.json()["identity_unverified"] is False
    assert (
        client.post(f"/api/v1/assessments/{aid}/final-decision", headers=analyst).status_code == 403
    )
    final = client.post(f"/api/v1/assessments/{aid}/final-decision", headers=reviewer)
    assert final.status_code == 200, final.text
    assert final.json()["identity_unverified"] is False
    assert client.delete(f"/api/v1/assessments/{aid}", headers=reviewer).status_code == 403
    assert client.post(f"/api/v1/assessments/{aid}/reopen", headers=reviewer).status_code == 200
    assert client.delete(f"/api/v1/assessments/{aid}", headers=admin).status_code == 204


def test_revoked_expired_forged_tokens(secure_client):
    client, provision, _ = secure_client
    valid, _, _ = provision("Org", "valid", "viewer")
    expired, _, _ = provision("Org", "expired", "viewer", expires_days=-1)
    revoked, _, _ = provision("Org", "revoked", "viewer", revoked=True)
    for headers in [
        expired,
        revoked,
        {"Authorization": "Bearer aia_fake"},
        {"Authorization": valid["Authorization"] + "garbage"},
    ]:
        assert client.get("/api/v1/assessments", headers=headers).status_code == 401
    assert client.get("/api/v1/assessments", headers=valid).status_code == 200


def test_unowned_legacy_rows_are_quarantined(secure_client):
    client, provision, engine = secure_client
    headers, _, _ = provision("Org", "analyst", "analyst")
    with Session(engine) as db:
        row = AssessmentRow(title="Legacy assessment without tenant")
        db.add(row)
        db.commit()
        aid = row.id
    assert client.get(f"/api/v1/assessments/{aid}", headers=headers).status_code == 404
    assert all(x["id"] != aid for x in client.get("/api/v1/assessments", headers=headers).json())


def test_security_response_headers(secure_client):
    client, _, _ = secure_client
    r = client.get("/health")
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-request-id"]
