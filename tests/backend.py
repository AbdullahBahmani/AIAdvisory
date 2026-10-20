"""Backend selection and database plumbing for the test suite.

The suite is *backend-parametrised*: the very same test bodies run against
SQLite (the default: fast, hermetic, no container) and against PostgreSQL
(when a PostgreSQL URL is provided). Nothing in here is a production control:
the PostgreSQL path is a local drill against a disposable instance.

Backend selection, in order of precedence:

1. ``ADVISORY_TEST_BACKEND=sqlite|postgres`` - explicit operator choice.
2. ``ADVISORY_PG_DRILL_URL`` pointing at PostgreSQL - the same variable the
   P5-03 drill tests already use.
3. ``DATABASE_URL`` pointing at PostgreSQL.

When PostgreSQL is selected the suite creates its own schema
(``aiadvisory_pg_suite``) inside the target database, applies the whole Alembic
chain to it and points ``DATABASE_URL`` at it, so the application's global
engine and the per-test engines all talk to the same migrated PostgreSQL
schema. The schema is dropped again when the session ends.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine

from app.db.models import Base

SQLITE = "sqlite"
POSTGRES = "postgres"

SUITE_SCHEMA = os.getenv("PG_DRILL_SUITE_SCHEMA", "aiadvisory_pg_suite")
HEAD_REVISION = "0008_portfolio"


def configured_postgres_url() -> str | None:
    """Return the PostgreSQL URL the operator asked for, if any."""
    url = os.getenv("ADVISORY_PG_DRILL_URL", "").strip()
    if url:
        return url
    candidate = os.getenv("DATABASE_URL", "").strip()
    if candidate.startswith("postgresql"):
        return candidate
    return None


def resolve_backend() -> str:
    """Return the backend the suite must run against."""
    explicit = os.getenv("ADVISORY_TEST_BACKEND", "").strip().lower()
    if explicit in (SQLITE, POSTGRES):
        return explicit
    if explicit:
        raise RuntimeError(
            f"ADVISORY_TEST_BACKEND must be 'sqlite' or 'postgres', got {explicit!r}"
        )
    return POSTGRES if configured_postgres_url() else SQLITE


#: The backend for this run, resolved once at import time.
BACKEND = resolve_backend()


@dataclass
class StatementCounter:
    """Counts the SQL statements a test run actually executed.

    This is the proof that a PostgreSQL run was not vacuous: the count must be
    greater than zero, otherwise the suite only *looked* like it ran against
    PostgreSQL.
    """

    total: int = 0
    statements: list[str] = field(default_factory=list)
    by_kind: dict[str, int] = field(default_factory=dict)
    dialects: list[str] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _listeners: list = field(default_factory=list, repr=False)

    def register(self, engine: Engine) -> Engine:
        """Attach the counting listener to ``engine`` (idempotent per engine)."""
        counter = self

        @event.listens_for(engine, "before_cursor_execute")
        def _count(conn, cursor, statement, parameters, context, executemany):  # noqa: ARG001
            counter.record(statement)

        with self._lock:
            self._listeners.append(_count)
            self.dialects.append(engine.dialect.name)
        return engine

    def record(self, statement: str) -> None:
        kind = statement.lstrip().split(" ", 1)[0].upper() or "UNKNOWN"
        with self._lock:
            self.total += 1
            self.by_kind[kind] = self.by_kind.get(kind, 0) + 1
            if len(self.statements) < 200:
                self.statements.append(statement)

    def snapshot(self) -> int:
        with self._lock:
            return self.total

    def kinds(self) -> dict[str, int]:
        with self._lock:
            return dict(self.by_kind)

    def engine_dialects(self) -> list[str]:
        with self._lock:
            return list(self.dialects)


#: Process-wide counter shared by the fixtures and the proof test.
STATEMENTS = StatementCounter()

#: Node ids of the tests that actually ran.
EXECUTED_NODE_IDS: list[str] = []

#: (node id, statements executed while the test ran) for every test.
PER_TEST_STATEMENTS: list[tuple[str, int]] = []

#: Test modules that exercise the database through the application.
APPLICATION_MODULES = (
    "tests/test_persistence.py",
    "tests/test_ai.py",
    "tests/test_security.py",
    "tests/test_phase6_foundation.py",
    "tests/test_phase7_collaboration.py",
    "tests/test_phase8_portfolio.py",
    "tests/test_release_hardening.py",
    "tests/test_reporting.py",
)


def is_application_test(nodeid: str) -> bool:
    """True when ``nodeid`` belongs to a test module that uses the database."""
    return any(nodeid.startswith(module) for module in APPLICATION_MODULES)


def truncate_all(engine: Engine) -> None:
    """Remove every application row so the next test starts from a clean slate.

    ``alembic_version`` is deliberately *not* truncated: it records the applied
    migration and the readiness probe depends on it.
    """
    tables = ", ".join(f'"{name}"' for name in sorted(Base.metadata.tables))
    if not tables:
        return
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE TABLE {tables} RESTART IDENTITY CASCADE"))


def sqlite_engine(path: Path) -> Engine:
    """Create a SQLite file engine with foreign keys enforced."""
    engine = create_engine(
        f"sqlite:///{path}", connect_args={"check_same_thread": False}, pool_pre_ping=True
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def create_sqlite_database(path: Path) -> Engine:
    """Create a SQLite file database straight from the ORM metadata."""
    engine = sqlite_engine(path)
    Base.metadata.create_all(engine)
    return engine
