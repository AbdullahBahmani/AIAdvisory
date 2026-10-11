"""Bounded, privacy-gated advisor and append-only proposal persistence."""

import json
import time

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.ai.prompts import prompt_for
from app.ai.provider import AIProvider, ProviderFailure
from app.ai.schemas import (AIRunRecord, AIRequest, Feature, PROMPT_VERSION, SCHEMA_VERSION,
                            SCHEMAS)
from app.db.models import AIRunRow, AssessmentRow, AuditRow
from app.services.discovery import summarize_discovery
from app.domain.schemas import Answer

MAX_CONTEXT_BYTES = 12000
MAX_PROVIDER_TEXT_BYTES = 16000


def _context(row: AssessmentRow) -> dict:
    # Do not include organization, owner, identities, evidence sources, or reviewer tokens.
    steps = []
    for workflow in row.workflows:
        for step in workflow.steps:
            steps.append({
                "step_id": step.id, "name": step.name,
                "tasks": [{"task_id": task.id, "title": task.title,
                           "description": task.description,
                           "decision_type": task.decision_type,
                           "actions": task.actions} for task in step.tasks],
            })
    answers = [Answer(key=a.key, value=a.value.get("value"), kind=a.kind,
                      source=a.source, confidence=a.confidence) for a in row.answers]
    discovery = summarize_discovery(answers)
    analysis = row.analysis_snapshot if row.analysis_revision == row.revision else None
    return {
        "title": row.title, "problem": row.business_problem,
        "assessment_revision": row.revision,
        "workflow_steps": steps,
        "answered_question_keys": sorted({a.key for a in row.answers if a.value.get("value") is not None}),
        "unanswered_question_keys": discovery.missing_question_keys,
        "deterministic_recommendations": [
            {"task_id": x["task_id"], "category": x["recommendation"]["category"],
             "mandatory_controls": x["recommendation"].get("required_controls", []),
             "missing_evidence": x["recommendation"].get("missing_evidence", [])}
            for x in analysis["tasks"]
        ] if analysis else [],
    }


def _privacy_gate(row: AssessmentRow, payload: AIRequest) -> None:
    if row.status in {"deleted", "finalized"}:
        raise HTTPException(status_code=409, detail="Assessment cannot be advised in this state")
    if not payload.send_to_provider or not payload.approved_non_sensitive_data:
        raise HTTPException(status_code=403, detail="Explicit per-request provider and data approval required")
    for wf in row.workflows:
        for step in wf.steps:
            for task in step.tasks:
                signals = task.signals or {}
                if signals.get("permission_to_use_data") is False or signals.get("provider_data_permitted") is False:
                    raise HTTPException(status_code=403, detail="Provider processing prohibited by task data policy")
                if signals.get("sensitive_data") is True and signals.get("provider_data_permitted") is not True:
                    raise HTTPException(status_code=403, detail="Sensitive task requires explicit provider permission")
    # There is no enterprise consent or DLP integration. Never claim that user attestation
    # proves compliance. The README limits this route to approved synthetic data.


def _check_references(feature: Feature, proposal: dict, context: dict) -> None:
    known_steps = {step["step_id"] for step in context["workflow_steps"]}
    known_tasks = {task["task_id"] for step in context["workflow_steps"] for task in step["tasks"]}
    if feature == Feature.WORKFLOW:
        identifiers = [task["step_id"] for task in proposal["suggested_tasks"]]
        known = known_steps
    elif feature == Feature.RISK:
        identifiers = [risk["task_id"] for risk in proposal["suggested_risks"]]
        known = known_tasks
    elif feature == Feature.ARCHITECTURE:
        identifiers = [pattern["task_id"] for pattern in proposal["patterns"]]
        known = known_tasks
    else:
        return
    if any(identifier not in known for identifier in identifiers):
        raise HTTPException(status_code=502, detail="Model referenced an unknown workflow step or task")


