"""Tenant-isolation and human workflow regression tests (synthetic local DB only)."""
from __future__ import annotations

from tests.test_security import secure_client  # reuse a real isolated SQLite auth fixture


def _assessment(client, admin):
    created = client.post("/api/v1/assessments", json={"title": "Enterprise discovery"}, headers=admin)
    assert created.status_code == 201, created.text
    aid = created.json()["id"]
    workflow = client.post(f"/api/v1/assessments/{aid}/workflows", json={"name": "Pricing"}, headers=admin)
    assert workflow.status_code == 201, workflow.text
    wid = workflow.json()["id"]
    step = client.post(f"/api/v1/workflows/{wid}/steps",
                       json={"name": "Calculate", "order": 0}, headers=admin)
    assert step.status_code == 201, step.text
    sid = step.json()["id"]
    task = client.post(f"/api/v1/workflow-steps/{sid}/tasks",
                       json={"title": "Pricing", "signals": {"exact_math": True, "deterministic_rules": True}},
                       headers=admin)
    assert task.status_code == 201, task.text
    analysis = client.post(f"/api/v1/assessments/{aid}/analyze", headers=admin)
    assert analysis.status_code == 200, analysis.text
    return aid


def test_assignment_is_tenant_and_membership_bound(secure_client):
    client, provision, _ = secure_client
    admin, _, _ = provision("Company A", "manager-a", "admin")
    analyst, _, _ = provision("Company A", "analyst-a", "analyst")
    reviewer, _, _ = provision("Company A", "reviewer-a", "reviewer")
    outsider, _, _ = provision("Company B", "reviewer-b", "admin")
    aid = _assessment(client, admin)
    uri = f"/api/v1/assessments/{aid}/reviewer"
    assert client.put(uri, json={"reviewer_subject": "reviewer-a"}, headers=analyst).status_code == 403
    assert client.put(uri, json={"reviewer_subject": "reviewer-b"}, headers=admin).status_code == 422
    assert client.put(uri, json={"reviewer_subject": "analyst-a"}, headers=admin).status_code == 422
    assert client.put(uri, json={"reviewer_subject": "reviewer-a"}, headers=outsider).status_code == 404
    assigned = client.put(uri, json={"reviewer_subject": "reviewer-a"}, headers=admin)
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["reviewer_subject"] == "reviewer-a"
    assert client.get(uri, headers=reviewer).json()["reviewer_subject"] == "reviewer-a"
    assert client.get(uri, headers=outsider).status_code == 404
    assert not any(m["subject"] == "reviewer-b" for m in client.get("/api/v1/workspace/members", headers=admin).json())


def test_comments_are_attributed_versioned_and_private(secure_client):
    client, provision, _ = secure_client
    admin, _, _ = provision("Tenant X", "admin-x", "admin")
    reviewer, _, _ = provision("Tenant X", "reviewer-x", "reviewer")
    outsider, _, _ = provision("Tenant Y", "admin-y", "admin")
    aid = _assessment(client, admin)
    uri = f"/api/v1/assessments/{aid}/comments"
    assert client.post(uri, headers=reviewer, json={"body": "  "}).status_code == 422
    posted = client.post(uri, headers=reviewer, json={"body": "Evidence needs review"})
    assert posted.status_code == 201, posted.text
    assert posted.json()["author"] == "reviewer-x"
    assert posted.json()["assessment_revision"] == client.get(f"/api/v1/assessments/{aid}", headers=reviewer).json()["revision"]
    assert client.get(uri, headers=outsider).status_code == 404
    assert client.post(uri, headers=outsider, json={"body": "Leak attempt"}).status_code == 404
    assert client.get(uri, headers=admin).json()[0]["body"] == "Evidence needs review"
    assert "assessment.comment_added" in [a["event_type"] for a in client.get(
        f"/api/v1/assessments/{aid}/audit", headers=admin).json()]


def test_ordered_distinct_approvals_and_revisions(secure_client):
    client, provision, _ = secure_client
    admin, _, _ = provision("Company", "business", "admin")
    tech, _, _ = provision("Company", "technical", "reviewer")
    security, _, _ = provision("Company", "security", "reviewer")
    aid = _assessment(client, admin)
    url = f"/api/v1/assessments/{aid}/approvals"
    assert client.post(url, json={"stage": "security", "decision": "approved", "rationale": "Security checks"}, headers=security).status_code == 409
    assert client.post(url, json={"stage": "business", "decision": "approved", "rationale": "Business checks"}, headers=tech).status_code == 403
    assert client.post(url, json={"stage": "technical", "decision": "approved", "rationale": "Technical checks"}, headers=tech).status_code == 201
    assert client.post(url, json={"stage": "security", "decision": "approved", "rationale": "Security checks"}, headers=tech).status_code == 409
    assert client.post(url, json={"stage": "security", "decision": "approved", "rationale": "Security checks"}, headers=security).status_code == 201
    assert client.post(url, json={"stage": "business", "decision": "approved", "rationale": "Business checks"}, headers=admin).status_code == 201
    assert client.get(url, headers=tech).json()["complete"] is True

    # A new technical vote invalidates the downstream stages.
    assert client.post(url, json={"stage": "technical", "decision": "changes_requested", "rationale": "New design issue"}, headers=tech).status_code == 201
    chain = client.get(url, headers=tech).json()
    assert chain["stages"] == {"technical": "changes_requested", "security": "pending", "business": "pending"}
    assert not chain["complete"]
    client.patch(f"/api/v1/assessments/{aid}", json={"title": "New requirements"}, headers=admin)
    assert client.get(url, headers=tech).json()["history"] == []


