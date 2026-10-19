"""Safe request logging: never include tokens, bodies, query strings, or response data."""

import json
import logging
import time
from uuid import uuid4

from fastapi import Request, Response

logger = logging.getLogger("aiadvisory.requests")
if not logger.handlers:
    logger.addHandler(logging.StreamHandler())
logger.setLevel(logging.INFO)


async def instrument_request(request: Request, call_next):
    started = time.perf_counter()
    identifier = str(uuid4())
    response: Response = await call_next(request)
    response.headers["X-Request-ID"] = identifier
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Frame-Options"] = "DENY"
    route = request.scope.get("route")
    template = getattr(route, "path", "unknown")
    logger.info(
        json.dumps(
            {
                "event": "http.request",
                "request_id": identifier,
                "method": request.method,
                "route": template,
                "status": response.status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            }
        )
    )
    return response
