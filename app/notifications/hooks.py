"""Automatic enqueueing of outbound deliveries.

The application generates an in-app notification; this module makes sure that
the generation also queues the matching outbound deliveries, without any
existing call site having to know that external delivery exists.

The listener runs inside the transaction of the request that generated the
notification, so the in-app row and the delivery records are committed
together: there is never a message that the application forgot to queue, and
never a queue without a message. It writes database rows only - it never
opens a socket, so a broken or hostile destination cannot fail the request
that produced the notification.
"""

from __future__ import annotations

from sqlalchemy import event

from app.db.models import InboxNotificationRow
from app.notifications.dispatcher import enqueue_deliveries


@event.listens_for(InboxNotificationRow, "after_insert")
def _queue_deliveries(mapper, connection, target) -> None:  # noqa: ARG001
    """Queue the outbound deliveries for a freshly written notification."""
    enqueue_deliveries(connection, target)
