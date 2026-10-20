"""Bounded, privacy-gated advisor and append-only proposal persistence."""

import json
import os
import re
import time
from decimal import Decimal, InvalidOperation

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.ai.data_guard import assert_no_obvious_sensitive_data
from app.ai.prompts import prompt_for
from app.ai.provider import AIProvider, ProviderFailure
from app.ai.schemas import PROMPT_VERSION, SCHEMA_VERSION, SCHEMAS, AIRequest, AIRunRecord, Feature
from app.db.models import AIRunRow, AssessmentRow
from app.domain.schemas import Answer
from app.services.audit import audit
from app.services.discovery import summarize_discovery

MAX_CONTEXT_BYTES = 12000
MAX_PROVIDER_TEXT_BYTES = 16000


def _optional_cost_estimate(
    input_tokens: int | None, output_tokens: int | None
) -> tuple[str | None, str | None]:
    """Only calculate a rough USD estimate with supplied, sourced pricing rates."""
    r1 = os.getenv("ADVISORY_AI_INPUT_USD_PER_MILLION")
    r2 = os.getenv("ADVISORY_AI_OUTPUT_USD_PER_MILLION")
    source = os.getenv("ADVISORY_AI_PRICING_SOURCE", "")
    if not (r1 and r2 and source and input_tokens is not None and output_tokens is not None):
        return None, None
    try:
        i, o = Decimal(r1), Decimal(r2)
    except InvalidOperation:
        return None, None
    if not i.is_finite() or not o.is_finite() or min(i, o) < 0 or len(source) > 200:
        return None, None
    amount = (Decimal(input_tokens) * i + Decimal(output_tokens) * o) / 1000000
    return str(amount.quantize(Decimal("0.000001"))), source


def _context(row: AssessmentRow) -> dict:
    # Do not include organization, owner, identities, evidence sources, or reviewer tokens.
    steps = []
    for workflow in row.workflows:
        for step in workflow.steps:
            steps.append(
                {
                    "step_id": step.id,
                    "name": step.name,
                    "tasks": [
                        {
                            "task_id": task.id,
                            "title": task.title,
                            "description": task.description,
                            "decision_type": task.decision_type,
                            "actions": task.actions,
                        }
                        for task in step.tasks
                    ],
                }
            )
    answers = [
        Answer(
            key=a.key,
            value=a.value.get("value"),
            kind=a.kind,
            source=a.source,
            confidence=a.confidence,
        )
        for a in row.answers
    ]
    discovery = summarize_discovery(answers)
    analysis = row.analysis_snapshot if row.analysis_revision == row.revision else None
    return {
        "title": row.title,
        "problem": row.business_problem,
        "assessment_revision": row.revision,
        "workflow_steps": steps,
        "answered_question_keys": sorted(
            {a.key for a in row.answers if a.value.get("value") is not None}
        ),
        "unanswered_question_keys": discovery.missing_question_keys,
        "contradictions": [
            {"key": finding.key, "kind": finding.kind.value}
            for finding in discovery.findings
            if finding.kind.value == "contradiction"
        ],
        "deterministic_recommendations": [
            {
                "task_id": x["task_id"],
                "category": x["recommendation"]["category"],
                "mandatory_controls": x["recommendation"].get("required_controls", []),
                "missing_evidence": x["recommendation"].get("missing_evidence", []),
            }
            for x in analysis["tasks"]
        ]
        if analysis
        else [],
    }


def _privacy_gate(row: AssessmentRow, payload: AIRequest) -> None:
    if row.status in {"deleted", "finalized"}:
        raise HTTPException(status_code=409, detail="Assessment cannot be advised in this state")
    if not payload.send_to_provider or not payload.approved_non_sensitive_data:
        raise HTTPException(
            status_code=403, detail="Explicit per-request provider and data approval required"
        )
    for wf in row.workflows:
        for step in wf.steps:
            for task in step.tasks:
                signals = task.signals or {}
                if (
                    signals.get("permission_to_use_data") is False
                    or signals.get("provider_data_permitted") is False
                ):
                    raise HTTPException(
                        status_code=403, detail="Provider processing prohibited by task data policy"
                    )
                if (
                    signals.get("sensitive_data") is True
                    and signals.get("provider_data_permitted") is not True
                ):
                    raise HTTPException(
                        status_code=403,
                        detail="Sensitive task requires explicit provider permission",
                    )
    # Defense in depth: conservative pattern matching over outbound model context.
    assert_no_obvious_sensitive_data(_context(row))
    # This is not enterprise DLP, consent verification, or provider permission.


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
        raise HTTPException(
            status_code=502, detail="Model referenced an unknown workflow step or task"
        )


