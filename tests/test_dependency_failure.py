"""The dependency-failure behaviour the recovery drill exposed.

The drill found three defects that only appear when the database misbehaves:

1. A stopped database made route handlers answer **500**, while the readiness probe and the
   rate limiter correctly answered 503. A 500 says "the application is broken"; a 503 says
   "the dependency is unavailable and the request may be retried".
2. An unhandled exception **bypassed the request middleware entirely**, so the reply carried
   no ``X-Request-ID``, no ``nosniff`` and no ``Cache-Control``, and no ``http.request`` line
   was written to the log.
3. There was **no connect or statement timeout**, so a silently partitioned database (packets
   accepted, never answered) wedged the request forever.

These tests pin the fixed behaviour. They do not need a real partitioned database: the
database error is injected, which is the part the application is responsible for.
"""

from __future__ import annotations

import logging

import pytest
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.db.session import get_db
from app.main import app
from app.telemetry import instrument_request
from tests.test_security import secure_client  # noqa: F401 - shared pytest fixture


@pytest.fixture
def failing_db(monkeypatch):
    """Make every database call raise, the way a stopped database does."""

    def override_db():
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    app.dependency_overrides[get_db] = override_db
    yield
    app.dependency_overrides.clear()


def test_a_stopped_database_answers_503_not_500(failing_db):
    """A dependency outage must not look like an application defect."""
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/v1/assessments")
    assert response.status_code == 503, response.text
    assert "unavailable" in response.json()["detail"].lower()
    assert response.headers.get("Retry-After") == "5"


def test_the_503_response_still_carries_the_security_headers(failing_db):
    """The middleware must decorate the failure response too."""
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/v1/assessments")
    assert response.headers["X-Request-ID"]
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Frame-Options"] == "DENY"


def test_an_unhandled_exception_still_produces_a_logged_request(failing_db, caplog):
    """Every request leaves a trace, including one that ends in a failure."""
    with caplog.at_level(logging.INFO, logger="aiadvisory.requests"):
        with TestClient(app, raise_server_exceptions=False) as client:
            client.get("/api/v1/assessments")
    events = [
        record.getMessage() for record in caplog.records if "http.request" in record.getMessage()
    ]
    assert events, "an unhandled exception must still write an http.request log line"


def test_a_route_that_raises_still_gets_the_security_headers(secure_client, monkeypatch):
    """A handler that raises must not strip the standard response headers."""
    client, provision, _ = secure_client
    headers, _, _ = provision("Header Org", "alice", "analyst")

    @app.get("/api/v1/_test_boom")
    def _boom():  # pragma: no cover - the point is that it raises
        raise RuntimeError("boom")

    with TestClient(app, raise_server_exceptions=False) as test_client:
        response = test_client.get("/api/v1/_test_boom", headers=headers)
    assert response.status_code == 500
    assert response.headers["X-Request-ID"]
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Cache-Control"] == "no-store"
    assert "boom" not in response.text, "the exception detail must not reach the client"


def test_the_timeouts_are_configurable_and_bounded():
    """The timeouts are read from the environment and clamped to sane bounds."""
    from app.db import session as session_module

    assert 1 <= session_module.CONNECT_TIMEOUT <= 120
    assert 1 <= session_module.STATEMENT_TIMEOUT <= 600
    assert 1 <= session_module.POOL_TIMEOUT <= 120


def test_an_out_of_range_timeout_falls_back_to_the_default(monkeypatch):
    from app.db.session import _seconds

    monkeypatch.setenv("ADVISORY_TEST_TIMEOUT", "not-a-number")
    assert _seconds("ADVISORY_TEST_TIMEOUT", 7, minimum=1, maximum=60) == 7
    monkeypatch.setenv("ADVISORY_TEST_TIMEOUT", "9999")
    assert _seconds("ADVISORY_TEST_TIMEOUT", 7, minimum=1, maximum=60) == 60
    monkeypatch.setenv("ADVISORY_TEST_TIMEOUT", "0")
    assert _seconds("ADVISORY_TEST_TIMEOUT", 7, minimum=1, maximum=60) == 1


def test_the_middleware_sets_the_headers_on_a_successful_response():
    """The middleware's contract, tested directly rather than through introspection."""
    import asyncio

    from starlette.requests import Request

    async def _call_next(_request):
        return JSONResponse(status_code=200, content={"ok": True})

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/v1/assessments",
        "raw_path": b"/api/v1/assessments",
        "root_path": "",
        "scheme": "http",
        "query_string": b"",
        "headers": [],
        "app": app,
    }
    request = Request(scope)
    response = asyncio.run(instrument_request(request, _call_next))
    assert response.headers["X-Request-ID"]
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Frame-Options"] == "DENY"


def test_the_middleware_sets_the_headers_when_the_handler_raises():
    """The defect: an exception skipped the header assignment entirely."""
    import asyncio

    from starlette.requests import Request

    async def _call_next(_request):
        raise RuntimeError("boom")

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/v1/assessments",
        "raw_path": b"/api/v1/assessments",
        "root_path": "",
        "scheme": "http",
        "query_string": b"",
        "headers": [],
        "app": app,
    }
    request = Request(scope)
    response = asyncio.run(instrument_request(request, _call_next))
    assert response.status_code == 500
    assert response.headers["X-Request-ID"]
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Cache-Control"] == "no-store"
    assert "boom" not in response.body.decode()
