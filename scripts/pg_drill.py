"""Local, disposable PostgreSQL drill for planner item P5-03.

This helper runs the *whole* P5-03 drill against a throwaway PostgreSQL
container so an operator can reproduce it:

    python -m scripts.pg_drill up             # start the disposable instance
    python -m scripts.pg_drill migrate        # alembic upgrade head + alembic check
    python -m scripts.pg_drill fresh          # fresh database + downgrade base
    python -m scripts.pg_drill backup-restore # pg_dump -> second container -> verify
    python -m scripts.pg_drill down           # remove the disposable instances

    python -m scripts.pg_drill all             # up + migrate + fresh + backup-restore + down

Nothing here is a production control. The instance is a local container with a
throwaway password, the dump is **not** encrypted and **not** off-site, and no
capacity, load or recovery-time objective is measured. The production
PostgreSQL gate stays open until an operator runs the drill against the real
managed cluster with encrypted off-site backups and a signed-off RPO/RTO.

Configuration (all optional, all local-only):

    PG_DRILL_IMAGE        default postgres:16-alpine
    PG_DRILL_CONTAINER    default aiadvisory-pg-drill
    PG_DRILL_RESTORE_CONTAINER  default aiadvisory-pg-drill-restore
    PG_DRILL_PORT         default 15432
    PG_DRILL_RESTORE_PORT default 15433
    PG_DRILL_USER         default aiadvisory
    PG_DRILL_DATABASE     default aiadvisory
    PG_DRILL_PASSWORD     default local_drill_only_pw (throwaway, local container only)
    PG_DRILL_KEEP         set to 1 to keep the containers and the dump file
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

IMAGE = os.getenv("PG_DRILL_IMAGE", "postgres:16-alpine")
CONTAINER = os.getenv("PG_DRILL_CONTAINER", "aiadvisory-pg-drill")
RESTORE_CONTAINER = os.getenv("PG_DRILL_RESTORE_CONTAINER", "aiadvisory-pg-drill-restore")
PORT = int(os.getenv("PG_DRILL_PORT", "15432"))
RESTORE_PORT = int(os.getenv("PG_DRILL_RESTORE_PORT", "15433"))
USER = os.getenv("PG_DRILL_USER", "aiadvisory")
DATABASE = os.getenv("PG_DRILL_DATABASE", "aiadvisory")
PASSWORD = os.getenv("PG_DRILL_PASSWORD", "local_drill_only_pw")
KEEP = os.getenv("PG_DRILL_KEEP") == "1"

KEY_TABLES = (
    "assessments",
    "workflows",
    "workflow_steps",
    "tasks",
    "answers",
    "audit_events",
    "reviews",
    "ai_runs",
    "organizations",
    "api_credentials",
    "opportunity_profiles",
    "poc_plans",
)


def _run(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    result = subprocess.run(command, check=False, capture_output=True, text=True, **kwargs)
    if result.returncode != 0:
        sys.stderr.write((result.stdout or "") + (result.stderr or ""))
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(command)}")
    return result


def _docker(*args: str, **kwargs) -> subprocess.CompletedProcess:
    return _run(["docker", *args], **kwargs)


def database_url(port: int = PORT, database: str = DATABASE) -> str:
    return f"postgresql+psycopg://{USER}:{PASSWORD}@127.0.0.1:{port}/{database}"


def redacted_url(port: int = PORT, database: str = DATABASE) -> str:
    return f"postgresql+psycopg://{USER}:***@127.0.0.1:{port}/{database}"


def _wait_ready(container: str, timeout: int = 90) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = subprocess.run(
            ["docker", "exec", container, "pg_isready", "-U", USER, "-d", "postgres"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return
        time.sleep(1)
    raise RuntimeError(f"container {container} did not become ready within {timeout}s")


def _drop_database(container: str, name: str) -> None:
    _docker(
        "exec",
        container,
        "psql",
        "-U",
        USER,
        "-d",
        "postgres",
        "-c",
        f"select pg_terminate_backend(pid) from pg_stat_activity where datname = '{name}'",
    )
    _docker(
        "exec",
        container,
        "psql",
        "-U",
        USER,
        "-d",
        "postgres",
        "-c",
        f'DROP DATABASE IF EXISTS "{name}"',
    )


def up() -> None:
    existing = subprocess.run(
        ["docker", "ps", "-a", "--filter", f"name=^{CONTAINER}$", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
    ).stdout.split()
    if CONTAINER in existing:
        _docker("rm", "-f", CONTAINER)
    _docker(
        "run",
        "--name",
        CONTAINER,
        "-e",
        f"POSTGRES_PASSWORD={PASSWORD}",
        "-e",
        f"POSTGRES_USER={USER}",
        "-e",
        f"POSTGRES_DB={DATABASE}",
        "-p",
        f"{PORT}:5432",
        "-d",
        IMAGE,
    )
    _wait_ready(CONTAINER)
    # Start every drill from an empty database so results are reproducible.
    _drop_database(CONTAINER, DATABASE)
    _docker(
        "exec",
        CONTAINER,
        "psql",
        "-U",
        USER,
        "-d",
        "postgres",
        "-c",
        f'CREATE DATABASE "{DATABASE}"',
    )
    print(f"started {CONTAINER} ({IMAGE}) on 127.0.0.1:{PORT} -> {redacted_url()}")


def down() -> None:
    for container in (CONTAINER, RESTORE_CONTAINER):
        existing = subprocess.run(
            ["docker", "ps", "-a", "--filter", f"name=^{container}$", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
        ).stdout.split()
        if container in existing:
            _docker("rm", "-f", container)
            print(f"removed {container}")


def _alembic(*args: str, url: str | None = None) -> str:
    env = dict(os.environ)
    env["DATABASE_URL"] = url or database_url()
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    sys.stdout.write(result.stderr)
    if result.returncode != 0:
        raise RuntimeError(f"alembic {args} failed with exit code {result.returncode}")
    return result.stderr


def migrate() -> None:
    print("== alembic upgrade head ==")
    _alembic("upgrade", "head")
    print("== alembic check ==")
    _alembic("check")


def fresh() -> None:
    """Apply the chain to a brand new database, then reverse it."""
    fresh_name = f"{DATABASE}_fresh"
    _drop_database(CONTAINER, fresh_name)
    _docker(
        "exec",
        CONTAINER,
        "psql",
        "-U",
        USER,
        "-d",
        "postgres",
        "-c",
        f'CREATE DATABASE "{fresh_name}"',
    )
    url = database_url(database=fresh_name)
    print(f"== fresh database {fresh_name}: upgrade head ==")
    _alembic("upgrade", "head", url=url)
    print(f"== fresh database {fresh_name}: alembic check ==")
    _alembic("check", url=url)
    print(f"== fresh database {fresh_name}: downgrade base ==")
    _alembic("downgrade", "base", url=url)
    print(f"== fresh database {fresh_name}: upgrade head again ==")
    _alembic("upgrade", "head", url=url)
    _docker(
        "exec",
        CONTAINER,
        "psql",
        "-U",
        USER,
        "-d",
        "postgres",
        "-c",
        f'DROP DATABASE IF EXISTS "{fresh_name}"',
    )
    print(f"fresh database cycle OK ({fresh_name} dropped again)")


def _seed(url: str) -> dict:
    """Create tenant, credential and assessments through the real API."""
    seed = r"""