def run_proposal(
    db: Session, row: AssessmentRow, feature: Feature, payload: AIRequest, provider: AIProvider
) -> AIRunRecord:
    _privacy_gate(row, payload)
    if len(row.ai_runs) >= 30:
        raise HTTPException(
            status_code=429, detail="Local demo AI proposal limit reached (30 per assessment)"
        )
    context = _context(row)
    if (
        feature in (Feature.ARCHITECTURE, Feature.CHALLENGE)
        and not context["deterministic_recommendations"]
    ):
        raise HTTPException(
            status_code=409, detail="Run deterministic analysis before this advisory feature"
        )
    if (
        feature in (Feature.WORKFLOW, Feature.RISK, Feature.ARCHITECTURE)
        and not context["workflow_steps"]
    ):
        raise HTTPException(
            status_code=409, detail="Map a workflow before requesting this AI feature"
        )
    serialized = json.dumps(context, ensure_ascii=True)
    if len(serialized.encode("utf-8")) > MAX_CONTEXT_BYTES:
        raise HTTPException(
            status_code=413, detail="Assessment context too large for bounded AI request"
        )
    model = SCHEMAS[feature]
    system, user = prompt_for(feature, context, model.model_json_schema())
    revision = row.revision
    start = time.monotonic()
    try:
        generated = provider.generate(system, user, model.model_json_schema())
    except ProviderFailure:
        raise HTTPException(
            status_code=502, detail="AI provider unavailable or refused output"
        ) from None
    elapsed_ms = int((time.monotonic() - start) * 1000)
    if len(generated.content.encode("utf-8")) > MAX_PROVIDER_TEXT_BYTES:
        raise HTTPException(status_code=502, detail="AI output exceeds maximum size")
    try:
        result = model.model_validate_json(generated.content).model_dump(mode="json")
    except (ValidationError, ValueError):
        raise HTTPException(
            status_code=502, detail="AI response failed strict schema validation"
        ) from None
    _check_references(feature, result, context)
    if feature == Feature.DISCOVERY:

        def normalize(value):
            return re.sub(r"[^a-z0-9]+", "", value.casefold())

        from app.services.discovery import QUESTIONS

        recorded_keys = set(context["answered_question_keys"])
        prior_questions = [
            run.proposal.get("question", "")
            for run in row.ai_runs
            if run.feature == Feature.DISCOVERY and run.assessment_revision == revision
        ]
        asked_questions = [q.text for q in QUESTIONS if q.key in recorded_keys]
        if normalize(result["question"]) in {
            normalize(x) for x in asked_questions + prior_questions
        }:
            raise HTTPException(
                status_code=502, detail="AI proposed a previously answered or suggested question"
            )
    if feature == Feature.ARCHITECTURE:
        categories = {
            item["task_id"]: item["category"] for item in context["deterministic_recommendations"]
        }
        for item in result["patterns"]:
            category = categories.get(item["task_id"])
            if category in {"human_led", "traditional_software"} and item["pattern"] in {
                "tool_using_agent",
                "ai_plus_deterministic_rules",
                "ai_plus_human_approval",
                "classification",
                "extraction",
                "rag_with_citations",
            }:
                raise HTTPException(
                    status_code=502, detail="AI architecture contradicts deterministic policy"
                )
            if category == "ai_human_approval" and not item["human_approval_required"]:
                raise HTTPException(
                    status_code=502, detail="AI cannot remove mandatory human approval"
                )
    db.refresh(row)
    if row.revision != revision or row.status in ("deleted", "finalized"):
        raise HTTPException(
            status_code=409, detail="Assessment changed during AI request; discard stale proposal"
        )
    # Store the validated advisory proposal only, not provider prompts, API secrets, or raw output.
    cost, price_source = _optional_cost_estimate(generated.input_tokens, generated.output_tokens)
    run = AIRunRow(
        assessment_id=row.id,
        assessment_revision=revision,
        feature=feature.value,
        proposal=result,
        provider=generated.provider,
        model=generated.model,
        prompt_version=PROMPT_VERSION,
        schema_version=SCHEMA_VERSION,
        elapsed_ms=elapsed_ms,
        input_tokens=generated.input_tokens,
        output_tokens=generated.output_tokens,
        estimated_cost_usd=cost,
        pricing_source=price_source,
    )
    db.add(run)
    db.flush()
    # Routed through the shared audit gate so this event carries the same identity
    # evidence and the same tamper-evident chain position as every other event.
    audit(
        db,
        row,
        "ai.proposal_generated",
        run_id=run.id,
        feature=feature.value,
        revision=revision,
        prompt_version=PROMPT_VERSION,
    )
    db.commit()
    return _run_out(run, revision)


def _run_out(run: AIRunRow, current_revision: int) -> AIRunRecord:
    return AIRunRecord(
        id=run.id,
        assessment_id=run.assessment_id,
        assessment_revision=run.assessment_revision,
        feature=run.feature,
        proposal=run.proposal,
        provider=run.provider,
        model=run.model,
        prompt_version=run.prompt_version,
        schema_version=run.schema_version,
        elapsed_ms=run.elapsed_ms,
        input_tokens=run.input_tokens,
        output_tokens=run.output_tokens,
        estimated_cost_usd=run.estimated_cost_usd,
        pricing_source=run.pricing_source,
        created_at=run.created_at.isoformat(),
        stale=current_revision != run.assessment_revision,
    )


def list_proposals(row: AssessmentRow) -> list[AIRunRecord]:
    return [_run_out(run, row.revision) for run in reversed(row.ai_runs)]
