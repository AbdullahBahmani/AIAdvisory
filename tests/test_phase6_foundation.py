"""Phase 6 offline regression tests; not a certification of live infrastructure."""
from __future__ import annotations
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.ai.data_guard import assert_no_obvious_sensitive_data
from app.concurrency import assert_current_revision
from app.db.models import AssessmentRow, Base
from app.deployment import check_deployment_configuration
from app.security import Principal


def test_stale_concurrent_edition_is_rejected(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'locking.sqlite3'}")
    Base.metadata.create_all(engine)
    with Session(engine) as setup:
        row = AssessmentRow(title="Initial")
        setup.add(row)
        setup.commit()
        aid = row.id
    with Session(engine) as one, Session(engine) as two:
        first = one.get(AssessmentRow, aid)
        stale = two.get(AssessmentRow, aid)
        first.title = "First writer"
        one.commit()
        stale.title = "Stale overwrite"
        with pytest.raises(StaleDataError):
            two.commit()
        two.rollback()
    with Session(engine) as check:
        assert check.get(AssessmentRow, aid).title == "First writer"
    engine.dispose()


def test_pilot_revision_precondition_returns_428_and_412(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("ADVISORY_DEPLOYMENT_MODE", "pilot")
    engine = create_engine(f"sqlite:///{tmp_path / 'revision.sqlite3'}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.info["principal"] = Principal("reviewer", "tenant-a", "reviewer")
        row = AssessmentRow(title="Draft", revision=7)
        db.add(row)
        db.flush()
        with pytest.raises(HTTPException) as absent:
            assert_current_revision(row)
        assert absent.value.status_code == 428
        db.info["if_match"] = '"6"'
        with pytest.raises(HTTPException) as stale:
            assert_current_revision(row)
        assert stale.value.status_code == 412
        db.info["if_match"] = '"7"'
        assert_current_revision(row)
        db.rollback()
    engine.dispose()


@pytest.mark.parametrize("content", [
    {"title": "Jane Doe jane@example.com"},
    {"input": "api_key=examplelongsecretvalue"},
    {"input": "-----BEGIN PRIVATE KEY-----"},
])
def test_obvious_sensitive_data_fails_closed(content):
    with pytest.raises(HTTPException) as exc:
        assert_no_obvious_sensitive_data(content)
    assert exc.value.status_code == 403
    assert "examplelongsecretvalue" not in exc.value.detail


def test_benign_context_passes_extra_guard():
    assert_no_obvious_sensitive_data({"title": "Classify fictional support requests", "tasks": ["no external actions"]})


def test_pilot_requires_oidc_postgres_and_verified_tls(monkeypatch):
    monkeypatch.delenv("ADVISORY_DEMO_MODE", raising=False)
    monkeypatch.setenv("ADVISORY_DEPLOYMENT_MODE", "pilot")
    monkeypatch.delenv("ADVISORY_OIDC_REQUIRED", raising=False)
    with pytest.raises(RuntimeError, match="OIDC-only"):
        check_deployment_configuration()
    monkeypatch.setenv("ADVISORY_OIDC_REQUIRED", "1")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    with pytest.raises(RuntimeError, match="PostgreSQL"):
        check_deployment_configuration()
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:pass@db.local/aiadvisory")
    with pytest.raises(RuntimeError, match="sslmode"):
        check_deployment_configuration()
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:pass@db.local/aiadvisory?sslmode=verify-full")
    monkeypatch.setenv("ADVISORY_OIDC_ISSUER", "https://idp.example.test/realms/app")
    monkeypatch.setenv("ADVISORY_OIDC_AUDIENCE", "aiadvisory-api")
    monkeypatch.setenv("ADVISORY_OIDC_JWKS_URL", "https://idp.example.test/keys")
    check_deployment_configuration()


def test_production_remains_blocked_even_with_pilot_configuration(monkeypatch):
    monkeypatch.delenv("ADVISORY_DEMO_MODE", raising=False)
    monkeypatch.setenv("ADVISORY_DEPLOYMENT_MODE", "production")
    with pytest.raises(RuntimeError, match="Production deployment blocked"):
        check_deployment_configuration()


def test_database_readiness_is_not_a_release_approval(tmp_path: Path):
    from fastapi.testclient import TestClient
    from sqlalchemy import text
    from app.main import app
    from app.db.session import get_db

    engine = create_engine(f"sqlite:///{tmp_path / 'readiness.sqlite3'}",
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)

    def override_db():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            assert client.get("/ready").status_code == 503
            with engine.begin() as connection:
                connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
                connection.execute(text("INSERT INTO alembic_version (version_num) VALUES ('0007_collaboration')"))
            assert client.get("/ready").json() == {"status": "ready"}
    finally:
        app.dependency_overrides.clear()
        engine.dispose()
