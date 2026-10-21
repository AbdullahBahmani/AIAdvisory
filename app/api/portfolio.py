"""Phase 8 portfolio intelligence: human-sourced metadata and measured PoC evidence.

All reads resolve assessment ownership on the server. None of these endpoints
calls an LLM, authorizes business actions or changes the deterministic rule engine.
"""

from __future__ import annotations

from decimal import Decimal
from urllib.parse import unquote

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.assessments import check_mutable, get_or_404
from app.concurrency import assert_current_revision
from app.db.models import (
    AssessmentRow,
    OpportunityEvidenceRow,
    OpportunityProfileRow,
    PocObservationRow,
    PocPlanRow,
    ValueScenarioRow,
    utcnow,
)
from app.db.session import get_db
from app.domain.portfolio import (
    AttachmentView,
    EvidenceIn,
    EvidenceView,
    PocObservationIn,
    PocObservationView,
    PocPlanIn,
    PocPlanView,
    PortfolioProfileIn,
    PortfolioProfileView,
    ScenarioComparisonView,
    SensitivityEnvelopeView,
    SensitivityRangeIn,
    SensitivityThresholdIn,
    ValueScenarioIn,
    ValueScenarioView,
)
from app.security import current_principal, require_role
from app.services import attachments
from app.services.approval_chain import require_completed_approvals
from app.services.audit import audit
from app.services.portfolio import priority_result, scenario_financial_model, value_projection
from app.services.sensitivity import (
    ASSUMPTIONS,
    IncomparableScenariosError,
    ScenarioForComparison,
    compare_scenarios,
    sensitivity_envelope,
    sensitivity_scenario_from,
)

router = APIRouter(prefix="/api/v1", tags=["portfolio-intelligence"])

ATTACHMENT_FILENAME_HEADER = "x-attachment-filename"
ATTACHMENT_TYPE_HEADER = "x-attachment-content-type"


def _fresh(row: AssessmentRow) -> bool:
    return row.analysis_snapshot is not None and row.analysis_revision == row.revision


def _ensure_current(db: Session, row: AssessmentRow) -> None:
    db.refresh(row, with_for_update=True)
    assert_current_revision(row)


def _evidence_view(e: OpportunityEvidenceRow, revision: int) -> EvidenceView:
    return EvidenceView(
        id=e.id,
        assessment_revision=e.assessment_revision,
        kind=e.kind,
        description=e.description,
        source_reference=e.source_reference,
        created_by=e.created_by,
        verified_by=e.verified_by,
        verified_at=e.verified_at,
        created_at=e.created_at,
        stale=e.assessment_revision != revision,
    )


def _scenario_view(s: ValueScenarioRow, revision: int) -> ValueScenarioView:
    inputs = ValueScenarioIn.model_validate(s.inputs)
    gross, net, payback = value_projection(inputs)
    return ValueScenarioView(
        **inputs.model_dump(),
        id=s.id,
        assessment_revision=s.assessment_revision,
        created_by=s.created_by,
        created_at=s.created_at,
        stale=s.assessment_revision != revision,
        gross_savings=gross,
        net_savings=net,
        payback_periods=payback,
        financial_model=scenario_financial_model(inputs),
    )


def _observation_view(ob: PocObservationRow, plan: PocPlanIn) -> PocObservationView:
    observed = Decimal(ob.actual)
    target_met = observed >= plan.target if plan.direction == "higher" else observed <= plan.target
    return PocObservationView(
        id=ob.id,
        actual=observed,
        source_reference=ob.source_reference,
        created_by=ob.created_by,
        created_at=ob.created_at,
        target_met=target_met,
    )


