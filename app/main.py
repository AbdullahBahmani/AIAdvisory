"""FastAPI public API. Phase 1 offers *stateless*, rules-only preview endpoints."""

import logging

from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.api.advisor import router as advisor_router
from app.api.approval_policies import router as approval_policies_router
from app.api.assessments import router as assessments_router
from app.api.notifications import router as notifications_router
from app.api.portfolio import router as portfolio_router
from app.api.reports import router as reports_router
from app.api.scim import router as scim_router
from app.api.workspace import router as workspace_router
from app.db.session import get_db, schema_is_current
from app.deployment import check_deployment_configuration
from app.domain.schemas import (
    AssessmentPreviewRequest,
    AssessmentPreviewResponse,
    DiscoveryRequest,
    DiscoveryResult,
    Question,
)
from app.security import Principal, authenticate_request
from app.services.assessment import analyze
from app.services.discovery import QUESTIONS, summarize_discovery
from app.services.scim_errors import SCIM_CONTENT_TYPE, ScimError
from app.telemetry import APP_VERSION, METRICS_CONTENT_TYPE, instrument_request, render_metrics

#: Schema revisions this build can serve. ``0008_portfolio`` is the previous
#: head and stays supported; ``0009_local_attribution`` adds the local actor
#: attribution columns; ``0010_index_and_constraint_hardening`` adds the
#: tenant-path indexes and the step-order uniqueness constraint;
#: ``0011_audit_chain`` adds the tamper-evident audit chain columns;
#: ``0012_scim_provisioning`` adds the SCIM provisioning and approval-policy
#: tables; ``0013_notification_delivery`` adds the notification destinations
#: and the queued outbound deliveries. All of them run the same application
#: code, so every revision in this set is a healthy database.
SUPPORTED_SCHEMA_REVISIONS = frozenset(
    {
        "0008_portfolio",
        "0009_local_attribution",
        "0010_index_and_constraint_hardening",
        "0011_audit_chain",
        "0012_scim_provisioning",
        "0013_notification_delivery",
    }
)

check_deployment_configuration()

app = FastAPI(
    title="AI Opportunity Advisor API",
    version=APP_VERSION,
    description="Tenant-scoped local pilot API; synthetic demo requires explicit opt-in.",
)


@app.exception_handler(StaleDataError)
async def stale_write_conflict(_request, _exc):
    return JSONResponse(
        status_code=409, content={"detail": "Assessment changed concurrently; reload and retry"}
    )


@app.exception_handler(SQLAlchemyError)
async def database_unavailable(_request, exc):
    """A database that cannot answer is a 503, not a 500.

    The failure-recovery drill found that a stopped database made route handlers
    answer ``500 Internal Server Error``, while the readiness probe and the rate
    limiter correctly answered 503. A 500 tells the caller the application is
    broken; a 503 tells them the dependency is unavailable and the request may be
    retried. The distinction matters operationally: a dependency outage must not
    look like an application defect, and must not look like a successful write.
    """
    logger = logging.getLogger("aiadvisory.database")
    logger.warning('{"event": "database.unavailable", "error": "%s"}', type(exc).__name__)
    return JSONResponse(
        status_code=503,
        content={"detail": "Database unavailable; the request was not applied"},
        headers={"Retry-After": "5"},
    )


@app.exception_handler(ScimError)
async def scim_error(_request, exc: ScimError):
    """Render a SCIM failure with the SCIM error schema.

    SCIM clients read the failure from the body, so the endpoint must not
    answer with the platform's generic ``{"detail": ...}`` shape.
    """
    return JSONResponse(
        status_code=exc.status,
        content=exc.payload(),
        media_type=SCIM_CONTENT_TYPE,
        headers={"WWW-Authenticate": "Bearer"} if exc.status == 401 else None,
    )


@app.get("/ready", include_in_schema=False, tags=["health"])
def readiness(db: Session = Depends(get_db)):
    """Database/schema readiness only, not enterprise release approval."""
    try:
        db.execute(text("SELECT 1 FROM organizations LIMIT 1")).all()
        current = db.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
        # A version string alone is not evidence that the required columns exist:
        # schema_is_current inspects the live catalogue for every column the
        # running code needs.
        if current not in SUPPORTED_SCHEMA_REVISIONS or not schema_is_current(db):
            raise HTTPException(status_code=503, detail="Database migration not current")
    except SQLAlchemyError:
        raise HTTPException(status_code=503, detail="Database not ready") from None
    return {"status": "ready"}


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    mode: str


@app.get("/health", response_model=HealthResponse, tags=["health"])
def health() -> HealthResponse:
    """Side-effect-free readiness response; no AI credentials required."""
    return HealthResponse(
        status="ok", service="ai-opportunity-advisor-api", version=APP_VERSION, mode="rules-only"
    )


@app.get("/metrics", include_in_schema=False, tags=["health"])
def metrics(
    principal: Principal = Depends(authenticate_request),
    db: Session = Depends(get_db),
) -> Response:
    """Operator metrics in the Prometheus text exposition format.

    Authenticated with the same dependency as the rest of the API (a viewer
    credential is enough) and scoped to the caller's organization, because the
    output contains tenant-derived business aggregates, not only liveness
    information. The endpoint exposes counts, sums and bounded labels only:
    never assessment content, customer data, reviewer names, tokens,
    identifiers, request bodies or request ids. See ``docs/metrics.md``.
    """
    organization_id = None if principal.demo else principal.organization_id
    body = render_metrics(db=db, organization_id=organization_id)
    return Response(content=body, media_type=METRICS_CONTENT_TYPE)


@app.get(
    "/api/v1/discovery/questions",
    response_model=list[Question],
    tags=["discovery"],
    dependencies=[Depends(authenticate_request)],
)
def questions() -> list[Question]:
    return list(QUESTIONS)


@app.post(
    "/api/v1/discovery/next-question",
    response_model=DiscoveryResult,
    tags=["discovery"],
    dependencies=[Depends(authenticate_request)],
)
def next_question(payload: DiscoveryRequest) -> DiscoveryResult:
    return summarize_discovery(payload.answers)


@app.post(
    "/api/v1/analysis/preview",
    response_model=AssessmentPreviewResponse,
    tags=["analysis"],
    dependencies=[Depends(authenticate_request)],
)
def preview_analysis(payload: AssessmentPreviewRequest) -> AssessmentPreviewResponse:
    return analyze(payload)


app.middleware("http")(instrument_request)


app.include_router(assessments_router, dependencies=[Depends(authenticate_request)])

app.include_router(advisor_router, dependencies=[Depends(authenticate_request)])
app.include_router(reports_router, dependencies=[Depends(authenticate_request)])

app.include_router(workspace_router, dependencies=[Depends(authenticate_request)])

app.include_router(notifications_router, dependencies=[Depends(authenticate_request)])

app.include_router(portfolio_router, dependencies=[Depends(authenticate_request)])

app.include_router(approval_policies_router, dependencies=[Depends(authenticate_request)])

# The SCIM provisioning endpoint authenticates with its own dedicated bearer
# token (see ``app/services/scim_auth.py``). It is deliberately *not* mounted
# with ``authenticate_request``: the synthetic demo identity and the ordinary
# user credentials must never reach it.
app.include_router(scim_router)
