"""The single audit gate.

Every audit event in the application is written through :func:`audit`, so the rules about
who may be named as the actor are enforced in exactly one place:

* the actor is always the **server-side principal**, never a value taken from the request
  body. A client-supplied reviewer label is kept separately as an explicitly unverified
  ``claimed_actor`` and is never promoted to an identity.
* every event records whether the actor was verified and where the identity came from, so
  an unverified synthetic demo decision can never be read as a verified human decision.

This module exists so that services (not just API routes) can write audit events without
importing a router. Before it existed, the provenance ledger wrote its row directly and
silently omitted the identity flags.
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
