"""Validated Phase 7 collaboration contracts (no production rollout authority)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

Stage = Literal["technical", "security", "business"]
Decision = Literal["approved", "changes_requested"]


class CommentCreate(BaseModel):
    body: str = Field(min_length=3, max_length=2000)

    @field_validator("body")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if len(value.strip()) < 3:
            raise ValueError("Comment must contain meaningful text")
        return value.strip()


class CommentView(BaseModel):
    id: str
    assessment_revision: int
    author: str
    body: str
    created_at: datetime


class AssignmentCreate(BaseModel):
    reviewer_subject: str = Field(min_length=1, max_length=200)


class AssignmentView(BaseModel):
    reviewer_subject: str
    assigned_by: str
    updated_at: datetime


class ApprovalCreate(BaseModel):
    stage: Stage
    decision: Decision
    rationale: str = Field(min_length=5, max_length=2000)

    @field_validator("rationale")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if len(value.strip()) < 5:
            raise ValueError("Approval requires a meaningful rationale")
        return value.strip()


class ApprovalView(ApprovalCreate):
    id: str
    assessment_revision: int
    actor: str
    created_at: datetime


class ApprovalSummary(BaseModel):
    revision: int
    stages: dict[str, str]
    complete: bool
    history: list[ApprovalView]


class MemberView(BaseModel):
    subject: str
    role: str
    provider: str


class NotificationView(BaseModel):
    id: str
    assessment_id: str
    event_type: str
    message: str
    created_at: datetime
    read_at: datetime | None


class WorkspaceSummary(BaseModel):
    total: int
    by_status: dict[str, int]
    pending_review: int
    assigned_to_me: int
    unread_notifications: int
