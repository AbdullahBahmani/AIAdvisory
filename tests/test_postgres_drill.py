"""PostgreSQL drill verification for planner item P5-03 (local, disposable instance).

These tests exercise the PostgreSQL code path that the default SQLite suite
cannot reach: the Alembic chain on a real PostgreSQL catalogue, the optimistic
assessment row version, the ``SELECT ... FOR UPDATE`` row lock used by the
workspace/portfolio routes, the ``If-Match`` preconditions (428/412/409) and the
atomic ``INSERT ... ON CONFLICT DO UPDATE`` rate limiter under parallel writers.

The whole module is skipped unless a PostgreSQL URL is configured, so the
default ``pytest -q`` run on SQLite is unchanged:

    ADVISORY_PG_DRILL_URL=postgresql+psycopg://user:pass@127.0.0.1:15432/db \\
        python -m pytest -q tests/test_postgres_drill.py

or simply point ``DATABASE_URL`` at PostgreSQL. The drill uses its own
``aiadvisory_pg_drill`` schema inside the target database and drops it again
when the session ends, so it never mutates the application schema.

A pass here is a *local* drill result, not a production PostgreSQL validation.
"""
from __future__ import annotations

import os
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pytest
from sqlalchemy import create_engine, event, inspect, select, text
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

REPO_ROOT = Path(__file__).resolve().parents[1]
DRILL_SCHEMA = "aiadvisory_pg_drill"
DOWNGRADE_SCHEMA = "aiadvisory_pg_drill_downgrade"
HEAD_REVISION = "0008_portfolio"


def _configured_pg_url() -> str | None:
    url = os.getenv("ADVISORY_PG_DRILL_URL", "").strip()
    if not url:
        candidate = os.getenv("DATABASE_URL", "").strip()
        url = candidate if candidate.startswith("postgresql") else ""
    return url or None


PG_URL = _configured_pg_url()

pytestmark = pytest.mark.skipif(
    PG_URL is None,
    reason=(
        "PostgreSQL drill skipped: set ADVISORY_PG_DRILL_URL (or DATABASE_URL) to a "
        "postgresql+psycopg:// URL to run the P5-03 PostgreSQL drill"
    ),
)


def _search_path_url(url: str, schema: str) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query))
    query["options"] = f"-csearch_path={schema}"
    return urlunsplit(parts._replace(query=urlencode(query)))


def _alembic_config():
    from alembic.config import Config

    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    return config


def _run_alembic(url: str, action: str, revision: str) -> None:
    """Run one Alembic command against ``url`` (schema-qualified)."""
    from alembic import command

    import app.db.session as session_module

    original = session_module.DATABASE_URL
    session_module.DATABASE_URL = url
    try:
        config = _alembic_config()
        if action == "upgrade":
            command.upgrade(config, revision)
        elif action == "downgrade":
            command.downgrade(config, revision)
        elif action == "check":
            command.check(config)
        else:  # pragma: no cover - programming error
            raise ValueError(f"unknown alembic action {action}")
    finally:
        session_module.DATABASE_URL = original


@pytest.fixture(scope="session")
def pg_base_engine():
    engine = create_engine(PG_URL, pool_pre_ping=True)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(scope="session")
def pg_drill(pg_base_engine):
    """A migrated, isolated PostgreSQL schema holding the application tables."""
    with pg_base_engine.begin() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{DRILL_SCHEMA}" CASCADE'))
        conn.execute(text(f'CREATE SCHEMA "{DRILL_SCHEMA}"'))
    url = _search_path_url(PG_URL, DRILL_SCHEMA)
    _run_alembic(url, "upgrade", "head")
    engine = create_engine(url, pool_pre_ping=True)
    try:
        yield SimpleNamespace(url=url, engine=engine, schema=DRILL_SCHEMA)
    finally:
        engine.dispose()
        with pg_base_engine.begin() as conn:
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{DRILL_SCHEMA}" CASCADE'))


