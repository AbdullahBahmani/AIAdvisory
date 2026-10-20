"""Validated contracts for the notification delivery API."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Channel = Literal["webhook", "file"]
Status = Literal["pending", "delivered", "failed"]


class DestinationView(BaseModel):
    """One configured destination of the caller's organization.

    The target is reduced to its host: a webhook URL may embed a secret of
    the receiving system, so the full URL is only ever shown on the
    operator's own terminal.
    """

    id: str
    channel: Channel
    target_host: str
    events: list[str]
    enabled: bool
    created_at: datetime


class DeliveryView(BaseModel):
    """The delivery status of one queued message."""

    id: str
    channel: Channel
    event_type: str
    status: Status
    attempts: int
    next_attempt_at: datetime
    delivered_at: datetime | None
    last_error: str | None
    created_at: datetime


class DispatchRequest(BaseModel):
    """A request to deliver the queued messages.

    The default is a dry run: nothing is sent and nothing changes until the
    caller explicitly asks for the deliveries to be made.
    """

    dry_run: bool = True
    limit: int = Field(default=100, ge=1, le=500)


class DispatchView(BaseModel):
    """What one dispatcher run did."""

    attempted: int
    delivered: int
    failed: int
    retrying: int
    dry_run: bool
    reasons: list[str]
