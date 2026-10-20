"""FastAPI public API. Phase 1 offers *stateless*, rules-only preview endpoints."""

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError
from app.db.session import get_db, schema_is_current
from app.deployment import check_deployment_configuration
from app.telemetry import instrument_request

from app.security import authenticate_request

from app.api.assessments import router as assessments_router
from app.api.advisor import router as advisor_router
from app.api.reports import router as reports_router
from app.api.workspace import router as workspace_router
from app.api.portfolio import router as portfolio_router
from pydantic import BaseModel

from app.domain.schemas import (
    AssessmentPreviewRequest,
    AssessmentPreviewResponse,
    DiscoveryRequest,
    DiscoveryResult,
    Question,
)
from app.services.assessment import analyze
from app.services.discovery import QUESTIONS, summarize_discovery

APP_VERSION = "0.8.0"

check_deployment_configuration()

app = FastAPI(
    title="AI Opportunity Advisor API",
    version=APP_VERSION,
    description="Tenant-scoped local pilot API; synthetic demo requires explicit opt-in.",
)


@app.exception_handler(StaleDataError)
async def stale_write_conflict(_request, _exc):
    return JSONResponse(status_code=409, content={"detail": "Assessment changed concurrently; reload and retry"})


@app.get("/ready", include_in_schema=False, tags=["health"])
def readiness(db: Session = Depends(get_db)):
    """Database/schema readiness only, not enterprise release approval."""
    try:
        db.execute(text("SELECT 1 FROM organizations LIMIT 1")).all()
        current = db.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
        # A version string alone is not evidence that the required columns exist.
        if current not in {"0008_portfolio", "0009_local_attribution"} or not schema_is_current(db):
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


@app.get("/api/v1/discovery/questions", response_model=list[Question], tags=["discovery"], dependencies=[Depends(authenticate_request)])
def questions() -> list[Question]:
    return list(QUESTIONS)


@app.post("/api/v1/discovery/next-question", response_model=DiscoveryResult, tags=["discovery"], dependencies=[Depends(authenticate_request)])
def next_question(payload: DiscoveryRequest) -> DiscoveryResult:
    return summarize_discovery(payload.answers)


@app.post("/api/v1/analysis/preview", response_model=AssessmentPreviewResponse, tags=["analysis"], dependencies=[Depends(authenticate_request)])
def preview_analysis(payload: AssessmentPreviewRequest) -> AssessmentPreviewResponse:
    return analyze(payload)


app.middleware("http")(instrument_request)


app.include_router(assessments_router, dependencies=[Depends(authenticate_request)])

app.include_router(advisor_router, dependencies=[Depends(authenticate_request)])
app.include_router(reports_router, dependencies=[Depends(authenticate_request)])

app.include_router(workspace_router, dependencies=[Depends(authenticate_request)])

app.include_router(portfolio_router, dependencies=[Depends(authenticate_request)])