def _poc_view(db: Session, poc: PocPlanRow, revision: int) -> PocPlanView:
    inputs = PocPlanIn.model_validate(poc.inputs)
    observations = db.scalars(
        select(PocObservationRow)
        .where(PocObservationRow.poc_id == poc.id)
        .order_by(PocObservationRow.created_at, PocObservationRow.id)
    ).all()
    return PocPlanView(
        **inputs.model_dump(),
        id=poc.id,
        assessment_revision=poc.assessment_revision,
        status=poc.status,
        created_by=poc.created_by,
        created_at=poc.created_at,
        stale=poc.assessment_revision != revision,
        observations=[_observation_view(o, inputs) for o in observations],
    )


@router.get("/portfolio/opportunities")
def portfolio_opportunities(
    db: Session = Depends(get_db), limit: int = Query(default=100, ge=1, le=200)
):
    p = current_principal(db)
    clauses = [AssessmentRow.status != "deleted"]
    if not p.demo:
        clauses.append(AssessmentRow.organization_id == p.organization_id)
    assessments = db.scalars(
        select(AssessmentRow)
        .where(*clauses)
        .order_by(AssessmentRow.created_at.desc(), AssessmentRow.id)
        .limit(limit)
    ).all()
    ids = [a.id for a in assessments]
    if not ids:
        return {"policy_version": "portfolio-v1", "items": [], "advisory_only": True}
    profiles = {
        x.assessment_id: x
        for x in db.scalars(
            select(OpportunityProfileRow).where(OpportunityProfileRow.assessment_id.in_(ids))
        )
    }
    verified_counts = {
        (aid, rev): count
        for aid, rev, count in db.execute(
            select(
                OpportunityEvidenceRow.assessment_id,
                OpportunityEvidenceRow.assessment_revision,
                func.count(OpportunityEvidenceRow.id),
            )
            .where(
                OpportunityEvidenceRow.assessment_id.in_(ids),
                OpportunityEvidenceRow.verified_at.is_not(None),
            )
            .group_by(
                OpportunityEvidenceRow.assessment_id, OpportunityEvidenceRow.assessment_revision
            )
        ).all()
    }
    scenarios = db.scalars(
        select(ValueScenarioRow)
        .where(ValueScenarioRow.assessment_id.in_(ids))
        .order_by(ValueScenarioRow.created_at.desc(), ValueScenarioRow.id.desc())
    ).all()
    latest = {}
    for scenario in scenarios:
        latest.setdefault((scenario.assessment_id, scenario.assessment_revision), scenario)

    plans = db.scalars(select(PocPlanRow).where(PocPlanRow.assessment_id.in_(ids))).all()
    observation_by_plan = {}
    if plans:
        observations = db.scalars(
            select(PocObservationRow)
            .where(PocObservationRow.poc_id.in_([p.id for p in plans]))
            .order_by(PocObservationRow.created_at.desc(), PocObservationRow.id.desc())
        ).all()
        for obs in observations:
            observation_by_plan.setdefault(obs.poc_id, obs)

    results = []
    for row in assessments:
        profile = profiles.get(row.id)
        evidence_count = verified_counts.get((row.id, row.revision), 0)
        gated = (
            any(
                task.get("recommendation", {}).get("category") in {"more_discovery", "human_led"}
                or any(
                    hit.get("severity") == "block_provider_data"
                    for hit in task.get("rule_hits", [])
                )
                for task in (row.analysis_snapshot or {}).get("tasks", [])
            )
            if _fresh(row)
            else False
        )
        priority = priority_result(
            alignment=profile.strategic_alignment if profile else None,
            urgency=profile.urgency if profile else None,
            effort=profile.delivery_effort if profile else None,
            verified_sources=evidence_count,
            analysis_fresh=_fresh(row),
            policy_gated=gated,
        )
        latest_scenario = latest.get((row.id, row.revision))
        forecast = None
        if latest_scenario:
            scenario = _scenario_view(latest_scenario, row.revision)
            forecast = {
                "name": scenario.name,
                "currency": scenario.currency,
                "period": scenario.period,
                "net_savings": str(scenario.net_savings),
                # The financial model is advisory and may be unavailable: a
                # scenario without a horizon or a discount rate has no net
                # present value, and the reason travels with it.
                "financial_model": scenario.financial_model.model_dump(mode="json"),
                "illustrative_only": True,
            }
        current_plans = [
            p for p in plans if p.assessment_id == row.id and p.assessment_revision == row.revision
        ]
        measured = [p for p in current_plans if p.status == "measured"]
        target_met = 0
        for plan in measured:
            observation = observation_by_plan.get(plan.id)
            if (
                observation is not None
                and _observation_view(observation, PocPlanIn.model_validate(plan.inputs)).target_met
            ):
                target_met += 1
        results.append(
            {
                "assessment_id": row.id,
                "title": row.title,
                "status": row.status,
                "revision": row.revision,
                "priority": priority,
                "verified_evidence_count": evidence_count,
                "latest_forecast": forecast,
                "poc_outcomes": {
                    "plans": len(current_plans),
                    "measured": len(measured),
                    "targets_met": target_met,
                    "self_reported": True,
                },
            }
        )
    results.sort(
        key=lambda item: (
            item["priority"]["priority_score"] is None,
            -(item["priority"]["priority_score"] or 0),
            item["assessment_id"],
        )
    )
    return {"policy_version": "portfolio-v1", "items": results, "advisory_only": True}