import json, os, sys
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
from sqlalchemy import select
from app.db.models import ApiCredentialRow, OrganizationRow
from app.db.session import SessionLocal
from app.security import digest_token, make_token

token = make_token()
with SessionLocal.begin() as db:
    org = OrganizationRow(name="drill-org-" + token[-8:])
    db.add(org)
    db.flush()
    db.add(ApiCredentialRow(organization_id=org.id, subject="drill-analyst", role="analyst",
                            token_hash=digest_token(token),
                            expires_at=datetime.now(timezone.utc) + timedelta(days=1)))
    org_id = org.id

from app.main import app
client = TestClient(app)
headers = {"Authorization": f"Bearer {token}"}
created = []
for index in range(200):
    response = client.post("/api/v1/assessments", json={"title": f"Drill assessment {index}"},
                           headers=headers)
    response.raise_for_status()
    created.append(response.json())
for assessment in created:
    workflow = client.post(f"/api/v1/assessments/{assessment['id']}/workflows",
                            json={"name": "Drill workflow"}, headers=headers)
    workflow.raise_for_status()
    workflow_id = workflow.json()["id"]
    for order in range(2):
        step = client.post(f"/api/v1/workflows/{workflow_id}/steps",
                           json={"name": f"Drill step {order}", "order": order}, headers=headers)
        step.raise_for_status()
        task = client.post(f"/api/v1/workflow-steps/{step.json()['id']}/tasks",
                           json={"title": f"Drill task {order}"}, headers=headers)
        task.raise_for_status()
    answer = client.post(f"/api/v1/assessments/{assessment['id']}/answers",
                         json={"key": "business_problem", "value": "Drill answer", "kind": "fact"},
                         headers=headers)
    answer.raise_for_status()
