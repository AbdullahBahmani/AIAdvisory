"""Fail-closed tenant and role authorization for scoped API-token pilot access.

An API token identifies a provisioned principal, not an SSO or human identity proof.
The explicit unauthenticated demo switch is only for loopback synthetic workflows.
"""
from __future__ import annotations

import hashlib
import os
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ApiCredentialRow
from app.db.session import get_db
from app.identity import oidc_identity
from app.throttling import enforce_limit

TOKEN_RE = re.compile(r"^Bearer (aia_[A-Za-z0-9_-]{35,})$")
ROLE_LEVEL = {"viewer": 1, "analyst": 2, "reviewer": 3, "admin": 4}


@dataclass(frozen=True)
class Principal:
    subject: str
    organization_id: str | None
    role: str
    demo: bool = False


def digest_token(token: str) -> str:
    """Hash high-entropy opaque tokens; no raw secret is stored in the database."""
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def make_token() -> str:
    return "aia_" + secrets.token_urlsafe(36)


def _demo_allowed(request: Request) -> bool:
    # Startup choice is intentionally explicit, never a fallback after bad credentials.
    # Container DNS can be used by the UI on a loopback-published Docker network;
    # do not treat Host headers or loopback checks as meaningful authentication.
    return os.getenv("ADVISORY_DEMO_MODE") == "1"


def authenticate_request(request: Request, db: Session = Depends(get_db)) -> Principal:
    if _demo_allowed(request):
        principal = Principal("local_demo", None, "admin", demo=True)
    else:
        authorization = request.headers.get("authorization", "")
        if not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Bearer credential required", headers={"WWW-Authenticate": "Bearer"})
        bearer = authorization[len("Bearer "):]
        match = TOKEN_RE.fullmatch(authorization)
        if match is not None:
            if os.getenv("ADVISORY_OIDC_REQUIRED") == "1":
                raise HTTPException(status_code=401, detail="OIDC access token required")
            row = db.scalar(select(ApiCredentialRow).where(ApiCredentialRow.token_hash == digest_token(match.group(1))))
            now = datetime.now(timezone.utc)
            if row is None or row.revoked_at is not None or row.role not in ROLE_LEVEL:
                raise HTTPException(status_code=401, detail="Invalid or revoked API credential")
            expiration = row.expires_at
            if expiration is not None and (expiration.replace(tzinfo=timezone.utc) if expiration.tzinfo is None else expiration) <= now:
                raise HTTPException(status_code=401, detail="Expired API credential")
            if not row.organization_id:
                raise HTTPException(status_code=401, detail="API credential has no organization")
            principal = Principal(subject=row.subject, organization_id=row.organization_id, role=row.role)
        else:
            # OIDC requires pinned issuer/audience/JWKS and pre-provisioned DB membership.
            if len(bearer) > 10000 or not bearer or bearer.count(".") != 2:
                raise HTTPException(status_code=401, detail="Invalid bearer credential")
            sub, organization, role = oidc_identity(db, bearer, request.headers.get("X-Organization-ID"))
            if role not in ROLE_LEVEL:
                raise HTTPException(status_code=403, detail="Invalid membership role")
            principal = Principal(subject=sub, organization_id=organization, role=role)
        enforce_limit(db, org=principal.organization_id, principal=principal.subject, role=principal.role)
    db.info["principal"] = principal
    request.state.principal = principal
    required = "viewer" if request.method in {"GET", "HEAD", "OPTIONS"} else "analyst"
    require_role(db, required)
    return principal


def current_principal(db: Session) -> Principal:
    principal = db.info.get("principal")
    if not isinstance(principal, Principal):
        raise HTTPException(status_code=401, detail="Authentication context required")
    return principal


def require_role(db: Session, role: str) -> Principal:
    principal = current_principal(db)
    if ROLE_LEVEL.get(principal.role, 0) < ROLE_LEVEL[role]:
        raise HTTPException(status_code=403, detail="Insufficient organization role")
    return principal


def require_reviewer_or_local_demo(db: Session, token: str | None) -> Principal:
    principal = current_principal(db)
    if principal.demo:
        configured = os.getenv("ADVISORY_REVIEW_TOKEN", "")
        if len(configured) < 16 or not token or not secrets.compare_digest(configured, token):
            raise HTTPException(status_code=403, detail="Local demo reviewer token required")
        return principal
    return require_role(db, "reviewer")