@router.get(
    "/assessments/{assessment_id}/portfolio-profile", response_model=PortfolioProfileView | None
)
def get_profile(assessment_id: str, db: Session = Depends(get_db)):
    get_or_404(db, AssessmentRow, assessment_id)
    profile = db.scalar(
        select(OpportunityProfileRow).where(OpportunityProfileRow.assessment_id == assessment_id)
    )
    return PortfolioProfileView.model_validate(profile, from_attributes=True) if profile else None


@router.put("/assessments/{assessment_id}/portfolio-profile", response_model=PortfolioProfileView)
def update_profile(assessment_id: str, payload: PortfolioProfileIn, db: Session = Depends(get_db)):
    require_role(db, "analyst")
    row = get_or_404(db, AssessmentRow, assessment_id)
    check_mutable(row)
    _ensure_current(db, row)
    p = current_principal(db)
    profile = db.scalar(
        select(OpportunityProfileRow).where(OpportunityProfileRow.assessment_id == assessment_id)
    )
    if profile is None:
        profile = OpportunityProfileRow(assessment_id=row.id, updated_by=p.subject)
        db.add(profile)
    for name, value in payload.model_dump().items():
        setattr(profile, name, value)
    profile.updated_by = p.subject
    profile.updated_at = utcnow()
    row.updated_at = utcnow()  # force optimistic row-version check
    audit(db, row, "portfolio.profile_updated", policy_version="portfolio-v1")
    db.commit()
    return PortfolioProfileView.model_validate(profile, from_attributes=True)


@router.get("/assessments/{assessment_id}/evidence", response_model=list[EvidenceView])
def list_evidence(assessment_id: str, db: Session = Depends(get_db)):
    row = get_or_404(db, AssessmentRow, assessment_id)
    records = db.scalars(
        select(OpportunityEvidenceRow)
        .where(OpportunityEvidenceRow.assessment_id == row.id)
        .order_by(OpportunityEvidenceRow.created_at.desc(), OpportunityEvidenceRow.id.desc())
    ).all()
    return [_evidence_view(x, row.revision) for x in records]


@router.post("/assessments/{assessment_id}/evidence", response_model=EvidenceView, status_code=201)
def add_evidence(assessment_id: str, payload: EvidenceIn, db: Session = Depends(get_db)):
    require_role(db, "analyst")
    row = get_or_404(db, AssessmentRow, assessment_id)
    check_mutable(row)
    _ensure_current(db, row)
    item = OpportunityEvidenceRow(
        assessment_id=row.id,
        assessment_revision=row.revision,
        **payload.model_dump(),
        created_by=current_principal(db).subject,
    )
    db.add(item)
    db.flush()
    row.updated_at = utcnow()
    audit(db, row, "portfolio.evidence_added", evidence_id=item.id, revision=row.revision)
    db.commit()
    return _evidence_view(item, row.revision)


