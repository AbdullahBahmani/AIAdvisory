"""Guard the migration chain against silent model/schema drift.

The repository documents `alembic check` as a release gate, but nothing enforced it.
In practice a clean checkout drifted: SQLAlchemy 2.1 infers DOUBLE for a bare Python
`float`, while migration 0001 created FLOAT, so `alembic check` failed on a fresh
database. This test builds a database from the migration chain alone and asserts the
models still describe that schema exactly.

The check runs against the selected backend: SQLite by default, PostgreSQL when the
suite runs against PostgreSQL (there the chain is applied to a throwaway schema).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from scripts.pg_drill import create_schema, drop_schema, schema_url
from tests.backend import BACKEND, POSTGRES, configured_postgres_url

REPO_ROOT = Path(__file__).resolve().parent.parent
ALEMBIC_INI = REPO_ROOT / "alembic.ini"
DRIFT_SCHEMA = "aiadvisory_pg_drift"


def _run_alembic(*args: str, database_url: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=REPO_ROOT,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "HOME": str(Path.home()),
            "DATABASE_URL": database_url,
            "ADVISORY_DEPLOYMENT_MODE": "local",
        },
        capture_output=True,
        text=True,
        timeout=180,
    )


@pytest.mark.skipif(not ALEMBIC_INI.exists(), reason="alembic.ini is missing")
def test_migration_chain_matches_models_without_drift(tmp_path):
    """A database built only from migrations must match the ORM models exactly."""
    if BACKEND == POSTGRES:
        base_url = configured_postgres_url()
        create_schema(base_url, DRIFT_SCHEMA)
        database_url = schema_url(base_url, DRIFT_SCHEMA)

        def cleanup() -> None:
            drop_schema(base_url, DRIFT_SCHEMA)

    else:
        database_url = f"sqlite:///{tmp_path / 'drift-check.sqlite3'}"

        def cleanup() -> None:
            return None

    try:
        upgrade = _run_alembic("upgrade", "head", database_url=database_url)
        assert upgrade.returncode == 0, f"alembic upgrade head failed:\n{upgrade.stderr}"

        check = _run_alembic("check", database_url=database_url)
        assert check.returncode == 0, (
            "alembic check reported schema drift between the migration chain and the "
            f"ORM models:\n{check.stdout}\n{check.stderr}"
        )
    finally:
        cleanup()
