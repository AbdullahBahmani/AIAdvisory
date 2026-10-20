"""Per-organization approval policy for the three-stage review chain.

The chain itself (three ordered stages: technical, security, business) and the
platform's hard safety rules stay where they are. This module only decides
*who may approve which stage*, and it can only ever make the platform's
defaults **stricter**:

* ``stage_roles`` may only narrow the roles that may approve a stage. The
  default is ``reviewer``/``admin`` for the technical and security stages and
  ``admin`` for the business stage; a policy can require more (for example
  "only an administrator may sign off the security stage") but never less.
* the separation-of-duties defaults are **not** configurable:
  ``allow_same_person_two_stages`` stays ``false``, ``allow_self_approval_of_
  contributed_stage`` stays ``false`` and ``require_distinct_reviewers`` stays
  ``true``. A policy that tries to change them is rejected, not ignored.
* the hard rules of the rules engine (human-only decisions, privacy,
  approval-required and severity gates in ``app/services/rule_engine.py`` and
  ``app/services/recommendation.py``) are not part of the policy document at
  all. A document that mentions them is rejected.

A policy change is itself audited, and it invalidates the approvals that were
cast under rules it changed: the affected votes stop counting towards the
chain, so the assessment needs to be approved again.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    ApiCredentialRow,
    ApprovalRow,
    AssessmentRow,
    OidcMembershipRow,
    ReviewerAssignmentRow,
)
from app.db.provisioning_models import ApprovalPolicyRow, ApprovalVoteBindingRow
from app.security import ROLE_LEVEL
from app.services.approval_chain import STAGES

#: Roles a stage accepts by default. A policy may only narrow these sets.
DEFAULT_STAGE_ROLES: dict[str, tuple[str, ...]] = {
    "technical": ("reviewer", "admin"),
    "security": ("reviewer", "admin"),
    "business": ("admin",),
}

#: Names a policy document may not contain. These are the platform's hard
#: safety rules: they are not organization configuration.
HARD_RULE_KEYS = frozenset(
    {
        "human_finalization_required",
        "human_only",
        "human_review_required",
        "require_human_approval",
        "require_approval_chain",
        "privacy_review_required",
        "provider_data_permitted",
        "approval_required",
        "severity_gate",
        "severity_floor",
        "min_severity",
        "skip_approval_chain",
        "allow_ai_finalization",
        "auto_approve",
        "single_approver",
    }
)

#: The role vocabulary is fixed by the platform.
KNOWN_ROLES = tuple(ROLE_LEVEL)


class PolicyError(Exception):
    """A policy document that the platform refuses to store."""


class ApprovalPolicy(BaseModel):
    """The approval policy of one organization."""

    model_config = ConfigDict(extra="forbid")

    #: Which roles may approve which stage. Narrowing only.
    stage_roles: dict[str, list[str]]
    #: Hard rule: a reviewer may not approve the stage they contributed to.
    allow_self_approval_of_contributed_stage: bool = False
    #: Hard rule: one person may not hold two stages of the same chain.
    allow_same_person_two_stages: bool = False
    #: Hard rule: the three stages need three different people.
    require_distinct_reviewers: bool = True
    #: Tightening knob: a contributor to any stage may not approve any stage.
    forbid_contributor_approval_of_any_stage: bool = False


def default_policy() -> ApprovalPolicy:
    """The platform default: the policy in force when an organization has none."""
    return ApprovalPolicy(
        stage_roles={stage: list(roles) for stage, roles in DEFAULT_STAGE_ROLES.items()}
    )


def _hard_rule_violation(document: Any) -> str | None:
    if not isinstance(document, dict):
        return "The policy document must be a JSON object"
    for key in document:
        if isinstance(key, str) and key.lower() in HARD_RULE_KEYS:
            return f"'{key}' is a hard safety rule and cannot be configured per organization"
    return None


def validate_policy(document: Any) -> ApprovalPolicy:
    """Validate a policy document, or refuse it.

    A policy may tighten the defaults; it may never loosen a hard rule. Every
    refusal is an explicit error, never a silent downgrade.
    """
    violation = _hard_rule_violation(document)
    if violation is not None:
        raise PolicyError(violation)
    try:
        policy = ApprovalPolicy.model_validate(document)
    except ValidationError as error:
        raise PolicyError(_describe_validation_error(error)) from None
    _check_stage_roles(policy)
    if policy.allow_self_approval_of_contributed_stage:
        raise PolicyError(
            "allow_self_approval_of_contributed_stage is a separation-of-duties rule and "
            "cannot be enabled"
        )
    if policy.allow_same_person_two_stages:
        raise PolicyError(
            "allow_same_person_two_stages is a separation-of-duties rule and cannot be enabled"
        )
    if not policy.require_distinct_reviewers:
        raise PolicyError("require_distinct_reviewers cannot be disabled")
    return policy


def _describe_validation_error(error: ValidationError) -> str:
    parts = []
    for item in error.errors():
        location = ".".join(str(part) for part in item.get("loc", ()))
        parts.append(f"{location}: {item.get('msg', 'invalid value')}")
    return "; ".join(parts) or "The policy document is invalid"


def _check_stage_roles(policy: ApprovalPolicy) -> None:
    stages = set(policy.stage_roles)
    if stages != set(STAGES):
        raise PolicyError(f"stage_roles must cover exactly: {', '.join(STAGES)}")
    for stage in STAGES:
        roles = policy.stage_roles[stage]
        if not roles:
            raise PolicyError(f"stage_roles.{stage} must not be empty")
        if len(set(roles)) != len(roles):
            raise PolicyError(f"stage_roles.{stage} contains duplicates")
        unknown = [role for role in roles if role not in KNOWN_ROLES]
        if unknown:
            raise PolicyError(f"stage_roles.{stage} contains unknown roles: {', '.join(unknown)}")
        allowed = set(DEFAULT_STAGE_ROLES[stage])
        extra = [role for role in roles if role not in allowed]
        if extra:
            raise PolicyError(
                f"stage_roles.{stage} may only narrow the default "
                f"({', '.join(sorted(allowed))}); it may not add {', '.join(extra)}"
            )


def required_role(policy: ApprovalPolicy, stage: str) -> str:
    """The minimum role that may approve ``stage`` under ``policy``."""
    roles = policy.stage_roles.get(stage) or list(DEFAULT_STAGE_ROLES[stage])
    return min(roles, key=lambda role: ROLE_LEVEL[role])


def role_satisfies(role: str | None, required: str) -> bool:
    """True when ``role`` is at least as strong as ``required``."""
    return ROLE_LEVEL.get(role or "", 0) >= ROLE_LEVEL[required]


def effective_policy(db: Session, organization_id: str | None) -> tuple[ApprovalPolicy, int]:
    """The policy in force for an organization and its version."""
    if not organization_id:
        return default_policy(), 0
    row = db.scalar(
        select(ApprovalPolicyRow).where(ApprovalPolicyRow.organization_id == organization_id)
    )
    if row is None:
        return default_policy(), 0
    try:
        policy = validate_policy(row.policy)
    except PolicyError:
        # A stored policy that no longer validates is treated as the default:
        # the platform default is always valid, and it is never weaker than
        # what a valid policy may express.
        return default_policy(), row.version
    return policy, row.version


def policy_document(policy: ApprovalPolicy) -> dict:
    """The wire representation of a policy."""
    return {
        "stage_roles": {stage: list(policy.stage_roles[stage]) for stage in STAGES},
        "allow_self_approval_of_contributed_stage": policy.allow_self_approval_of_contributed_stage,
        "allow_same_person_two_stages": policy.allow_same_person_two_stages,
        "require_distinct_reviewers": policy.require_distinct_reviewers,
        "forbid_contributor_approval_of_any_stage": policy.forbid_contributor_approval_of_any_stage,
    }


# ---------------------------------------------------------------------------
# Stage contributions (who contributed to which stage)
# ---------------------------------------------------------------------------


def stage_contributors(db: Session, assessment: AssessmentRow) -> dict[str, set[str]]:
    """Who contributed to which stage of this assessment.

    The platform records one contribution per assessment today: the *assigned
    reviewer* performs the technical (FDE) review. There is no server-side
    record of a security or business contribution — the assessment owner is a
    free-text label, not an identity — so the rule has no effect on those
    stages until the platform records such a contribution. This is a documented
    limitation, not a silent gap.
    """
    contributors: dict[str, set[str]] = {stage: set() for stage in STAGES}
    assignment = db.scalar(
        select(ReviewerAssignmentRow).where(ReviewerAssignmentRow.assessment_id == assessment.id)
    )
    if assignment is not None:
        contributors["technical"].add(assignment.reviewer_subject)
    return contributors


def contribution_conflict(
    db: Session,
    assessment: AssessmentRow,
    stage: str,
    subject: str,
    policy: ApprovalPolicy,
) -> str | None:
    """Return why ``subject`` may not approve ``stage``, or ``None``.

    The default rule is separation of duties: the reviewer who contributed to
    a stage may not approve that same stage. ``forbid_contributor_approval_of_
    any_stage`` tightens it to every stage of the assessment.
    """
    if policy.allow_self_approval_of_contributed_stage:
        return None
    contributors = stage_contributors(db, assessment)
    if subject in contributors.get(stage, set()):
        return "The reviewer who contributed to this stage may not approve it"
    if policy.forbid_contributor_approval_of_any_stage:
        for stage_name, subjects in contributors.items():
            if subject in subjects:
                return (
                    "A contributor to the "
                    f"{stage_name} stage may not approve any stage of this assessment"
                )
    return None


# ---------------------------------------------------------------------------
# Chain evaluation
# ---------------------------------------------------------------------------


def invalidated_vote_ids(db: Session, assessment: AssessmentRow) -> set[str]:
    """The votes of this assessment that a policy change invalidated."""
    rows = db.scalars(
        select(ApprovalVoteBindingRow).where(
            ApprovalVoteBindingRow.assessment_id == assessment.id,
            ApprovalVoteBindingRow.invalidated_at.is_not(None),
        )
    ).all()
    return {row.vote_id for row in rows}


def policy_aware_chain(
    db: Session, assessment: AssessmentRow
) -> tuple[dict[str, str], dict[str, str]]:
    """Replay the approval chain, ignoring votes a policy change invalidated.

    An invalidated stage also invalidates the stages after it: a chain whose
    security stage no longer counts cannot be complete through a business
    approval that was given on top of it.
    """
    from app.services.approval_chain import current_chain

    states, actors = current_chain(db, assessment, excluded=invalidated_vote_ids(db, assessment))
    for position, stage in enumerate(STAGES):
        if stage not in states:
            for later in STAGES[position + 1 :]:
                states.pop(later, None)
                actors.pop(later, None)
            break
    return states, actors


# ---------------------------------------------------------------------------
# Vote bindings and invalidation
# ---------------------------------------------------------------------------


def record_vote_binding(
    db: Session,
    assessment: AssessmentRow,
    vote: ApprovalRow,
    actor_role: str,
    policy_version: int,
) -> ApprovalVoteBindingRow:
    """Record which policy version applied to a vote."""
    binding = ApprovalVoteBindingRow(
        vote_id=vote.id,
        organization_id=assessment.organization_id,
        assessment_id=assessment.id,
        stage=vote.stage,
        actor_role=actor_role,
        policy_version=policy_version,
    )
    db.add(binding)
    return binding


def _actor_role(db: Session, organization_id: str, subject: str) -> str | None:
    """The role a subject currently holds in an organization."""
    membership = db.scalar(
        select(OidcMembershipRow).where(
            OidcMembershipRow.organization_id == organization_id,
            OidcMembershipRow.subject == subject,
            OidcMembershipRow.active.is_(True),
        )
    )
    if membership is not None:
        return membership.role
    credential = db.scalar(
        select(ApiCredentialRow).where(
            ApiCredentialRow.organization_id == organization_id,
            ApiCredentialRow.subject == subject,
            ApiCredentialRow.revoked_at.is_(None),
        )
    )
    if credential is not None:
        expiration = credential.expires_at
        if expiration.tzinfo is None:
            expiration = expiration.replace(tzinfo=UTC)
        if expiration > datetime.now(UTC):
            return credential.role
    return None


def _current_votes(db: Session, organization_id: str) -> list[tuple[ApprovalRow, AssessmentRow]]:
    """The votes of an organization that still belong to a current revision."""
    rows = db.execute(
        select(ApprovalRow, AssessmentRow)
        .join(AssessmentRow, AssessmentRow.id == ApprovalRow.assessment_id)
        .where(
            AssessmentRow.organization_id == organization_id,
            ApprovalRow.assessment_revision == AssessmentRow.revision,
        )
        .order_by(ApprovalRow.created_at, ApprovalRow.id)
    ).all()
    return [(row[0], row[1]) for row in rows]


def invalidate_affected_votes(
    db: Session,
    organization_id: str,
    old_policy: ApprovalPolicy,
    new_policy: ApprovalPolicy,
) -> list[dict]:
    """Invalidate the approvals whose rules changed.

    A vote is invalidated when the role requirements of its stage changed, or
    when the role its actor held no longer satisfies the new requirement of
    that stage. Votes of stages whose rules did not change keep counting.
    """
    invalidated: list[dict] = []
    bindings = {
        row.vote_id: row
        for row in db.scalars(
            select(ApprovalVoteBindingRow).where(
                ApprovalVoteBindingRow.organization_id == organization_id,
                ApprovalVoteBindingRow.invalidated_at.is_(None),
            )
        ).all()
    }
    for vote, assessment in _current_votes(db, organization_id):
        binding = bindings.get(vote.id)
        stage = vote.stage
        if stage not in DEFAULT_STAGE_ROLES:
            continue
        old_roles = set(old_policy.stage_roles.get(stage, DEFAULT_STAGE_ROLES[stage]))
        new_roles = set(new_policy.stage_roles.get(stage, DEFAULT_STAGE_ROLES[stage]))
        rules_changed = old_roles != new_roles
        actor_role = binding.actor_role if binding is not None else None
        if binding is None:
            # A vote cast before the platform recorded bindings: look the role
            # up and fall back to "unknown", which never satisfies a rule.
            actor_role = _actor_role(db, organization_id, vote.actor)
            binding = ApprovalVoteBindingRow(
                vote_id=vote.id,
                organization_id=organization_id,
                assessment_id=assessment.id,
                stage=stage,
                actor_role=actor_role or "unknown",
                policy_version=0,
            )
            db.add(binding)
            db.flush()
        if not rules_changed and role_satisfies(actor_role, required_role(new_policy, stage)):
            continue
        reason = (
            "the role requirements of the stage changed"
            if rules_changed
            else "the actor no longer satisfies the role requirement of the stage"
        )
        binding.invalidated_at = datetime.now(UTC)
        binding.invalidation_reason = reason[:200]
        invalidated.append(
            {
                "vote_id": vote.id,
                "assessment_id": assessment.id,
                "stage": stage,
                "actor": vote.actor,
                "reason": reason,
            }
        )
    return invalidated


def store_policy(
    db: Session,
    organization_id: str,
    document: Any,
    actor_subject: str,
) -> dict:
    """Validate, store and apply a new policy for an organization.

    Returns a summary with the new version and the invalidated approvals.
    """
    policy = validate_policy(document)
    row = db.scalar(
        select(ApprovalPolicyRow).where(ApprovalPolicyRow.organization_id == organization_id)
    )
    old_policy, old_version = effective_policy(db, organization_id)
    changed = policy_document(policy) != policy_document(old_policy)
    if row is None:
        row = ApprovalPolicyRow(
            organization_id=organization_id,
            version=old_version + 1,
            policy=policy_document(policy),
            updated_by=actor_subject,
        )
        db.add(row)
    else:
        row.policy = policy_document(policy)
        row.version = old_version + 1
        row.updated_by = actor_subject
        row.updated_at = datetime.now(UTC)
    db.flush()
    invalidated = invalidate_affected_votes(db, organization_id, old_policy, policy)
    return {
        "organization_id": organization_id,
        "version": row.version,
        "changed": changed,
        "policy": policy_document(policy),
        "invalidated_approvals": invalidated,
    }
