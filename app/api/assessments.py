"""Persistence-backed API; all decisions remain advisory and require a reviewer."""

import os
import secrets
from decimal import Decimal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    AnswerRow, AssessmentRow, AuditRow, ReviewRow, StepRow, TaskRow, WorkflowRow,
)
from app.db.session import get_db
from app.concurrency import assert_current_revision
from app.security import current_principal, require_role, require_reviewer_or_local_demo
from app.domain.persistence import (
    AssessmentCreate, AssessmentDetail, AssessmentPatch, AssessmentSummary, AuditEntry,
    ReviewRequest, StepCreate, StepPatch, StoredAnswer, StoredReview, StoredStep,
    StoredTask, StoredWorkflow, TaskCreate, TaskPatch, WorkflowCreate,
)
from app.domain.schemas import (
    Answer, AssessmentPreviewRequest, AssessmentPreviewResponse, DiscoveryResult, Task, WorkflowStep,
)
from app.services.assessment import analyze
from app.services.discovery import summarize_discovery

router = APIRouter(prefix="/api/v1", tags=["assessments"])


def get_or_404(db: Session, model, identifier: str):
    row = db.get(model, identifier)
    if row is None or (model is AssessmentRow and row.status == "deleted"):
        raise HTTPException(status_code=404, detail="Resource not found")
    principal = current_principal(db)
    if not principal.demo:
        if isinstance(row, AssessmentRow):
            parent = row
        elif isinstance(row, WorkflowRow):
            parent = row.assessment
        elif isinstance(row, StepRow):
            parent = row.workflow.assessment
        elif isinstance(row, TaskRow):
            parent = row.step.workflow.assessment
        elif isinstance(row, AnswerRow):
            parent = row.assessment
        elif isinstance(row, ReviewRow):
            parent = row.assessment
        else:
            raise HTTPException(status_code=404, detail="Resource not found")
        if parent.organization_id is None or parent.organization_id != principal.organization_id or parent.status == "deleted":
            raise HTTPException(status_code=404, detail="Resource not found")
    return row


def check_mutable(row: AssessmentRow) -> None:
    assert_current_revision(row)
    if row.status == "finalized":
        raise HTTPException(status_code=409, detail="Finalized assessment must be explicitly reopened")
    if row.status == "deleted":
        raise HTTPException(status_code=404, detail="Resource not found")


def audit(db: Session, row: AssessmentRow, event: str, *, actor: str = "local_demo", **details) -> None:
    principal = current_principal(db)
    # Never trust a submitted reviewer label as the authenticated audit actor.
    db.add(AuditRow(assessment_id=row.id, event_type=event,
                    actor=actor if principal.demo else principal.subject, details=details))


def change_inputs(row: AssessmentRow) -> None:
    check_mutable(row)
    row.revision += 1
    row.analysis_snapshot = None
    row.analysis_revision = None
    row.status = "discovering"


def parent_for_workflow(db: Session, row: WorkflowRow) -> AssessmentRow:
    return get_or_404(db, AssessmentRow, row.assessment_id)


def parent_for_step(db: Session, step: StepRow) -> AssessmentRow:
    return parent_for_workflow(db, get_or_404(db, WorkflowRow, step.workflow_id))


def parent_for_task(db: Session, task: TaskRow) -> AssessmentRow:
    return parent_for_step(db, get_or_404(db, StepRow, task.step_id))


def task_out(row: TaskRow) -> StoredTask:
    return StoredTask(id=row.id, title=row.title, description=row.description,
                      decision_type=row.decision_type, signals=row.signals,
                      actions=row.actions, risks=row.risks)


def step_out(row: StepRow) -> StoredStep:
    return StoredStep(id=row.id, name=row.name, order=row.position,
                      owner=row.owner, system=row.system,
                      input_description=row.input_description, output_description=row.output_description,
                      tasks=[task_out(t) for t in row.tasks])


def workflow_out(row: WorkflowRow) -> StoredWorkflow:
    return StoredWorkflow(id=row.id, name=row.name, steps=[step_out(s) for s in row.steps])


def summary(row: AssessmentRow) -> AssessmentSummary:
    return AssessmentSummary(id=row.id, title=row.title, status=row.status,
                             revision=row.revision, organization=row.organization,
                             owner=row.owner, updated_at=row.updated_at)


