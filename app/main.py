"""FastAPI public API. Phase 1 offers *stateless*, rules-only preview endpoints."""

from fastapi import FastAPI

from app.api.assessments import router as assessments_router
from app.api.advisor import router as advisor_router
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

APP_VERSION = "0.4.0"

app = FastAPI(
    title="AI Opportunity Advisor API",
    version=APP_VERSION,
    description="Local-only human-led assessments and optional gated AI proposals.",
)


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


@app.get("/api/v1/discovery/questions", response_model=list[Question], tags=["discovery"])
def questions() -> list[Question]:
    return list(QUESTIONS)


@app.post("/api/v1/discovery/next-question", response_model=DiscoveryResult, tags=["discovery"])
def next_question(payload: DiscoveryRequest) -> DiscoveryResult:
    return summarize_discovery(payload.answers)


@app.post("/api/v1/analysis/preview", response_model=AssessmentPreviewResponse, tags=["analysis"])
def preview_analysis(payload: AssessmentPreviewRequest) -> AssessmentPreviewResponse:
    return analyze(payload)


app.include_router(assessments_router)
app.include_router(advisor_router)
