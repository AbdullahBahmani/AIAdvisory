"""Generation-side enqueueing and the out-of-band delivery dispatcher.

Two halves, deliberately separated:

* :func:`enqueue_deliveries` runs inside the request that generated the
  notification. It only writes database rows - it never opens a socket, so a
  broken destination cannot slow down or fail the request.
* :func:`dispatch_pending` runs out of band (operator command or an
  administrator-triggered run). It performs the actual delivery, records the
  outcome, retries with bounded backoff and keeps the order of the messages.

Ordering: deliveries carry a monotonic database sequence number. For one
destination the dispatcher works through that sequence in order and stops at
the first message that could not be delivered, so an approval request is
always delivered before the decision that follows it.

Idempotency: the delivery id is generated once, when the message is queued.
Every attempt reuses it and re-sends the byte-identical payload, so a
receiver can deduplicate, and a message that already succeeded is never sent
again.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from app.db.models import new_id
from app.notifications import channels, events
from app.notifications.models import (
    CHANNEL_FILE,
    CHANNEL_WEBHOOK,
    STATUS_DELIVERED,
    STATUS_FAILED,
    STATUS_PENDING,
    NotificationDeliveryRow,
    NotificationDestinationRow,
)

logger = logging.getLogger("aiadvisory.notifications")
if not logger.handlers:
    logger.addHandler(logging.StreamHandler())
logger.setLevel(logging.INFO)


def _log(event: str, **fields) -> None:
    """Write one sanitised log line.

    The line follows the redaction policy of ``app/telemetry.py``: it carries
    no assessment content, no rationale, no person, no target URL, no token
    and no error message. Only closed-vocabulary values, a counter and the
    random delivery id (the same kind of handle as the request id) are
    logged.
    """
    payload = {"event": event, **fields}
    logger.info(json.dumps(payload, sort_keys=True))


#: Environment variable bounding the number of attempts per delivery.
MAX_ATTEMPTS_ENV = "ADVISORY_NOTIFICATION_MAX_ATTEMPTS"

#: Default number of attempts (the first delivery plus its retries).
DEFAULT_MAX_ATTEMPTS = 5

#: Bounded exponential backoff, in seconds.
BACKOFF_BASE_SECONDS = 30
BACKOFF_MAX_SECONDS = 3600

#: Maximum number of deliveries one dispatcher run touches.
DEFAULT_BATCH_LIMIT = 100


def max_attempts() -> int:
    """The configured attempt budget, clamped to a sane range."""
    raw = os.getenv(MAX_ATTEMPTS_ENV, "")
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MAX_ATTEMPTS
    return max(1, min(10, value))


def backoff_seconds(attempts: int) -> int:
    """Bounded exponential backoff for the given number of attempts so far."""
    exponent = max(0, attempts - 1)
    return min(BACKOFF_BASE_SECONDS * (2**exponent), BACKOFF_MAX_SECONDS)


def _aware(moment: datetime | None) -> datetime:
    """Normalise a timestamp to timezone-aware UTC."""
    if moment is None:
        return datetime.now(UTC)
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def _matches(events_wanted: object, event_type: str) -> bool:
    """True when a destination wants this event type.

    An empty event list means "every event"; a destination never receives an
    event it did not subscribe to.
    """
    if not isinstance(events_wanted, list):
        return False
    if not events_wanted:
        return True
    return event_type in events_wanted


def enqueue_deliveries(connection, notification) -> int:
    """Queue one outbound delivery per matching destination of the tenant.

    This runs inside the request that generated the notification. It writes
    database rows only: no network call, no external dependency, no way for a
    broken destination to fail the request that produced the message.

    Returns the number of deliveries that were queued.
    """
    organization_id = notification.organization_id
    if not organization_id:
        return 0
    # The columns are selected explicitly: this runs on the flush connection,
    # where the ORM loading path is not available.
    destinations = connection.execute(
        select(
            NotificationDestinationRow.id,
            NotificationDestinationRow.channel,
            NotificationDestinationRow.target,
            NotificationDestinationRow.events,
        ).where(
            NotificationDestinationRow.organization_id == organization_id,
            NotificationDestinationRow.enabled.is_(True),
        )
    ).all()
    matching = [item for item in destinations if _matches(item.events, notification.event_type)]
    if not matching:
        return 0
    occurred_at = _aware(notification.created_at)
    stamp = occurred_at.isoformat()
    rows = []
    for destination in matching:
        delivery_id = new_id()
        payload = events.build_payload(
            delivery_id=delivery_id,
            event_type=notification.event_type,
            assessment_id=notification.assessment_id,
            organization_id=organization_id,
            occurred_at=stamp,
            link_path=events.assessment_link(notification.assessment_id),
        )
        rows.append(
            {
                "id": delivery_id,
                "organization_id": organization_id,
                "notification_id": notification.id,
                "destination_id": destination.id,
                "channel": destination.channel,
                "event_type": notification.event_type,
                "status": STATUS_PENDING,
                "attempts": 0,
                "next_attempt_at": occurred_at,
                "last_error": None,
                "payload": payload,
                "created_at": occurred_at,
                "updated_at": occurred_at,
                "delivered_at": None,
            }
        )
    connection.execute(insert(NotificationDeliveryRow), rows)
    for row in rows:
        _log(
            "notification.queued",
            channel=row["channel"],
            delivery_id=row["id"],
            status=STATUS_PENDING,
        )
    return len(rows)


@dataclass
class DispatchReport:
    """What one dispatcher run did. Contains counts and reason codes only."""

    attempted: int = 0
    delivered: int = 0
    failed: int = 0
    retrying: int = 0
    dry_run: bool = False
    reasons: list[str] = field(default_factory=list)

    def note(self, reason: str) -> None:
        if reason not in self.reasons:
            self.reasons.append(reason)


def _terminate(delivery: NotificationDeliveryRow, reason: str, now: datetime) -> None:
    """Record a terminal failure without ever sending anything."""
    delivery.status = STATUS_FAILED
    delivery.last_error = reason
    delivery.updated_at = now


def _attempt_send(
    delivery: NotificationDeliveryRow,
    destination: NotificationDestinationRow,
) -> channels.DeliveryError | None:
    """Send one delivery and report the failure, if there was one.

    An unexpected error is recorded like any other failure instead of
    escaping: a dispatcher run must never crash because one destination
    misbehaves.
    """
    payload = delivery.payload or {}
    try:
        channels.send(
            delivery.channel,
            target=destination.target,
            organization_id=delivery.organization_id,
            payload=payload,
            occurred_at=str(payload.get("occurred_at", "")),
        )
    except channels.DeliveryError as exc:
        return exc
    except Exception:
        logger.warning(
            json.dumps(
                {
                    "event": "notification.delivery_error",
                    "channel": delivery.channel,
                    "delivery_id": delivery.id,
                    "reason": "internal_error",
                },
                sort_keys=True,
            )
        )
        return channels.DeliveryError("internal_error")
    return None


def dispatch_pending(
    db: Session,
    *,
    organization_id: str | None = None,
    now: datetime | None = None,
    dry_run: bool = False,
    limit: int = DEFAULT_BATCH_LIMIT,
) -> DispatchReport:
    """Deliver the queued notifications that are due.

    ``dry_run`` reports what would be sent and changes nothing. A failure is
    recorded on the delivery row, retried with bounded backoff while it can
    still succeed, and surfaced in the report - never swallowed.
    """
    moment = _aware(now)
    report = DispatchReport(dry_run=dry_run)
    attempts_budget = max_attempts()
    query = (
        select(NotificationDeliveryRow)
        .where(
            NotificationDeliveryRow.status == STATUS_PENDING,
            NotificationDeliveryRow.next_attempt_at <= moment,
        )
        .order_by(NotificationDeliveryRow.seq)
        .limit(limit)
    )
    if organization_id:
        query = query.where(NotificationDeliveryRow.organization_id == organization_id)
    rows = db.scalars(query).all()

    # Group by destination, keeping the sequence order inside each group.
    groups: dict[str, list[NotificationDeliveryRow]] = {}
    for delivery in rows:
        groups.setdefault(delivery.destination_id, []).append(delivery)

    for group in groups.values():
        for delivery in group:
            destination = db.get(NotificationDestinationRow, delivery.destination_id)
            if destination is None:
                _terminate(delivery, "destination_missing", moment)
                report.failed += 1
                report.note("destination_missing")
                _log(
                    "notification.delivery_refused",
                    channel=delivery.channel,
                    delivery_id=delivery.id,
                    reason="destination_missing",
                )
                continue
            if destination.organization_id != delivery.organization_id:
                # A delivery may never leave the tenant that owns it.
                _terminate(delivery, "tenant_mismatch", moment)
                report.failed += 1
                report.note("tenant_mismatch")
                _log(
                    "notification.delivery_refused",
                    channel=delivery.channel,
                    delivery_id=delivery.id,
                    reason="tenant_mismatch",
                )
                continue
            if not destination.enabled:
                _terminate(delivery, "destination_disabled", moment)
                report.failed += 1
                report.note("destination_disabled")
                _log(
                    "notification.delivery_refused",
                    channel=delivery.channel,
                    delivery_id=delivery.id,
                    reason="destination_disabled",
                )
                continue
            if dry_run:
                report.attempted += 1
                continue
            report.attempted += 1
            failure = _attempt_send(delivery, destination)
            if failure is not None:
                delivery.attempts += 1
                delivery.last_error = failure.reason
                delivery.updated_at = moment
                report.note(failure.reason)
                if not failure.retryable or delivery.attempts >= attempts_budget:
                    delivery.status = STATUS_FAILED
                    report.failed += 1
                    _log(
                        "notification.delivery_failed",
                        attempts=delivery.attempts,
                        channel=delivery.channel,
                        delivery_id=delivery.id,
                        reason=failure.reason,
                        status=STATUS_FAILED,
                    )
                else:
                    delivery.status = STATUS_PENDING
                    delivery.next_attempt_at = moment + timedelta(
                        seconds=backoff_seconds(delivery.attempts)
                    )
                    report.retrying += 1
                    _log(
                        "notification.delivery_retry",
                        attempts=delivery.attempts,
                        channel=delivery.channel,
                        delivery_id=delivery.id,
                        reason=failure.reason,
                        status=STATUS_PENDING,
                    )
                    # Head-of-line: the next message for this destination has
                    # to wait, so the order of the messages is preserved.
                    break
            else:
                delivery.attempts += 1
                delivery.status = STATUS_DELIVERED
                delivery.delivered_at = moment
                delivery.updated_at = moment
                delivery.last_error = None
                report.delivered += 1
                _log(
                    "notification.delivered",
                    attempts=delivery.attempts,
                    channel=delivery.channel,
                    delivery_id=delivery.id,
                    status=STATUS_DELIVERED,
                )
    db.commit()
    return report


def retry_delivery(
    db: Session,
    delivery_id: str,
    *,
    execute: bool = False,
    now: datetime | None = None,
) -> tuple[NotificationDeliveryRow | None, bool]:
    """Re-queue a failed delivery for another attempt.

    The delivery id is kept, so the retry is the same message with the same
    idempotency key. Without ``execute`` nothing is changed.
    """
    delivery = db.scalar(
        select(NotificationDeliveryRow).where(NotificationDeliveryRow.id == delivery_id)
    )
    if delivery is None:
        return None, False
    if not execute:
        return delivery, False
    moment = _aware(now)
    delivery.status = STATUS_PENDING
    delivery.next_attempt_at = moment
    delivery.updated_at = moment
    db.commit()
    return delivery, True


def purge_failed_deliveries(
    db: Session,
    organization_id: str,
    *,
    before: datetime | None = None,
    execute: bool = False,
) -> int:
    """Delete failed delivery rows of one organization.

    Without ``execute`` nothing is deleted and the count is a preview.
    """
    if not organization_id:
        raise ValueError("Explicit tenant required")
    conditions = [
        NotificationDeliveryRow.organization_id == organization_id,
        NotificationDeliveryRow.status == STATUS_FAILED,
    ]
    if before is not None:
        conditions.append(NotificationDeliveryRow.updated_at < _aware(before))
    if not execute:
        # The preview applies exactly the same conditions as the delete.
        preview = db.scalars(select(NotificationDeliveryRow.id).where(*conditions)).all()
        return len(preview)
    result = db.execute(delete(NotificationDeliveryRow).where(*conditions))
    db.commit()
    return int(result.rowcount or 0)


def status_counts(db: Session, organization_id: str) -> dict[str, int]:
    """Delivery counts per status for one organization."""
    rows = db.execute(
        select(NotificationDeliveryRow.status, NotificationDeliveryRow.id).where(
            NotificationDeliveryRow.organization_id == organization_id
        )
    ).all()
    counts = {STATUS_PENDING: 0, STATUS_DELIVERED: 0, STATUS_FAILED: 0}
    for status, _ in rows:
        counts[status] = counts.get(status, 0) + 1
    return counts


__all__ = [
    "CHANNEL_FILE",
    "CHANNEL_WEBHOOK",
    "DispatchReport",
    "backoff_seconds",
    "dispatch_pending",
    "enqueue_deliveries",
    "max_attempts",
    "purge_failed_deliveries",
    "retry_delivery",
    "status_counts",
]
