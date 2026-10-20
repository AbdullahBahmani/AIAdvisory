"""Organization approval policy (who may approve which review stage).

The policy is organization configuration: an administrator may *tighten* the
platform's default role gates for the three review stages, but the platform's
hard safety rules and the separation-of-duties defaults are not configurable
and a policy that tries to weaken them is rejected (422), never stored.

A policy change is audited and invalidates the approvals it affected, so an
assessment approved under the old rules has to be approved again.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.security import current_principal, require_role
from app.services.approval_policy import (
    DEFAULT_STAGE_ROLES,
    HARD_RULE_KEYS,
    PolicyError,
    effective_policy,
    policy_document,
    required_role,
    store_policy,
)
from app.services.audit import audit_organization

router = APIRouter(prefix="/api/v1/workspace", tags=["approval-policy"])


def _verified_organization(db: Session) -> str:
    principal = current_principal(db)
    if not principal.verified or not principal.organization_id:
        raise HTTPException(
            status_code=403,
            detail="Approval policies require an authenticated organization administrator",
        )
    return principal.organization_id


@router.get("/approval-policy")
def get_approval_policy(db: Session = Depends(get_db)) -> dict:
    """The approval policy in force for the caller's organization."""
    organization_id = _verified_organization(db)
    policy, version = effective_policy(db, organization_id)
    return {
        "organization_id": organization_id,
        "version": version,
        "policy": policy_document(policy),
        "stage_requirements": {
            stage: required_role(policy, stage) for stage in DEFAULT_STAGE_ROLES
        },
        "defaults": {stage: list(roles) for stage, roles in DEFAULT_STAGE_ROLES.items()},
        "hard_rules": {
            "allow_self_approval_of_contributed_stage": False,
            "allow_same_person_two_stages": False,
            "require_distinct_reviewers": True,
            "non_configurable": sorted(HARD_RULE_KEYS),
        },
    }


@router.put("/approval-policy")
def put_approval_policy(
    payload: dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
) -> dict:
    """Set the approval policy of the caller's organization.

    Only a verified organization administrator may change the policy. The
    document is validated first: it may narrow the stage role requirements,
    and every attempt to loosen a hard rule is rejected.
    """
    organization_id = _verified_organization(db)
    require_role(db, "admin")
    try:
        result = store_policy(db, organization_id, payload, current_principal(db).subject)
    except PolicyError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    audit_organization(
        db,
        organization_id,
        "approval.policy_changed",
        policy_version=result["version"],
        changed=result["changed"],
        policy=result["policy"],
        invalidated_approvals=result["invalidated_approvals"],
    )
    db.commit()
    return result
