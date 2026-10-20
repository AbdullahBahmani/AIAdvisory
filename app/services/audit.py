"""The single audit gate.

Every audit event in the application is written through :func:`audit`, so the rules about
who may be named as the actor are enforced in exactly one place:

* the actor is always the **server-side principal**, never a value taken from the request
  body. A client-supplied reviewer label is kept separately as an explicitly unverified
  ``claimed_actor`` and is never promoted to an identity.
* every event records whether the actor was verified and where the identity came from, so
  an unverified synthetic demo decision can never be read as a verified human decision.

Each event is also chained: it carries its position in the assessment's audit chain and a
hash over its own canonical content chained onto the previous event's hash. The chain alone
detects an in-place alteration; the out-of-database high-water mark (see
``app/services/audit_chain.py``) is what detects a *rollback*, which is what a restore does.

This module exists so that services (not just API routes) can write audit events without
importing a router. Before it existed, the provenance ledger wrote its row directly and
silently omitted the identity flags.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import AssessmentRow, AuditRow
from app.services.audit_chain import (
    GENESIS_HASH,
    HighWaterMark,
    canonical_payload,
    compute_chain_hash,
)

#: How many times to retry when two writers race for the same chain position.
_CHAIN_RETRIES = 3


def _normalize_timestamp(value: datetime) -> datetime:
    """Return the timestamp as an aware UTC datetime.

    SQLite does not store timezone information, so a value read back from the database is
    naive even though it was written as UTC. Normalising on both sides keeps the chain
    hash stable across a write and a later read.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _last_chain_event(db: Session, assessment_id: str) -> AuditRow | None:
    """The most recent audit event of an assessment, or None."""
    return db.scalars(
        select(AuditRow)
        .where(AuditRow.assessment_id == assessment_id)
        .order_by(AuditRow.sequence.desc(), AuditRow.created_at.desc())
        .limit(1)
    ).first()


def audit(
    db: Session, row: AssessmentRow, event: str, *, actor: str | None = None, **details
) -> None:
    """Record an audit event attributed to the authenticated principal.

    The actor is always taken from the server-side principal. A client supplied
    reviewer label is never trusted: it is recorded separately as an explicitly
    unverified `claimed_actor` and never as the audit actor.
    """
    from fastapi import HTTPException

    from app.security import current_principal

    principal = current_principal(db)
    if actor and actor != principal.subject:
        details["claimed_actor"] = actor
    details.setdefault("identity_unverified", not principal.verified)
    details.setdefault("identity_source", principal.source)

    for _ in range(_CHAIN_RETRIES):
        previous = _last_chain_event(db, row.id)
        previous_hash = previous.chain_hash if previous and previous.chain_hash else GENESIS_HASH
        previous_sequence = previous.sequence if previous else 0

        event_id = str(uuid4())
        created_at = datetime.now(UTC)
        sequence = previous_sequence + 1
        payload = canonical_payload(
            event_id=event_id,
            sequence=sequence,
            assessment_id=row.id,
            event_type=event,
            actor=principal.subject,
            actor_verified=bool(principal.verified),
            actor_organization_id=principal.organization_id,
            details=details,
            created_at=created_at,
        )
        entry = AuditRow(
            id=event_id,
            assessment_id=row.id,
            event_type=event,
            actor=principal.subject,
            actor_organization_id=principal.organization_id,
            actor_verified=bool(principal.verified),
            details=details,
            sequence=sequence,
            chain_hash=compute_chain_hash(previous_hash, payload),
            created_at=created_at,
        )
        try:
            # A savepoint, so that losing the race for a chain position does not
            # destroy the caller's surrounding transaction.
            with db.begin_nested():
                db.add(entry)
                db.flush()
        except IntegrityError:
            # Another writer took this position between our read and our insert.
            continue
        _register_pending_mark(db, row.id, sequence, entry.chain_hash)
        return

    raise HTTPException(status_code=503, detail="Audit chain contention; retry the request")


def _register_pending_mark(db: Session, assessment_id: str, sequence: int, chain_hash: str) -> None:
    """Record the chain state to persist once the transaction commits.

    The mark must only be written after a successful commit: a mark written for a
    transaction that later rolled back would report a false rollback.
    """
    pending = db.info.setdefault("audit_pending_marks", {})
    existing = pending.get(assessment_id)
    if existing and int(existing[0]) >= sequence:
        return
    pending[assessment_id] = (sequence, chain_hash)


@event.listens_for(Session, "after_commit")
def _persist_audit_marks(session: Session) -> None:
    """Write the high-water mark once the transaction has committed."""
    pending = session.info.pop("audit_pending_marks", None)
    if not pending:
        return
    mark = HighWaterMark()
    for assessment_id, (sequence, chain_hash) in pending.items():
        try:
            mark.record(assessment_id, sequence, chain_hash)
        except OSError:
            # The audit rows are committed and the chain is intact in the database. A
            # failure to write the local mark must not fail the request, but it does
            # mean the rollback detection is degraded, so it is logged.
            import logging

            logging.getLogger("aiadvisory.audit").warning(
                '{"event": "audit.high_water_mark_write_failed"}'
            )
