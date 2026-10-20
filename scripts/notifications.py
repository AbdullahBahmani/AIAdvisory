"""Operator control of external notification delivery.

Configuration and inspection happen here, on the operator's own host, in the
same spirit as ``scripts/admin.py``: there is no public endpoint that
configures a destination or hands out a secret.

Examples:
  python -m scripts.notifications configure-destination --organization Example \\
      --channel webhook --target https://hooks.example.org/aiadvisory
  python -m scripts.notifications configure-destination --organization Example \\
      --channel file --target messages.jsonl --events reviewer.assigned
  python -m scripts.notifications list-destinations --organization Example
  python -m scripts.notifications list-deliveries --organization Example --status failed
  python -m scripts.notifications dispatch --organization Example          # dry run
  python -m scripts.notifications dispatch --organization Example --execute
  python -m scripts.notifications retry --delivery-id <id> --execute
  python -m scripts.notifications purge-failed --organization Example --execute

Anything that changes or sends something is a **dry run by default** and needs
an explicit ``--execute``.
"""

from __future__ import annotations

import argparse
import ipaddress
from datetime import UTC, datetime

from sqlalchemy import select

from app.db.models import OrganizationRow
from app.db.session import SessionLocal
from app.notifications import channels, dispatcher, egress
from app.notifications.models import (
    CHANNEL_FILE,
    CHANNELS,
    STATUS_FAILED,
    STATUS_PENDING,
    NotificationDeliveryRow,
    NotificationDestinationRow,
)


def _organization(db, reference: str) -> OrganizationRow:
    """Resolve an organization by name or by id."""
    row = db.scalar(select(OrganizationRow).where(OrganizationRow.id == reference))
    if row is None:
        row = db.scalar(select(OrganizationRow).where(OrganizationRow.name == reference))
    if row is None:
        raise ValueError(f"Unknown organization: {reference}")
    return row


def _validate_target(channel: str, target: str) -> None:
    """Refuse a target that must never be contacted.

    The check is deliberately offline: it never resolves a name, so
    configuring a destination never turns into a network call. A name is
    checked again immediately before every delivery.
    """
    if channel == CHANNEL_FILE:
        try:
            channels.outbox_path("check", target)
        except channels.DeliveryError as exc:
            raise ValueError(f"Refusing outbox target ({exc.reason})") from None
        return
    if "://" not in target:
        raise ValueError("Webhook target must be an absolute URL")
    scheme, _, remainder = target.partition("://")
    scheme = scheme.lower()
    if scheme not in {"http", "https"}:
        raise ValueError("Webhook target must use HTTPS")
    host = remainder.split("/", 1)[0].split("@")[-1]
    if host.startswith("["):
        host = host[1:].split("]", 1)[0]
    else:
        host = host.split(":", 1)[0]
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        # A name: it is resolved and checked again before every delivery.
        # Plain HTTP is only accepted for an explicitly enabled loopback
        # development target.
        if scheme == "http" and not egress.loopback_allowed():
            raise ValueError("Webhook target must use HTTPS") from None
        return
    kind = egress.classify_address(str(address))
    if kind == "loopback":
        if not egress.loopback_allowed() or scheme != "http":
            raise ValueError("Refusing loopback webhook target")
        return
    if kind != "public":
        raise ValueError(f"Refusing webhook target address ({kind})")
    if scheme != "https":
        raise ValueError("Webhook target must use HTTPS")


def configure_destination(
    db,
    organization: str,
    channel: str,
    target: str,
    *,
    event_types: list[str] | None = None,
    enabled: bool = True,
) -> NotificationDestinationRow:
    """Create or update one destination of an organization."""
    if channel not in CHANNELS:
        raise ValueError(f"Unknown channel: {channel}")
    _validate_target(channel, target)
    org = _organization(db, organization)
    row = db.scalar(
        select(NotificationDestinationRow).where(
            NotificationDestinationRow.organization_id == org.id,
            NotificationDestinationRow.channel == channel,
            NotificationDestinationRow.target == target,
        )
    )
    if row is None:
        row = NotificationDestinationRow(
            organization_id=org.id,
            channel=channel,
            target=target,
            events=list(event_types or []),
            enabled=enabled,
        )
        db.add(row)
    else:
        row.events = list(event_types or [])
        row.enabled = enabled
        row.updated_at = datetime.now(UTC)
    db.commit()
    return row


def list_deliveries(db, organization: str, status: str | None = None, limit: int = 50):
    """The delivery status of one organization."""
    org = _organization(db, organization)
    query = (
        select(NotificationDeliveryRow)
        .where(NotificationDeliveryRow.organization_id == org.id)
        .order_by(NotificationDeliveryRow.seq.desc())
        .limit(limit)
    )
    if status:
        query = query.where(NotificationDeliveryRow.status == status)
    return db.scalars(query).all()


