"""Fail-closed tenant and role authorization for scoped API-token pilot access.

An API token identifies a provisioned principal, not an SSO or human identity proof.
The explicit unauthenticated demo switch is only for loopback synthetic workflows.

Local/demo deployments authenticate exactly like the production path: a presented
credential is resolved from the stored credential table into a server-verified
subject, organization and role. Only the *absence* of any credential falls back to
the synthetic `local_demo` identity, which is never verified and is always marked
as such in the audit trail.
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
SYNTHETIC_DEMO_SUBJECT = "local_demo"


@dataclass(frozen=True)
class Principal:
    """The actor of one request.

    `demo` marks the explicitly synthetic local-demo identity: it carries no
    organization, is never verified and can never be used as an identity proof.
    Every other principal is resolved by the server from a stored credential or a
    pre-provisioned membership, so subject, organization and role are verified.
    """

    subject: str
    organization_id: str | None
    role: str
    demo: bool = False
    source: str = "api_credential"

    @property
    def verified(self) -> bool:
        """True only when the server itself resolved this identity."""
        return not self.demo


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


def _credential_principal(db: Session, token: str) -> Principal:
    """Resolve a stored API credential into a verified, organization-scoped principal.

    Revoked, expired, malformed and organization-less credentials all fail closed.
    """
    row = db.scalar(select(ApiCredentialRow).where(ApiCredentialRow.token_hash == digest_token(token)))
    now = datetime.now(timezone.utc)
    if row is None or row.revoked_at is not None or row.role not in ROLE_LEVEL:
        raise HTTPException(status_code=401, detail="Invalid or revoked API credential")
    expiration = row.expires_at
    if expiration is not None and (expiration.replace(tzinfo=timezone.utc) if expiration.tzinfo is None else expiration) <= now:
        raise HTTPException(status_code=401, detail="Expired API credential")
    if not row.organization_id:
        raise HTTPException(status_code=401, detail="API credential has no organization")
    return Principal(subject=row.subject, organization_id=row.organization_id,
                     role=row.role, demo=False, source="api_credential")


def _oidc_principal(db: Session, bearer: str, organization_id: str | None) -> Principal:
    """Resolve a validated OIDC access token through a pre-provisioned membership."""
    sub, organization, role = oidc_identity(db, bearer, organization_id)
    if role not in ROLE_LEVEL:
        raise HTTPException(status_code=403, detail="Invalid membership role")
    return Principal(subject=sub, organization_id=organization, role=role,
                     demo=False, source="oidc_membership")


def _bearer_principal(request: Request, db: Session, authorization: str) -> Principal:
    """Authenticate a presented bearer credential with the deployment's own rules."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Bearer credential required", headers={"WWW-Authenticate": "Bearer"})
    bearer = authorization[len("Bearer "):]
    match = TOKEN_RE.fullmatch(authorization)
    if match is not None:
        if os.getenv("ADVISORY_OIDC_REQUIRED") == "1":
            raise HTTPException(status_code=401, detail="OIDC access token required")
        return _credential_principal(db, match.group(1))
    # OIDC requires pinned issuer/audience/JWKS and pre-provisioned DB membership.
    if len(bearer) > 10000 or not bearer or bearer.count(".") != 2:
        raise HTTPException(status_code=401, detail="Invalid bearer credential")
    return _oidc_principal(db, bearer, request.headers.get("X-Organization-ID"))


def _demo_principal(request: Request, db: Session, authorization: str) -> Principal:
    """Demo mode still authenticates a presented credential exactly like production.

    Without any credential the request runs as the synthetic local-demo identity.
    That identity has no organization, is never verified, and every record it
    produces stays marked `identity_unverified`.
    """
    if not authorization.strip():
        return Principal(SYNTHETIC_DEMO_SUBJECT, None, "admin", demo=True,
                         source="synthetic_demo")
    return _bearer_principal(request, db, authorization)


def authenticate_request(request: Request, db: Session = Depends(get_db)) -> Principal:
    authorization = request.headers.get("authorization", "")
    if _demo_allowed(request):
        principal = _demo_principal(request, db, authorization)
    else:
        principal = _bearer_principal(request, db, authorization)
    if principal.verified:
        enforce_limit(db, org=principal.organization_id, principal=principal.subject, role=principal.role)
    db.info["if_match"] = request.headers.get("if-match")
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
    """Reviewer gate for finalization, reopen, soft delete and reviewer-guarded AI actions.

    A verified principal (stored credential or pre-provisioned membership) must
    hold the reviewer role. The synthetic demo identity has no server-verified
    identity, so it keeps the explicitly shared local reviewer token and stays
    permanently unverified.
    """
    principal = current_principal(db)
    if principal.verified:
        return require_role(db, "reviewer")
    configured = os.getenv("ADVISORY_REVIEW_TOKEN", "")
    if len(configured) < 16 or not token or not secrets.compare_digest(configured, token):
        raise HTTPException(status_code=403, detail="Local demo reviewer token required")
    return principal
