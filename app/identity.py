"""OIDC identity validation for deployments with a configured identity provider.

Only credentials from the pinned OIDC issuer + audience can authenticate. A valid
JWT has NO tenant privileges until a local operator provisions an active membership.
No network calls are made when OIDC is unconfigured (rules-only demo still works).
"""

from __future__ import annotations

import os
from functools import lru_cache
from urllib.parse import urlparse

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import OidcMembershipRow


def _config() -> tuple[str, str, str]:
    issuer = os.getenv("ADVISORY_OIDC_ISSUER", "").rstrip("/")
    audience = os.getenv("ADVISORY_OIDC_AUDIENCE", "")
    jwks_url = os.getenv("ADVISORY_OIDC_JWKS_URL", "")
    if not issuer or not audience or not jwks_url:
        raise HTTPException(status_code=401, detail="OIDC sign-in not configured")
    a, b = urlparse(issuer), urlparse(jwks_url)
    if (
        a.scheme != "https"
        or b.scheme != "https"
        or not a.hostname
        or a.hostname != b.hostname
        or a.username
        or b.username
        or a.password
        or b.password
        or a.fragment
        or b.fragment
    ):
        raise HTTPException(status_code=503, detail="Unsafe OIDC configuration")
    return issuer, audience, jwks_url


@lru_cache(maxsize=8)
def _jwks_client(url: str):
    from jwt import PyJWKClient

    return PyJWKClient(url, cache_jwk_set=True, lifespan=300, timeout=3)


def oidc_identity(db: Session, token: str, organization_id: str | None):
    """Validate RS256 + issuer/audience/expiry, then map to a server-side membership.

    JWT role or organization claims are deliberately ignored. Authentication must
    not create users or organizations on demand; membership is pre-provisioned.
    """
    import jwt

    issuer, audience, jwks_url = _config()
    try:
        key = _jwks_client(jwks_url).get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token,
            key=key,
            algorithms=["RS256"],
            audience=audience,
            issuer=issuer,
            options={"require": ["exp", "iat", "sub", "iss", "aud"]},
            leeway=30,
        )
    except (jwt.PyJWTError, ValueError, OSError, TimeoutError):
        raise HTTPException(status_code=401, detail="Invalid OIDC access token") from None
    sub = claims.get("sub")
    if not isinstance(sub, str) or not 1 <= len(sub) <= 200:
        raise HTTPException(status_code=401, detail="Invalid OIDC subject")
    records = db.scalars(
        select(OidcMembershipRow).where(
            OidcMembershipRow.issuer == issuer,
            OidcMembershipRow.subject == sub,
            OidcMembershipRow.active.is_(True),
        )
    ).all()
    if organization_id:
        records = [m for m in records if m.organization_id == organization_id]
    if len(records) != 1:
        # An identity with memberships in two orgs must explicitly choose one.
        raise HTTPException(status_code=403, detail="No unique active organization membership")
    return sub, records[0].organization_id, records[0].role
