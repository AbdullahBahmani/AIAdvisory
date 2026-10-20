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
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

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

#: Strict expiry leeway, in seconds.
#:
#: A stored credential is refused from this many seconds **before** its stated
#: expiry, never after it. This is deliberately not a grace period: it bounds
#: the damage of a server clock that runs slow (or a client clock that runs
#: fast) by exactly 30 seconds, so a credential can never be used more than
#: 30 seconds past its real expiry. The value is part of the documented
#: policy in ``docs/credential-lifecycle.md`` and is asserted by the test
#: suite; changing it is a policy change, not a refactor.
CREDENTIAL_EXPIRY_LEEWAY = timedelta(seconds=30)


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


def as_utc(value: datetime) -> datetime:
    """Interpret a stored timestamp as UTC.

    SQLite hands back naive datetimes; PostgreSQL hands back aware ones.
    """
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


#: Window in which repeated rejections of the same credential are collapsed
#: into one audit event.
REJECTION_AUDIT_WINDOW_SECONDS = 60

#: Credential id -> monotonic time of the last rejection event written.
_rejection_audited_at: dict[str, float] = {}


def _should_audit_rejection(credential_id: str) -> bool:
    """True when this rejection must be written to the audit trail."""
    global _rejection_audited_at
    now = time.monotonic()
    last = _rejection_audited_at.get(credential_id)
    if last is not None and now - last < REJECTION_AUDIT_WINDOW_SECONDS:
        return False
    if len(_rejection_audited_at) > 4096:
        cutoff = now - REJECTION_AUDIT_WINDOW_SECONDS
        _rejection_audited_at = {
            key: value for key, value in _rejection_audited_at.items() if value >= cutoff
        }
    _rejection_audited_at[credential_id] = now
    return True


def credential_is_expired(row: ApiCredentialRow, now: datetime | None = None) -> bool:
    """True when a credential must no longer be accepted.

    A credential without an expiry is only valid when the operator explicitly
    opted into an unlimited credential. A row that lost that flag (for example
    because it was edited outside the operator CLI) fails closed instead of
    becoming a standing key by accident.

    The expiry is compared with the strict leeway: the credential dies
    ``CREDENTIAL_EXPIRY_LEEWAY`` *before* its stated expiry, so a clock that
    is skewed cannot extend its life.
    """
    now = now or datetime.now(UTC)
    if row.expires_at is None:
        return not bool(row.unlimited)
    return as_utc(row.expires_at) - CREDENTIAL_EXPIRY_LEEWAY <= now


def credential_is_live(row: ApiCredentialRow, now: datetime | None = None) -> bool:
    """True when a credential may still authenticate a request."""
    now = now or datetime.now(UTC)
    if row.revoked_at is not None:
        return False
    if row.overlap_until is not None and as_utc(row.overlap_until) <= now:
        # The bounded rotation overlap is over; the credential is dead.
        return False
    return not credential_is_expired(row, now)


def _audit_rejected_credential(db: Session, row: ApiCredentialRow, reason: str) -> None:
    """Record the attempted use of a revoked or expired credential.

    The rejection itself must never depend on the audit write succeeding: a
    failure here is swallowed and the request is still refused. The event is
    written and committed immediately because the request is about to fail and
    the request-scoped session would otherwise roll the row back.

    The write is throttled per credential: somebody presenting a revoked
    credential in a loop must not be able to fill the audit table with one
    event per request. The first use of a credential is always audited; repeats
    inside the window are collapsed into that first event.
    """
    from app.services.audit import audit_tenant

    if not _should_audit_rejection(row.id):
        return
    try:
        audit_tenant(
            db,
            "credential.use_rejected",
            organization_id=row.organization_id,
            actor=row.subject,
            credential_id=row.id,
            credential_subject=row.subject,
            role=row.role,
            reason=reason,
        )
        db.commit()
    except Exception:  # pragma: no cover - auditing must not change the answer
        db.rollback()


def _credential_principal(db: Session, token: str) -> Principal:
    """Resolve a stored API credential into a verified, organization-scoped principal.

    Revoked, expired, malformed and organization-less credentials all fail closed.
    The use of a revoked or expired credential is audited.
    """
    row = db.scalar(
        select(ApiCredentialRow).where(ApiCredentialRow.token_hash == digest_token(token))
    )
    now = datetime.now(UTC)
    if row is None:
        raise HTTPException(status_code=401, detail="Invalid or revoked API credential")
    if row.revoked_at is not None:
        _audit_rejected_credential(db, row, "revoked")
        raise HTTPException(status_code=401, detail="Invalid or revoked API credential")
    if row.role not in ROLE_LEVEL:
        raise HTTPException(status_code=401, detail="Invalid or revoked API credential")
    if not credential_is_live(row, now):
        reason = "expired" if credential_is_expired(row, now) else "overlap_ended"
        _audit_rejected_credential(db, row, reason)
        raise HTTPException(status_code=401, detail="Expired API credential")
    if not row.organization_id:
        raise HTTPException(status_code=401, detail="API credential has no organization")
    return Principal(
        subject=row.subject,
        organization_id=row.organization_id,
        role=row.role,
        demo=False,
        source="api_credential",
    )


def _oidc_principal(db: Session, bearer: str, organization_id: str | None) -> Principal:
    """Resolve a validated OIDC access token through a pre-provisioned membership."""
    sub, organization, role = oidc_identity(db, bearer, organization_id)
    if role not in ROLE_LEVEL:
        raise HTTPException(status_code=403, detail="Invalid membership role")
    return Principal(
        subject=sub, organization_id=organization, role=role, demo=False, source="oidc_membership"
    )


def _bearer_principal(request: Request, db: Session, authorization: str) -> Principal:
    """Authenticate a presented bearer credential with the deployment's own rules."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Bearer credential required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    bearer = authorization[len("Bearer ") :]
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
        return Principal(SYNTHETIC_DEMO_SUBJECT, None, "admin", demo=True, source="synthetic_demo")
    return _bearer_principal(request, db, authorization)


def authenticate_request(request: Request, db: Session = Depends(get_db)) -> Principal:
    authorization = request.headers.get("authorization", "")
    if _demo_allowed(request):
        principal = _demo_principal(request, db, authorization)
    else:
        principal = _bearer_principal(request, db, authorization)
    if principal.verified:
        enforce_limit(
            db, org=principal.organization_id, principal=principal.subject, role=principal.role
        )
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
