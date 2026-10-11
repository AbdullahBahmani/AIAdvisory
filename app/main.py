"""Minimal API entrypoint for the AI Opportunity Advisor foundation."""

from fastapi import FastAPI
from pydantic import BaseModel

APP_VERSION = "0.1.0"

app = FastAPI(
    title="AI Opportunity Advisor API",
    version=APP_VERSION,
    description="Human-led AI opportunity decision support. Phase 0: foundation only.",
)


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    mode: str


@app.get("/health", response_model=HealthResponse, tags=["health"])
def health() -> HealthResponse:
    """Return a stable, side-effect-free readiness response."""
    return HealthResponse(
        status="ok",
        service="ai-opportunity-advisor-api",
        version=APP_VERSION,
        mode="rules-only",
    )
