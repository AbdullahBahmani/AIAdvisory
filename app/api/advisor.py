"""Phase 3 optional AI proposal routes: no tools, no task modifications, no approvals."""

import os
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.ai.provider import AIProvider, get_ai_provider
from app.ai.schemas import AcceptTaskSuggestions, AIRequest, AIRunRecord, Feature
from app.ai.service import list_proposals, run_proposal
from app.api.assessments import change_inputs, check_mutable, get_or_404, task_out
from app.db.models import AIRunRow, AssessmentRow, StepRow, TaskRow
from app.db.session import get_db
from app.domain.persistence import StoredTask
from app.security import current_principal, require_reviewer_or_local_demo
from app.services.audit import audit

router = APIRouter(prefix="/api/v1/assessments", tags=["ai-advisory"])


@router.get("/{assessment_id}/ai-runs", response_model=list[AIRunRecord])
def get_proposals(assessment_id: str, db: Session = Depends(get_db)):
    return list_proposals(get_or_404(db, AssessmentRow, assessment_id))


def require_ai_access(token: str | None) -> None:
    """Developer-only spend guard, not user authentication or tenant authorization."""
    configured = os.getenv("ADVISORY_AI_ACCESS_TOKEN", "")
    if len(configured) < 16 or token is None or not secrets.compare_digest(token, configured):
        raise HTTPException(status_code=403, detail="Local AI access token required")


@router.post("/{assessment_id}/ai/{feature}", response_model=AIRunRecord, status_code=201)
def generate_proposal(
    assessment_id: str,
    feature: Feature,
    payload: AIRequest,
    db: Session = Depends(get_db),
    provider: AIProvider = Depends(get_ai_provider),
    x_ai_access_token: str | None = Header(default=None),
):
    require_ai_access(x_ai_access_token)
    return run_proposal(
        db, get_or_404(db, AssessmentRow, assessment_id), feature, payload, provider
    )


@router.post(
    "/{assessment_id}/ai-runs/{run_id}/accept-tasks",
    response_model=list[StoredTask],
    status_code=201,
)
def accept_task_drafts(
    assessment_id: str,
    run_id: str,
    payload: AcceptTaskSuggestions,
    x_reviewer_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    # A deliberate, token-guarded action, not automatic model acceptance. The
    # dedicated reviewer token is still local-only and not real user identity.
    require_reviewer_or_local_demo(db, x_reviewer_token)
    row = get_or_404(db, AssessmentRow, assessment_id)
    check_mutable(row)
    run = db.get(AIRunRow, run_id)
    if run is None or run.assessment_id != row.id or run.feature != Feature.WORKFLOW:
        raise HTTPException(status_code=404, detail="Workflow AI proposal not found")
    if run.assessment_revision != row.revision:
        raise HTTPException(
            status_code=409, detail="Stale AI proposal; regenerate after assessment edits"
        )
    drafts = run.proposal.get("suggested_tasks", [])
    selected = payload.selected_indices
    if len(set(selected)) != len(selected) or any(i < 0 or i >= len(drafts) for i in selected):
        raise HTTPException(status_code=422, detail="Invalid or repeated proposed task index")
    rows = []
    # Validate every reference BEFORE mutating the revision or rows.
    for index in selected:
        item = drafts[index]
        step = db.get(StepRow, item["step_id"])
        if step is None or step.workflow.assessment_id != row.id:
            raise HTTPException(status_code=409, detail="Proposed workflow step no longer exists")
        rows.append((step, item))
    change_inputs(row)
    created = []
    for step, item in rows:
        task = TaskRow(
            step_id=step.id,
            title=item["title"],
            description=item["description"],
            decision_type="unknown",
            signals={},
            actions=[],
            risks=[],
        )
        db.add(task)
        db.flush()
        created.append(task_out(task))
    principal = current_principal(db)
    # The audit actor is the authenticated principal; a typed label is only kept
    # as an explicitly unverified claim.
    audit(
        db,
        row,
        "ai.task_drafts_accepted",
        actor=payload.reviewer_label,
        source_run_id=run.id,
        selected_indices=selected,
        rationale=payload.rationale,
        identity_unverified=not principal.verified,
    )
    db.commit()
    return created