def main():
    parser = argparse.ArgumentParser(
        description="Configure and inspect external notification delivery"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    configure = sub.add_parser(
        "configure-destination", help="Create or update a delivery destination"
    )
    configure.add_argument("--organization", required=True, help="Name or id of the tenant")
    configure.add_argument("--channel", choices=list(CHANNELS), required=True)
    configure.add_argument("--target", required=True, help="Webhook URL or outbox path")
    configure.add_argument(
        "--events",
        default="",
        help="Comma-separated event types; empty means every event",
    )
    configure.add_argument("--disable", action="store_true", help="Store the destination disabled")
    configure.add_argument("--reveal-target", action="store_true", help="Print the target in full")

    destinations = sub.add_parser("list-destinations", help="List the destinations of a tenant")
    destinations.add_argument("--organization", required=True)
    destinations.add_argument("--reveal-target", action="store_true")

    deliveries = sub.add_parser("list-deliveries", help="List the delivery status of a tenant")
    deliveries.add_argument("--organization", required=True)
    deliveries.add_argument("--status", choices=[STATUS_PENDING, "delivered", STATUS_FAILED])
    deliveries.add_argument("--limit", type=int, default=50)

    dispatch = sub.add_parser("dispatch", help="Deliver the queued messages")
    dispatch.add_argument("--organization", help="Limit the run to one tenant")
    dispatch.add_argument("--limit", type=int, default=100)
    dispatch.add_argument(
        "--execute", action="store_true", help="Required to actually send anything"
    )

    retry = sub.add_parser("retry", help="Re-queue a failed delivery")
    retry.add_argument("--delivery-id", required=True)
    retry.add_argument("--execute", action="store_true", help="Required to re-queue")

    purge = sub.add_parser("purge-failed", help="Delete failed delivery rows")
    purge.add_argument("--organization", required=True)
    purge.add_argument("--before", help="ISO timestamp with timezone")
    purge.add_argument(
        "--execute", action="store_true", help="Required to actually delete anything"
    )

    args = parser.parse_args()

    try:
        with SessionLocal.begin() as db:
            if args.command == "configure-destination":
                event_types = [item.strip() for item in args.events.split(",") if item.strip()]
                row = configure_destination(
                    db,
                    args.organization,
                    args.channel,
                    args.target,
                    event_types=event_types,
                    enabled=not args.disable,
                )
                shown = args.target if args.reveal_target else _masked(row.target)
                print(f"Destination ID: {row.id}\nChannel: {row.channel}\nTarget: {shown}")
            elif args.command == "list-destinations":
                org = _organization(db, args.organization)
                rows = db.scalars(
                    select(NotificationDestinationRow)
                    .where(NotificationDestinationRow.organization_id == org.id)
                    .order_by(NotificationDestinationRow.created_at)
                ).all()
                if not rows:
                    print("No destinations configured")
                for row in rows:
                    shown = row.target if args.reveal_target else _masked(row.target)
                    print(
                        f"{row.id}  {row.channel}  {shown}  "
                        f"events={','.join(row.events or []) or 'all'}  "
                        f"enabled={row.enabled}"
                    )
            elif args.command == "list-deliveries":
                org = _organization(db, args.organization)
                rows = list_deliveries(db, args.organization, args.status, args.limit)
                if not rows:
                    print("No deliveries")
                for row in rows:
                    print(
                        f"{row.id}  {row.channel}  {row.event_type}  {row.status}  "
                        f"attempts={row.attempts}  error={row.last_error or '-'}"
                    )
                counts = dispatcher.status_counts(db, org.id)
                print(
                    "Totals: "
                    + ", ".join(f"{status}={count}" for status, count in sorted(counts.items()))
                )
            elif args.command == "dispatch":
                organization_id = None
                if args.organization:
                    organization_id = _organization(db, args.organization).id
                report = dispatcher.dispatch_pending(
                    db,
                    organization_id=organization_id,
                    dry_run=not args.execute,
                    limit=args.limit,
                )
                print(
                    f"{'Dry run: ' if report.dry_run else ''}attempted={report.attempted} "
                    f"delivered={report.delivered} failed={report.failed} "
                    f"retrying={report.retrying}"
                )
                if report.reasons:
                    print("Reasons: " + ", ".join(report.reasons))
                if report.dry_run:
                    print("Nothing was sent. Add --execute to deliver the queued messages.")
            elif args.command == "retry":
                delivery, changed = dispatcher.retry_delivery(
                    db, args.delivery_id, execute=args.execute
                )
                if delivery is None:
                    parser.error("Delivery not found")
                print(
                    f"{'Re-queued' if changed else 'Dry run: would re-queue'} "
                    f"delivery {delivery.id}"
                )
                if not changed:
                    print("Nothing was changed. Add --execute to re-queue the delivery.")
            elif args.command == "purge-failed":
                org = _organization(db, args.organization)
                before = None
                if args.before:
                    before = datetime.fromisoformat(args.before)
                count = dispatcher.purge_failed_deliveries(
                    db, org.id, before=before, execute=args.execute
                )
                print(f"{'Purged' if args.execute else 'Dry run:'} {count} failed deliveries")
                if not args.execute:
                    print("Nothing was deleted. Add --execute to delete the rows.")
    except ValueError as exc:
        parser.error(str(exc))


def _masked(target: str) -> str:
    """Show the host of a target, but never its path or query.

    A webhook URL may embed a secret of the receiving system, so the full
    target is only printed on request.
    """
    if "://" in target:
        scheme, _, remainder = target.partition("://")
        host = remainder.split("/", 1)[0]
        return f"{scheme}://{host}/..."
    return target


if __name__ == "__main__":
    main()
