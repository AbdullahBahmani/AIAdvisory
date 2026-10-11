"""Read-only exports; reject stale or missing deterministic analysis."""

from typing import Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse, PlainTextResponse
from sqlalchemy.orm import Session

from app.api.assessments import detail, get_or_404, require_analysis
from app.db.models import AssessmentRow
from app.db.session import get_db
from app.reporting.render import render_html, render_markdown

router = APIRouter(prefix="/api/v1/assessments", tags=["reporting"])


@router.get("/{assessment_id}/report", response_class=PlainTextResponse)
def assessment_report(
    assessment_id: str,
    format: Literal["markdown", "html"] = Query(default="markdown"),
    db: Session = Depends(get_db),
):
    assessment = get_or_404(db, AssessmentRow, assessment_id)
    require_analysis(assessment)
    # AI suggestions are never authoritative; include recorded revisions for clarity.
    proposals = [{
        "assessment_revision": run.assessment_revision,
        "feature": run.feature,
        "provider": run.provider,
        "model": run.model,
        "prompt_version": run.prompt_version,
        "schema_version": run.schema_version,
        "proposal": run.proposal,
    } for run in getattr(assessment, "ai_runs", [])]
    document = detail(assessment)
    headers = {
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "no-store",
        "Content-Disposition": f'attachment; filename="assessment-{assessment.id}.{ "md" if format == "markdown" else "html"}"',
    }
    if format == "html":
        headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
        return HTMLResponse(render_html(document, proposals), headers=headers)
    return PlainTextResponse(render_markdown(document, proposals), media_type="text/markdown; charset=utf-8", headers=headers)
