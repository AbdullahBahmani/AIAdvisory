"""Assessment write preconditions for authenticated pilot deployments.

The client-visible assessment revision detects stale UI edits; SQLAlchemy's
independent write_version column is the transactional lost-update guard.
"""
from __future__ import annotations
import os
from fastapi import HTTPException
from sqlalchemy.orm import object_session

def assert_current_revision(assessment) -> None:
    session = object_session(assessment)
    if session is None:
        raise RuntimeError("Assessment must be attached to a database session")
    principal = session.info.get("principal")
    if principal is None or principal.demo:
        return
    required = (
        os.getenv("ADVISORY_DEPLOYMENT_MODE", "local").lower() == "pilot"
        or os.getenv("ADVISORY_REQUIRE_IF_MATCH") == "1"
    )
    if not required:
        return
    expected = session.info.get("if_match")
    if expected is None:
        raise HTTPException(status_code=428, detail="If-Match assessment revision required; reload the assessment")
    if expected != f'"{assessment.revision}"':
        raise HTTPException(status_code=412, detail="Assessment revision changed; reload before writing")
