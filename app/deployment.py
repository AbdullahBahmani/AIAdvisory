"""Fail-closed startup validation; production requires independent release approval."""
import os
from sqlalchemy.engine import make_url


def check_deployment_configuration() -> None:
    mode = os.getenv("ADVISORY_DEPLOYMENT_MODE", "local").lower()
    if mode not in ("local", "pilot", "production"):
        raise RuntimeError("Invalid ADVISORY_DEPLOYMENT_MODE")
    if mode != "local" and os.getenv("ADVISORY_DEMO_MODE") == "1":
        raise RuntimeError("Demo authentication bypass forbidden outside local mode")
    if mode == "production":
        # An environment flag is not evidence of IdP, GDPR, pen test or recovery sign-off.
        raise RuntimeError("Production deployment blocked pending independent release review")
    if mode == "pilot":
        if os.getenv("ADVISORY_OIDC_REQUIRED") != "1":
            raise RuntimeError("Pilot requires OIDC-only authentication")
        database = make_url(os.getenv("DATABASE_URL", "sqlite:///./aiadvisory.sqlite3"))
        if database.get_backend_name() != "postgresql":
            raise RuntimeError("Pilot requires PostgreSQL; SQLite is for local demos only")
        if database.query.get("sslmode") != "verify-full":
            raise RuntimeError("Pilot PostgreSQL connection requires sslmode=verify-full")
        from app.identity import _config
        _config()