def detail(row: AssessmentRow) -> AssessmentDetail:
    return AssessmentDetail(
        id=row.id, title=row.title, organization=row.organization, owner=row.owner,
        business_problem=row.business_problem, status=row.status, revision=row.revision,
        value_inputs=row.value_inputs, minimum_net_value_per_period=row.minimum_net_value_per_period,
        analysis_revision=row.analysis_revision,
        analysis=AssessmentPreviewResponse.model_validate(row.analysis_snapshot) if row.analysis_snapshot else None,
        workflows=[workflow_out(w) for w in row.workflows],
        answers=[StoredAnswer(id=a.id, key=a.key, value=a.value.get("value"), kind=a.kind,
                              source=a.source, confidence=a.confidence, created_at=a.created_at)
                 for a in row.answers],
        reviews=[StoredReview(id=r.id, reviewer_id=r.reviewer_id,
                              selected_category=r.selected_category, rationale=r.rationale,
                              override_reason=r.override_reason, analysis_revision=r.analysis_revision,
                              is_final=r.is_final, created_at=r.created_at,
                              identity_unverified=not r.identity_verified) for r in row.reviews],
        created_at=row.created_at, updated_at=row.updated_at,
    )


def apply_patch(row, patch, field_mapping: dict[str, str] | None = None) -> None:
    values = patch.model_dump(exclude_unset=True, mode="json")
    for key, value in values.items():
        if key in {"title", "name", "order", "decision_type", "signals", "actions", "risks"} and value is None:
            raise HTTPException(status_code=422, detail=f"{key} cannot be null")
        setattr(row, (field_mapping or {}).get(key, key), value)


def validate_review_category(row: AssessmentRow, category: str, override_reason: str | None):
    if not row.analysis_snapshot or row.analysis_revision != row.revision:
        raise HTTPException(status_code=409, detail="Run analysis for the current assessment revision first")
    categories = {t["recommendation"]["category"] for t in row.analysis_snapshot["tasks"]}
    if category not in categories and not override_reason:
        raise HTTPException(status_code=422, detail="Explain any override from analysis recommendations")


def require_local_reviewer(token: str | None, db: Session | None = None):
    if db is not None:
        return require_reviewer_or_local_demo(db, token)
    if os.getenv("ADVISORY_DEMO_MODE") != "1":
        raise HTTPException(status_code=403, detail="Reviewer credential required")
    configured = os.getenv("ADVISORY_REVIEW_TOKEN", "")
    if len(configured) < 16 or token is None or not secrets.compare_digest(token, configured):
        raise HTTPException(status_code=403, detail="Explicit local reviewer token required; production identity is not supported")


@router.post("/assessments", response_model=AssessmentDetail, status_code=201)
def create_assessment(payload: AssessmentCreate, db: Session = Depends(get_db)):
    principal = current_principal(db)
    row = AssessmentRow(**payload.model_dump(mode="json"), organization_id=principal.organization_id)
    if row.minimum_net_value_per_period is not None:
        row.minimum_net_value_per_period = str(row.minimum_net_value_per_period)
    db.add(row)
    db.flush()
    audit(db, row, "assessment.created")
    db.commit()
    return detail(row)


@router.get("/assessments", response_model=list[AssessmentSummary])
def list_assessments(db: Session = Depends(get_db), limit: int = Query(default=50, ge=1, le=200)):
    principal = current_principal(db)
    scope = [AssessmentRow.status != "deleted"]
    if not principal.demo:
        scope.append(AssessmentRow.organization_id == principal.organization_id)
    rows = db.scalars(select(AssessmentRow).where(*scope).order_by(AssessmentRow.created_at.desc()).limit(limit)).all()
    return [summary(row) for row in rows]


@router.get("/assessments/{assessment_id}", response_model=AssessmentDetail)
def get_assessment(assessment_id: str, response: Response, db: Session = Depends(get_db)):
    row = get_or_404(db, AssessmentRow, assessment_id)
    response.headers["ETag"] = f'"{row.revision}"'
    return detail(row)


@router.patch("/assessments/{assessment_id}", response_model=AssessmentDetail)
def update_assessment(assessment_id: str, payload: AssessmentPatch, db: Session = Depends(get_db)):
    row = get_or_404(db, AssessmentRow, assessment_id)
    check_mutable(row)
    if payload.model_fields_set:
        apply_patch(row, payload)
        change_inputs(row)
        audit(db, row, "assessment.updated", fields=sorted(payload.model_fields_set))
        db.commit()
    return detail(row)


