"""Per-organization approval policy: tightening only, never loosening.

The policy may narrow who may approve which review stage. It may not weaken a
hard safety rule, it may not weaken the separation-of-duties defaults, and a
policy change invalidates the approvals it affected.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.provisioning_models import OrganizationAuditEventRow
from tests.test_security import secure_client  # noqa: F401 - reuse the isolated auth fixture

POLICY_URL = "/api/v1/workspace/approval-policy"


def _assessment(client, admin):
    created = client.post("/api/v1/assessments", json={"title": "Policy assessment"}, headers=admin)
    assert created.status_code == 201, created.text
    aid = created.json()["id"]
    workflow = client.post(
        f"/api/v1/assessments/{aid}/workflows", json={"name": "Pricing"}, headers=admin
    )
    assert workflow.status_code == 201, workflow.text
    wid = workflow.json()["id"]
    step = client.post(
        f"/api/v1/workflows/{wid}/steps", json={"name": "Calculate", "order": 0}, headers=admin
    )
    assert step.status_code == 201, step.text
    sid = step.json()["id"]
    task = client.post(
        f"/api/v1/workflow-steps/{sid}/tasks",
        json={"title": "Pricing", "signals": {"exact_math": True, "deterministic_rules": True}},
        headers=admin,
    )
    assert task.status_code == 201, task.text
    assert client.post(f"/api/v1/assessments/{aid}/analyze", headers=admin).status_code == 200
    return aid


def _approve(client, aid, headers, stage):
    return client.post(
        f"/api/v1/assessments/{aid}/approvals",
        json={"stage": stage, "decision": "approved", "rationale": f"Verified {stage}"},
        headers=headers,
    )


def _policy(**overrides) -> dict:
    policy = {
        "stage_roles": {
            "technical": ["reviewer", "admin"],
            "security": ["reviewer", "admin"],
            "business": ["admin"],
        },
        "allow_self_approval_of_contributed_stage": False,
        "allow_same_person_two_stages": False,
        "require_distinct_reviewers": True,
        "forbid_contributor_approval_of_any_stage": False,
    }
    policy.update(overrides)
    return policy


def test_policy_defaults_are_the_platform_defaults(secure_client):
    client, provision, _ = secure_client
    admin, _, _ = provision("Policy Org", "admin", "admin")
    response = client.get(POLICY_URL, headers=admin)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["version"] == 0
    assert body["stage_requirements"] == {
        "technical": "reviewer",
        "security": "reviewer",
        "business": "admin",
    }
    assert body["hard_rules"]["allow_same_person_two_stages"] is False
    assert body["hard_rules"]["require_distinct_reviewers"] is True


def test_policy_may_be_tightened(secure_client):
    client, provision, _ = secure_client
    admin, _, _ = provision("Tightening", "admin", "admin")
    tightened = _policy(
        stage_roles={
            "technical": ["reviewer", "admin"],
            "security": ["admin"],
            "business": ["admin"],
        }
    )
    response = client.put(POLICY_URL, json=tightened, headers=admin)
    assert response.status_code == 200, response.text
    assert response.json()["version"] == 1
    assert response.json()["policy"]["stage_roles"]["security"] == ["admin"]

    current = client.get(POLICY_URL, headers=admin).json()
    assert current["stage_requirements"]["security"] == "admin"


def test_policy_cannot_loosen_a_hard_rule(secure_client):
    client, provision, _ = secure_client
    admin, _, _ = provision("Hard Rules", "admin", "admin")
    refusals = [
        # separation of duties is not organization configuration
        _policy(allow_same_person_two_stages=True),
        _policy(allow_self_approval_of_contributed_stage=True),
        _policy(require_distinct_reviewers=False),
        # a stage may not accept a weaker role than the platform default
        _policy(
            stage_roles={
                "technical": ["analyst", "reviewer", "admin"],
                "security": ["reviewer", "admin"],
                "business": ["admin"],
            }
        ),
        _policy(
            stage_roles={
                "technical": ["reviewer", "admin"],
                "security": ["reviewer", "admin"],
                "business": ["reviewer", "admin"],
            }
        ),
        # the rules engine's hard rules are not configurable at all
        {"human_finalization_required": False},
        {"severity_gate": "low"},
        {"require_approval_chain": False},
        # a stage may not be dropped
        _policy(stage_roles={"technical": ["reviewer"], "security": ["admin"]}),
        # unknown keys are refused instead of ignored
        _policy(make_everything_fine=True),
    ]
    for document in refusals:
        response = client.put(POLICY_URL, json=document, headers=admin)
        assert response.status_code == 422, (document, response.text)
    # Nothing was stored: the policy is still the platform default.
    assert client.get(POLICY_URL, headers=admin).json()["version"] == 0


def test_policy_requires_a_verified_administrator(secure_client):
    client, provision, _ = secure_client
    admin, _, _ = provision("Governed", "admin", "admin")
    analyst, _, _ = provision("Governed", "analyst", "analyst")
    response = client.put(POLICY_URL, json=_policy(), headers=analyst)
    assert response.status_code == 403
    assert client.get(POLICY_URL, headers=analyst).status_code == 200


def test_policy_change_is_audited(secure_client):
    client, provision, engine = secure_client
    admin, org_id, _ = provision("Audited", "admin", "admin")
    response = client.put(POLICY_URL, json=_policy(), headers=admin)
    assert response.status_code == 200
    with Session(engine) as db:
        events = db.scalars(
            select(OrganizationAuditEventRow)
            .where(OrganizationAuditEventRow.organization_id == org_id)
            .order_by(OrganizationAuditEventRow.id)
        ).all()
    assert [event.event_type for event in events] == ["approval.policy_changed"]
    event = events[0]
    assert event.actor == "admin"
    assert event.actor_verified is True
    assert event.details["policy_version"] == 1


def test_policy_change_invalidates_the_approvals_it_affected(secure_client, monkeypatch):
    client, provision, _ = secure_client
    admin, _, _ = provision("Invalidated", "admin", "admin")
    sponsor, _, _ = provision("Invalidated", "sponsor", "admin")
    tech, _, _ = provision("Invalidated", "tech", "reviewer")
    sec, _, _ = provision("Invalidated", "sec", "reviewer")
    aid = _assessment(client, admin)
    assert _approve(client, aid, tech, "technical").status_code == 201
    assert _approve(client, aid, sec, "security").status_code == 201
    assert _approve(client, aid, admin, "business").status_code == 201
    assert (
        client.get(f"/api/v1/assessments/{aid}/approvals", headers=admin).json()["complete"] is True
    )

    # The organization now wants an administrator to sign off the security stage.
    tightened = _policy(
        stage_roles={
            "technical": ["reviewer", "admin"],
            "security": ["admin"],
            "business": ["admin"],
        }
    )
    response = client.put(POLICY_URL, json=tightened, headers=admin)
    assert response.status_code == 200, response.text
    invalidated = response.json()["invalidated_approvals"]
    assert [item["stage"] for item in invalidated] == ["security"]

    chain = client.get(f"/api/v1/assessments/{aid}/approvals", headers=admin).json()
    assert chain["stages"]["technical"] == "approved"
    assert chain["stages"]["security"] == "pending"
    assert chain["complete"] is False

    # The reviewer can no longer approve the security stage at all.
    assert _approve(client, aid, sec, "security").status_code == 403
    # An administrator can, and the chain is complete again once the business
    # stage is approved by somebody else: one person may not hold two stages.
    assert _approve(client, aid, admin, "security").status_code == 201
    assert _approve(client, aid, sponsor, "business").status_code == 201
    assert (
        client.get(f"/api/v1/assessments/{aid}/approvals", headers=admin).json()["complete"] is True
    )


def test_policy_stage_gate_is_enforced(secure_client):
    client, provision, _ = secure_client
    admin, _, _ = provision("Gated", "admin", "admin")
    tech, _, _ = provision("Gated", "tech", "reviewer")
    sec, _, _ = provision("Gated", "sec", "reviewer")
    aid = _assessment(client, admin)
    tightened = _policy(
        stage_roles={
            "technical": ["reviewer", "admin"],
            "security": ["admin"],
            "business": ["admin"],
        }
    )
    assert client.put(POLICY_URL, json=tightened, headers=admin).status_code == 200
    assert _approve(client, aid, tech, "technical").status_code == 201
    assert _approve(client, aid, sec, "security").status_code == 403
    assert _approve(client, aid, admin, "security").status_code == 201


def test_separation_of_duties_defaults_hold(secure_client):
    """One person may not hold two stages, and not the stage they contributed to."""
    client, provision, _ = secure_client
    admin, _, _ = provision("SoD", "admin", "admin")
    reviewer, _, _ = provision("SoD", "fde", "reviewer")
    aid = _assessment(client, admin)
    assert _approve(client, aid, reviewer, "technical").status_code == 201
    # The same person may not take the next stage.
    assert _approve(client, aid, reviewer, "security").status_code == 409
    # The assigned reviewer contributed to the technical stage and may not
    # approve it.
    assert (
        client.put(
            f"/api/v1/assessments/{aid}/reviewer",
            json={"reviewer_subject": "fde"},
            headers=admin,
        ).status_code
        == 200
    )
    assert _approve(client, aid, reviewer, "technical").status_code == 409


def test_policy_does_not_affect_other_organizations(secure_client):
    client, provision, _ = secure_client
    admin_a, _, _ = provision("Tenant A", "admin-a", "admin")
    admin_b, _, _ = provision("Tenant B", "admin-b", "admin")
    tightened = _policy(
        stage_roles={
            "technical": ["admin"],
            "security": ["admin"],
            "business": ["admin"],
        }
    )
    assert client.put(POLICY_URL, json=tightened, headers=admin_a).status_code == 200
    assert client.get(POLICY_URL, headers=admin_b).json()["version"] == 0
    assert client.get(POLICY_URL, headers=admin_b).json()["stage_requirements"]["technical"] == (
        "reviewer"
    )
