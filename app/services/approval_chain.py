"""Ordered, revision-bound human review gates; never approves production deployment."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ApprovalRow, AssessmentRow

STAGES = ("technical", "security", "business")


def current_chain(db: Session, assessment: AssessmentRow) -> tuple[dict[str, str], dict[str, str]]:
    """Replay append-only votes. Upstream re-votes invalidate downstream stages."""
    votes = db.scalars(
        select(ApprovalRow)
        .where(
            ApprovalRow.assessment_id == assessment.id,
            ApprovalRow.assessment_revision == assessment.revision,
        )
        .order_by(ApprovalRow.created_at, ApprovalRow.id)
    ).all()
    states: dict[str, str] = {}
    actors: dict[str, str] = {}
    for item in votes:
        # A changed earlier approval invalidates previously given downstream votes.
        if item.stage == "technical":
            for stage in ("security", "business"):
                states.pop(stage, None)
                actors.pop(stage, None)
        elif item.stage == "security":
            states.pop("business", None)
            actors.pop("business", None)
        states[item.stage] = item.decision
        actors[item.stage] = item.actor
    return states, actors


def require_completed_approvals(db: Session, assessment: AssessmentRow) -> None:
    states, actors = current_chain(db, assessment)
    if any(states.get(stage) != "approved" for stage in STAGES):
        raise HTTPException(
            status_code=409,
            detail="Technical, security and business reviews must approve this revision",
        )
    if len({actors[stage] for stage in STAGES}) != len(STAGES):
        raise HTTPException(
            status_code=409, detail="Approvals require three distinct authenticated reviewers"
        )
