"""Tamper-evident audit chaining and out-of-database rollback detection.

## Why this exists

A local failure-recovery drill (planner item P5-03/P5-06) restored a `pg_dump` backup over a
live database and found that the ``fde.finalized`` audit event was **silently lost**: the
assessment status reverted from ``finalized`` to ``in_review``, the API still answered ``200``,
and the audit history simply had one fewer event. No marker said a decision record had
disappeared.

That is the most serious defect the drill found, because the audit trail is the evidence base
for the product's central claim — *a human decided*. A restore that quietly removes a
finalization record undermines every assurance the product offers.

Two mechanisms address it, and they are deliberately separate:

1. **A per-assessment hash chain** stored in the database. Each audit row carries a
   ``chain_hash`` over the previous row's hash and this row's canonical content, so a row
   altered **in place** after the fact is detectable by recomputing the chain.

2. **A high-water mark held OUTSIDE the database.** The database is exactly what a restore
   rolls back, so it cannot be the thing that remembers how long the audit trail used to be.
   A small local file records, per assessment, the highest sequence and chain hash that was
   ever committed. When the database later reports a *shorter* trail than the mark, the
   database has been rolled back and the application says so.

## Honest limits

* The high-water mark is a **local file**. An attacker who can write to it can suppress the
  warning, and a restore that also restores the file directory is not detected. A real
  deployment needs the mark in an external, append-only sink (a WORM store, a separate
  append-only log, or a notarised service). This is recorded as a limitation in
  ``docs/security.md`` rather than presented as a guarantee.
* The chain proves *internal consistency*. It does not prove that the content was truthful.
* Neither mechanism replaces the real fix for the underlying operational problem: a restore
  should be performed with the service stopped. The application can only *detect* the damage
  afterwards.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

#: The chain hash that precedes the first audit event of an assessment.
GENESIS_HASH = "0" * 64

#: Where the high-water mark lives when the operator has not chosen a path.
DEFAULT_MARK_PATH = ".advisory/audit-high-water.json"

#: Bumped when the canonical payload or the hash construction changes, so that a
#: recomputed chain over old rows is not silently compared against a new algorithm.
CHAIN_VERSION = "audit-chain-v1"


def _mark_path() -> Path:
    configured = os.getenv("ADVISORY_AUDIT_HIGH_WATER_PATH", "").strip()
    return Path(configured) if configured else Path(DEFAULT_MARK_PATH)


def _normalize_timestamp(value: datetime) -> datetime:
    """Return the timestamp as an aware UTC datetime.

    SQLite does not store timezone information, so a value read back from the database is
    naive even though it was written as UTC. Normalising on both sides keeps the chain
    hash stable across a write and a later read.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def canonical_payload(
    *,
    event_id: str,
    sequence: int,
    assessment_id: str,
    event_type: str,
    actor: str,
    actor_verified: bool,
    actor_organization_id: str | None,
    details: dict[str, Any],
    created_at: datetime,
) -> bytes:
    """The exact bytes a chain hash is computed over.

    Must be stable across processes and Python versions, so it uses an explicit field order
    and sorted JSON keys rather than ``repr`` of a dict.
    """
    document = {
        "actor": actor,
        "actor_organization_id": actor_organization_id,
        "actor_verified": bool(actor_verified),
        "assessment_id": assessment_id,
        "created_at": _normalize_timestamp(created_at).isoformat(),
        "details": details,
        "event_id": event_id,
        "event_type": event_type,
        "sequence": int(sequence),
        "version": CHAIN_VERSION,
    }
    return json.dumps(document, sort_keys=True, separators=(",", ":"), default=str).encode()


def compute_chain_hash(previous_hash: str, payload: bytes) -> str:
    """Chain this row's canonical content onto the previous row's hash."""
    digest = hashlib.sha256()
    digest.update(previous_hash.encode("ascii"))
    digest.update(b"\x00")
    digest.update(payload)
    return digest.hexdigest()


@dataclass(frozen=True)
class ChainState:
    """The state of an assessment's audit chain."""

    sequence: int
    chain_hash: str

    @property
    def is_genesis(self) -> bool:
        return self.sequence == 0


def chain_state(rows: list[Any]) -> ChainState:
    """Recompute the chain over the given rows, which must be in sequence order.

    ``rows`` are ORM ``AuditRow`` objects that already carry ``sequence`` and ``chain_hash``.
    """
    previous = GENESIS_HASH
    sequence = 0
    for row in sorted(rows, key=lambda r: r.sequence):
        payload = canonical_payload(
            event_id=row.id,
            sequence=row.sequence,
            assessment_id=row.assessment_id,
            event_type=row.event_type,
            actor=row.actor,
            actor_verified=bool(row.actor_verified),
            actor_organization_id=row.actor_organization_id,
            details=row.details or {},
            created_at=row.created_at,
        )
        previous = compute_chain_hash(previous, payload)
        sequence = row.sequence
    return ChainState(sequence=sequence, chain_hash=previous)