@router.post(
    "/assessments/{assessment_id}/evidence/{evidence_id}/verify", response_model=EvidenceView
)
def verify_evidence(assessment_id: str, evidence_id: str, db: Session = Depends(get_db)):
    require_role(db, "reviewer")
    row = get_or_404(db, AssessmentRow, assessment_id)
    check_mutable(row)
    _ensure_current(db, row)
    item = db.scalar(
        select(OpportunityEvidenceRow).where(
            OpportunityEvidenceRow.id == evidence_id,
            OpportunityEvidenceRow.assessment_id == row.id,
        )
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Evidence not found")
    if item.assessment_revision != row.revision:
        raise HTTPException(status_code=409, detail="Old revision evidence cannot be verified")
    if item.verified_at is not None:
        raise HTTPException(status_code=409, detail="Evidence already verified")
    actor = current_principal(db)
    if not actor.demo and item.created_by == actor.subject:
        raise HTTPException(status_code=409, detail="Independent reviewer must verify evidence")
    item.verified_by = actor.subject
    item.verified_at = utcnow()
    row.updated_at = utcnow()
    audit(db, row, "portfolio.evidence_verified", evidence_id=item.id, revision=row.revision)
    db.commit()
    return _evidence_view(item, row.revision)


def _attachment_view(record: attachments.AttachmentRecord, revision: int) -> AttachmentView:
    return AttachmentView(
        id=record.id,
        assessment_id=record.assessment_id,
        assessment_revision=record.assessment_revision,
        evidence_id=record.evidence_id,
        display_name=record.display_name,
        download_name=attachments.download_filename(record.display_name, record.content_type),
        content_type=record.content_type,
        size_bytes=record.size_bytes,
        sha256=record.sha256,
        uploaded_by=record.uploaded_by,
        uploaded_at=record.uploaded_at,
        stale=record.assessment_revision != revision,
    )


def _attachment_or_404(
    db: Session, row: AssessmentRow, attachment_id: str
) -> attachments.AttachmentRecord:
    """Resolve an attachment inside the caller's organization or fail closed."""
    record = attachments.load_record(attachments.storage_root(), row.id, attachment_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Attachment not found")
    principal = current_principal(db)
    if not principal.demo and (
        record.organization_id is None
        or record.organization_id != principal.organization_id
        or row.organization_id != principal.organization_id
    ):
        raise HTTPException(status_code=404, detail="Attachment not found")
    return record


def _declared_content_type(request: Request) -> str:
    declared = request.headers.get(ATTACHMENT_TYPE_HEADER)
    if declared:
        return declared
    request_type = request.headers.get("content-type", "")
    media_type = request_type.split(";")[0].strip().lower()
    if media_type and media_type not in {"application/octet-stream", "multipart/form-data"}:
        return media_type
    return "application/octet-stream"


def _declared_filename(request: Request) -> str | None:
    raw = request.headers.get(ATTACHMENT_FILENAME_HEADER)
    if raw is None:
        return None
    try:
        return unquote(raw, encoding="utf-8", errors="strict")
    except (UnicodeDecodeError, ValueError):
        return None


async def _limited_attachment_body(request: Request) -> bytes:
    """Read the upload body with a hard ceiling; never buffer an unbounded file."""
    limit = attachments.max_bytes()
    declared_length = request.headers.get("content-length")
    if declared_length and declared_length.isdigit() and int(declared_length) > limit:
        raise HTTPException(status_code=413, detail=f"Attachment exceeds the {limit} byte limit")
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            raise HTTPException(
                status_code=413, detail=f"Attachment exceeds the {limit} byte limit"
            )
        chunks.append(chunk)
    return b"".join(chunks)


@router.get(
    "/assessments/{assessment_id}/evidence-attachments", response_model=list[AttachmentView]
)
def list_evidence_attachments(
    assessment_id: str,
    evidence_id: str | None = Query(default=None, max_length=64),
    db: Session = Depends(get_db),
):
    row = get_or_404(db, AssessmentRow, assessment_id)
    records = attachments.list_records(attachments.storage_root(), row.id)
    if evidence_id is not None:
        records = [record for record in records if record.evidence_id == evidence_id]
    return [_attachment_view(record, row.revision) for record in records]


@router.post(
    "/assessments/{assessment_id}/evidence-attachments",
    response_model=AttachmentView,
    status_code=201,
)
async def add_evidence_attachment(
    assessment_id: str,
    request: Request,
    evidence_id: str | None = Query(default=None, max_length=64),
    db: Session = Depends(get_db),
):
    """Store one attachment outside every web-served directory.

    The request body is the raw file; the original name and the declared media
    type travel in headers because they are untrusted metadata. The on-disk
    name is always a server-generated opaque token.
    """
    require_role(db, "analyst")
    row = get_or_404(db, AssessmentRow, assessment_id)
    check_mutable(row)
    _ensure_current(db, row)
    if evidence_id is not None:
        linked = db.scalar(
            select(OpportunityEvidenceRow).where(
                OpportunityEvidenceRow.id == evidence_id,
                OpportunityEvidenceRow.assessment_id == row.id,
            )
        )
        if linked is None:
            raise HTTPException(status_code=404, detail="Evidence not found")
    data = await _limited_attachment_body(request)
    principal = current_principal(db)
    try:
        record = attachments.store_attachment(
            root=attachments.storage_root(),
            assessment_id=row.id,
            assessment_revision=row.revision,
            uploaded_by=principal.subject,
            filename=_declared_filename(request),
            declared_content_type=_declared_content_type(request),
            data=data,
            evidence_id=evidence_id,
            organization_id=row.organization_id,
        )
    except attachments.AttachmentError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    row.updated_at = utcnow()
    audit(
        db,
        row,
        "portfolio.attachment_added",
        attachment_id=record.id,
        revision=row.revision,
        size_bytes=record.size_bytes,
        content_type=record.content_type,
        sha256=record.sha256,
    )
    db.commit()
    return _attachment_view(record, row.revision)


@router.get("/assessments/{assessment_id}/evidence-attachments/{attachment_id}/download")
def download_evidence_attachment(
    assessment_id: str, attachment_id: str, db: Session = Depends(get_db)
):
    """Serve an attachment as an inert download; never render it in our origin."""
    row = get_or_404(db, AssessmentRow, assessment_id)
    record = _attachment_or_404(db, row, attachment_id)
    data = attachments.read_blob(attachments.storage_root(), record.id)
    if data is None:
        raise HTTPException(status_code=404, detail="Attachment not found")
    filename = attachments.download_filename(record.display_name, record.content_type)
    headers = {
        "Content-Disposition": attachments.content_disposition(filename),
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; sandbox; frame-ancestors 'none'",
        "Cache-Control": "no-store",
        "X-Attachment-Sha256": record.sha256,
        "X-Attachment-Assessment-Revision": str(record.assessment_revision),
        "X-Attachment-Stale": "true" if record.assessment_revision != row.revision else "false",
    }
    return Response(content=data, media_type="application/octet-stream", headers=headers)


@router.delete("/assessments/{assessment_id}/evidence-attachments/{attachment_id}", status_code=204)
def delete_evidence_attachment(
    assessment_id: str, attachment_id: str, db: Session = Depends(get_db)
):
    """Delete an attachment and remove the stored file from disk."""
    require_role(db, "analyst")
    row = get_or_404(db, AssessmentRow, assessment_id)
    check_mutable(row)
    _ensure_current(db, row)
    record = _attachment_or_404(db, row, attachment_id)
    if not attachments.delete_attachment(attachments.storage_root(), row.id, record.id):
        raise HTTPException(status_code=404, detail="Attachment not found")
    row.updated_at = utcnow()
    audit(
        db,
        row,
        "portfolio.attachment_deleted",
        attachment_id=record.id,
        revision=row.revision,
    )
    db.commit()
    return Response(status_code=204)


@router.get("/assessments/{assessment_id}/value-scenarios", response_model=list[ValueScenarioView])
def list_scenarios(assessment_id: str, db: Session = Depends(get_db)):
    row = get_or_404(db, AssessmentRow, assessment_id)
    scenarios = db.scalars(
        select(ValueScenarioRow)
        .where(ValueScenarioRow.assessment_id == row.id)
        .order_by(ValueScenarioRow.created_at.desc(), ValueScenarioRow.id.desc())
    ).all()
    return [_scenario_view(s, row.revision) for s in scenarios]


@router.post(
    "/assessments/{assessment_id}/value-scenarios",
    response_model=ValueScenarioView,
    status_code=201,
)
def add_scenario(assessment_id: str, payload: ValueScenarioIn, db: Session = Depends(get_db)):
    require_role(db, "analyst")
    row = get_or_404(db, AssessmentRow, assessment_id)
    check_mutable(row)
    _ensure_current(db, row)
    item = ValueScenarioRow(
        assessment_id=row.id,
        assessment_revision=row.revision,
        inputs=payload.model_dump(mode="json"),
        created_by=current_principal(db).subject,
    )
    db.add(item)
    db.flush()
    row.updated_at = utcnow()
    audit(db, row, "portfolio.scenario_created", scenario_id=item.id, revision=row.revision)
    db.commit()
    return _scenario_view(item, row.revision)


def _scenario_row(db: Session, row: AssessmentRow, scenario_id: str) -> ValueScenarioRow:
    """Fetch a scenario of this assessment; other tenants get the same 404."""
    scenario = db.scalar(
        select(ValueScenarioRow).where(
            ValueScenarioRow.id == scenario_id,
            ValueScenarioRow.assessment_id == row.id,
        )
    )
    if scenario is None:
        raise HTTPException(status_code=404, detail="Scenario not found")
    return scenario


def _sensitivity_ranges(
    lows: dict[str, Decimal | None], highs: dict[str, Decimal | None]
) -> dict[str, SensitivityRangeIn]:
    """Pair the optional per-assumption range query parameters."""
    ranges: dict[str, SensitivityRangeIn] = {}
    for assumption in ASSUMPTIONS:
        low, high = lows[assumption], highs[assumption]
        if low is None and high is None:
            continue
        if low is None or high is None:
            raise HTTPException(
                status_code=422,
                detail=f"Provide both {assumption}_low and {assumption}_high, or neither",
            )
        if high < low:
            raise HTTPException(
                status_code=422,
                detail=f"{assumption}_high must not be below {assumption}_low",
            )
        ranges[assumption] = SensitivityRangeIn(low=low, high=high)
    return ranges


def _threshold_spec(
    row: AssessmentRow, values: ValueScenarioIn, supplied: Decimal | None
) -> SensitivityThresholdIn | None:
    """The minimum net value threshold to apply, with its stated basis.

    The assessment stores the threshold as a bare per-period amount with no
    currency and no period, so the basis is always stated explicitly. A threshold
    whose period cannot be established is reported as unavailable rather than
    compared against a period the analyst never chose.
    """
    if supplied is not None:
        return SensitivityThresholdIn(
            minimum_net_value=supplied,
            basis="caller_supplied_query_parameter",
            period=values.period,
            currency_basis=(
                f"applied in the scenario currency {values.currency}; the caller supplied "
                "no currency"
            ),
        )
    raw = row.minimum_net_value_per_period
    if raw is None:
        return None
    try:
        amount = Decimal(str(raw))
    except ArithmeticError:
        return None
    period = None
    inputs = row.value_inputs or {}
    if inputs.get("period") in {"month", "quarter", "year"}:
        period = inputs["period"]
    return SensitivityThresholdIn(
        minimum_net_value=amount,
        basis="assessment_minimum_net_value_per_period",
        period=period,
        currency_basis=(
            "the assessment threshold records no currency; it is applied in the "
            f"scenario currency {values.currency}"
        ),
    )


@router.get(
    "/assessments/{assessment_id}/value-scenarios/{scenario_id}/sensitivity",
    response_model=SensitivityEnvelopeView,
)
def scenario_sensitivity(
    assessment_id: str,
    scenario_id: str,
    db: Session = Depends(get_db),
    cases_per_period_low: Decimal | None = Query(default=None, ge=0),
    cases_per_period_high: Decimal | None = Query(default=None, ge=0),
    baseline_minutes_low: Decimal | None = Query(default=None, ge=0),
    baseline_minutes_high: Decimal | None = Query(default=None, ge=0),
    target_minutes_low: Decimal | None = Query(default=None, ge=0),
    target_minutes_high: Decimal | None = Query(default=None, ge=0),
    hourly_cost_low: Decimal | None = Query(default=None, ge=0),
    hourly_cost_high: Decimal | None = Query(default=None, ge=0),
    minimum_net_value: Decimal | None = Query(default=None, ge=0),
):
    """Read-only sensitivity envelope for one scenario. Advisory, never a decision."""
    row = get_or_404(db, AssessmentRow, assessment_id)
    scenario = _scenario_row(db, row, scenario_id)
    values = ValueScenarioIn.model_validate(scenario.inputs)
    ranges = _sensitivity_ranges(
        {
            "cases_per_period": cases_per_period_low,
            "baseline_minutes": baseline_minutes_low,
            "target_minutes": target_minutes_low,
            "hourly_cost": hourly_cost_low,
        },
        {
            "cases_per_period": cases_per_period_high,
            "baseline_minutes": baseline_minutes_high,
            "target_minutes": target_minutes_high,
            "hourly_cost": hourly_cost_high,
        },
    )
    return sensitivity_envelope(
        sensitivity_scenario_from(values),
        ranges=ranges,
        threshold=_threshold_spec(row, values, minimum_net_value),
        scenario_id=scenario.id,
        assessment_id=row.id,
        assessment_revision=row.revision,
        stale=scenario.assessment_revision != row.revision,
    )


@router.get(
    "/assessments/{assessment_id}/value-scenarios/comparison",
    response_model=ScenarioComparisonView,
)
def compare_value_scenarios(
    assessment_id: str,
    scenario_ids: list[str] = Query(min_length=1, max_length=25),
    db: Session = Depends(get_db),
):
    """Compare scenarios of one assessment. Mixed currency or period is refused."""
    row = get_or_404(db, AssessmentRow, assessment_id)
    wanted = list(dict.fromkeys(scenario_ids))
    scenarios = db.scalars(
        select(ValueScenarioRow).where(
            ValueScenarioRow.assessment_id == row.id,
            ValueScenarioRow.id.in_(wanted),
        )
    ).all()
    found = {scenario.id: scenario for scenario in scenarios}
    if any(scenario_id not in found for scenario_id in wanted):
        raise HTTPException(status_code=404, detail="Scenario not found")
    items = [
        ScenarioForComparison(
            scenario_id=scenario_id,
            scenario=ValueScenarioIn.model_validate(found[scenario_id].inputs),
            stale=found[scenario_id].assessment_revision != row.revision,
        )
        for scenario_id in wanted
    ]
    try:
        return compare_scenarios(items)
    except IncomparableScenariosError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from None


@router.get("/assessments/{assessment_id}/pocs", response_model=list[PocPlanView])
def list_pocs(assessment_id: str, db: Session = Depends(get_db)):
    row = get_or_404(db, AssessmentRow, assessment_id)
    plans = db.scalars(
        select(PocPlanRow)
        .where(PocPlanRow.assessment_id == row.id)
        .order_by(PocPlanRow.created_at.desc(), PocPlanRow.id.desc())
    ).all()
    return [_poc_view(db, p, row.revision) for p in plans]


@router.post("/assessments/{assessment_id}/pocs", response_model=PocPlanView, status_code=201)
def create_poc(assessment_id: str, payload: PocPlanIn, db: Session = Depends(get_db)):
    require_role(db, "analyst")
    row = get_or_404(db, AssessmentRow, assessment_id)
    check_mutable(row)
    _ensure_current(db, row)
    if not _fresh(row):
        raise HTTPException(
            status_code=409, detail="Run current revision analysis before a PoC plan"
        )
    item = PocPlanRow(
        assessment_id=row.id,
        assessment_revision=row.revision,
        inputs=payload.model_dump(mode="json"),
        status="planned",
        created_by=current_principal(db).subject,
    )
    db.add(item)
    db.flush()
    row.updated_at = utcnow()
    audit(db, row, "portfolio.poc_planned", poc_id=item.id, revision=row.revision)
    db.commit()
    return _poc_view(db, item, row.revision)


def _current_poc(db: Session, row: AssessmentRow, poc_id: str) -> PocPlanRow:
    item = db.scalar(
        select(PocPlanRow).where(
            PocPlanRow.id == poc_id,
            PocPlanRow.assessment_id == row.id,
        )
    )
    if item is None:
        raise HTTPException(status_code=404, detail="PoC not found")
    if item.assessment_revision != row.revision or not _fresh(row):
        raise HTTPException(status_code=409, detail="PoC belongs to an old or unanalyzed revision")
    return item


@router.post("/assessments/{assessment_id}/pocs/{poc_id}/start", response_model=PocPlanView)
def start_poc(assessment_id: str, poc_id: str, db: Session = Depends(get_db)):
    require_role(db, "reviewer")
    row = get_or_404(db, AssessmentRow, assessment_id)
    assert_current_revision(row)
    _ensure_current(db, row)
    item = _current_poc(db, row, poc_id)
    if item.status != "planned":
        raise HTTPException(status_code=409, detail="PoC must be planned before tracking starts")
    if not current_principal(db).demo:
        from os import getenv

        if getenv("ADVISORY_DEPLOYMENT_MODE", "local").lower() == "pilot":
            require_completed_approvals(db, row)
    item.status = "in_progress"
    row.updated_at = utcnow()
    audit(db, row, "portfolio.poc_tracking_started", poc_id=item.id, revision=row.revision)
    db.commit()
    return _poc_view(db, item, row.revision)


@router.post(
    "/assessments/{assessment_id}/pocs/{poc_id}/observations",
    response_model=PocObservationView,
    status_code=201,
)
def observe_poc(
    assessment_id: str, poc_id: str, payload: PocObservationIn, db: Session = Depends(get_db)
):
    require_role(db, "reviewer")
    row = get_or_404(db, AssessmentRow, assessment_id)
    _ensure_current(db, row)
    item = _current_poc(db, row, poc_id)
    if item.status not in {"in_progress", "measured"}:
        raise HTTPException(status_code=409, detail="Start PoC tracking before adding measurements")
    observed = PocObservationRow(
        poc_id=item.id,
        actual=str(payload.actual),
        source_reference=payload.source_reference,
        created_by=current_principal(db).subject,
    )
    db.add(observed)
    db.flush()
    item.status = "measured"
    row.updated_at = utcnow()
    audit(db, row, "portfolio.poc_observed", poc_id=item.id, observation_id=observed.id)
    db.commit()
    return _observation_view(observed, PocPlanIn.model_validate(item.inputs))
