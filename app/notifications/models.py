"""Persistence for external notification delivery.

These tables are declared in their own module but live on the application's
shared declarative base, so they are created by the same migration chain and
the same ``alembic check`` drift gate as the rest of the schema.

Two rules shape the schema:

* a delivery belongs to exactly one organization, and it names the
  destination it was queued for, so a delivery can never be re-pointed at
  another tenant's destination;
* a delivery carries the exact payload that was signed, so a retry sends
  byte-identical content under the same delivery id.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base, new_id, utcnow

#: Delivery channels. ``webhook`` posts a signed JSON payload to a URL;
#: ``file`` appends the delivered message to an on-disk outbox that the
#: operator wires to whatever mail infrastructure they already run.
CHANNEL_WEBHOOK = "webhook"
CHANNEL_FILE = "file"
CHANNELS = (CHANNEL_WEBHOOK, CHANNEL_FILE)

#: Delivery states. ``pending`` is retryable, ``delivered`` is terminal and
#: successful, ``failed`` is terminal and exhausted (or refused).
STATUS_PENDING = "pending"
STATUS_DELIVERED = "delivered"
STATUS_FAILED = "failed"
STATUSES = (STATUS_PENDING, STATUS_DELIVERED, STATUS_FAILED)


class NotificationDestinationRow(Base):
    """One operator-configured delivery destination for one organization.

    The target never contains a secret from this application's point of view:
    a webhook URL may embed a token of the *receiving* system, so the target
    is never logged and is only shown in full on the operator's own terminal.
    """

    __tablename__ = "notification_destinations"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "channel", "target", name="uq_notification_destination_target"
        ),
        Index(
            "ix_notification_destinations_organization_id_enabled",
            "organization_id",
            "enabled",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    #: Webhook URL, or a path relative to the outbox root for the file channel.
    target: Mapped[str] = mapped_column(String(500), nullable=False)
    #: Event types this destination wants; an empty list means "every event".
    events: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )


class NotificationDeliveryRow(Base):
    """One queued outbound message for one notification and one destination.

    ``seq`` is the database's monotonic ordering key: it gives the dispatcher
    a total order that survives concurrent writers, so an approval request is
    always delivered before the decision that follows it.

    ``id`` is the stable delivery id. It is the idempotency key of the
    message: a retry reuses it, so the receiver can deduplicate, and the
    dispatcher can refuse to send a delivery that already succeeded.
    """

    __tablename__ = "notification_deliveries"
    __table_args__ = (
        UniqueConstraint("id", name="uq_notification_delivery_id"),
        Index(
            "ix_notification_deliveries_organization_id_status",
            "organization_id",
            "status",
        ),
        Index(
            "ix_notification_deliveries_status_next_attempt_at",
            "status",
            "next_attempt_at",
        ),
        Index(
            "ix_notification_deliveries_destination_id_seq",
            "destination_id",
            "seq",
        ),
        Index("ix_notification_deliveries_notification_id", "notification_id"),
    )

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id: Mapped[str] = mapped_column(String(36), nullable=False, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    #: The in-app notification this message was generated from. Deleting the
    #: notification removes its outbound deliveries as well.
    notification_id: Mapped[str] = mapped_column(
        ForeignKey("inbox_notifications.id", ondelete="CASCADE"), nullable=False
    )
    #: The destination this delivery was queued for. The dispatcher re-checks
    #: that the destination still belongs to the same organization before it
    #: sends anything.
    destination_id: Mapped[str] = mapped_column(String(36), nullable=False)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=STATUS_PENDING)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: When the next attempt may run. Exponential backoff, bounded.
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    #: A closed-vocabulary reason code. Never a URL, a message or a secret.
    last_error: Mapped[str | None] = mapped_column(String(40))
    #: The exact payload that was signed, stored so a retry is byte-identical.
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


def utcnow_local() -> datetime:
    """Timezone-aware 'now' helper kept next to the models it serves."""
    return datetime.now(UTC)