@router.delete("/assessments/{assessment_id}", status_code=204)
def delete_assessment(assessment_id: str, x_reviewer_token: str | None = Header(default=None), db: Session = Depends(get_db)):
    if current_principal(db).demo:
        require_reviewer_or_local_demo(db, x_reviewer_token)
    else:
        require_role(db, "admin")
    row = get_or_404(db, AssessmentRow, assessment_id)
    check_mutable(row)
    row.status = "deleted"
    audit(db, row, "assessment.soft_deleted")
    db.commit()


@router.post("/assessments/{assessment_id}/answers", response_model=AssessmentDetail, status_code=201)
def submit_answer(assessment_id: str, payload: Answer, db: Session = Depends(get_db)):
    row = get_or_404(db, AssessmentRow, assessment_id)
    change_inputs(row)
    db.add(AnswerRow(assessment_id=row.id, key=payload.key, value={"value": payload.value},
                     kind=payload.kind.value, source=payload.source, confidence=payload.confidence))
    audit(db, row, "answer.recorded", key=payload.key, kind=payload.kind.value)
    db.commit()
    return detail(row)


@router.get("/assessments/{assessment_id}/next-question", response_model=DiscoveryResult)
@router.get("/assessments/{assessment_id}/missing-information", response_model=DiscoveryResult)
def assessment_discovery(assessment_id: str, db: Session = Depends(get_db)):
    row = get_or_404(db, AssessmentRow, assessment_id)
    answers = [Answer(key=a.key, value=a.value.get("value"), kind=a.kind, source=a.source, confidence=a.confidence) for a in row.answers]
    return summarize_discovery(answers)


@router.post("/assessments/{assessment_id}/workflows", response_model=StoredWorkflow, status_code=201)
def add_workflow(assessment_id: str, payload: WorkflowCreate, db: Session = Depends(get_db)):
    row = get_or_404(db, AssessmentRow, assessment_id)
    change_inputs(row)
    wf = WorkflowRow(assessment_id=row.id, name=payload.name)
    db.add(wf)
    db.flush()
    audit(db, row, "workflow.created", workflow_id=wf.id)
    db.commit()
    return workflow_out(wf)


@router.get("/workflows/{workflow_id}", response_model=StoredWorkflow)
def read_workflow(workflow_id: str, db: Session = Depends(get_db)):
    wf = get_or_404(db, WorkflowRow, workflow_id)
    parent_for_workflow(db, wf)
    return workflow_out(wf)


@router.delete("/workflows/{workflow_id}", status_code=204)
def delete_workflow(workflow_id: str, db: Session = Depends(get_db)):
    wf = get_or_404(db, WorkflowRow, workflow_id)
    assessment = parent_for_workflow(db, wf)
    change_inputs(assessment)
    audit(db, assessment, "workflow.deleted", workflow_id=wf.id)
    db.delete(wf)
    db.commit()


@router.post("/workflows/{workflow_id}/steps", response_model=StoredStep, status_code=201)
def add_step(workflow_id: str, payload: StepCreate, db: Session = Depends(get_db)):
    wf = get_or_404(db, WorkflowRow, workflow_id)
    assessment = parent_for_workflow(db, wf)
    change_inputs(assessment)
    if any(s.position == payload.order for s in wf.steps):
        raise HTTPException(status_code=409, detail="Step order already used in this workflow")
    values = payload.model_dump()
    values["position"] = values.pop("order")
    step = StepRow(workflow_id=wf.id, **values)
    db.add(step)
    db.flush()
    audit(db, assessment, "workflow.step_created", step_id=step.id)
    db.commit()
    return step_out(step)


@router.patch("/workflow-steps/{step_id}", response_model=StoredStep)
def update_step(step_id: str, payload: StepPatch, db: Session = Depends(get_db)):
    step = get_or_404(db, StepRow, step_id)
    assessment = parent_for_step(db, step)
    check_mutable(assessment)
    if payload.model_fields_set:
        if payload.order is not None and payload.order != step.position and any(s.position == payload.order for s in step.workflow.steps):
            raise HTTPException(status_code=409, detail="Step order already used in this workflow")
        apply_patch(step, payload, {"order": "position"})
        change_inputs(assessment)
        audit(db, assessment, "workflow.step_updated", step_id=step.id)
        db.commit()
    return step_out(step)