def run_proposal(db: Session, row: AssessmentRow, feature: Feature, payload: AIRequest,
                 provider: AIProvider) -> AIRunRecord:
    _privacy_gate(row, payload)
    if len(row.ai_runs) >= 30:
        raise HTTPException(status_code=429, detail="Local demo AI proposal limit reached (30 per assessment)")
    context = _context(row)
    if feature in (Feature.ARCHITECTURE, Feature.CHALLENGE) and not context["deterministic_recommendations"]:
        raise HTTPException(status_code=409, detail="Run deterministic analysis before this advisory feature")
    if feature in (Feature.WORKFLOW, Feature.RISK, Feature.ARCHITECTURE) and not context["workflow_steps"]:
        raise HTTPException(status_code=409, detail="Map a workflow before requesting this AI feature")
    serialized = json.dumps(context, ensure_ascii=True)
    if len(serialized.encode("utf-8")) > MAX_CONTEXT_BYTES:
        raise HTTPException(status_code=413, detail="Assessment context too large for bounded AI request")
    model = SCHEMAS[feature]
    system, user = prompt_for(feature, context, model.model_json_schema())
    revision = row.revision
    start = time.monotonic()
    try:
        generated = provider.generate(system, user, model.model_json_schema())
    except ProviderFailure:
        raise HTTPException(status_code=502, detail="AI provider unavailable or refused output") from None
    elapsed_ms = int((time.monotonic() - start) * 1000)
    if len(generated.content.encode("utf-8")) > MAX_PROVIDER_TEXT_BYTES:
        raise HTTPException(status_code=502, detail="AI output exceeds maximum size")
    try:
        result = model.model_validate_json(generated.content).model_dump(mode="json")
    except (ValidationError, ValueError):
        raise HTTPException(status_code=502, detail="AI response failed strict schema validation") from None
    _check_references(feature, result, context)
    if feature == Feature.ARCHITECTURE:
        categories = {item["task_id"]: item["category"]
                      for item in context["deterministic_recommendations"]}
        for item in result["patterns"]:
            category = categories.get(item["task_id"])
            if category in {"human_led", "traditional_software"} and item["pattern"] in {
                "tool_using_agent", "ai_plus_deterministic_rules", "ai_plus_human_approval",
                "classification", "extraction", "rag_with_citations",
            }:
                raise HTTPException(status_code=502, detail="AI architecture contradicts deterministic policy")
            if category == "ai_human_approval" and not item["human_approval_required"]:
                raise HTTPException(status_code=502, detail="AI cannot remove mandatory human approval")
    db.refresh(row)
    if row.revision != revision or row.status in ("deleted", "finalized"):
        raise HTTPException(status_code=409, detail="Assessment changed during AI request; discard stale proposal")
    # Store the validated advisory proposal only, not provider prompts, API secrets, or raw output.
    run = AIRunRow(assessment_id=row.id, assessment_revision=revision, feature=feature.value,
                   proposal=result, provider=generated.provider, model=generated.model,
                   prompt_version=PROMPT_VERSION, schema_version=SCHEMA_VERSION,
                   elapsed_ms=elapsed_ms, input_tokens=generated.input_tokens,
                   output_tokens=generated.output_tokens)
    db.add(run)
    db.flush()
    db.add(AuditRow(assessment_id=row.id, event_type="ai.proposal_generated",
                    details={"run_id": run.id, "feature": feature.value,
                             "revision": revision, "prompt_version": PROMPT_VERSION}))
    db.commit()
    return _run_out(run, revision)


def _run_out(run: AIRunRow, current_revision: int) -> AIRunRecord:
    return AIRunRecord(
        id=run.id, assessment_id=run.assessment_id,
        assessment_revision=run.assessment_revision, feature=run.feature,
        proposal=run.proposal, provider=run.provider, model=run.model,
        prompt_version=run.prompt_version, schema_version=run.schema_version,
        elapsed_ms=run.elapsed_ms, input_tokens=run.input_tokens,
        output_tokens=run.output_tokens, created_at=run.created_at.isoformat(),
        stale=current_revision != run.assessment_revision,
    )


def list_proposals(row: AssessmentRow) -> list[AIRunRecord]:
    return [_run_out(run, row.revision) for run in reversed(row.ai_runs)]
