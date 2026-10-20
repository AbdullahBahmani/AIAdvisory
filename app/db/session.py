"""DB session dependency. Use Alembic to create the schema before starting the API."""

import os
from collections.abc import Generator

from sqlalchemy import create_engine, event, inspect
from sqlalchemy.orm import Session, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./aiadvisory.sqlite3")
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
    pool_pre_ping=True,
)

# Columns the running code writes on every request. A database that does not
# provide them is not ready for this code, whatever its version string says.
REQUIRED_SCHEMA_COLUMNS: dict[str, tuple[str, ...]] = {
    "reviews": ("reviewer_organization_id",),
    "audit_events": ("actor_organization_id", "actor_verified"),
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