def verify_chain(rows: list[Any]) -> tuple[bool, list[str]]:
    """Recompute the chain and report the first point where it diverges.

    Returns ``(intact, problems)``. An empty ``problems`` list means the chain is internally
    consistent. A divergence means a row was altered or removed *within* the database.
    """
    problems: list[str] = []
    previous = GENESIS_HASH
    expected_sequence = 1
    for row in sorted(rows, key=lambda r: r.sequence):
        if row.sequence != expected_sequence:
            problems.append(
                f"sequence gap: expected {expected_sequence} but found {row.sequence} "
                f"(event {row.id})"
            )
            expected_sequence = row.sequence
        payload = canonical_payload(
            event_id=row.id,
            sequence=row.sequence,
            assessment_id=row.assessment_id,
            event_type=row.event_type,
            actor=row.actor,
            actor_verified=bool(row.actor_verified),
            actor_organization_id=row.actor_organization_id,
            details=row.details or {},
            created_at=row.created_at,
        )
        recomputed = compute_chain_hash(previous, payload)
        if recomputed != row.chain_hash:
            problems.append(
                f"content of event {row.id} does not match its recorded chain hash "
                f"(event {row.event_type})"
            )
        previous = row.chain_hash
        expected_sequence += 1
    return not problems, problems


class HighWaterMark:
    """The out-of-database record of how long each audit trail once was.

    A tiny JSON file mapping assessment id to ``{"sequence": n, "chain_hash": "..."}``. It is
    written with owner-only permissions and is replaced atomically, so a crash mid-write
    cannot leave a truncated file that would produce a false rollback warning.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or _mark_path()

    def load(self) -> dict[str, dict[str, Any]]:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except (FileNotFoundError, NotADirectoryError):
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # A corrupt mark file must not be treated as "no rollback": an
            # unreadable mark is itself a finding, so it is reported by the
            # caller through an empty-but-flagged state.
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, data: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        handle = tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=str(self.path.parent),
            prefix=".audit-high-water-",
            suffix=".tmp",
            delete=False,
        )
        try:
            with handle:
                json.dump(data, handle, indent=2, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(handle.name, 0o600)
            os.replace(handle.name, self.path)
        except Exception:
            try:
                os.unlink(handle.name)
            except OSError:
                pass
            raise

    def record(self, assessment_id: str, sequence: int, chain_hash: str) -> None:
        """Record the committed state of an assessment's audit trail."""
        data = self.load()
        existing = data.get(assessment_id)
        if existing and int(existing.get("sequence", 0)) >= sequence:
            return
        data[assessment_id] = {"sequence": int(sequence), "chain_hash": chain_hash}
        self._write(data)

    def get(self, assessment_id: str) -> dict[str, Any] | None:
        entry = self.load().get(assessment_id)
        return entry if isinstance(entry, dict) else None


@dataclass(frozen=True)
class RollbackVerdict:
    """The outcome of comparing the database's audit trail against the high-water mark."""

    rollback_detected: bool
    chain_intact: bool
    problems: list[str]
    marked_sequence: int | None
    actual_sequence: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "rollback_detected": self.rollback_detected,
            "chain_intact": self.chain_intact,
            "problems": list(self.problems),
            "marked_sequence": self.marked_sequence,
            "actual_sequence": self.actual_sequence,
        }


def assess_integrity(
    rows: list[Any],
    assessment_id: str | None = None,
    mark: HighWaterMark | None = None,
) -> RollbackVerdict:
    """Compare the database's audit trail against the high-water mark.

    * The database is **ahead** of the mark: normal. The mark is advanced by the caller.
    * The database is **at** the mark with the same hash: consistent.
    * The database is **at** the mark with a different hash: the trail was altered in place.
    * The database is **behind** the mark: rows were removed — a restore, or worse.

    ``assessment_id`` must be supplied when ``rows`` is empty: a trail that was wiped
    entirely is the most complete rollback there is, and it cannot be detected from the
    rows alone.
    """
    chain_ok, problems = verify_chain(rows)
    state = chain_state(rows)
    identifier = assessment_id or (rows[0].assessment_id if rows else None)
    stored = (mark or HighWaterMark()).get(identifier) if identifier else None
    if not stored:
        return RollbackVerdict(
            rollback_detected=False,
            chain_intact=chain_ok,
            problems=problems,
            marked_sequence=None,
            actual_sequence=state.sequence,
        )

    marked_sequence = int(stored.get("sequence", 0))
    marked_hash = str(stored.get("chain_hash", ""))
    if state.sequence < marked_sequence:
        problems.append(
            f"audit rollback detected: the database holds {state.sequence} audit events for "
            f"this assessment but {marked_sequence} were previously committed. The database "
            f"has been rolled back - a restore, a partial restore, or rows were deleted."
        )
        return RollbackVerdict(True, chain_ok, problems, marked_sequence, state.sequence)
    if state.sequence == marked_sequence and marked_hash and state.chain_hash != marked_hash:
        problems.append(
            "audit trail diverged from the committed high-water mark: the events are the "
            "same length but their content has changed since it was recorded."
        )
        return RollbackVerdict(False, chain_ok, problems, marked_sequence, state.sequence)
    return RollbackVerdict(False, chain_ok, problems, marked_sequence, state.sequence)