@pytest.fixture
def api_client(pg_drill, monkeypatch):
    """TestClient bound to the drill PostgreSQL schema with demo auth disabled."""
    from fastapi.testclient import TestClient

    from app.db.session import get_db
    from app.main import app

    engine = pg_drill.engine

    def override_db():
        with Session(engine) as session:
            yield session

    monkeypatch.setenv("ADVISORY_DEMO_MODE", "0")
    monkeypatch.setenv("ADVISORY_REQUIRE_IF_MATCH", "1")
    monkeypatch.setenv("ADVISORY_REQUESTS_PER_MINUTE", "10000")
    monkeypatch.setenv("ADVISORY_DEPLOYMENT_MODE", "local")
    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def api_principal(pg_drill):
    """Provision a throwaway organization plus analyst API credential."""
    from app.db.models import ApiCredentialRow, OrganizationRow
    from app.security import digest_token, make_token

    token = make_token()
    with Session(pg_drill.engine) as db:
        organization = OrganizationRow(name=f"drill-org-{token[-10:]}")
        db.add(organization)
        db.flush()
        db.add(
            ApiCredentialRow(
                organization_id=organization.id,
                subject="drill-analyst",
                role="analyst",
                token_hash=digest_token(token),
                expires_at=datetime.now(UTC) + timedelta(days=1),
            )
        )
        db.commit()
        organization_id = organization.id
    return SimpleNamespace(
        token=token,
        organization_id=organization_id,
        headers={"Authorization": f"Bearer {token}"},
    )