print(json.dumps({"organization_id": org_id, "assessments": len(created)}))
"""
    env = dict(os.environ)
    env["DATABASE_URL"] = url
    env["ADVISORY_DEMO_MODE"] = "0"
    env["ADVISORY_DEPLOYMENT_MODE"] = "local"
    env["ADVISORY_REQUESTS_PER_MINUTE"] = "10000"
    result = subprocess.run(
        [sys.executable, "-c", seed],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stdout + result.stderr)
        raise RuntimeError("seeding failed")
    return json.loads(result.stdout.strip().splitlines()[-1])


def _row_counts(url: str) -> dict[str, int]:
    from sqlalchemy import create_engine, text

    engine = create_engine(url)
    counts: dict[str, int] = {}
    try:
        with engine.connect() as conn:
            for table in KEY_TABLES:
                counts[table] = conn.execute(text(f"select count(*) from {table}")).scalar_one()
    finally:
        engine.dispose()
    return counts


def _api_read(url: str) -> dict:
    reader = r"""
import json
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.main import app

engine = create_engine(__import__("os").environ["DATABASE_URL"])
def override_db():
    with Session(engine) as session:
        yield session
app.dependency_overrides[get_db] = override_db
client = TestClient(app)
response = client.get("/api/v1/assessments")
items = response.json() if response.status_code == 200 else []
detail_status = None
detail_title = None
if items:
    detail = client.get(f"/api/v1/assessments/{items[0]['id']}")
    detail_status = detail.status_code
    if detail.status_code == 200:
        detail_title = detail.json()["title"]
print(json.dumps({"status": response.status_code, "count": len(items),
                  "detail_status": detail_status, "first_title": detail_title}))