@router.delete("/workflow-steps/{step_id}", status_code=204)
def delete_step(step_id: str, db: Session = Depends(get_db)):
    step = get_or_404(db, StepRow, step_id)
    assessment = parent_for_step(db, step)
    change_inputs(assessment)
    audit(db, assessment, "workflow.step_deleted", step_id=step.id)
    db.delete(step)
    db.commit()


@router.post("/workflow-steps/{step_id}/tasks", response_model=StoredTask, status_code=201)
def add_task(step_id: str, payload: TaskCreate, db: Session = Depends(get_db)):
    step = get_or_404(db, StepRow, step_id)
    assessment = parent_for_step(db, step)
    change_inputs(assessment)
    task = TaskRow(step_id=step.id, **payload.model_dump(mode="json"))
    db.add(task)
    db.flush()
    audit(db, assessment, "task.created", task_id=task.id)
    db.commit()
    return task_out(task)


@router.patch("/tasks/{task_id}", response_model=StoredTask)
def update_task(task_id: str, payload: TaskPatch, db: Session = Depends(get_db)):
    task = get_or_404(db, TaskRow, task_id)
    assessment = parent_for_task(db, task)
    check_mutable(assessment)
    if payload.model_fields_set:
        apply_patch(task, payload)
        change_inputs(assessment)
        audit(db, assessment, "task.updated", task_id=task.id, fields=sorted(payload.model_fields_set))
        db.commit()
    return task_out(task)


@router.delete("/tasks/{task_id}", status_code=204)
def delete_task(task_id: str, db: Session = Depends(get_db)):
    task = get_or_404(db, TaskRow, task_id)
    assessment = parent_for_task(db, task)
    change_inputs(assessment)
    audit(db, assessment, "task.deleted", task_id=task.id)
    db.delete(task)
    db.commit()


def require_analysis(row: AssessmentRow) -> dict:
    if row.analysis_snapshot is None or row.analysis_revision != row.revision:
        raise HTTPException(status_code=409, detail="Analysis is missing or stale; rerun analysis")
    return row.analysis_snapshot


@router.post("/assessments/{assessment_id}/analyze", response_model=AssessmentPreviewResponse)
def analyze_assessment(assessment_id: str, db: Session = Depends(get_db)):
    row = get_or_404(db, AssessmentRow, assessment_id)
    check_mutable(row)
    steps = []
    for workflow in row.workflows:
        for step in workflow.steps:
            if not step.tasks:
                raise HTTPException(status_code=409, detail="Each workflow step needs at least one task before analysis")
            steps.append(WorkflowStep(id=step.id, name=step.name, order=step.position,
                                      owner=step.owner, system=step.system,
                                      input_description=step.input_description,
                                      output_description=step.output_description,
                                      tasks=[Task(id=t.id, title=t.title, description=t.description,
                                                  decision_type=t.decision_type, signals=t.signals,
                                                  actions=t.actions, risks=t.risks) for t in step.tasks]))
    if not steps:
        raise HTTPException(status_code=409, detail="Add workflow steps and tasks before analysis")
    # Different workflows can reuse ordinal positions; actual task identifiers remain UUID unique.
    steps = [s.model_copy(update={"order": index}) for index, s in enumerate(steps)]
    payload = AssessmentPreviewRequest(title=row.title, steps=steps,
                                       value_inputs=row.value_inputs,
                                       minimum_net_value_per_period=Decimal(row.minimum_net_value_per_period) if row.minimum_net_value_per_period else None)
    result = analyze(payload)
    row.analysis_snapshot = result.model_dump(mode="json")
    row.analysis_revision = row.revision
    row.status = "analyzed"
    audit(db, row, "assessment.analyzed", revision=row.revision, policy_version=result.policy_version)
    db.commit()
    return result


@router.get("/assessments/{assessment_id}/scores")
def scores(assessment_id: str, db: Session = Depends(get_db)):
    assessment = get_or_404(db, AssessmentRow, assessment_id)
    snapshot = require_analysis(assessment)
    return {"revision": assessment.revision, "tasks": [{"task_id": t["task_id"], "ai_suitability": t["ai_suitability"], "technical_feasibility": t["technical_feasibility"], "data_readiness": t["data_readiness"]} for t in snapshot["tasks"]]}


