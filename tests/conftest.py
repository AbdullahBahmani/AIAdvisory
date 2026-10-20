"""Shared pytest fixtures: backend selection, database provisioning and proof.

The suite is backend-parametrised. The same test bodies run against SQLite
(the default: fast, hermetic, no container) and against PostgreSQL when a
PostgreSQL URL is provided:

    ADVISORY_PG_DRILL_URL=postgresql+psycopg://user:pw@127.0.0.1:15432/aiadvisory \\
        python -m pytest -q

``ADVISORY_TEST_BACKEND=sqlite|postgres`` forces a backend explicitly.

When PostgreSQL is selected the suite works inside its own schema
(``aiadvisory_pg_suite``) in the target database:

* the schema is created and the whole Alembic chain is applied to it, so the
  tests run against a *migrated* catalogue and not against ``create_all``;
* ``DATABASE_URL`` is pointed at that schema before the application is
  imported, so the application's global engine is a PostgreSQL engine too;
* every test starts from an empty database (SQLite: a fresh file; PostgreSQL:
  the shared schema is truncated between tests);
* the schema is dropped again when the session ends.

A statement counter is attached to the PostgreSQL engines so the run can prove
that it really executed SQL there instead of silently passing.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from app.db.models import Base
from scripts.pg_drill import create_schema, drop_schema, run_alembic, schema_url
from tests.backend import (
    EXECUTED_NODE_IDS,
    PER_TEST_STATEMENTS,
    POSTGRES,
    STATEMENTS,
    SUITE_SCHEMA,
    configured_postgres_url,
    create_sqlite_database,
    is_application_test,
    resolve_backend,
    truncate_all,
)

#: Resolved once, at import time, before the application is imported.
BACKEND = resolve_backend()

#: Statement counter baseline per running test (see pytest_runtest_setup).
_statement_baseline: dict[int, int] = {}

if BACKEND == POSTGRES:
    BASE_URL = configured_postgres_url()
    # Pin the drill URL to the *unmodified* database URL: the P5-03 drill module
    # manages its own schema and must not inherit the suite's search_path.
    os.environ["ADVISORY_PG_DRILL_URL"] = BASE_URL
    # Point the application's global engine at the suite schema.
    os.environ["DATABASE_URL"] = schema_url(BASE_URL, SUITE_SCHEMA)


@pytest.fixture(autouse=True)
def synthetic_demo_for_legacy_tests(monkeypatch):
    monkeypatch.setenv("ADVISORY_DEMO_MODE", "1")


@pytest.fixture(scope="session")
def backend() -> str:
    """The backend the suite runs against (``sqlite`` or ``postgres``)."""
    return BACKEND


@pytest.fixture(scope="session")
def postgres_suite_engine() -> Iterator[Engine]:
    """A migrated, isolated PostgreSQL schema holding the application tables."""
    if BACKEND != POSTGRES:
        pytest.skip("PostgreSQL backend not selected (set ADVISORY_PG_DRILL_URL)")
    base_url = configured_postgres_url()
    create_schema(base_url, SUITE_SCHEMA)
    run_alembic(schema_url(base_url, SUITE_SCHEMA), "upgrade", "head")
    engine = create_engine(schema_url(base_url, SUITE_SCHEMA), pool_pre_ping=True)
    STATEMENTS.register(engine)
    # The application's global engine must be a PostgreSQL engine as well.
    import app.db.session as session_module

    STATEMENTS.register(session_module.engine)
    try:
        yield engine
    finally:
        engine.dispose()
        drop_schema(base_url, SUITE_SCHEMA)


@pytest.fixture
def postgres_unmigrated_engine() -> Iterator[Engine]:
    """A PostgreSQL schema with the application tables but no Alembic version."""
    if BACKEND != POSTGRES:
        pytest.skip("PostgreSQL backend not selected (set ADVISORY_PG_DRILL_URL)")
    base_url = configured_postgres_url()
    schema = f"{SUITE_SCHEMA}_unmigrated_{os.getpid()}"
    create_schema(base_url, schema)
    engine = create_engine(schema_url(base_url, schema), pool_pre_ping=True)
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()
        drop_schema(base_url, schema)


@pytest.fixture
def db_engine(request) -> Iterator[Engine]:
    """The database the application tests run against.

    SQLite: a throwaway file database built from the ORM metadata.
    PostgreSQL: the migrated suite schema, truncated so each test starts
    empty.
    """
    if BACKEND == POSTGRES:
        engine = request.getfixturevalue("postgres_suite_engine")
        truncate_all(engine)
        yield engine
        return
    tmp_path = request.getfixturevalue("tmp_path")
    engine = create_sqlite_database(tmp_path / "suite.sqlite3")
    STATEMENTS.register(engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def unmigrated_engine(request) -> Iterator[Engine]:
    """A database whose schema exists but was never migrated.

    Used by the readiness probe test, which needs a database without an
    ``alembic_version`` table.
    """
    if BACKEND == POSTGRES:
        engine = request.getfixturevalue("postgres_unmigrated_engine")
        yield engine
        return
    tmp_path = request.getfixturevalue("tmp_path")
    engine = create_sqlite_database(tmp_path / "unmigrated.sqlite3")
    STATEMENTS.register(engine)
    try:
        yield engine
    finally:
        engine.dispose()


def pytest_configure(config):
    config.addinivalue_line("markers", "last: run this test at the end of the session")


def pytest_collection_modifyitems(items):
    """Run ``@pytest.mark.last`` tests at the very end of the session.

    The PostgreSQL proof test observes the statements executed by the other
    tests, so it must not run first.
    """
    tail = [item for item in items if item.get_closest_marker("last")]
    head = [item for item in items if not item.get_closest_marker("last")]
    items[:] = head + tail


def pytest_runtest_logreport(report):
    if report.when == "call":
        EXECUTED_NODE_IDS.append(report.nodeid)


def pytest_runtest_setup(item):
    _statement_baseline[id(item)] = STATEMENTS.snapshot()


def pytest_runtest_teardown(item, nextitem):  # noqa: ARG001
    start = _statement_baseline.pop(id(item), None)
    if start is not None:
        PER_TEST_STATEMENTS.append((item.nodeid, STATEMENTS.snapshot() - start))


def pytest_terminal_summary(terminalreporter, exitstatus, config):  # noqa: ARG001
    terminalreporter.write_sep("-", f"test backend: {BACKEND}")
    terminalreporter.write_line(
        f"SQL statements executed on the {BACKEND} engine: {STATEMENTS.snapshot()}"
    )
    per_module: dict[str, int] = {}
    for nodeid, count in PER_TEST_STATEMENTS:
        module = nodeid.split("::", 1)[0]
        per_module[module] = per_module.get(module, 0) + count
    for module, count in sorted(per_module.items(), key=lambda item: -item[1]):
        terminalreporter.write_line(f"  {module}: {count} statements")


def pytest_sessionfinish(session, exitstatus):
    """A PostgreSQL run that executed no SQL at all is a failure, not a pass."""
    application_tests = [nodeid for nodeid in EXECUTED_NODE_IDS if is_application_test(nodeid)]
    if BACKEND == POSTGRES and application_tests and STATEMENTS.snapshot() == 0:
        sys.stderr.write(
            "\nERROR: the PostgreSQL run executed zero SQL statements while "
            f"{len(application_tests)} application tests ran. "
            "The suite did not really run against PostgreSQL.\n"
        )
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
