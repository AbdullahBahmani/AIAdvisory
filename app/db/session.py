"""DB session dependency. Use Alembic to create the schema before starting the API."""

import os
from collections.abc import Generator

from sqlalchemy import create_engine, event, inspect
from sqlalchemy.orm import Session, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./aiadvisory.sqlite3")


def _seconds(name: str, default: int, *, minimum: int, maximum: int) -> int:
    """Read a timeout from the environment, falling back to a documented default."""
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


#: How long to wait for a database connection before giving up. Without this a
#: silently partitioned database (packets accepted, never answered) wedges the
#: request forever, and enough wedged requests exhaust the pool and stop
#: /health answering either.
CONNECT_TIMEOUT = _seconds("ADVISORY_DB_CONNECT_TIMEOUT", 5, minimum=1, maximum=120)
#: How long a single statement may run server-side.
STATEMENT_TIMEOUT = _seconds("ADVISORY_DB_STATEMENT_TIMEOUT", 30, minimum=1, maximum=600)
#: How long to wait for a connection from the pool before giving up.
POOL_TIMEOUT = _seconds("ADVISORY_DB_POOL_TIMEOUT", 10, minimum=1, maximum=120)

if DATABASE_URL.startswith("sqlite"):
    _connect_args: dict = {"check_same_thread": False, "timeout": CONNECT_TIMEOUT}
else:
    # psycopg honours connect_timeout as a number of seconds.
    _connect_args = {"connect_timeout": CONNECT_TIMEOUT}

engine = create_engine(
    DATABASE_URL,
    connect_args=_connect_args,
    pool_pre_ping=True,
    pool_timeout=POOL_TIMEOUT,
)


@event.listens_for(engine, "connect")
def _apply_statement_timeout(dbapi_connection, connection_record):  # noqa: ARG001
    """Bound how long a single statement may run.

    PostgreSQL supports a server-side statement timeout; SQLite has no
    equivalent, so the busy timeout in the connect args is the bound there.
    """
    if engine.dialect.name == "postgresql":
        cursor = dbapi_connection.cursor()
        cursor.execute(f"SET statement_timeout = {STATEMENT_TIMEOUT * 1000}")
        cursor.close()


# Columns the running code writes on every request. A database that does not
# provide them is not ready for this code, whatever its version string says.
REQUIRED_SCHEMA_COLUMNS: dict[str, tuple[str, ...]] = {
    "reviews": ("reviewer_organization_id",),
    # The chain columns are required: the running code writes them on every audit
    # event, so a database without them is not one this build can serve.
    "audit_events": (
        "actor_organization_id",
        "actor_verified",
        "sequence",
        "chain_hash",
        "organization_id",
    ),
    "api_credentials": ("unlimited", "overlap_until", "replaced_by_id"),
}


@event.listens_for(engine, "connect")
def sqlite_foreign_keys(dbapi_connection, connection_record):  # noqa: ARG001
    if engine.dialect.name == "sqlite":
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session


def schema_is_current(db: Session) -> bool:
    """True when every column the running code requires is actually present."""
    try:
        inspector = inspect(db.get_bind())
        tables = set(inspector.get_table_names())
        for table, columns in REQUIRED_SCHEMA_COLUMNS.items():
            if table not in tables:
                return False
            present = {column["name"] for column in inspector.get_columns(table)}
            if not set(columns).issubset(present):
                return False
    except Exception:
        return False
    return True
