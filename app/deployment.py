"""Start-up guard to prevent accidental exposure of unreviewed local/demo mode."""
import os


def check_deployment_configuration() -> None:
    mode = os.getenv("ADVISORY_DEPLOYMENT_MODE", "local").lower()
    if mode not in ("local", "pilot", "production"):
        raise RuntimeError("Invalid ADVISORY_DEPLOYMENT_MODE")
    if mode != "local" and os.getenv("ADVISORY_DEMO_MODE") == "1":
        raise RuntimeError("Demo authentication bypass forbidden outside local mode")
    if mode == "production":
        # Infrastructure operations, independent security review and verified
        # governance cannot be certified by environment variables alone.
        raise RuntimeError("Production deployment blocked pending independent release review")
    if mode == "pilot" and os.getenv("ADVISORY_OIDC_REQUIRED") == "1":
        from app.identity import _config
        _config()
