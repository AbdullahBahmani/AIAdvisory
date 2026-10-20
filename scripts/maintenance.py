"""Offline maintenance for SQLite backups and scoped hard deletion.

Never exposes deletion over HTTP. Backups must be encrypted and governed by the
operator's retention policy. PostgreSQL operators should use pg_dump/pg_restore.

The credential and membership sweep follows the same convention as the purge
below: it **reports** by default and only changes something with an explicit
``--execute``.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from app.db.models import AssessmentRow
from app.db.session import DATABASE_URL, SessionLocal
from app.services.credential_lifecycle import (
    OPERATOR_ACTOR,
    SweepReport,
    sweep_lifecycle,
)


def sqlite_database_path() -> Path:
    if not DATABASE_URL.startswith("sqlite:///") or DATABASE_URL == "sqlite:///:memory:":
        raise ValueError("SQLite backup requires a disk-backed sqlite:/// database")
    return Path(DATABASE_URL[len("sqlite:///") :]).resolve()


def backup_sqlite(target: Path) -> Path:
    """Take a consistent SQLite online backup without locking app data files."""
    source = sqlite_database_path()
    if not source.is_file():
        raise FileNotFoundError("Source database does not exist")
    target = target.resolve()
    if target == source or target.exists():
        raise ValueError("Backup target must be a new path distinct from the source")
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".backup-", dir=target.parent)
    os.chmod(tmp_name, 0o600)
    os.close(fd)
    try:
        with (
            sqlite3.connect(f"file:{source}?mode=ro", uri=True) as old,
            sqlite3.connect(tmp_name) as fresh,
        ):
            old.backup(fresh)
            if fresh.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Backup integrity check failed")
        os.replace(tmp_name, target)
    finally:
        Path(tmp_name).unlink(missing_ok=True)
    return target


def retention_candidates(db, organization_id: str, before: datetime):
    return db.scalars(
        select(AssessmentRow).where(
            AssessmentRow.organization_id == organization_id,
            AssessmentRow.status == "deleted",
            AssessmentRow.updated_at < before,
        )
    ).all()


def purge_soft_deleted(organization_id: str, before: datetime, execute: bool = False):
    """Hard-delete only old, soft-deleted records for one explicit organization.

    Deletes dependent reviews/audit/AI output via model cascading relationships.
    Run with a verified backup and organizational retention/legal-hold approval.
    """
    if not organization_id or not before.tzinfo:
        raise ValueError("Explicit tenant and timezone-aware cutoff required")
    if before >= datetime.now(UTC):
        raise ValueError("Cutoff must be in the past")
    with SessionLocal.begin() as db:
        rows = retention_candidates(db, organization_id, before)
        count = len(rows)
        if execute:
            for row in rows:
                db.delete(row)
        return count


def sweep_membership_lifecycle(
    organization_id: str | None = None, execute: bool = False
) -> SweepReport:
    """Report (and with ``execute`` repair) credential and membership drift.

    Nothing is changed and nothing is audited unless ``execute`` is True.
    """
    with SessionLocal.begin() as db:
        return sweep_lifecycle(
            db,
            organization_id=organization_id,
            actor=OPERATOR_ACTOR,
            execute=execute,
        )


def main():
    p = argparse.ArgumentParser(
        description="Local, explicitly scoped maintenance; no hosted runners"
    )
    sub = p.add_subparsers(dest="command", required=True)
    back = sub.add_parser("backup-sqlite")
    back.add_argument("--output", required=True)
    purge = sub.add_parser("purge-soft-deleted")
    purge.add_argument("--organization-id", required=True)
    purge.add_argument("--before", required=True, help="ISO timestamp with timezone")
    purge.add_argument(
        "--execute", action="store_true", help="Required to actually delete anything"
    )
    sweep = sub.add_parser(
        "sweep-membership-lifecycle",
        help="Report (and with --execute repair) credential and membership drift",
    )
    sweep.add_argument(
        "--organization-id", default=None, help="Limit the sweep to one organization"
    )
    sweep.add_argument(
        "--execute", action="store_true", help="Required to actually change anything"
    )
    args = p.parse_args()
    if args.command == "backup-sqlite":
        print("Backup created:", backup_sqlite(Path(args.output)))
    elif args.command == "sweep-membership-lifecycle":
        report = sweep_membership_lifecycle(args.organization_id, execute=args.execute)
        print(
            f"{'Sweep executed' if args.execute else 'Dry run:'} {len(report.findings)} finding(s)"
        )
        for finding in report.findings:
            status = finding.action_taken or finding.severity
            blocked = f" [BLOCKED: {finding.blocked_reason}]" if finding.blocked_reason else ""
            print(f"- {finding.kind}: {finding.description} -> {status}{blocked}")
        if not args.execute:
            print("Nothing was changed. Re-run with --execute to apply the findings.")
    else:
        try:
            timestamp = datetime.fromisoformat(args.before)
            count = purge_soft_deleted(args.organization_id, timestamp, execute=args.execute)
        except (ValueError, OSError) as e:
            p.error(str(e))
        print(
            f"{'Purged' if args.execute else 'Dry run:'} {count} "
            f"soft-deleted assessments for given tenant"
        )
        if not args.execute:
            print("No records were deleted. Back up and obtain approval before --execute.")


if __name__ == "__main__":
    main()
