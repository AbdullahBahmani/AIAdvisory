"""The two delivery channels: a signed webhook and an on-disk outbox.

Both channels carry exactly the same minimal payload. Neither channel ever
sees assessment content, a reviewer rationale, a person, a token or a
credential.

* **webhook** - a JSON body is signed with HMAC-SHA256 and POSTed to the
  configured URL. The signature, the delivery id and the event type travel in
  headers so a receiver can deduplicate and verify without parsing the body.
* **file** - the delivered message is appended to an on-disk outbox below the
  operator's outbox root, one file per organization. An operator wires that
  file (or the directory) to whatever mail infrastructure they already run,
  so this project never takes on a mail stack.

The file channel is idempotent by construction: an append is skipped when the
delivery id is already present in the file, so a retry after a crash between
the write and the status update cannot duplicate a message.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from app.notifications import egress, events
from app.notifications.models import CHANNEL_FILE, CHANNEL_WEBHOOK

#: Environment variable holding the root of the on-disk outbox.
OUTBOX_ENV = "ADVISORY_NOTIFICATION_OUTBOX_DIR"

#: Default outbox root, relative to the working directory of the process.
DEFAULT_OUTBOX_DIR = "var/notification-outbox"

#: Webhook headers. The names are namespaced so a receiver cannot mistake
#: them for its own headers.
HEADER_EVENT = "X-AIADVISORY-Event"
HEADER_DELIVERY_ID = "X-AIADVISORY-Delivery-Id"
HEADER_TIMESTAMP = "X-AIADVISORY-Timestamp"
HEADER_SIGNATURE = "X-AIADVISORY-Signature"
HEADER_IDEMPOTENCY = "Idempotency-Key"
USER_AGENT = "aiadvisory-notifications/1.0"


class DeliveryError(Exception):
    """A delivery attempt failed. ``reason`` is a closed-vocabulary code.

    ``retryable`` says whether trying again could plausibly succeed: a
    connection error may, a refused destination or a missing secret may not.
    """

    def __init__(self, reason: str, retryable: bool = True) -> None:
        super().__init__(reason)
        self.reason = reason
        self.retryable = retryable


def outbox_root() -> Path:
    """The operator-configured root of the on-disk outbox."""
    raw = os.getenv(OUTBOX_ENV, DEFAULT_OUTBOX_DIR)
    return Path(raw).expanduser().resolve()


def outbox_path(organization_id: str, target: str) -> Path:
    """Resolve a file-channel target inside the organization's outbox folder.

    The target is a relative path. Absolute paths, parent traversal and
    symlink escapes are refused, so one organization can never write into
    another organization's folder or outside the outbox root.
    """
    if not target or len(target) > 500:
        raise DeliveryError("target_invalid", retryable=False)
    if target.startswith("/") or target.startswith("~") or ":" in target.split("/")[0]:
        raise DeliveryError("target_invalid", retryable=False)
    parts = [part for part in target.replace("\\", "/").split("/") if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        raise DeliveryError("target_invalid", retryable=False)
    root = outbox_root()
    organization_root = (root / organization_id).resolve()
    path = organization_root.joinpath(*parts)
    resolved = path.resolve()
    if resolved != organization_root and organization_root not in resolved.parents:
        raise DeliveryError("target_invalid", retryable=False)
    return path


def _webhook_headers(
    *,
    payload: dict,
    body: bytes,
    secret: bytes,
    occurred_at: str,
) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
        HEADER_EVENT: str(payload.get("event_type", "")),
        HEADER_DELIVERY_ID: str(payload.get("delivery_id", "")),
        HEADER_TIMESTAMP: occurred_at,
        HEADER_SIGNATURE: events.sign(secret, occurred_at, body),
        # The delivery id is the idempotency key of the message.
        HEADER_IDEMPOTENCY: str(payload.get("delivery_id", "")),
    }


def send_webhook(target: str, payload: dict, occurred_at: str) -> None:
    """Deliver one notification to a webhook destination.

    Raises :class:`DeliveryError` with a closed-vocabulary reason code.
    """
    secret = events.signing_secret()
    if secret is None:
        # Fail closed: an unsigned notification is never sent.
        raise DeliveryError("secret_unavailable", retryable=False)
    body = events.canonical_body(payload)
    headers = _webhook_headers(payload=payload, body=body, secret=secret, occurred_at=occurred_at)
    try:
        status = egress.post_json(target, body, headers)
    except egress.TargetError as exc:
        # Refused destinations are never retried: the configuration, not the
        # network, has to change first.
        raise DeliveryError(exc.reason, retryable=False) from None
    if 200 <= status < 300:
        return
    if status in {408, 425, 429} or status >= 500:
        raise DeliveryError(f"http_{status}")
    raise DeliveryError(f"http_{status}", retryable=False)


def send_file(
    target: str,
    organization_id: str,
    payload: dict,
    occurred_at: str,
) -> None:
    """Append one delivered notification to the on-disk outbox.

    The append is idempotent: a delivery id that is already in the file is
    not written again.
    """
    path = outbox_path(organization_id, target)
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError as exc:
        raise DeliveryError("outbox_unavailable", retryable=False) from exc
    delivery_id = str(payload.get("delivery_id", ""))
    marker = f'"delivery_id":"{delivery_id}"'.encode()
    try:
        if path.exists():
            with path.open("rb") as handle:
                if marker in handle.read():
                    # Already delivered: the retry must not duplicate it.
                    return
    except OSError as exc:
        raise DeliveryError("outbox_unavailable", retryable=False) from exc
    secret = events.signing_secret()
    record = {
        "payload": payload,
        "signed_at": occurred_at,
        "signature": events.sign(secret, occurred_at, events.canonical_body(payload))
        if secret
        else None,
    }
    line = json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(descriptor, line.encode("utf-8"))
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise DeliveryError("outbox_unavailable", retryable=False) from exc


def send(
    channel: str,
    *,
    target: str,
    organization_id: str,
    payload: dict,
    occurred_at: str,
) -> None:
    """Dispatch one payload over the named channel."""
    if channel == CHANNEL_WEBHOOK:
        send_webhook(target, payload, occurred_at)
        return
    if channel == CHANNEL_FILE:
        send_file(target, organization_id, payload, occurred_at)
        return
    raise DeliveryError("unknown_channel", retryable=False)
