"""Phase 3 optional AI proposal routes: no tools, no task modifications, no approvals."""

import os
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.ai.provider import AIProvider, get_ai_provider
from app.ai.schemas import AIRunRecord, AIRequest, Feature
from app.ai.service import list_proposals, run_proposal
from app.api.assessments import get_or_404
from app.db.models import AssessmentRow
from app.db.session import get_db

router = APIRouter(prefix="/api/v1/assessments", tags=["ai-advisory"])


@router.get("/{assessment_id}/ai-runs", response_model=list[AIRunRecord])
def get_proposals(assessment_id: str, db: Session = Depends(get_db)):
    return list_proposals(get_or_404(db, AssessmentRow, assessment_id))


def require_ai_access(token: str | None) -> None:
    """Developer-only spend guard, not user authentication or tenant authorization."""
    configured = os.getenv("ADVISORY_AI_ACCESS_TOKEN", "")
    if len(configured) < 16 or token is None or not secrets.compare_digest(token, configured):
        raise HTTPException(status_code=403, detail="Local AI access token required")


@router.post("/{assessment_id}/ai/{feature}", response_model=AIRunRecord, status_code=201)
def generate_proposal(
    assessment_id: str, feature: Feature, payload: AIRequest,
    db: Session = Depends(get_db), provider: AIProvider = Depends(get_ai_provider),
    x_ai_access_token: str | None = Header(default=None),
):
    require_ai_access(x_ai_access_token)
    return run_proposal(db, get_or_404(db, AssessmentRow, assessment_id), feature, payload, provider)
