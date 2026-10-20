"""Backend-parametrisation checks: prove the suite really ran on the backend.

"The suite passes on PostgreSQL" is only meaningful if the run actually
executed SQL on PostgreSQL. These tests make a vacuous PostgreSQL run
impossible: they assert which backend was selected, that the engines really
are PostgreSQL engines, that the schema is migrated, and - last in the
session - that the run executed a non-zero number of statements.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text

from tests.backend import (
    BACKEND,
    EXECUTED_NODE_IDS,
    POSTGRES,
    SQLITE,
    STATEMENTS,
    configured_postgres_url,
    expected_head_revision,
    is_application_test,
    resolve_backend,
)


def test_backend_selection_matches_the_environment(backend):
    """The selected backend follows the documented precedence rules."""
    assert backend in (SQLITE, POSTGRES)
    assert backend == resolve_backend()
    explicit = os.getenv("ADVISORY_TEST_BACKEND", "").strip().lower()
    if explicit:
        assert backend == explicit
    else:
        assert backend == (POSTGRES if configured_postgres_url() else SQLITE)


@pytest.mark.skipif(
    BACKEND != SQLITE,
    reason=(
        "SQLite-backend property: the PostgreSQL run is covered by the other tests in this module"
    ),
)
def test_sqlite_backend_needs_no_postgres_server():
    """A plain local run stays on SQLite: no container, no network."""
    explicit = os.getenv("ADVISORY_TEST_BACKEND", "").strip().lower()
    if explicit:
        assert explicit == SQLITE
    else:
        assert configured_postgres_url() is None


def test_postgres_run_uses_postgresql_engines(postgres_suite_engine):
    """Both the suite engine and the application's global engine are PostgreSQL."""
    import app.db.session as session_module

    assert postgres_suite_engine.dialect.name == "postgresql"
    assert session_module.engine.dialect.name == "postgresql"


def test_postgres_schema_is_migrated_to_head(postgres_suite_engine):
    """The suite runs against a migrated catalogue, not against ``create_all``."""
    with postgres_suite_engine.connect() as conn:
        version = conn.execute(text("select version_num from alembic_version")).scalar_one()
    # Derive the expected head from the migration chain instead of hard-coding it:
    # a new migration must not turn this into a false failure, and a stale
    # hard-coded revision would silently stop checking what it claims to check.
    assert version == expected_head_revision()


@pytest.mark.last
def test_postgres_run_executed_sql_statements(postgres_suite_engine):
    """The PostgreSQL run must have executed SQL - otherwise it was vacuous."""
    total = STATEMENTS.snapshot()
    kinds = STATEMENTS.kinds()
    dialects = STATEMENTS.engine_dialects()
    assert total > 0, (
        "the PostgreSQL run executed zero SQL statements: the suite did not really "
        "run against PostgreSQL"
    )
    assert dialects, "no engine was instrumented"
    assert all(dialect == "postgresql" for dialect in dialects), dialects
    application_tests = [nodeid for nodeid in EXECUTED_NODE_IDS if is_application_test(nodeid)]
    if application_tests:
        # The application tests wrote to and read from the database.
        assert kinds.get("INSERT", 0) > 0, kinds
        assert kinds.get("SELECT", 0) > 0, kinds
        assert kinds.get("UPDATE", 0) > 0, kinds
    print(f"\nPostgreSQL statements executed: {total} {kinds} on {dialects}")
