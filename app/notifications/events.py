"""Notification event catalogue, payload construction and request signing.

The payload is deliberately the smallest useful message:

``schema``
    A version marker, so a receiver can reject what it does not understand.
``delivery_id``
    The stable delivery id. It is the idempotency key: a retry reuses it.
``event_type``
    A closed, code-controlled vocabulary of what happened.
``assessment_id``
    The assessment the event is about, so the receiver can look it up.
``organization_id``
    The owning tenant, so a shared relay can route the message.
``occurred_at``
    When the event happened (UTC, ISO 8601).
``link_path``
    A relative path back into the application. It is a path, not a URL: the
    receiver resolves it against its own base address.

What the payload deliberately does **not** carry:

* assessment content - title, business problem, answers, analysis snapshot,
  scores, recommendations, evidence or value scenarios;
* reviewer content - rationale, override reason, comments, reviewer names or
  any person identifier;
* secrets - API tokens, credentials, signing secrets, or the target URL of
  the destination itself (a webhook URL may embed the receiver's secret);
* anything that could be used to act on the assessment. A notification is a
  message *about* an event; it is never an event and never an authorization.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from typing import Any

#: Payload schema version.
SCHEMA = "aiadvisory.notification.v1"

#: Events the application generates today.
EVENT_REVIEWER_ASSIGNED = "reviewer.assigned"
EVENT_COMMENT_ADDED = "comment.added"
EVENT_APPROVAL_RECORDED = "approval.recorded"

KNOWN_EVENTS = frozenset(
    {
        EVENT_REVIEWER_ASSIGNED,
        EVENT_COMMENT_ADDED,
        EVENT_APPROVAL_RECORDED,
    }
)

#: Environment variable holding the webhook signing secret. It is never
#: stored in the database and never written to a log line.
SECRET_ENV = "ADVISORY_NOTIFICATION_WEBHOOK_SECRET"

#: Minimum accepted secret length. A short secret is not a secret.
MIN_SECRET_LENGTH = 16


def signing_secret() -> bytes | None:
    """Return the signing secret from the environment, or ``None``.

    The secret is never persisted, never logged and never part of a payload.
    An unset or too-short secret disables the webhook channel: the delivery
    fails closed instead of going out unsigned.
    """
    raw = os.getenv(SECRET_ENV, "")
    if len(raw) < MIN_SECRET_LENGTH:
        return None
    return raw.encode("utf-8")


def canonical_body(payload: dict[str, Any]) -> bytes:
    """Render a payload as the exact bytes that are signed and sent.

    Sorted keys and compact separators make the bytes reproducible, so a
    retry produces a byte-identical body under the same delivery id.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sign(secret: bytes, timestamp: str, body: bytes) -> str:
    """Return the ``sha256=<hex>`` signature of ``timestamp.body``.

    The timestamp is the event time and is part of the signed material, so a
    receiver can reject a replay and so a retry keeps the same signature.
    """
    mac = hmac.new(secret, timestamp.encode("utf-8") + b"." + body, hashlib.sha256)
    return "sha256=" + mac.hexdigest()


def verify(secret: bytes, timestamp: str, body: bytes, signature: str) -> bool:
    """Constant-time check of a signature produced by :func:`sign`."""
    return hmac.compare_digest(sign(secret, timestamp, body), signature)


def build_payload(
    *,
    delivery_id: str,
    event_type: str,
    assessment_id: str,
    organization_id: str,
    occurred_at: str,
    link_path: str,
) -> dict[str, Any]:
    """Build the outbound payload. See the module docstring for what is out."""
    return {
        "schema": SCHEMA,
        "delivery_id": delivery_id,
        "event_type": event_type,
        "assessment_id": assessment_id,
        "organization_id": organization_id,
        "occurred_at": occurred_at,
        "link_path": link_path,
    }


def assessment_link(assessment_id: str) -> str:
    """The relative path a receiver uses to open the assessment."""
    return f"/api/v1/assessments/{assessment_id}"
