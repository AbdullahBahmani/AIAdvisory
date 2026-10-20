"""Read-only exports; reject stale or missing deterministic analysis."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from sqlalchemy.orm import Session

from app.api.assessments import detail, get_or_404, require_analysis
from app.db.models import AssessmentRow
from app.db.session import get_db
from app.localization import DEFAULT_LOCALE, LOCALES, Translator
from app.reporting.render import render_html, render_markdown
from app.services.provenance import provenance_view

router = APIRouter(prefix="/api/v1/assessments", tags=["reporting"])


@router.get("/{assessment_id}/report", response_class=PlainTextResponse)
def assessment_report(
    assessment_id: str,
    format: Literal["markdown", "html", "pdf"] = Query(default="markdown"),
    locale: str = Query(default=DEFAULT_LOCALE),
    db: Session = Depends(get_db),
):
    if locale not in LOCALES:
        raise HTTPException(status_code=422, detail=f"Unsupported locale: {locale}")
    assessment = get_or_404(db, AssessmentRow, assessment_id)
    require_analysis(assessment)
    # AI suggestions are never authoritative; include recorded revisions for clarity.
    proposals = [
        {
            "assessment_revision": run.assessment_revision,
            "feature": run.feature,
            "provider": run.provider,
            "model": run.model,
            "prompt_version": run.prompt_version,
            "schema_version": run.schema_version,
            "proposal": run.proposal,
        }
        for run in getattr(assessment, "ai_runs", [])
    ]
    document = detail(assessment)
    # The ledger states which inputs the final decision was taken against.
    ledger = provenance_view(db, assessment)
    translator = Translator(locale)
    extension = {"markdown": "md", "html": "html", "pdf": "pdf"}[format]
    headers = {
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "no-store",
        # The export states its own locale and how complete that catalogue is,
        # so a fallback to English is visible instead of silent.
        "X-Report-Locale": locale,
        "X-Report-Missing-Translations": str(translator.missing_count()),
        "Content-Disposition": f'attachment; filename="assessment-{assessment.id}.{extension}"',
    }
    if format == "html":
        headers["Content-Security-Policy"] = (
            "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; "
            "form-action 'none'; frame-ancestors 'none'"
        )
        return HTMLResponse(
            render_html(document, proposals, ledger, locale=locale), headers=headers
        )
    if format == "pdf":
        # The PDF is built from the same view model as the other exports, so
        # it cannot say anything the Markdown export would not say.
        from app.reporting.pdf import render_pdf

        rendered = render_pdf(document, proposals, ledger, locale=locale)
        if rendered.unrenderable_characters:
            # Text the PDF font cannot draw is replaced by a placeholder.  The
            # count is reported instead of being hidden, because the Markdown
            # export still carries the complete text.
            headers["X-PDF-Unrenderable-Characters"] = str(rendered.unrenderable_characters)
        return Response(
            content=rendered.data,
            media_type="application/pdf",
            headers=headers,
        )
    return PlainTextResponse(
        render_markdown(document, proposals, ledger, locale=locale),
        media_type="text/markdown; charset=utf-8",
        headers=headers,
    )
