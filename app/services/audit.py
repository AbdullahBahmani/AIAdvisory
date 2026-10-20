"""The single audit gate.

Every audit event in the application is written through this module, so the rules about
who may be named as the actor are enforced in exactly one place:

* the actor is always the **server-side principal**, never a value taken from the request
  body. A client-supplied reviewer label is kept separately as an explicitly unverified
  ``claimed_actor`` and is never promoted to an identity.
* every event records whether the actor was verified and where the identity came from, so
  an unverified synthetic demo decision can never be read as a verified human decision.

This module exists so that services (not just API routes) can write audit events without
importing a router. Before it existed, the provenance ledger wrote its row directly and
silently omitted the identity flags.

Two scopes exist:

* :func:`audit` writes an event that belongs to one assessment and is attributed to the
  principal of the current request.
* :func:`audit_tenant` writes an event that belongs to an organization rather than to a
  single assessment (credential issuance, rotation, revocation, de-provisioning). It is
  used by the operator CLI, which runs outside a request and therefore has no principal:
  such events keep their explicit, **unverified** operator actor.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import AssessmentRow, AuditRow


def audit(
    db: Session, row: AssessmentRow, event: str, *, actor: str | None = None, **details
) -> None:
    """Record an audit event attributed to the authenticated principal.

    The actor is always taken from the server-side principal. A client supplied
    reviewer label is never trusted: it is recorded separately as an explicitly
    unverified `claimed_actor` and never as the audit actor.
    """
    from app.security import current_principal

    principal = current_principal(db)
    if actor and actor != principal.subject:
        details["claimed_actor"] = actor
    details.setdefault("identity_unverified", not principal.verified)
    details.setdefault("identity_source", principal.source)
    db.add(
        AuditRow(assessment_id=row.id, event_type=event, actor=principal.subject, details=details)
    )


def audit_tenant(
    db: Session,
    event: str,
    *,
    organization_id: str | None,
    actor: str,
    verified: bool = False,
    source: str = "operator_cli",
    assessment_id: str | None = None,
    **details,
) -> None:
    """Record an audit event that is scoped to an organization, not to one assessment.

    Credential and membership lifecycle changes (issue, rotate, revoke,
    de-provision, sweep) belong to the organization as a whole. They are still
    auditable: the event carries the tenant scope, the actor and the same
    provenance flags as every other event.

    An operator command is **not** a verified human identity: unless the caller
    explicitly passes ``verified=True`` (never done by the operator CLI) the
    event stays marked unverified, exactly like the synthetic demo identity.
    """
    details.setdefault("identity_unverified", not verified)
    details.setdefault("identity_source", source)
    db.add(
        AuditRow(
            assessment_id=assessment_id,
            organization_id=organization_id,
            event_type=event,
            actor=actor,
            actor_organization_id=organization_id,
            actor_verified=verified,
            details=details,
        )
    )