@router.get("/assessments/{assessment_id}/risks")
def risks(assessment_id: str, db: Session = Depends(get_db)):
    snapshot = require_analysis(get_or_404(db, AssessmentRow, assessment_id))
    return {"tasks": [{"task_id": t["task_id"], "risks": t["risks"], "rule_hits": t["rule_hits"]} for t in snapshot["tasks"]]}


@router.get("/assessments/{assessment_id}/recommendations")
def recommendations(assessment_id: str, db: Session = Depends(get_db)):
    snapshot = require_analysis(get_or_404(db, AssessmentRow, assessment_id))
    return {"tasks": [{"task_id": t["task_id"], "recommendation": t["recommendation"]} for t in snapshot["tasks"]]}


@router.post("/assessments/{assessment_id}/fde-review", response_model=StoredReview, status_code=201)
def record_review(assessment_id: str, payload: ReviewRequest, db: Session = Depends(get_db)):
    if not current_principal(db).demo:
        require_role(db, "reviewer")
    row = get_or_404(db, AssessmentRow, assessment_id)
    check_mutable(row)
    validate_review_category(row, payload.selected_category.value, payload.override_reason)
    principal = current_principal(db)
    if not principal.demo and payload.reviewer_id != principal.subject:
        raise HTTPException(status_code=403, detail="Reviewer label must match credential subject")
    review = ReviewRow(assessment_id=row.id, analysis_revision=row.revision,
                       identity_verified=not principal.demo, **payload.model_dump(mode="json"))
    db.add(review)
    db.flush()
    row.status = "in_review"
    audit(db, row, "fde.review_recorded", actor=payload.reviewer_id, review_id=review.id, identity_unverified=principal.demo)
    db.commit()
    return StoredReview.model_validate({"id": review.id, **payload.model_dump(), "analysis_revision": review.analysis_revision,
                                        "is_final": False, "created_at": review.created_at,
                                        "identity_unverified": principal.demo})


@router.post("/assessments/{assessment_id}/final-decision", response_model=StoredReview)
def final_decision(assessment_id: str, x_reviewer_token: str | None = Header(default=None), db: Session = Depends(get_db)):
    principal = require_reviewer_or_local_demo(db, x_reviewer_token)
    row = get_or_404(db, AssessmentRow, assessment_id)
    check_mutable(row)
    require_analysis(row)
    review = next((r for r in reversed(row.reviews) if r.analysis_revision == row.revision and not r.is_final), None)
    if review is None:
        raise HTTPException(status_code=409, detail="Record an FDE review first")
    if not principal.demo and principal.role != "admin" and review.reviewer_id != principal.subject:
        raise HTTPException(status_code=403, detail="Only the reviewing FDE or organization admin can finalize")
    review.is_final = True
    row.status = "finalized"
    audit(db, row, "fde.finalized", actor=review.reviewer_id, review_id=review.id, identity_unverified=principal.demo)
    db.commit()
    return StoredReview(id=review.id, reviewer_id=review.reviewer_id,
                        selected_category=review.selected_category, rationale=review.rationale,
                        override_reason=review.override_reason, analysis_revision=review.analysis_revision,
                        is_final=True, created_at=review.created_at,
                        identity_unverified=not review.identity_verified)


@router.post("/assessments/{assessment_id}/reopen", response_model=AssessmentDetail)
def reopen_assessment(assessment_id: str, x_reviewer_token: str | None = Header(default=None), db: Session = Depends(get_db)):
    require_reviewer_or_local_demo(db, x_reviewer_token)
    row = get_or_404(db, AssessmentRow, assessment_id)
    if row.status != "finalized":
        raise HTTPException(status_code=409, detail="Only finalized assessments need reopening")
    change_reopen(row)
    audit(db, row, "fde.local_demo_reopened", identity_unverified=True)
    db.commit()
    return detail(row)


def change_reopen(row: AssessmentRow) -> None:
    row.revision += 1
    row.status = "discovering"
    row.analysis_snapshot = None
    row.analysis_revision = None


@router.get("/assessments/{assessment_id}/audit", response_model=list[AuditEntry])
def audit_history(assessment_id: str, db: Session = Depends(get_db)):
    row = get_or_404(db, AssessmentRow, assessment_id)
    return [AuditEntry(id=a.id, event_type=a.event_type, actor=a.actor,
                       details=a.details, created_at=a.created_at) for a in row.audit_events]