def _create_assessment(client, headers, title="Drill assessment"):
    response = client.post("/api/v1/assessments", json={"title": title}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


# --------------------------------------------------------------------------
# 1. Migration chain
# --------------------------------------------------------------------------


def test_migration_chain_applies_to_a_fresh_postgres_schema(pg_drill):
    """The whole 0001..0008 chain applies to an empty PostgreSQL database."""
    with pg_drill.engine.connect() as conn:
        version = conn.execute(text("select version_num from alembic_version")).scalar_one()
    assert version == HEAD_REVISION

    inspector = inspect(pg_drill.engine)
    tables = set(inspector.get_table_names(schema=pg_drill.schema))
    expected = {
        "assessments", "workflows", "workflow_steps", "tasks", "answers", "reviews",
        "audit_events", "ai_runs", "organizations", "api_credentials",
        "oidc_memberships", "request_limits", "reviewer_assignments",
        "assessment_comments", "approval_votes", "inbox_notifications",
        "opportunity_profiles", "opportunity_evidence", "value_scenarios",
        "poc_plans", "poc_observations",
    }
    assert expected <= tables


def test_alembic_check_reports_no_drift_on_postgres(pg_drill):
    """`alembic check` is clean against the migrated PostgreSQL catalogue."""
    from app.db.models import Base

    _run_alembic(pg_drill.url, "check", "head")
    inspector = inspect(pg_drill.engine)
    model_tables = set(Base.metadata.tables)
    catalogue_tables = set(inspector.get_table_names(schema=pg_drill.schema)) - {"alembic_version"}
    assert catalogue_tables == model_tables


def test_migration_chain_downgrades_to_base_and_re_upgrades(pg_drill, pg_base_engine):
    """`alembic downgrade base` works on PostgreSQL (0004 drops a FK column)."""
    with pg_base_engine.begin() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{DOWNGRADE_SCHEMA}" CASCADE'))
        conn.execute(text(f'CREATE SCHEMA "{DOWNGRADE_SCHEMA}"'))
    url = _search_path_url(PG_URL, DOWNGRADE_SCHEMA)
    try:
        _run_alembic(url, "upgrade", "head")
        _run_alembic(url, "downgrade", "base")
        remaining = set(inspect(pg_drill.engine).get_table_names(schema=DOWNGRADE_SCHEMA))
        assert remaining == {"alembic_version"}, remaining
        _run_alembic(url, "upgrade", "head")
        tables = set(inspect(pg_drill.engine).get_table_names(schema=DOWNGRADE_SCHEMA))
        assert "assessments" in tables and "poc_observations" in tables
    finally:
        with pg_base_engine.begin() as conn:
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{DOWNGRADE_SCHEMA}" CASCADE'))


# --------------------------------------------------------------------------
# 2. Index and constraint review against the real catalogue
# --------------------------------------------------------------------------


def test_expected_indexes_exist_in_the_postgres_catalogue(pg_drill):
    with pg_drill.engine.connect() as conn:
        rows = conn.execute(
            text("select indexname from pg_indexes where schemaname = :schema"),
            {"schema": pg_drill.schema},
        ).scalars().all()
    present = set(rows)
    expected = {
        "ix_assessments_organization_id",
        "ix_workflows_assessment_id",
        "ix_workflow_steps_workflow_id",
        "ix_tasks_step_id",
        "ix_answers_assessment_id",
        "ix_answers_key",
        "ix_reviews_assessment_id",
        "ix_audit_events_assessment_id",
        "ix_ai_runs_assessment_id",
        "ix_api_credentials_organization_id",
        "ix_oidc_memberships_organization_id",
        "ix_assessment_comments_assessment_id",
        "ix_approval_votes_assessment_id",
        "ix_inbox_notifications_organization_id",
        "ix_inbox_notifications_recipient",
        "ix_opportunity_evidence_assessment_id",
        "ix_value_scenarios_assessment_id",
        "ix_poc_plans_assessment_id",
        "ix_poc_observations_poc_id",
    }
    assert expected <= present, sorted(expected - present)


def test_expected_unique_constraints_and_foreign_keys_exist(pg_drill):
    with pg_drill.engine.connect() as conn:
        constraints = conn.execute(
            text(
                "select conname, contype, pg_get_constraintdef(oid) as definition "
                "from pg_constraint where connamespace = to_regnamespace(:schema)"
            ),
            {"schema": pg_drill.schema},
        ).all()
    definitions = [row.definition for row in constraints]
    for expected in (
        "UNIQUE (issuer, subject, organization_id)",
        "UNIQUE (assessment_id)",
        "UNIQUE (name)",
        "UNIQUE (token_hash)",
    ):
        assert any(expected in definition for definition in definitions), expected
    # Tenant isolation is enforced by the database, not only by application code.
    assert any(
        "FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE RESTRICT" in d
        for d in definitions
    )
    assert sum(1 for d in definitions if "ON DELETE CASCADE" in d) >= 10


def test_tenant_list_query_uses_the_organization_index(pg_drill):
    """The hot tenant listing path is served by ix_assessments_organization_index."""
    from app.db.models import AssessmentRow, OrganizationRow

    with Session(pg_drill.engine) as db:
        organization = OrganizationRow(name="drill-index-org")
        other = OrganizationRow(name="drill-index-org-other")
        db.add_all([organization, other])
        db.flush()
        org_id = organization.id
        other_id = other.id
        db.add_all(
            [
                AssessmentRow(title=f"mine {i}", organization_id=org_id, status="draft")
                for i in range(50)
            ]
            + [
                AssessmentRow(title=f"other {i}", organization_id=other_id, status="draft")
                for i in range(20000)
            ]
        )
        db.commit()

    try:
        with pg_drill.engine.connect() as conn:
            conn.execute(text("analyze assessments"))
            plan = conn.execute(
                text(
                    "explain (analyze, buffers) select id from assessments "
                    "where status != 'deleted' and organization_id = :org "
                    "order by created_at desc limit 50"
                ),
                {"org": org_id},
            ).scalars().all()
        rendered = "\n".join(plan)
        assert "ix_assessments_organization_id" in rendered, rendered
        assert "Seq Scan on assessments" not in rendered, rendered
    finally:
        with Session(pg_drill.engine) as db:
            db.query(AssessmentRow).filter(
                AssessmentRow.organization_id.in_([org_id, other_id])
            ).delete()
            db.query(OrganizationRow).filter(OrganizationRow.id.in_([org_id, other_id])).delete()
            db.commit()


def _fk_index_coverage(engine, schema: str) -> list[tuple[str, str, str]]:
    """Return foreign keys whose referencing column has no leading-column index."""
    with engine.connect() as conn:
        foreign_keys = conn.execute(
            text(
                "select c.conname, c.conrelid::regclass::text as table_name, "
                "c.conkey::int2[]::int[] as columns "
                "from pg_constraint c "
                "where c.contype = 'f' and c.connamespace = to_regnamespace(:schema)"
            ),
            {"schema": schema},
        ).all()
        indexes = conn.execute(
            text(
                "select i.indrelid::regclass::text as table_name, "
                "i.indkey::int2[]::int[] as columns "
                "from pg_index i join pg_class c on c.oid = i.indrelid "
                "where c.relnamespace = to_regnamespace(:schema)"
            ),
            {"schema": schema},
        ).all()
    leading = {}
    for row in indexes:
        leading.setdefault(row.table_name, []).append(tuple(row.columns[:1]))
    unindexed = []
    for row in foreign_keys:
        if len(row.columns) != 1:
            continue
        if (row.columns[0],) not in leading.get(row.table_name, []):
            unindexed.append((row.conname, row.table_name, str(row.columns[0])))
    return unindexed


def test_every_foreign_key_has_a_supporting_index(pg_drill):
    """PostgreSQL does not index foreign keys automatically; check we do.

    ``inbox_notifications.assessment_id`` is a recorded, accepted gap for P5-03:
    the column has no index, so a parent assessment delete must scan the
    notification table. The gap is listed explicitly so that a *new* unindexed
    foreign key fails this test.
    """
    accepted_gaps = {"inbox_notifications_assessment_id_fkey"}
    unindexed = _fk_index_coverage(pg_drill.engine, pg_drill.schema)
    found = {name for name, _table, _column in unindexed}
    assert found <= accepted_gaps, f"unindexed foreign keys: {sorted(found - accepted_gaps)}"


def test_tenant_list_ordering_has_no_index_backed_sort(pg_drill):
    """Documents the known gap: no composite (organization_id, created_at) index.

    ``GET /api/v1/assessments`` and ``GET /api/v1/portfolio/opportunities`` order
    by ``created_at DESC`` inside one tenant. Only ``ix_assessments_organization_id``
    exists, so PostgreSQL filters through the index and then sorts the tenant's
    rows in memory (top-N heapsort). This test fails on purpose once a
    composite index is added, so the schema change is reviewed.
    """
    from app.db.models import AssessmentRow, OrganizationRow

    with Session(pg_drill.engine) as db:
        organization = OrganizationRow(name="drill-order-org")
        other = OrganizationRow(name="drill-order-org-other")
        db.add_all([organization, other])
        db.flush()
        org_id = organization.id
        other_id = other.id
        db.add_all(
            [
                AssessmentRow(title=f"mine {i}", organization_id=org_id, status="draft")
                for i in range(50)
            ]
            + [
                AssessmentRow(title=f"other {i}", organization_id=other_id, status="draft")
                for i in range(20000)
            ]
        )
        db.commit()
    try:
        with pg_drill.engine.connect() as conn:
            conn.execute(text("analyze assessments"))
            plan = conn.execute(
                text(
                    "explain (analyze) select id from assessments "
                    "where status != 'deleted' and organization_id = :org "
                    "order by created_at desc limit 50"
                ),
                {"org": org_id},
            ).scalars().all()
        rendered = "\n".join(plan)
        assert "Sort" in rendered, rendered
    finally:
        with Session(pg_drill.engine) as db:
            db.query(AssessmentRow).filter(
                AssessmentRow.organization_id.in_([org_id, other_id])
            ).delete()
            db.query(OrganizationRow).filter(OrganizationRow.id.in_([org_id, other_id])).delete()
            db.commit()


# --------------------------------------------------------------------------
# 3. Concurrency: optimistic version, row locks and If-Match preconditions
# --------------------------------------------------------------------------


def test_concurrent_writers_cannot_lose_an_update_on_postgres(pg_drill):
    """The independent write_version column rejects a stale concurrent write."""
    from app.db.models import AssessmentRow

    with Session(pg_drill.engine) as setup:
        row = AssessmentRow(title="Initial")
        setup.add(row)
        setup.commit()
        assessment_id = row.id

    barrier = threading.Barrier(2)
    outcome: dict[str, str] = {}

    def writer(name: str, title: str) -> None:
        with Session(pg_drill.engine) as db:
            current = db.get(AssessmentRow, assessment_id)
            barrier.wait(timeout=10)
            current.title = title
            try:
                db.commit()
                outcome[name] = "committed"
            except StaleDataError:
                db.rollback()
                outcome[name] = "stale-rejected"

    threads = [
        threading.Thread(target=writer, args=("first", "First writer")),
        threading.Thread(target=writer, args=("second", "Second writer")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert sorted(outcome.values()) == ["committed", "stale-rejected"], outcome
    with Session(pg_drill.engine) as check:
        stored = check.get(AssessmentRow, assessment_id)
        assert stored.title in {"First writer", "Second writer"}
        assert stored.title != "Initial"


def test_postgres_row_lock_blocks_a_concurrent_writer(pg_drill):
    """SELECT ... FOR UPDATE really blocks a second writer on PostgreSQL."""
    from app.db.models import AssessmentRow

    with Session(pg_drill.engine) as setup:
        row = AssessmentRow(title="Locked")
        setup.add(row)
        setup.commit()
        assessment_id = row.id

    holder = Session(pg_drill.engine)
    try:
        locked = holder.get(AssessmentRow, assessment_id)
        holder.refresh(locked, with_for_update=True)
        acquired = threading.Event()
        finished = threading.Event()

        def contender() -> None:
            with Session(pg_drill.engine) as db:
                other = db.get(AssessmentRow, assessment_id)
                db.refresh(other, with_for_update=True)
                acquired.set()
                db.rollback()
            finished.set()

        thread = threading.Thread(target=contender)
        thread.start()
        assert not acquired.wait(timeout=1.0), "second writer was not blocked by the row lock"
        assert not finished.is_set()
        holder.commit()
        assert acquired.wait(timeout=15), "second writer never acquired the row lock"
        thread.join(timeout=15)
    finally:
        holder.rollback()
        holder.close()


def test_if_match_preconditions_return_428_and_412_on_postgres(pg_drill, api_client, api_principal):
    assessment = _create_assessment(api_client, api_principal.headers)
    url = f"/api/v1/assessments/{assessment['id']}"
    revision = assessment["revision"]

    missing = api_client.patch(url, json={"title": "No precondition"},
                               headers=api_principal.headers)
    assert missing.status_code == 428, missing.text

    stale = api_client.patch(
        url,
        json={"title": "Stale precondition"},
        headers={**api_principal.headers, "If-Match": f'"{revision - 1}"'},
    )
    assert stale.status_code == 412, stale.text

    current = api_client.patch(
        url,
        json={"title": "Fresh precondition"},
        headers={**api_principal.headers, "If-Match": f'"{revision}"'},
    )
    assert current.status_code == 200, current.text
    assert current.json()["revision"] == revision + 1


def test_concurrent_api_write_returns_409_and_never_silently_overwrites(
    pg_drill, api_client, api_principal
):
    """A write that loses the optimistic version race is rejected with 409."""
    assessment = _create_assessment(api_client, api_principal.headers)
    url = f"/api/v1/assessments/{assessment['id']}"
    revision = assessment["revision"]
    headers = {**api_principal.headers, "If-Match": f'"{revision}"'}

    competing = create_engine(pg_drill.url, pool_pre_ping=True)
    fired = {"value": False}

    def inject_competing_write(conn, cursor, statement, parameters, context, executemany):  # noqa: ARG001
        if fired["value"] or not statement.lstrip().upper().startswith("UPDATE ASSESSMENTS"):
            return
        fired["value"] = True
        with competing.begin() as other:
            other.execute(
                text("update assessments set write_version = write_version + 1 where id = :id"),
                {"id": assessment["id"]},
            )

    event.listen(pg_drill.engine, "before_cursor_execute", inject_competing_write)
    try:
        response = api_client.patch(url, json={"title": "Racing writer"}, headers=headers)
    finally:
        event.remove(pg_drill.engine, "before_cursor_execute", inject_competing_write)
        competing.dispose()

    assert fired["value"], "the competing write was never injected"
    assert response.status_code == 409, response.text

    with Session(pg_drill.engine) as db:
        from app.db.models import AssessmentRow

        stored = db.get(AssessmentRow, assessment["id"])
        assert stored.title == assessment["title"]
        assert stored.revision == revision


def test_two_parallel_api_writes_produce_exactly_one_winner(pg_drill, api_client, api_principal):
    """Concurrent PATCHes never both succeed and never corrupt the row."""
    assessment = _create_assessment(api_client, api_principal.headers)
    url = f"/api/v1/assessments/{assessment['id']}"
    revision = assessment["revision"]
    headers = {**api_principal.headers, "If-Match": f'"{revision}"'}

    results: list[int] = []
    lock = threading.Lock()
    barrier = threading.Barrier(2)

    def writer(title: str) -> None:
        barrier.wait(timeout=10)
        response = api_client.patch(url, json={"title": title}, headers=headers)
        with lock:
            results.append(response.status_code)

    threads = [
        threading.Thread(target=writer, args=("Writer A",)),
        threading.Thread(target=writer, args=("Writer B",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert len(results) == 2, results
    assert results.count(200) == 1, results
    assert all(code in (200, 409, 412) for code in results), results
    with Session(pg_drill.engine) as db:
        from app.db.models import AssessmentRow

        stored = db.get(AssessmentRow, assessment["id"])
        assert stored.revision == revision + 1
        assert stored.title in {"Writer A", "Writer B"}


def test_cross_tenant_access_is_denied_on_postgres(pg_drill, api_client, api_principal):
    """A credential from one organization must not read another tenant's data."""
    from app.db.models import ApiCredentialRow, OrganizationRow
    from app.security import digest_token, make_token

    other_token = make_token()
    with Session(pg_drill.engine) as db:
        organization = OrganizationRow(name=f"drill-other-org-{other_token[-8:]}")
        db.add(organization)
        db.flush()
        db.add(
            ApiCredentialRow(
                organization_id=organization.id,
                subject="other-analyst",
                role="analyst",
                token_hash=digest_token(other_token),
                expires_at=datetime.now(UTC) + timedelta(days=1),
            )
        )
        db.commit()

    created = _create_assessment(api_client, api_principal.headers, title="Tenant A assessment")
    other_headers = {"Authorization": f"Bearer {other_token}"}

    hidden = api_client.get(f"/api/v1/assessments/{created['id']}", headers=other_headers)
    assert hidden.status_code == 404, hidden.text

    listed = api_client.get("/api/v1/assessments", headers=other_headers)
    assert listed.status_code == 200
    assert all(item["id"] != created["id"] for item in listed.json()), listed.text

    write = api_client.patch(
        f"/api/v1/assessments/{created['id']}",
        json={"title": "Cross tenant write"},
        headers={**other_headers, "If-Match": f'"{created["revision"]}"'},
    )
    assert write.status_code == 404, write.text


# --------------------------------------------------------------------------
# 4. Rate limiter atomicity
# --------------------------------------------------------------------------


def _identity_hash(org: str, role: str, principal: str) -> str:
    import hashlib

    return hashlib.sha256(f"{org}:{role}:{principal}".encode()).hexdigest()


def _bucket_counts(engine, identity_hash: str) -> dict[int, int]:
    from app.db.models import RequestLimitRow

    with Session(engine) as db:
        rows = db.execute(
            select(RequestLimitRow.minute_bucket, RequestLimitRow.requests).where(
                RequestLimitRow.identity_hash == identity_hash
            )
        ).all()
    return {bucket: count for bucket, count in rows}


def test_rate_limiter_is_atomic_under_parallel_writers(pg_drill, monkeypatch):
    """The ON CONFLICT upsert must not lose increments on PostgreSQL."""
    from fastapi import HTTPException

    from app.throttling import enforce_limit

    capacity = 10
    monkeypatch.setenv("ADVISORY_REQUESTS_PER_MINUTE", str(capacity))
    total = 24
    principal = f"parallel-{time.time_ns()}"
    org = "drill-org"
    allowed: list[int] = []
    limited: list[int] = []
    lock = threading.Lock()
    barrier = threading.Barrier(total)

    def call() -> None:
        with Session(pg_drill.engine) as db:
            barrier.wait(timeout=30)
            try:
                enforce_limit(db, org=org, principal=principal, role="analyst")
            except HTTPException as exc:
                with lock:
                    limited.append(exc.status_code)
            else:
                with lock:
                    allowed.append(1)

    threads = [threading.Thread(target=call) for _ in range(total)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert len(allowed) + len(limited) == total
    assert all(code == 429 for code in limited), limited
    counts = _bucket_counts(pg_drill.engine, _identity_hash(org, "analyst", principal))
    # Atomicity: no increment is lost, the counter equals the number of requests.
    assert sum(counts.values()) == total, counts
    # Capacity: each minute bucket admits at most `capacity` requests.
    assert len(allowed) == sum(min(capacity, value) for value in counts.values()), (allowed, counts)


def test_http_rate_limit_returns_429_for_requests_over_the_limit(
    pg_drill, api_client, api_principal, monkeypatch
):
    capacity = 5
    monkeypatch.setenv("ADVISORY_REQUESTS_PER_MINUTE", str(capacity))
    statuses: list[int] = []
    lock = threading.Lock()

    def call() -> None:
        response = api_client.get("/api/v1/assessments", headers=api_principal.headers)
        with lock:
            statuses.append(response.status_code)

    threads = [threading.Thread(target=call) for _ in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert len(statuses) == 20
    assert all(code in (200, 429) for code in statuses), statuses
    counts = _bucket_counts(
        pg_drill.engine,
        _identity_hash(api_principal.organization_id, "analyst", "drill-analyst"),
    )
    assert sum(counts.values()) == 20, counts
    expected_allowed = sum(min(capacity, value) for value in counts.values())
    assert statuses.count(200) == expected_allowed, (statuses, counts)
    assert statuses.count(429) == 20 - expected_allowed, (statuses, counts)
