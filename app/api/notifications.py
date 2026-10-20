"""Tenant-scoped inspection of notification delivery.

These routes are read-only apart from the explicit dispatch trigger, and they
are scoped to the caller's organization exactly like the rest of the
workspace API. They never expose a payload, a target URL, a token or any
assessment content.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.notifications import dispatcher

# Importing the hooks registers the listener that queues the outbound
# deliveries whenever the application writes an in-app notification.
from app.notifications import hooks as _hooks  # noqa: F401
from app.notifications.models import (
    STATUS_PENDING,
    NotificationDeliveryRow,
    NotificationDestinationRow,
)
from app.notifications.schemas import (
    DeliveryView,
    DestinationView,
    DispatchRequest,
    DispatchView,
)
from app.security import current_principal, require_role

router = APIRouter(prefix="/api/v1", tags=["notifications"])


def _tenant(db: Session) -> str:
    """The caller's organization, or a refusal.

    A notification delivery status is tenant data, so an unverified identity
    (the synthetic local demo) never sees one.
    """
    principal = current_principal(db)
    if not principal.verified or not principal.organization_id:
        raise HTTPException(
            status_code=403,
            detail="Notification delivery status requires an authenticated organization",
        )
    return principal.organization_id


def _target_host(target: str) -> str:
    """The host of a destination target, without the path or query."""
    if "://" in target:
        remainder = target.split("://", 1)[1]
        host = remainder.split("/", 1)[0]
        return host.rsplit("@", 1)[-1]
    return target.split("/", 1)[0]


@router.get("/notifications/destinations", response_model=list[DestinationView])
def list_destinations(db: Session = Depends(get_db)):
    """List the delivery destinations configured for the caller's organization."""
    require_role(db, "admin")
    organization_id = _tenant(db)
    rows = db.scalars(
        select(NotificationDestinationRow)
        .where(NotificationDestinationRow.organization_id == organization_id)
        .order_by(NotificationDestinationRow.created_at, NotificationDestinationRow.id)
    ).all()
    return [
        DestinationView(
            id=row.id,
            channel=row.channel,
            target_host=_target_host(row.target),
            events=list(row.events or []),
            enabled=bool(row.enabled),
            created_at=row.created_at,
        )
        for row in rows
    ]


@router.get("/notifications/deliveries", response_model=list[DeliveryView])
def list_deliveries(
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """List the delivery status of the caller's organization."""
    require_role(db, "admin")
    organization_id = _tenant(db)
    if status is not None and status not in {STATUS_PENDING, "delivered", "failed"}:
        raise HTTPException(status_code=422, detail="Unknown delivery status")
    query = (
        select(NotificationDeliveryRow)
        .where(NotificationDeliveryRow.organization_id == organization_id)
        .order_by(NotificationDeliveryRow.seq.desc())
        .limit(limit)
    )
    if status is not None:
        query = query.where(NotificationDeliveryRow.status == status)
    rows = db.scalars(query).all()
    return [
        DeliveryView(
            id=row.id,
            channel=row.channel,
            event_type=row.event_type,
            status=row.status,
            attempts=row.attempts,
            next_attempt_at=row.next_attempt_at,
            delivered_at=row.delivered_at,
            last_error=row.last_error,
            created_at=row.created_at,
        )
        for row in rows
    ]


@router.post("/notifications/dispatch", response_model=DispatchView)
def run_dispatch(payload: DispatchRequest, db: Session = Depends(get_db)):
    """Deliver the queued messages of the caller's organization.

    The request is a dry run unless the caller asks for the deliveries to be
    made. A dry run reports what would be sent and changes nothing.
    """
    require_role(db, "admin")
    organization_id = _tenant(db)
    report = dispatcher.dispatch_pending(
        db,
        organization_id=organization_id,
        dry_run=payload.dry_run,
        limit=payload.limit,
    )
    return DispatchView(
        attempted=report.attempted,
        delivered=report.delivered,
        failed=report.failed,
        retrying=report.retrying,
        dry_run=report.dry_run,
        reasons=report.reasons,
    )
