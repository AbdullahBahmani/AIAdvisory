"""Offline release-gate regression tests; no IdP, provider or GitHub runner needed."""

import sqlite3
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AssessmentRow, OidcMembershipRow, OrganizationRow, RequestLimitRow
from app.deployment import check_deployment_configuration
from app.identity import oidc_identity
from app.throttling import enforce_limit
from scripts.maintenance import backup_sqlite, retention_candidates
from tests.backend import BACKEND, POSTGRES


@pytest.fixture
def session(db_engine):
    with Session(db_engine) as db:
        yield db


def test_oidc_is_disabled_if_unconfigured(session, monkeypatch):
    for key in ("ADVISORY_OIDC_ISSUER", "ADVISORY_OIDC_AUDIENCE", "ADVISORY_OIDC_JWKS_URL"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(HTTPException) as caught:
        oidc_identity(session, "something", None)
    assert caught.value.status_code == 401


def test_signed_oidc_requires_preprovisioned_membership_and_correct_audience(session, monkeypatch):
    import app.identity as identity

    issuer = "https://login.example.test/realm"
    monkeypatch.setenv("ADVISORY_OIDC_ISSUER", issuer)
    monkeypatch.setenv("ADVISORY_OIDC_AUDIENCE", "aiadvisory-api")
    monkeypatch.setenv("ADVISORY_OIDC_JWKS_URL", "https://login.example.test/.well-known/jwks.json")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(
        identity,
        "_jwks_client",
        lambda _: SimpleNamespace(
            get_signing_key_from_jwt=lambda _: SimpleNamespace(key=key.public_key())
        ),
    )
    now = datetime.now(UTC)

    def signed(**overrides):
        claims = dict(
            sub="alice",
            iss=issuer,
            aud="aiadvisory-api",
            iat=now,
            exp=now + timedelta(minutes=5),
            role="admin",
        )
        claims.update(overrides)
        return jwt.encode(claims, key, algorithm="RS256")

    with pytest.raises(HTTPException) as none:
        oidc_identity(session, signed(), None)
    assert none.value.status_code == 403
    org = OrganizationRow(name="Tenant A")
    session.add(org)
    session.flush()
    record = OidcMembershipRow(
        issuer=issuer, subject="alice", organization_id=org.id, role="viewer"
    )
    session.add(record)
    session.commit()
    assert oidc_identity(session, signed(), None) == ("alice", org.id, "viewer")
    # Never use JWT's claimed role to grant admin; DB membership remains viewer.
    for token in [
        signed(aud="other"),
        signed(iss="https://evil.test"),
        signed(exp=now - timedelta(minutes=3)),
    ]:
        with pytest.raises(HTTPException) as e:
            oidc_identity(session, token, None)
        assert e.value.status_code == 401
    record.active = False
    session.commit()
    with pytest.raises(HTTPException) as disabled:
        oidc_identity(session, signed(), None)
    assert disabled.value.status_code == 403


def test_ambiguous_oidc_memberships_require_selected_org(session, monkeypatch):
    import app.identity as identity

    monkeypatch.setenv("ADVISORY_OIDC_ISSUER", "https://login.example.test")
    monkeypatch.setenv("ADVISORY_OIDC_AUDIENCE", "advisor")
    monkeypatch.setenv("ADVISORY_OIDC_JWKS_URL", "https://login.example.test/keys")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(
        identity,
        "_jwks_client",
        lambda _: SimpleNamespace(
            get_signing_key_from_jwt=lambda _: SimpleNamespace(key=key.public_key())
        ),
    )
    orgs = [OrganizationRow(name=x) for x in ("A", "B")]
    session.add_all(orgs)
    session.flush()
    session.add_all(
        [
            OidcMembershipRow(
                issuer="https://login.example.test",
                subject="alice",
                organization_id=o.id,
                role="analyst",
            )
            for o in orgs
        ]
    )
    session.commit()
    now = datetime.now(UTC)
    token = jwt.encode(
        dict(
            iss="https://login.example.test",
            aud="advisor",
            sub="alice",
            iat=now,
            exp=now + timedelta(minutes=10),
        ),
        key,
        algorithm="RS256",
    )
    with pytest.raises(HTTPException) as e:
        oidc_identity(session, token, None)
    assert e.value.status_code == 403
    assert oidc_identity(session, token, orgs[1].id)[1] == orgs[1].id


def test_atomic_database_rate_limit_and_isolation(session, monkeypatch):
    monkeypatch.setenv("ADVISORY_REQUESTS_PER_MINUTE", "2")
    enforce_limit(session, org="org-1", principal="alice", role="analyst")
    enforce_limit(session, org="org-1", principal="alice", role="analyst")
    with pytest.raises(HTTPException) as e:
        enforce_limit(session, org="org-1", principal="alice", role="analyst")
    assert e.value.status_code == 429 and "Retry-After" in e.value.headers
    enforce_limit(session, org="org-2", principal="alice", role="analyst")
    enforce_limit(session, org="org-1", principal="bob", role="analyst")
    assert session.scalar(select(RequestLimitRow.requests).limit(1)) in (1, 3)
    monkeypatch.setenv("ADVISORY_REQUESTS_PER_MINUTE", "zero")
    with pytest.raises(HTTPException) as bad:
        enforce_limit(session, org="org", principal="subject", role="admin")
    assert bad.value.status_code == 503


@pytest.mark.skipif(
    BACKEND == POSTGRES,
    reason=(
        "SQLite-specific: the offline backup uses the sqlite3 online-backup API, "
        "which does not exist on PostgreSQL (PostgreSQL operators use pg_dump)"
    ),
)
def test_offline_backup_integrity_and_no_overwrites(tmp_path, monkeypatch):
    import scripts.maintenance as maint

    source = tmp_path / "original.sqlite3"
    with sqlite3.connect(source) as db:
        db.execute("CREATE TABLE items(value TEXT)")
        db.execute("INSERT INTO items VALUES (?)", ("sample",))
    monkeypatch.setattr(maint, "DATABASE_URL", f"sqlite:///{source}")
    target = tmp_path / "backup.sqlite3"
    assert backup_sqlite(target) == target
    assert target.stat().st_mode & 0o077 == 0
    with sqlite3.connect(target) as conn:
        assert conn.execute("SELECT value FROM items").fetchone()[0] == "sample"
    with pytest.raises(ValueError):
        backup_sqlite(target)
    with pytest.raises(ValueError):
        backup_sqlite(source)


def test_retention_only_selects_explicit_tenant_deleted_old_rows(session):
    tenants = [OrganizationRow(name="A"), OrganizationRow(name="B")]
    session.add_all(tenants)
    session.flush()
    a = AssessmentRow(
        title="old A",
        organization_id=tenants[0].id,
        status="deleted",
        updated_at=datetime(2020, 1, 1),
    )
    b = AssessmentRow(
        title="B", organization_id=tenants[1].id, status="deleted", updated_at=datetime(2020, 1, 1)
    )
    c = AssessmentRow(
        title="active A",
        organization_id=tenants[0].id,
        status="draft",
        updated_at=datetime(2020, 1, 1),
    )
    session.add_all([a, b, c])
    session.flush()
    matches = retention_candidates(session, tenants[0].id, datetime(2021, 1, 1, tzinfo=UTC))
    assert [r.title for r in matches] == ["old A"]


def test_production_never_boots_and_pilot_demo_bypass_fails(monkeypatch):
    monkeypatch.setenv("ADVISORY_DEPLOYMENT_MODE", "production")
    monkeypatch.setenv("ADVISORY_DEMO_MODE", "1")
    with pytest.raises(RuntimeError, match="Demo authentication bypass"):
        check_deployment_configuration()
    monkeypatch.delenv("ADVISORY_DEMO_MODE")
    with pytest.raises(RuntimeError, match="Production deployment blocked"):
        check_deployment_configuration()
    monkeypatch.setenv("ADVISORY_DEPLOYMENT_MODE", "local")
    check_deployment_configuration()


def test_authenticated_route_is_rate_limited_cross_process_style(monkeypatch, db_engine):
    """One shared SQL bucket is enforced at the FastAPI authorization boundary."""
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session

    from app.db.models import ApiCredentialRow
    from app.db.session import get_db
    from app.main import app
    from app.security import digest_token, make_token

    engine = db_engine
    with Session(engine) as db:
        org = OrganizationRow(name="HTTP org")
        db.add(org)
        db.flush()
        secret = make_token()
        db.add(
            ApiCredentialRow(
                organization_id=org.id,
                subject="user",
                role="viewer",
                token_hash=digest_token(secret),
                expires_at=datetime.now(UTC) + timedelta(days=1),
            )
        )
        db.commit()

    def override_db():
        with Session(engine) as db:
            yield db

    monkeypatch.delenv("ADVISORY_DEMO_MODE", raising=False)
    monkeypatch.setenv("ADVISORY_REQUESTS_PER_MINUTE", "1")
    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            headers = {"Authorization": f"Bearer {secret}"}
            assert client.get("/api/v1/assessments", headers=headers).status_code == 200
            throttled = client.get("/api/v1/assessments", headers=headers)
            assert throttled.status_code == 429 and "Retry-After" in throttled.headers
            assert client.get("/health").status_code == 200
    finally:
        app.dependency_overrides.clear()