def test_enterprise_chain_blocks_finalization_until_complete(secure_client, monkeypatch):
    client, provision, _ = secure_client
    admin, _, _ = provision("Approvals", "sponsor", "admin")
    reviewer, _, _ = provision("Approvals", "fde", "reviewer")
    security, _, _ = provision("Approvals", "sec", "reviewer")
    aid = _assessment(client, admin)
    review = client.post(f"/api/v1/assessments/{aid}/fde-review", headers=reviewer, json={
        "reviewer_id": "fde", "selected_category": "traditional_software", "rationale": "Exact pricing rules"})
    assert review.status_code == 201, review.text
    monkeypatch.setenv("ADVISORY_REQUIRE_APPROVAL_CHAIN", "1")
    final = f"/api/v1/assessments/{aid}/final-decision"
    assert client.post(final, headers=reviewer).status_code == 409
    url = f"/api/v1/assessments/{aid}/approvals"
    for who, stage in ((reviewer, "technical"), (security, "security"), (admin, "business")):
        vote = client.post(url, json={"stage": stage, "decision": "approved", "rationale": f"Verified {stage}"}, headers=who)
        assert vote.status_code == 201, vote.text
    assert client.post(final, headers=reviewer).status_code == 200


def test_notification_inbox_scopes_and_read_receipt(secure_client):
    client, provision, _ = secure_client
    admin, _, _ = provision("Inbox A", "admin", "admin")
    assigned, _, _ = provision("Inbox A", "fde", "reviewer")
    outsider, _, _ = provision("Inbox B", "other", "admin")
    aid = _assessment(client, admin)
    assert client.put(f"/api/v1/assessments/{aid}/reviewer",
                      json={"reviewer_subject": "fde"}, headers=admin).status_code == 200
    assert client.post(f"/api/v1/assessments/{aid}/comments",
                       json={"body": "Please review the assumptions"}, headers=admin).status_code == 201
    inbox = client.get("/api/v1/workspace/inbox", headers=assigned).json()
    assert any(x["id"] == aid for x in inbox["assigned_assessments"])
    assert len(inbox["notifications"]) >= 2
    nid = inbox["notifications"][0]["id"]
    assert client.post(f"/api/v1/workspace/notifications/{nid}/read", headers=outsider).status_code == 404
    assert client.post(f"/api/v1/workspace/notifications/{nid}/read", headers=admin).status_code == 404
    assert client.post(f"/api/v1/workspace/notifications/{nid}/read", headers=assigned).status_code == 200
    summary = client.get("/api/v1/workspace/summary", headers=assigned).json()
    assert summary["assigned_to_me"] == 1
    assert summary["unread_notifications"] >= 1
    assert client.get("/api/v1/workspace/summary", headers=outsider).json()["total"] == 0


def test_only_assigned_reviewer_can_record_review(secure_client):
    client, provision, _ = secure_client
    admin, _, _ = provision("Assigned", "admin", "admin")
    primary, _, _ = provision("Assigned", "primary", "reviewer")
    other, _, _ = provision("Assigned", "other", "reviewer")
    aid = _assessment(client, admin)
    assignment = client.put(f"/api/v1/assessments/{aid}/reviewer",
                            json={"reviewer_subject": "primary"}, headers=admin)
    assert assignment.status_code == 200
    endpoint = f"/api/v1/assessments/{aid}/fde-review"
    body = {"selected_category": "traditional_software", "rationale": "Rule-based math"}
    assert client.post(endpoint, json={**body, "reviewer_id": "other"}, headers=other).status_code == 403
    accepted = client.post(endpoint, json={**body, "reviewer_id": "primary"}, headers=primary)
    assert accepted.status_code == 201, accepted.text


def test_new_workspace_routes_are_authenticated_and_tenant_scoped(secure_client):
    client, provision, _ = secure_client
    admin, _, _ = provision("Protected", "admin", "admin")
    outside, _, _ = provision("Other", "outside", "admin")
    aid = _assessment(client, admin)
    for url in ("/api/v1/workspace/inbox", "/api/v1/workspace/summary",
                f"/api/v1/assessments/{aid}/approvals",
                f"/api/v1/assessments/{aid}/comments"):
        assert client.get(url).status_code == 401, url
    assert client.get(f"/api/v1/assessments/{aid}/approvals", headers=outside).status_code == 404
    assert client.post(f"/api/v1/assessments/{aid}/approvals",
        json={"stage": "technical", "decision": "approved", "rationale": "Attempt to cross tenant"},
        headers=outside).status_code == 404
