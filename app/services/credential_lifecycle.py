"""Credential and membership lifecycle.

This module owns the policy that the credential table alone cannot express. It is
deliberately free of HTTP concerns: the operator CLI (`scripts/admin.py`) and the
tests call the same functions, so the policy cannot drift between the CLI and the
API.

The policy (see ``docs/credential-lifecycle.md``):

* **Expiry is mandatory.** Every credential carries an expiry. A credential that
  never expires is an explicit operator opt-in (``unlimited``) and is labelled
  as such wherever credentials are listed.
* **Strict leeway.** A credential stops working ``CREDENTIAL_EXPIRY_LEEWAY``
  seconds *before* its stated expiry. This is not a grace period; it bounds the
  damage of a skewed clock by exactly the documented amount.
* **Rotation without a window of two valid keys.** Rotation retires the previous
  credential and issues the replacement inside one transaction. A short overlap is
  possible, but only when the operator asks for it explicitly and it is bounded,
  and the listing always reports it.
* **De-provisioning on role change.** When a membership is reduced or deactivated,
  the credentials that relied on the old role are revoked and the reviewer
  assignments that can no longer be acted on are cleared, so an assessment never
  stays blocked on somebody who cannot act.
* **The last administrator is never locked out by accident.** A revocation that
  would leave an organization without an administrator is refused unless the
  operator overrides it explicitly; the override is audited.
* **Everything is audited.** Every change goes through the shared audit gate in
  ``app/services/audit.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    ApiCredentialRow,
    AssessmentRow,
    OidcMembershipRow,
    OrganizationRow,
    ReviewerAssignmentRow,
    utcnow,
)
from app.security import (
    CREDENTIAL_EXPIRY_LEEWAY,
    ROLE_LEVEL,
    as_utc,
    credential_is_expired,
    credential_is_live,
    digest_token,
    make_token,
)
from app.services.audit import audit_tenant

#: Default lifetime of a newly issued credential.
DEFAULT_CREDENTIAL_LIFETIME_DAYS = 30

#: Longest lifetime an operator may issue.
MAX_CREDENTIAL_LIFETIME_DAYS = 365

#: Longest rotation overlap an operator may ask for (15 minutes).
MAX_ROTATION_OVERLAP_SECONDS = 900

#: Actor recorded for operator commands that run outside a request. It is never
#: a verified identity: operator actions stay marked unverified in the audit trail.
OPERATOR_ACTOR = "operator:cli"


class LifecycleError(Exception):
    """A lifecycle operation was refused."""


class LastAdminError(LifecycleError):
    """The operation would have left the organization without an administrator."""


@dataclass(frozen=True)
class CredentialView:
    """One credential as it is shown to an operator."""

    id: str
    subject: str
    role: str
    state: str
    label: str
    expires_at: datetime | None
    unlimited: bool
    overlap_until: datetime | None
    replaced_by_id: str | None
    revoked_at: datetime | None
    created_at: datetime | None

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "subject": self.subject,
            "role": self.role,
            "state": self.state,
            "label": self.label,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "unlimited": self.unlimited,
            "overlap_until": self.overlap_until.isoformat() if self.overlap_until else None,
            "replaced_by_id": self.replaced_by_id,
            "revoked_at": self.revoked_at.isoformat() if self.revoked_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


@dataclass(frozen=True)
class RotationResult:
    """The outcome of a rotation: the replacement and the retired credential."""

    credential: ApiCredentialRow
    previous: ApiCredentialRow
    token: str
    overlap_until: datetime | None

    @property
    def overlap_seconds(self) -> int:
        if self.overlap_until is None:
            return 0
        return max(0, int((as_utc(self.overlap_until) - utcnow()).total_seconds()))


@dataclass
class DeprovisionResult:
    """What a de-provisioning pass did (or would do)."""

    actions: list[dict] = field(default_factory=list)
    blocked: list[dict] = field(default_factory=list)

    @property
    def revoked_credentials(self) -> list[str]:
        return [a["credential_id"] for a in self.actions if a["kind"] == "credential_revoked"]

    @property
    def cleared_assignments(self) -> list[str]:
        return [a["assessment_id"] for a in self.actions if a["kind"] == "assignment_cleared"]


@dataclass
class Finding:
    """One thing the sweep found."""

    kind: str
    severity: str
    description: str
    organization_id: str | None = None
    subject: str | None = None
    credential_id: str | None = None
    assessment_id: str | None = None
    action_taken: str | None = None
    blocked_reason: str | None = None

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "severity": self.severity,
            "description": self.description,
            "organization_id": self.organization_id,
            "subject": self.subject,
            "credential_id": self.credential_id,
            "assessment_id": self.assessment_id,
            "action_taken": self.action_taken,
            "blocked_reason": self.blocked_reason,
        }


@dataclass
class SweepReport:
    """The result of a lifecycle sweep."""

    findings: list[Finding] = field(default_factory=list)
    executed: bool = False

    def of_kind(self, kind: str) -> list[Finding]:
        return [f for f in self.findings if f.kind == kind]

    def as_dict(self) -> dict:
        return {
            "executed": self.executed,
            "findings": [f.as_dict() for f in self.findings],
        }


# --------------------------------------------------------------------------------------
# reading the current state
# --------------------------------------------------------------------------------------


def credential_state(row: ApiCredentialRow, now: datetime | None = None) -> str:
    """``live``, ``expired`` or ``revoked``."""
    now = now or utcnow()
    if row.revoked_at is not None:
        return "revoked"
    if not credential_is_live(row, now):
        return "expired"
    return "live"


def describe_credential(row: ApiCredentialRow, now: datetime | None = None) -> CredentialView:
    """Describe one credential for the listing and the access review."""
    now = now or utcnow()
    state = credential_state(row, now)
    expires_at = row.expires_at
    if state == "revoked":
        label = "REVOKED"
    elif state == "expired":
        label = "EXPIRED"
    elif row.overlap_until is not None:
        label = f"LIVE - rotation overlap until {as_utc(row.overlap_until).isoformat()}"
    elif row.unlimited:
        label = "LIVE - UNLIMITED (never expires)"
    else:
        label = f"LIVE - expires {as_utc(expires_at).isoformat()}"
    return CredentialView(
        id=row.id,
        subject=row.subject,
        role=row.role,
        state=state,
        label=label,
        expires_at=expires_at,
        unlimited=bool(row.unlimited),
        overlap_until=row.overlap_until,
        replaced_by_id=row.replaced_by_id,
        revoked_at=row.revoked_at,
        created_at=row.created_at,
    )


def membership_rows(db: Session, organization_id: str, subject: str) -> list[OidcMembershipRow]:
    """Every membership record of one subject in one organization."""
    return list(
        db.scalars(
            select(OidcMembershipRow).where(
                OidcMembershipRow.organization_id == organization_id,
                OidcMembershipRow.subject == subject,
            )
        ).all()
    )


def effective_membership_role(db: Session, organization_id: str, subject: str) -> str | None:
    """The role the subject currently holds through an *active* membership.

    ``None`` means the subject has no active membership in the organization.
    """
    rows = [m for m in membership_rows(db, organization_id, subject) if m.active]
    if not rows:
        return None
    return max(rows, key=lambda m: ROLE_LEVEL.get(m.role, 0)).role


def subject_can_review(db: Session, organization_id: str, subject: str) -> bool:
    """True when the subject can still act as a reviewer in the organization.

    A subject can review while it holds an active reviewer/admin membership or
    while it holds a live reviewer/admin credential.
    """
    for membership in membership_rows(db, organization_id, subject):
        if membership.active and ROLE_LEVEL.get(membership.role, 0) >= ROLE_LEVEL["reviewer"]:
            return True
    credentials = db.scalars(
        select(ApiCredentialRow).where(
            ApiCredentialRow.organization_id == organization_id,
            ApiCredentialRow.subject == subject,
            ApiCredentialRow.revoked_at.is_(None),
        )
    ).all()
    return any(
        credential_is_live(c) and ROLE_LEVEL.get(c.role, 0) >= ROLE_LEVEL["reviewer"]
        for c in credentials
    )


def admin_subjects(
    db: Session, organization_id: str, *, exclude_credential_ids: set[str] | None = None
) -> set[str]:
    """The subjects that currently hold administrator rights in the organization."""
    excluded = exclude_credential_ids or set()
    admins: set[str] = set()
    for membership in db.scalars(
        select(OidcMembershipRow).where(
            OidcMembershipRow.organization_id == organization_id,
            OidcMembershipRow.active.is_(True),
        )
    ).all():
        if ROLE_LEVEL.get(membership.role, 0) >= ROLE_LEVEL["admin"]:
            admins.add(membership.subject)
    for credential in db.scalars(
        select(ApiCredentialRow).where(
            ApiCredentialRow.organization_id == organization_id,
            ApiCredentialRow.revoked_at.is_(None),
        )
    ).all():
        if credential.id in excluded:
            continue
        if (
            credential_is_live(credential)
            and ROLE_LEVEL.get(credential.role, 0) >= ROLE_LEVEL["admin"]
        ):
            admins.add(credential.subject)
    return admins


def live_credentials(db: Session, organization_id: str, subject: str) -> list[ApiCredentialRow]:
    """The credentials of one subject in one organization that still work."""
    rows = db.scalars(
        select(ApiCredentialRow).where(
            ApiCredentialRow.organization_id == organization_id,
            ApiCredentialRow.subject == subject,
            ApiCredentialRow.revoked_at.is_(None),
        )
    ).all()
    return [row for row in rows if credential_is_live(row)]


def unrevoked_credentials(
    db: Session, organization_id: str, subject: str
) -> list[ApiCredentialRow]:
    """Every credential of one subject that was not revoked, expired or not."""
    return list(
        db.scalars(
            select(ApiCredentialRow).where(
                ApiCredentialRow.organization_id == organization_id,
                ApiCredentialRow.subject == subject,
                ApiCredentialRow.revoked_at.is_(None),
            )
        ).all()
    )


# --------------------------------------------------------------------------------------
# issuing, rotating and revoking
# --------------------------------------------------------------------------------------


def issue_credential(
    db: Session,
    *,
    organization_id: str,
    subject: str,
    role: str,
    days: int | None = DEFAULT_CREDENTIAL_LIFETIME_DAYS,
    unlimited: bool = False,
    reason: str | None = None,
    actor: str = OPERATOR_ACTOR,
) -> tuple[ApiCredentialRow, str]:
    """Issue a credential and return the row plus the raw token (shown once).

    An expiry is mandatory. ``unlimited=True`` is the only way to get a
    credential that never expires and it has to be requested explicitly.
    """
    if role not in ROLE_LEVEL:
        raise LifecycleError(f"Unknown role: {role}")
    if not subject or len(subject) > 200:
        raise LifecycleError("Subject must have 1..200 characters")
    now = utcnow()
    if unlimited:
        expires_at: datetime | None = None
    else:
        if days is None:
            raise LifecycleError(
                "An expiry is required: pass a lifetime in days or opt in explicitly "
                "with --unlimited"
            )
        if not isinstance(days, int) or not 1 <= days <= MAX_CREDENTIAL_LIFETIME_DAYS:
            raise LifecycleError(f"Lifetime must be 1..{MAX_CREDENTIAL_LIFETIME_DAYS} days")
        expires_at = now + timedelta(days=days)
    token = make_token()
    row = ApiCredentialRow(
        organization_id=organization_id,
        subject=subject,
        role=role,
        token_hash=digest_token(token),
        expires_at=expires_at,
        unlimited=unlimited,
    )
    db.add(row)
    db.flush()
    audit_tenant(
        db,
        "credential.issued",
        organization_id=organization_id,
        actor=actor,
        credential_id=row.id,
        subject=subject,
        role=role,
        expires_at=expires_at.isoformat() if expires_at else None,
        unlimited=unlimited,
        reason=reason,
    )
    return row, token


def rotate_credential(
    db: Session,
    credential_id: str,
    *,
    days: int | None = DEFAULT_CREDENTIAL_LIFETIME_DAYS,
    role: str | None = None,
    overlap_seconds: int = 0,
    reason: str | None = None,
    actor: str = OPERATOR_ACTOR,
) -> RotationResult:
    """Replace a credential without leaving two usable credentials behind.

    The previous credential is retired **first** and the replacement is issued
    afterwards, inside one transaction. If the replacement cannot be issued the
    whole transaction is rolled back, so the previous credential stays exactly as
    it was: the operator never ends up with two valid credentials and never ends
    up with none.

    ``overlap_seconds`` (0..900) asks for a short, explicit overlap: the
    previous credential then keeps working until the overlap ends and expires by
    itself. It is never longer than the bound and it is reported by the listing.
    """
    if overlap_seconds < 0 or overlap_seconds > MAX_ROTATION_OVERLAP_SECONDS:
        raise LifecycleError(f"Overlap must be 0..{MAX_ROTATION_OVERLAP_SECONDS} seconds")
    previous = db.get(ApiCredentialRow, credential_id)
    if previous is None:
        raise LifecycleError("Credential not found")
    if previous.revoked_at is not None:
        raise LifecycleError("Credential is already revoked and cannot be rotated")
    if role is not None and role not in ROLE_LEVEL:
        raise LifecycleError(f"Unknown role: {role}")
    now = utcnow()
    with db.begin_nested():
        overlap_until: datetime | None = None
        if overlap_seconds:
            overlap_until = now + timedelta(seconds=overlap_seconds)
            # The retired credential dies with the overlap window; it can never
            # outlive it.
            previous.overlap_until = overlap_until
            previous.expires_at = overlap_until
            previous.unlimited = False
        else:
            previous.revoked_at = now
        db.flush()
        replacement, token = issue_credential(
            db,
            organization_id=previous.organization_id,
            subject=previous.subject,
            role=role or previous.role,
            days=days,
            unlimited=False,
            reason=reason or "rotation",
            actor=actor,
        )
        previous.replaced_by_id = replacement.id
        db.flush()
        audit_tenant(
            db,
            "credential.rotated",
            organization_id=previous.organization_id,
            actor=actor,
            credential_id=replacement.id,
            previous_credential_id=previous.id,
            subject=previous.subject,
            role=replacement.role,
            overlap_until=overlap_until.isoformat() if overlap_until else None,
            reason=reason,
        )
        # A rotation that lowers the role can leave the person unable to act
        # as a reviewer: their outstanding assignments are cleared instead of
        # blocking an assessment forever.
        clear_stale_assignments(
            db,
            previous.organization_id,
            previous.subject,
            actor=actor,
            reason="credential rotated",
        )
    return RotationResult(
        credential=replacement, previous=previous, token=token, overlap_until=overlap_until
    )


def revoke_credential(
    db: Session,
    credential_id: str,
    *,
    reason: str | None = None,
    actor: str = OPERATOR_ACTOR,
) -> ApiCredentialRow:
    """Revoke one credential and audit the revocation."""
    row = db.get(ApiCredentialRow, credential_id)
    if row is None:
        raise LifecycleError("Credential not found")
    if row.revoked_at is None:
        row.revoked_at = utcnow()
        audit_tenant(
            db,
            "credential.revoked",
            organization_id=row.organization_id,
            actor=actor,
            credential_id=row.id,
            subject=row.subject,
            role=row.role,
            reason=reason,
        )
    return row


# --------------------------------------------------------------------------------------
# de-provisioning
# --------------------------------------------------------------------------------------


def clear_stale_assignments(
    db: Session,
    organization_id: str,
    subject: str,
    *,
    actor: str = OPERATOR_ACTOR,
    reason: str | None = None,
) -> list[dict]:
    """Clear the reviewer assignments of a subject that can no longer act.

    An assignment to somebody who lost the reviewer role (or whose membership
    was deactivated) blocks the assessment forever: nobody may review it, but
    the assessment still waits for a review.
    """
    assignments = db.scalars(
        select(ReviewerAssignmentRow)
        .join(AssessmentRow, ReviewerAssignmentRow.assessment_id == AssessmentRow.id)
        .where(
            AssessmentRow.organization_id == organization_id,
            ReviewerAssignmentRow.reviewer_subject == subject,
        )
    ).all()
    if not assignments:
        return []
    if subject_can_review(db, organization_id, subject):
        return []
    cleared: list[dict] = []
    for assignment in assignments:
        db.delete(assignment)
        cleared.append({"kind": "assignment_cleared", "assessment_id": assignment.assessment_id})
        audit_tenant(
            db,
            "reviewer.assignment_cleared",
            organization_id=organization_id,
            assessment_id=assignment.assessment_id,
            actor=actor,
            reviewer_subject=subject,
            reason=reason or "reviewer can no longer act",
        )
    return cleared


def deprovision_subject(
    db: Session,
    organization_id: str,
    subject: str,
    *,
    actor: str = OPERATOR_ACTOR,
    reason: str | None = None,
    protect_last_admin: bool = True,
) -> DeprovisionResult:
    """De-provision one subject after a membership change.

    Two things are cleaned up:

    * every live credential whose role the membership no longer supports is
      revoked (a deactivated membership revokes all of them);
    * every reviewer assignment of a subject that can no longer act is cleared,
      so an assessment never stays blocked on somebody who cannot review it.

    When ``protect_last_admin`` is set, a revocation that would leave the
    organization without an administrator is refused and reported instead.
    """
    result = DeprovisionResult()
    memberships = membership_rows(db, organization_id, subject)
    if memberships:
        effective = effective_membership_role(db, organization_id, subject)
        candidates = live_credentials(db, organization_id, subject)
        if effective is None:
            # The membership is gone: the subject may not act at all any more.
            to_revoke = candidates
        else:
            to_revoke = [
                c for c in candidates if ROLE_LEVEL.get(c.role, 0) > ROLE_LEVEL.get(effective, 0)
            ]
        if to_revoke and protect_last_admin:
            admins_before = admin_subjects(db, organization_id)
            admins_after = admin_subjects(
                db, organization_id, exclude_credential_ids={c.id for c in to_revoke}
            )
            if admins_before and not admins_after:
                for credential in to_revoke:
                    result.blocked.append(
                        {
                            "kind": "credential_revoked",
                            "credential_id": credential.id,
                            "subject": subject,
                            "reason": "would remove the last administrator of the organization",
                        }
                    )
                to_revoke = []
        for credential in to_revoke:
            credential.revoked_at = utcnow()
            result.actions.append(
                {
                    "kind": "credential_revoked",
                    "credential_id": credential.id,
                    "subject": subject,
                    "role": credential.role,
                    "reason": reason or "membership role changed",
                }
            )
            audit_tenant(
                db,
                "credential.revoked",
                organization_id=organization_id,
                actor=actor,
                credential_id=credential.id,
                subject=subject,
                role=credential.role,
                reason=reason or "membership role changed",
                cause="membership_lifecycle",
            )
    for action in clear_stale_assignments(db, organization_id, subject, actor=actor, reason=reason):
        result.actions.append(action)
    return result


def change_membership_role(
    db: Session,
    membership_id: str,
    role: str,
    *,
    reason: str | None = None,
    actor: str = OPERATOR_ACTOR,
    override_last_admin: bool = False,
) -> tuple[OidcMembershipRow, DeprovisionResult]:
    """Change the role of a membership and de-provision what depended on it."""
    if role not in ROLE_LEVEL:
        raise LifecycleError(f"Unknown role: {role}")
    membership = db.get(OidcMembershipRow, membership_id)
    if membership is None:
        raise LifecycleError("Membership not found")
    previous_role = membership.role
    if previous_role == role and membership.active:
        return membership, DeprovisionResult()
    if not override_last_admin:
        _guard_last_admin(db, membership, previous_role, role)
    membership.role = role
    db.flush()
    audit_tenant(
        db,
        "membership.role_changed",
        organization_id=membership.organization_id,
        actor=actor,
        membership_id=membership.id,
        subject=membership.subject,
        previous_role=previous_role,
        role=role,
        reason=reason,
        last_admin_override=override_last_admin,
    )
    result = deprovision_subject(
        db,
        membership.organization_id,
        membership.subject,
        actor=actor,
        reason=reason or "membership role changed",
        protect_last_admin=False,
    )
    return membership, result


def disable_membership(
    db: Session,
    membership_id: str,
    *,
    reason: str | None = None,
    actor: str = OPERATOR_ACTOR,
    override_last_admin: bool = False,
) -> tuple[OidcMembershipRow, DeprovisionResult]:
    """Deactivate a membership and de-provision what depended on it."""
    membership = db.get(OidcMembershipRow, membership_id)
    if membership is None:
        raise LifecycleError("Membership not found")
    if not membership.active:
        return membership, DeprovisionResult()
    if not override_last_admin:
        _guard_last_admin(db, membership, membership.role, None)
    membership.active = False
    db.flush()
    audit_tenant(
        db,
        "membership.disabled",
        organization_id=membership.organization_id,
        actor=actor,
        membership_id=membership.id,
        subject=membership.subject,
        role=membership.role,
        reason=reason,
        last_admin_override=override_last_admin,
    )
    result = deprovision_subject(
        db,
        membership.organization_id,
        membership.subject,
        actor=actor,
        reason=reason or "membership deactivated",
        protect_last_admin=False,
    )
    return membership, result


def _guard_last_admin(
    db: Session,
    membership: OidcMembershipRow,
    previous_role: str,
    new_role: str | None,
) -> None:
    """Refuse a change that would leave the organization without an administrator."""
    loses_admin = ROLE_LEVEL.get(previous_role, 0) >= ROLE_LEVEL["admin"] and (
        new_role is None or ROLE_LEVEL.get(new_role, 0) < ROLE_LEVEL["admin"]
    )
    if not loses_admin:
        return
    others = admin_subjects(db, membership.organization_id) - {membership.subject}
    if others:
        return
    raise LastAdminError(
        f"Refusing to remove the last administrator of organization "
        f"{membership.organization_id}: grant another administrator first or pass "
        "--override-last-admin to proceed deliberately."
    )


# --------------------------------------------------------------------------------------
# sweep
# --------------------------------------------------------------------------------------


def sweep_lifecycle(
    db: Session,
    *,
    organization_id: str | None = None,
    actor: str = OPERATOR_ACTOR,
    execute: bool = False,
    override_last_admin: bool = False,
) -> SweepReport:
    """Find (and optionally repair) lifecycle drift.

    The default is a **report**: nothing is changed and nothing is audited. With
    ``execute=True`` every finding is acted on and audited individually.
    """
    report = SweepReport(executed=execute)
    organizations = _scoped_organizations(db, organization_id)
    for organization in organizations:
        org_id = organization.id
        for subject in _organization_subjects(db, org_id):
            memberships = membership_rows(db, org_id, subject)
            effective = effective_membership_role(db, org_id, subject)
            for credential in unrevoked_credentials(db, org_id, subject):
                if credential_is_expired(credential):
                    expired_at = (
                        as_utc(credential.expires_at).isoformat()
                        if credential.expires_at
                        else "n/a"
                    )
                    report.findings.append(
                        Finding(
                            kind="expired_credential",
                            severity="action",
                            description=(
                                f"Credential {credential.id} for {subject} expired at {expired_at}"
                            ),
                            organization_id=org_id,
                            subject=subject,
                            credential_id=credential.id,
                        )
                    )
                    continue
                if not memberships:
                    # A local principal without a membership record: the
                    # credential is the membership and stands on its own.
                    continue
                if effective is None:
                    report.findings.append(
                        Finding(
                            kind="credential_membership_inactive",
                            severity="action",
                            description=(
                                f"Credential {credential.id} for {subject} relies on a "
                                "membership that is no longer active"
                            ),
                            organization_id=org_id,
                            subject=subject,
                            credential_id=credential.id,
                        )
                    )
                elif ROLE_LEVEL.get(credential.role, 0) > ROLE_LEVEL.get(effective, 0):
                    report.findings.append(
                        Finding(
                            kind="credential_role_exceeds_membership",
                            severity="action",
                            description=(
                                f"Credential {credential.id} for {subject} holds role "
                                f"'{credential.role}' but the membership now holds "
                                f"'{effective}'"
                            ),
                            organization_id=org_id,
                            subject=subject,
                            credential_id=credential.id,
                        )
                    )
            # Active memberships without any usable credential.
            if memberships and effective is not None and not live_credentials(db, org_id, subject):
                report.findings.append(
                    Finding(
                        kind="member_without_credential",
                        severity="report",
                        description=(
                            f"{subject} holds the role '{effective}' but has no live credential"
                        ),
                        organization_id=org_id,
                        subject=subject,
                    )
                )
        # Reviewer assignments that point at somebody who can no longer act.
        for assignment in _organization_assignments(db, org_id):
            if subject_can_review(db, org_id, assignment.reviewer_subject):
                continue
            report.findings.append(
                Finding(
                    kind="stale_reviewer_assignment",
                    severity="action",
                    description=(
                        f"Assessment {assignment.assessment_id} is assigned to "
                        f"{assignment.reviewer_subject}, who can no longer act as a reviewer"
                    ),
                    organization_id=org_id,
                    subject=assignment.reviewer_subject,
                    assessment_id=assignment.assessment_id,
                )
            )
    if execute:
        _execute_sweep(db, report, actor=actor, override_last_admin=override_last_admin)
    return report


def _execute_sweep(
    db: Session,
    report: SweepReport,
    *,
    actor: str,
    override_last_admin: bool,
) -> None:
    """Apply the findings of a sweep, one auditable action at a time."""
    for finding in report.findings:
        if finding.severity != "action":
            continue
        if finding.kind == "expired_credential":
            credential = db.get(ApiCredentialRow, finding.credential_id)
            if credential is None or credential.revoked_at is not None:
                continue
            credential.revoked_at = utcnow()
            finding.action_taken = "revoked"
            audit_tenant(
                db,
                "credential.revoked",
                organization_id=finding.organization_id,
                actor=actor,
                credential_id=credential.id,
                subject=credential.subject,
                role=credential.role,
                reason="sweep: expired credential",
                cause="lifecycle_sweep",
            )
        elif finding.kind in {
            "credential_membership_inactive",
            "credential_role_exceeds_membership",
        }:
            credential = db.get(ApiCredentialRow, finding.credential_id)
            if credential is None or credential.revoked_at is not None:
                continue
            if not override_last_admin:
                admins_before = admin_subjects(db, finding.organization_id)
                admins_after = admin_subjects(
                    db, finding.organization_id, exclude_credential_ids={credential.id}
                )
                if admins_before and not admins_after:
                    finding.blocked_reason = (
                        "would remove the last administrator of the organization; "
                        "pass --override-last-admin to proceed"
                    )
                    continue
            credential.revoked_at = utcnow()
            finding.action_taken = "revoked"
            audit_tenant(
                db,
                "credential.revoked",
                organization_id=finding.organization_id,
                actor=actor,
                credential_id=credential.id,
                subject=credential.subject,
                role=credential.role,
                reason=f"sweep: {finding.kind}",
                cause="lifecycle_sweep",
            )
        elif finding.kind == "stale_reviewer_assignment":
            assignment = db.scalar(
                select(ReviewerAssignmentRow).where(
                    ReviewerAssignmentRow.assessment_id == finding.assessment_id
                )
            )
            if assignment is None:
                continue
            db.delete(assignment)
            finding.action_taken = "assignment_cleared"
            audit_tenant(
                db,
                "reviewer.assignment_cleared",
                organization_id=finding.organization_id,
                assessment_id=finding.assessment_id,
                actor=actor,
                reviewer_subject=finding.subject,
                reason="sweep: reviewer can no longer act",
                cause="lifecycle_sweep",
            )


def _scoped_organizations(db: Session, organization_id: str | None) -> list[OrganizationRow]:
    if organization_id:
        organization = db.get(OrganizationRow, organization_id)
        return [organization] if organization is not None else []
    return list(db.scalars(select(OrganizationRow).order_by(OrganizationRow.name)).all())


def _organization_subjects(db: Session, organization_id: str) -> list[str]:
    subjects: set[str] = set()
    for membership in db.scalars(
        select(OidcMembershipRow).where(OidcMembershipRow.organization_id == organization_id)
    ).all():
        subjects.add(membership.subject)
    for credential in db.scalars(
        select(ApiCredentialRow).where(ApiCredentialRow.organization_id == organization_id)
    ).all():
        subjects.add(credential.subject)
    return sorted(subjects)


def _organization_assignments(db: Session, organization_id: str) -> list[ReviewerAssignmentRow]:
    return list(
        db.scalars(
            select(ReviewerAssignmentRow)
            .join(AssessmentRow, ReviewerAssignmentRow.assessment_id == AssessmentRow.id)
            .where(AssessmentRow.organization_id == organization_id)
        ).all()
    )


# --------------------------------------------------------------------------------------
# access review
# --------------------------------------------------------------------------------------


def access_review(db: Session, organization_id: str, *, now: datetime | None = None) -> dict:
    """Build the human-readable access review of one organization.

    The review answers the questions an auditor asks: who holds which role,
    which credentials are live, when they expire, which assessments a person
    can act on and which assignments are still outstanding.
    """
    now = now or utcnow()
    organization = db.get(OrganizationRow, organization_id)
    if organization is None:
        raise LifecycleError("Organization not found")
    credentials = db.scalars(
        select(ApiCredentialRow)
        .where(ApiCredentialRow.organization_id == organization_id)
        .order_by(ApiCredentialRow.created_at, ApiCredentialRow.id)
    ).all()
    memberships = db.scalars(
        select(OidcMembershipRow).where(OidcMembershipRow.organization_id == organization_id)
    ).all()
    assignments = _organization_assignments(db, organization_id)
    subjects = sorted({c.subject for c in credentials} | {m.subject for m in memberships})
    members = []
    for subject in subjects:
        subject_memberships = [m for m in memberships if m.subject == subject]
        subject_credentials = [c for c in credentials if c.subject == subject]
        subject_assignments = [a for a in assignments if a.reviewer_subject == subject]
        members.append(
            {
                "subject": subject,
                "memberships": [
                    {
                        "id": m.id,
                        "issuer": m.issuer,
                        "role": m.role,
                        "active": bool(m.active),
                    }
                    for m in subject_memberships
                ],
                "effective_role": effective_membership_role(db, organization_id, subject),
                "credentials": [describe_credential(c, now).as_dict() for c in subject_credentials],
                "live_credential_count": sum(
                    1 for c in subject_credentials if credential_state(c, now) == "live"
                ),
                "can_review": subject_can_review(db, organization_id, subject),
                "assigned_assessments": [
                    {
                        "assessment_id": a.assessment_id,
                        "title": _assessment_title(db, a.assessment_id),
                        "status": _assessment_status(db, a.assessment_id),
                    }
                    for a in subject_assignments
                ],
            }
        )
    outstanding = []
    for assignment in assignments:
        outstanding.append(
            {
                "assessment_id": assignment.assessment_id,
                "title": _assessment_title(db, assignment.assessment_id),
                "status": _assessment_status(db, assignment.assessment_id),
                "reviewer_subject": assignment.reviewer_subject,
                "reviewer_can_act": subject_can_review(
                    db, organization_id, assignment.reviewer_subject
                ),
                "assigned_by": assignment.assigned_by,
                "updated_at": assignment.updated_at.isoformat() if assignment.updated_at else None,
            }
        )
    return {
        "organization": {"id": organization.id, "name": organization.name},
        "generated_at": now.isoformat(),
        "expiry_leeway_seconds": int(CREDENTIAL_EXPIRY_LEEWAY.total_seconds()),
        "members": members,
        "outstanding_assignments": outstanding,
        "live_credentials": sum(1 for c in credentials if credential_state(c, now) == "live"),
        "unlimited_credentials": [
            c.id for c in credentials if c.unlimited and credential_state(c, now) == "live"
        ],
    }


def _assessment_title(db: Session, assessment_id: str) -> str | None:
    row = db.get(AssessmentRow, assessment_id)
    return row.title if row is not None else None


def _assessment_status(db: Session, assessment_id: str) -> str | None:
    row = db.get(AssessmentRow, assessment_id)
    return row.status if row is not None else None


def render_access_review(review: dict, *, output_format: str = "text") -> str:
    """Render an access review as text, JSON or CSV."""
    if output_format == "json":
        import json

        return json.dumps(review, indent=2, sort_keys=True)
    if output_format == "csv":
        import csv
        import io

        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(
            [
                "organization",
                "subject",
                "effective_role",
                "credential_id",
                "credential_state",
                "credential_label",
                "expires_at",
                "unlimited",
                "overlap_until",
                "can_review",
                "assigned_assessments",
            ]
        )
        for member in review["members"]:
            credentials = member["credentials"] or [
                {
                    "id": "",
                    "state": "",
                    "label": "",
                    "expires_at": "",
                    "unlimited": "",
                    "overlap_until": "",
                }
            ]
            for credential in credentials:
                writer.writerow(
                    [
                        review["organization"]["name"],
                        member["subject"],
                        member["effective_role"] or "",
                        credential["id"],
                        credential["state"],
                        credential["label"],
                        credential["expires_at"] or "",
                        credential["unlimited"],
                        credential["overlap_until"] or "",
                        member["can_review"],
                        ";".join(a["assessment_id"] for a in member["assigned_assessments"]),
                    ]
                )
        return buffer.getvalue()
    return render_access_review_text(review)


def render_access_review_text(review: dict) -> str:
    """The human-readable form of the access review."""
    organization = review["organization"]
    lines = [
        f"ACCESS REVIEW - {organization['name']} ({organization['id']})",
        f"Generated: {review['generated_at']}",
        f"Expiry leeway: {review['expiry_leeway_seconds']}s (strict, before the stated expiry)",
        "",
        "MEMBERS",
    ]
    for member in review["members"]:
        role = member["effective_role"] or "(no active membership)"
        lines.append(f"- {member['subject']} [{role}]")
        for membership in member["memberships"]:
            state = "active" if membership["active"] else "INACTIVE"
            lines.append(
                f"    membership {membership['id']} issuer={membership['issuer']} "
                f"role={membership['role']} ({state})"
            )
        for credential in member["credentials"]:
            lines.append(f"    credential {credential['id']}: {credential['label']}")
        if member["assigned_assessments"]:
            for assignment in member["assigned_assessments"]:
                lines.append(
                    f"    assigned {assignment['assessment_id']} "
                    f"({assignment['status']}) {assignment['title']}"
                )
    lines.append("")
    lines.append("OUTSTANDING REVIEWER ASSIGNMENTS")
    if not review["outstanding_assignments"]:
        lines.append("- none")
    for assignment in review["outstanding_assignments"]:
        marker = "" if assignment["reviewer_can_act"] else "  [REVIEWER CANNOT ACT]"
        lines.append(
            f"- {assignment['assessment_id']} ({assignment['status']}) "
            f"{assignment['title']} -> {assignment['reviewer_subject']}{marker}"
        )
    lines.append("")
    lines.append(f"Live credentials: {review['live_credentials']}")
    if review["unlimited_credentials"]:
        lines.append(
            "UNLIMITED credentials (never expire): " + ", ".join(review["unlimited_credentials"])
        )
    return "\n".join(lines)