"""
    env = dict(os.environ)
    env["DATABASE_URL"] = url
    env["ADVISORY_DEMO_MODE"] = "1"
    env["ADVISORY_DEPLOYMENT_MODE"] = "local"
    result = subprocess.run(
        [sys.executable, "-c", reader],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stdout + result.stderr)
        raise RuntimeError("restored-instance API read failed")
    return json.loads(result.stdout.strip().splitlines()[-1])


def backup_restore() -> None:
    source_url = database_url()
    dump_path = Path(os.getenv("PG_DRILL_DUMP", "/tmp/aiadvisory-pg-drill.dump"))
    restore_db = f"{DATABASE}_restored"

    print("== seeding source instance through the API ==")
    seeded = _seed(source_url)
    print(
        f"seeded {seeded['assessments']} assessments for organization {seeded['organization_id']}"
    )
    source_counts = _row_counts(source_url)
    print("source row counts:", json.dumps(source_counts, indent=2))
    source_read = _api_read(source_url)
    print("source instance API read:", source_read)

    print("== pg_dump ==")
    started = time.perf_counter()
    with dump_path.open("wb") as handle:
        subprocess.run(
            [
                "docker",
                "exec",
                "-i",
                CONTAINER,
                "pg_dump",
                "-U",
                USER,
                "-d",
                DATABASE,
                "-Fc",
                "--no-owner",
            ],
            stdout=handle,
            check=True,
        )
    dump_seconds = time.perf_counter() - started
    print(f"dump written to {dump_path} ({dump_path.stat().st_size} bytes) in {dump_seconds:.2f}s")

    print("== starting the restore instance (off-site equivalent) ==")
    existing = subprocess.run(
        ["docker", "ps", "-a", "--filter", f"name=^{RESTORE_CONTAINER}$", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
    ).stdout.split()
    if RESTORE_CONTAINER in existing:
        _docker("rm", "-f", RESTORE_CONTAINER)
    _docker(
        "run",
        "--name",
        RESTORE_CONTAINER,
        "-e",
        f"POSTGRES_PASSWORD={PASSWORD}",
        "-e",
        f"POSTGRES_USER={USER}",
        "-e",
        f"POSTGRES_DB={DATABASE}",
        "-p",
        f"{RESTORE_PORT}:5432",
        "-d",
        IMAGE,
    )
    _wait_ready(RESTORE_CONTAINER)
    _docker(
        "exec",
        RESTORE_CONTAINER,
        "psql",
        "-U",
        USER,
        "-d",
        "postgres",
        "-c",
        f'CREATE DATABASE "{restore_db}"',
    )

    print("== pg_restore ==")
    started = time.perf_counter()
    with dump_path.open("rb") as handle:
        subprocess.run(
            [
                "docker",
                "exec",
                "-i",
                RESTORE_CONTAINER,
                "pg_restore",
                "-U",
                USER,
                "-d",
                restore_db,
                "--no-owner",
                "--no-privileges",
                "--clean",
                "--if-exists",
            ],
            stdin=handle,
            check=True,
        )
    restore_seconds = time.perf_counter() - started
    print(f"restore completed in {restore_seconds:.2f}s")

    restore_url = database_url(port=RESTORE_PORT, database=restore_db)
    restored_counts = _row_counts(restore_url)
    print("restored row counts:", json.dumps(restored_counts, indent=2))
    if source_counts != restored_counts:
        print("MISMATCH between source and restored row counts")
    else:
        print("row counts match between source and restored instance")

    api_result = _api_read(restore_url)
    print("restored instance API read:", api_result)

    report = {
        "timestamp": datetime.now(UTC).isoformat(),
        "source": redacted_url(),
        "restore": redacted_url(RESTORE_PORT, restore_db),
        "dump_bytes": dump_path.stat().st_size,
        "dump_seconds": round(dump_seconds, 2),
        "restore_seconds": round(restore_seconds, 2),
        "total_seconds": round(dump_seconds + restore_seconds, 2),
        "row_counts_match": source_counts == restored_counts,
        "source_api_read": source_read,
        "restored_api_read": api_result,
    }
    print("== drill report (local, NOT a production RPO/RTO) ==")
    print(json.dumps(report, indent=2))

    if not KEEP:
        dump_path.unlink(missing_ok=True)
        print(f"removed dump {dump_path}")
    else:
        print(f"kept dump {dump_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Disposable local PostgreSQL drill (P5-03)")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("up", "down", "migrate", "fresh", "backup-restore", "all"):
        sub.add_parser(name)
    args = parser.parse_args()
    if args.command == "up":
        up()
    elif args.command == "down":
        down()
    elif args.command == "migrate":
        migrate()
    elif args.command == "fresh":
        fresh()
    elif args.command == "backup-restore":
        backup_restore()
    elif args.command == "all":
        up()
        try:
            migrate()
            fresh()
            backup_restore()
        finally:
            if not KEEP:
                down()


if __name__ == "__main__":
    main()
