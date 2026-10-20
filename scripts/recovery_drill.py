"""Local, disposable failure-recovery drill (P5-03 follow-up, planner P5-06).

The P5-03 PostgreSQL drill verified the migration chain, concurrency, the
atomic rate limiter and a `pg_dump`/`pg_restore` round trip. It explicitly
listed what stayed unverified:

    "failure-recovery drills (crash mid-write, disk full, network partition,
     restore into a running service, audit-history survival)"

This module exercises exactly those paths against a throwaway PostgreSQL
container and a real API process, and reports honestly what it found.

    python -m scripts.recovery_drill up       # start the disposable instances
    python -m scripts.recovery_drill run      # run every scenario
    python -m scripts.recovery_drill all      # up + run + down
    python -m scripts.recovery_drill down     # remove the disposable instances

**What this is:** evidence that the application takes *safe code paths* when a
dependency fails, a process dies mid-write, storage refuses a write or a
backup is restored underneath a live service.

**What this is NOT:** disaster-recovery readiness. A local drill cannot
simulate real hardware failure, real disk exhaustion on a managed volume, a
real network partition with a managed database, point-in-time recovery,
WAL shipping, replication failover or a multi-instance fleet. It measures no
RPO and no RTO. The production recovery gate stays open until an operator runs
a rehearsed restore against the real managed cluster with encrypted off-site
backups and a signed-off RPO/RTO.

Configuration (all optional, all local-only):

    RECOVERY_DRILL_IMAGE             default postgres:16-alpine
    RECOVERY_DRILL_CONTAINER         default aiadvisory-recovery-pg
    RECOVERY_DRILL_FULL_CONTAINER    default aiadvisory-recovery-pg-full
    RECOVERY_DRILL_PORT              default 15470
    RECOVERY_DRILL_FULL_PORT         default 15471
    RECOVERY_DRILL_API_PORT          default 18100
    RECOVERY_DRILL_FULL_API_PORT     default 18101
    RECOVERY_DRILL_USER              default aiadvisory
    RECOVERY_DRILL_DATABASE          default aiadvisory
    RECOVERY_DRILL_PASSWORD          default local_recovery_drill_only_pw
                                     (throwaway, local container only)
    RECOVERY_DRILL_KEEP              set to 1 to keep the containers and the dump
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# Configuration (all optional, all local-only)
# ---------------------------------------------------------------------------

IMAGE = os.getenv("RECOVERY_DRILL_IMAGE", "postgres:16-alpine")
CONTAINER = os.getenv("RECOVERY_DRILL_CONTAINER", "aiadvisory-recovery-pg")
FULL_CONTAINER = os.getenv("RECOVERY_DRILL_FULL_CONTAINER", "aiadvisory-recovery-pg-full")
PORT = int(os.getenv("RECOVERY_DRILL_PORT", "15470"))
FULL_PORT = int(os.getenv("RECOVERY_DRILL_FULL_PORT", "15471"))
API_PORT = int(os.getenv("RECOVERY_DRILL_API_PORT", "18100"))
FULL_API_PORT = int(os.getenv("RECOVERY_DRILL_FULL_API_PORT", "18101"))
USER = os.getenv("RECOVERY_DRILL_USER", "aiadvisory")
DATABASE = os.getenv("RECOVERY_DRILL_DATABASE", "aiadvisory")
PASSWORD = os.getenv("RECOVERY_DRILL_PASSWORD", "local_recovery_drill_only_pw")
KEEP = os.getenv("RECOVERY_DRILL_KEEP") == "1"

HEAD_REVISION = "0008_portfolio"

# ---------------------------------------------------------------------------
# Pure analysis layer: no Docker, no network, no database.
# This is what tests/test_recovery_drill.py exercises offline.
# ---------------------------------------------------------------------------

VERDICT_PASS = "PASS"
VERDICT_FAIL = "FAIL"
VERDICT_FINDING = "FINDING"

NOT_DISASTER_RECOVERY = (
    "This drill is NOT disaster-recovery readiness. It is a local, disposable "
    "rehearsal of failure code paths in the application. It proves nothing about "
    "hardware failure, real disk exhaustion, point-in-time recovery, WAL "
    "shipping, replication failover, backup integrity in the field, or the "
    "measured RPO/RTO of the production database."
)

#: Every scenario inherits these caveats.
COMMON_NOT_SIMULATED = (
    "A real incident on the managed cluster: instance loss, AZ failure, "
    "corrupted storage, an operator-triggered failover, or a regional outage.",
    "Any measured recovery objective. No RPO, RTO or recovery-time figure is "
    "produced or implied by this drill.",
    "The production deployment topology: one API process on one host against "
    "one local database. There is no load balancer, no replica set, no "
    "connection pooler, no rolling restart and no multi-instance fleet.",
)


@dataclass
class Verdict:
    """Outcome of one scenario analysis."""

    verdict: str
    findings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class ScenarioResult:
    """A named drill step with its verdict and its evidence."""

    name: str
    title: str
    verdict: str = VERDICT_PASS
    summary: str = ""
    observations: dict[str, Any] = field(default_factory=dict)
    findings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    simulated: list[str] = field(default_factory=list)
    not_simulated: list[str] = field(default_factory=list)


def classify_crash_mid_write(obs: dict[str, Any]) -> Verdict:
    """Classify the crash-mid-write scenario.

    The safe outcome is: the killed process leaves **no** partial write, **no**
    orphaned child row, **no** half-applied migration, and the service comes
    back and reads the pre-crash state correctly.
    """
    findings: list[str] = []
    notes: list[str] = []

    if not obs.get("write_in_flight"):
        findings.append(
            "The write never reached an in-flight, blocked state, so the scenario "
            "did not test what it claims to test."
        )
        return Verdict(VERDICT_FAIL, findings, notes)

    partial: list[str] = []
    for key, label in (
        ("assessment_title_after", "assessment title"),
        ("assessment_revision_after", "assessment revision"),
        ("workflow_count_after", "workflow row count"),
        ("audit_event_count_after", "audit event count"),
    ):
        before_key = key.replace("_after", "_before")
        if key in obs and before_key in obs and obs[key] != obs[before_key]:
            partial.append(f"{label} changed across the crash")
    if partial:
        findings.append("A partial write survived the crash: " + "; ".join(partial) + ".")

    orphans = obs.get("orphaned_rows") or []
    if orphans:
        findings.append(
            "Orphaned rows survived the crash: " + ", ".join(str(o) for o in orphans) + "."
        )

    if obs.get("alembic_version") != obs.get("expected_alembic_version"):
        findings.append(
            "The migration state is not at the expected revision after the crash "
            f"({obs.get('alembic_version')!r} != {obs.get('expected_alembic_version')!r}): "
            "a half-applied migration would mean a broken schema."
        )
    missing = obs.get("missing_tables") or []
    if missing:
        findings.append(
            "Tables are missing after the crash: " + ", ".join(str(t) for t in missing) + "."
        )

    if not obs.get("restart_ok"):
        findings.append("The API process did not come back up after the crash.")
    if not obs.get("read_after_restart_ok"):
        findings.append("The API did not serve the assessment correctly after the restart.")
    elif obs.get("read_after_restart_title") != obs.get("assessment_title_before"):
        findings.append(
            "The restarted API served a different title than before the crash "
            f"({obs.get('read_after_restart_title')!r} != {obs.get('assessment_title_before')!r})."
        )

    if obs.get("backend_reaped_naturally") is False:
        notes.append(
            "The database did not notice the dead client within "
            f"{obs.get('backend_reaped_after_seconds') or 60} s; the drill had to "
            "terminate the backend explicitly to release its locks. The transaction "
            "was still rolled back, but a crashed process can keep its row and table "
            "locks — and therefore block other writers — for as long as the server "
            "takes to notice."
        )
    elif obs.get("backend_reaped_naturally"):
        notes.append(
            "The database noticed the dead client after "
            f"{obs.get('backend_reaped_after_seconds')} s and rolled the transaction back."
        )
    if obs.get("blocked_statement"):
        notes.append(f"The in-flight write was blocked on: {obs['blocked_statement']}")

    if findings:
        return Verdict(VERDICT_FAIL, findings, notes)
    return Verdict(
        VERDICT_PASS,
        findings,
        notes + ["The crashed transaction was rolled back atomically by the database."],
    )


def classify_restore_into_running_service(obs: dict[str, Any]) -> Verdict:
    """Classify the restore-into-a-running-service scenario.

    The safe outcome is: the service does not serve rows the restore removed,
    does not leak a stack trace or a credential, and recovers on its own once
    the restore finishes. Requests that overlap the restore are expected to
    fail, but they must fail with an honest error.
    """
    findings: list[str] = []
    notes: list[str] = []

    if obs.get("post_restore_status") != 200:
        findings.append(
            "After the restore the API did not return 200 "
            f"(status={obs.get('post_restore_status')!r}, "
            f"body={_short(obs.get('post_restore_body'))!r}); "
            "the service did not recover from the restore on its own."
        )
    if obs.get("served_removed_rows"):
        findings.append(
            "The service kept serving rows that the restore removed: "
            + ", ".join(str(r) for r in obs["served_removed_rows"])
            + ". That is stale data served after a restore."
        )
    if not obs.get("post_restore_matches_backup", True):
        findings.append(
            "After the restore the API did not serve the restored (pre-backup) row set."
        )
    if obs.get("stack_trace_in_body"):
        findings.append("The error response body contained a stack trace.")
    if obs.get("credentials_in_log"):
        findings.append("The database credentials appeared in the API server log.")

    statuses = obs.get("statuses_during_restore") or {}
    errors = {code: count for code, count in statuses.items() if not code.startswith("2")}
    bodies = obs.get("error_bodies_during_restore") or []
    if errors:
        notes.append(
            "Requests overlapping the restore failed: "
            + ", ".join(f"HTTP {code} x{count}" for code, count in sorted(errors.items()))
            + "."
        )
        for body in bodies[:5]:
            notes.append(f"  error body: {_short(body)}")
        unsafe_5xx = {
            code: count for code, count in errors.items() if code.startswith("5") and code != "503"
        }
        if unsafe_5xx:
            findings.append(
                "Requests that overlapped the restore returned 5xx instead of a "
                "retryable/503 response; the service has no restore-in-progress "
                "signal for clients."
            )
        if errors.get("503"):
            notes.append(
                "The 503 responses were the rate limiter failing closed "
                "('Rate limiter unavailable') while its table was being rebuilt."
            )
        misleading_auth = [b for b in bodies if b.startswith("HTTP 401")]
        if misleading_auth:
            findings.append(
                "A request that overlapped the restore was rejected with HTTP 401 "
                "for a valid credential, because the credential table was dropped "
                "and recreated underneath the running service. A restore looks "
                "like an authentication failure to the client."
            )
    else:
        notes.append("No request failed while the restore was running.")

    if findings:
        return Verdict(VERDICT_FINDING if _only_soft(findings) else VERDICT_FAIL, findings, notes)
    return Verdict(VERDICT_PASS, findings, notes)


def classify_audit_history_survival(obs: dict[str, Any]) -> Verdict:
    """Classify the audit-history survival scenario.

    The audit trail is the evidence base for "a human decided". The safe
    outcome is: a crash does not change the trail, a restore leaves the trail
    either intact or *explicitly* reported as rewound, and the restored trail
    is never silently truncated in the middle.
    """
    findings: list[str] = []
    notes: list[str] = []

    lost_after_crash = obs.get("lost_after_crash") or []
    if lost_after_crash:
        findings.append(
            f"The crash changed the audit trail: {len(lost_after_crash)} audit event(s) "
            "disappeared or were altered. An append-only evidence base must not "
            "change when a process dies."
        )
    if not obs.get("trail_is_prefix_after_restore", True):
        findings.append(
            "The restored audit trail is NOT a prefix of the pre-restore trail: "
            "events are missing from the middle of the history. That is a silent "
            "truncation of the evidence base."
        )
    inconsistencies = obs.get("trail_inconsistencies") or []
    if inconsistencies:
        findings.append(
            "The restored audit trail is internally inconsistent: "
            + "; ".join(str(i) for i in inconsistencies)
            + "."
        )
    if obs.get("api_status_after_restore") not in (200, None):
        findings.append(
            "The API could not serve the audit history after the restore "
            f"(status={obs.get('api_status_after_restore')!r})."
        )

    lost_after_restore = obs.get("lost_after_restore") or []
    if lost_after_restore:
        affected = obs.get("affected_assessments") or []
        notes.append(
            f"LOUD FINDING: the restore rewound the audit history of "
            f"{len(affected)} assessment(s) by {len(lost_after_restore)} event(s). "
            "The API keeps returning HTTP 200 with the shortened trail and there "
            "is no marker, counter or signal that the history was rewound: a "
            "reader cannot tell a rewound trail from a trail that never had "
            "those events."
        )
        notes.append("Lost audit events:")
        for event in lost_after_restore[:20]:
            notes.append(
                f"  {event.get('assessment_id')} {event.get('event_type')} "
                f"actor={event.get('actor')} at {event.get('created_at')}"
            )
        findings.append(
            "The audit trail is not append-only across a restore: "
            f"{len(lost_after_restore)} event(s) were silently lost."
        )
        if obs.get("decision_evidence_lost"):
            findings.append(
                "The lost events include the human final decision "
                "('fde.finalized'). The evidence that a human decided is gone, "
                "the assessment is silently back in review, and the API reports "
                "HTTP 200 with no indication that a decision record was lost."
            )
    else:
        notes.append("The audit trail was fully intact after the restore.")

    if findings:
        return Verdict(VERDICT_FINDING if _only_soft(findings) else VERDICT_FAIL, findings, notes)
    return Verdict(VERDICT_PASS, findings, notes)


def classify_dependency_failure(obs: dict[str, Any]) -> Verdict:
    """Classify the "database unreachable / network partition" scenario.

    The safe outcome is: the service fails closed, never serves stale or
    partial data, never answers 200 with a broken dependency, never leaks a
    stack trace or a credential, and ideally answers 503 rather than 500.
    """
    findings: list[str] = []
    notes: list[str] = []
    cases = obs.get("cases") or {}
    for name, case in cases.items():
        verdict = classify_dependency_case(case)
        for note in verdict.notes:
            notes.append(f"[{name}] {note}")
        if verdict.verdict == VERDICT_FAIL:
            findings.extend(f"[{name}] {f}" for f in verdict.findings)
        elif verdict.verdict == VERDICT_FINDING:
            findings.extend(f"[{name}] {f}" for f in verdict.findings)
    if not findings:
        return Verdict(VERDICT_PASS, findings, notes)
    return Verdict(VERDICT_FINDING if _only_soft(findings) else VERDICT_FAIL, findings, notes)


def classify_dependency_case(case: dict[str, Any]) -> Verdict:
    """Classify one dependency-failure observation."""
    findings: list[str] = []
    notes: list[str] = []
    status = case.get("status")

    if case.get("served_stale_data"):
        findings.append("The service served data while its database was unusable.")
    if case.get("stack_trace_in_body"):
        findings.append("The error response body contained a stack trace.")
    if case.get("credentials_in_log"):
        findings.append("The database credentials appeared in the API server log.")
    if status == 200:
        findings.append(
            "The service answered HTTP 200 while the database was unusable (a misleading success)."
        )
    elif status is None and case.get("hung"):
        notes.append(
            "The request never completed: it hung until the client timeout. No "
            "connect or statement timeout is configured, so a silently dropped "
            "connection wedges the request instead of failing it."
        )
        findings.append(
            "A partitioned database makes the request hang indefinitely instead of "
            "failing closed with a bounded, safe error."
        )
    elif status is None:
        notes.append(f"The request failed at the transport level: {case.get('error')!r}.")
        findings.append(
            "The service did not answer with a safe HTTP status when the database was unreachable."
        )
    elif status == 503:
        notes.append("The service failed closed with HTTP 503.")
    elif 500 <= int(status) < 600:
        notes.append(f"The service failed closed with HTTP {status}.")
        findings.append(
            f"A dependency failure surfaces as HTTP {status} rather than a "
            "dependency-aware 503; a 500 is reported as an application bug "
            "rather than an unavailable dependency."
        )
    else:
        notes.append(f"The service answered HTTP {status}.")
        findings.append(f"The service answered HTTP {status} while the database was unusable.")
    if not case.get("security_headers_present", True):
        notes.append(
            "The error response did not carry the standard security headers "
            "(X-Request-ID, Cache-Control: no-store, X-Content-Type-Options)."
        )
    return Verdict(
        VERDICT_FAIL
        if findings and _has_hard_finding(findings)
        else (VERDICT_FINDING if findings else VERDICT_PASS),
        findings,
        notes,
    )


def classify_disk_full(obs: dict[str, Any]) -> Verdict:
    """Classify the storage-exhaustion (disk full) scenario.

    The safe outcome is: the write fails, nothing partial is committed, the
    database stays readable and consistent, and the error is an honest
    storage/dependency error rather than a misleading success.
    """
    findings: list[str] = []
    notes: list[str] = []
    status = obs.get("write_status")
    write_succeeded = status in (200, 201)

    if write_succeeded and obs.get("storage_full"):
        findings.append(
            "The storage was full but the write still succeeded: PostgreSQL had "
            "pre-allocated the space it needed, so the write-failure path was not "
            "exercised by this run. The drill cannot conclude anything about the "
            "error handling of a failed write."
        )
    if not write_succeeded and not obs.get("storage_full"):
        findings.append(
            "The storage was never actually full, so the scenario did not test what "
            "it claims to test."
        )
    if not write_succeeded:
        partial = obs.get("partial_rows") or []
        if partial:
            findings.append(
                "The failed write left partial rows behind: "
                + ", ".join(str(r) for r in partial)
                + "."
            )
        else:
            notes.append(
                "The failed write committed nothing: the row counts match the "
                f"{obs.get('writes_succeeded_before_failure')} write(s) that had "
                "succeeded before it, and the audit trail grew by the same amount."
            )
    if obs.get("data_readable_after_failure") is False:
        findings.append("The database was no longer readable after the storage failure.")
    if obs.get("data_intact_after_restart") is False:
        findings.append("Data was lost or corrupted after restarting the database.")
    if obs.get("stack_trace_in_body"):
        findings.append("The error response body contained a stack trace.")
    if obs.get("credentials_in_log"):
        findings.append("The database credentials appeared in the API server log.")
    if obs.get("security_headers_present") is False:
        findings.append(
            "The error response did not carry the standard security headers "
            "(X-Request-ID, Cache-Control: no-store, X-Content-Type-Options): the "
            "unhandled exception bypasses the request middleware."
        )
    if status is not None and 500 <= int(status) < 600 and status != 503:
        notes.append(f"The storage failure surfaced as HTTP {status}.")
        findings.append(
            f"A storage failure surfaces as HTTP {status} rather than a dependency-aware 503."
        )
    elif status is None:
        notes.append(f"The write failed at the transport level: {obs.get('error')!r}.")
    elif write_succeeded:
        notes.append(f"The write was accepted by the database (HTTP {status}).")
    if obs.get("wal_switch_failed_with_enospc"):
        notes.append(
            "The database could not create the next WAL segment: the storage was "
            "genuinely exhausted (ENOSPC), not merely 'full'."
        )
    if obs.get("storage_full"):
        notes.append("The tablespace was genuinely full: the write failed with ENOSPC.")
    if obs.get("database_crashed"):
        notes.append(
            "The database server itself stopped when the storage filled up "
            "(the write path could not log its own transaction). This is the "
            "realistic outcome of a genuinely full volume."
        )
    if obs.get("write_after_recovery_status") in (200, 201):
        notes.append("After the space was freed the service accepted writes again.")
    if not findings:
        return Verdict(VERDICT_PASS, findings, notes)
    return Verdict(VERDICT_FINDING if _only_soft(findings) else VERDICT_FAIL, findings, notes)


def _only_soft(findings: list[str]) -> bool:
    """True when every finding is a gap in behaviour, not a corruption."""
    hard_markers = (
        "partial write survived",
        "orphaned rows",
        "half-applied migration",
        "tables are missing",
        "served data while",
        "misleading success",
        "stack trace",
        "credentials appeared",
        "partial rows behind",
        "was no longer readable",
        "lost or corrupted",
        "not a prefix",
        "internally inconsistent",
        "did not come back up",
        "did not serve the assessment",
        "served a different title",
        "answered http 200",
        "did not return 200",
        "kept serving rows",
        "changed the audit trail",
        "did not serve the restored",
    )
    lowered = [finding.lower() for finding in findings]
    return not any(marker in finding for marker in hard_markers for finding in lowered)


def _has_hard_finding(findings: list[str]) -> bool:
    return not _only_soft(findings)


def _short(value: Any, limit: int = 200) -> str:
    text = "" if value is None else str(value)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + "..."


def trail_signature(events: list[dict[str, Any]]) -> list[str]:
    """Stable identity of an audit trail: the event ids in order."""
    return [str(event.get("id")) for event in events]


def trail_is_prefix(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> bool:
    """True when ``after`` is exactly the head of ``before`` (no mid-trail loss)."""
    before_ids = trail_signature(before)
    after_ids = trail_signature(after)
    return after_ids == before_ids[: len(after_ids)]


def trail_missing_events(
    before: list[dict[str, Any]], after: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Events present in ``before`` but absent from ``after``."""
    after_ids = set(trail_signature(after))
    return [event for event in before if str(event.get("id")) not in after_ids]


def audit_trail_inconsistencies(events: list[dict[str, Any]]) -> list[str]:
    """Check the evidence chain of an audit trail.

    A trail is inconsistent when the ordering is not monotonic, when it does
    not start with the creation event, or when a human final decision appears
    without a preceding recorded review.
    """
    problems: list[str] = []
    if not events:
        return problems
    timestamps = [str(event.get("created_at")) for event in events]
    if timestamps != sorted(timestamps):
        problems.append("audit events are not in non-decreasing creation order")
    if events[0].get("event_type") != "assessment.created":
        problems.append(
            f"the trail starts with {events[0].get('event_type')!r} instead of 'assessment.created'"
        )
    seen_review = False
    for event in events:
        event_type = event.get("event_type")
        if event_type == "fde.review_recorded":
            seen_review = True
        if event_type == "fde.finalized" and not seen_review:
            problems.append("a final decision ('fde.finalized') has no preceding recorded review")
            break
    return problems


def render_report(results: list[ScenarioResult], meta: dict[str, Any] | None = None) -> str:
    """Render the human-readable drill report."""
    meta = meta or {}
    lines: list[str] = []
    lines.append("=" * 78)
    lines.append("AI Opportunity Advisor — LOCAL failure-recovery drill")
    lines.append(NOT_DISASTER_RECOVERY)
    lines.append("=" * 78)
    for key in ("started", "finished", "environment"):
        if meta.get(key):
            lines.append(f"{key}: {meta[key]}")
    lines.append("")

    for result in results:
        lines.append("-" * 78)
        lines.append(f"{result.name}: {result.title}")
        lines.append(f"  VERDICT: {result.verdict}")
        if result.summary:
            lines.append(f"  {result.summary}")
        if result.observations:
            lines.append("  Observed:")
            for key, value in result.observations.items():
                lines.append(f"    {key} = {value}")
        if result.notes:
            lines.append("  Notes:")
            for note in result.notes:
                lines.append(f"    - {note}")
        if result.findings:
            lines.append("  Findings:")
            for finding in result.findings:
                lines.append(f"    ! {finding}")
        if result.simulated:
            lines.append("  What was actually simulated:")
            for item in result.simulated:
                lines.append(f"    * {item}")
        if result.not_simulated:
            lines.append("  What was NOT simulated:")
            for item in result.not_simulated:
                lines.append(f"    - {item}")
        lines.append("")

    lines.append("-" * 78)
    lines.append("Summary")
    lines.append(f"  {'scenario':28s} {'verdict':9s} summary")
    for result in results:
        summary = " ".join(result.summary.split())
        if len(summary) > 60:
            summary = summary[:57] + "..."
        lines.append(f"  {result.name:28s} {result.verdict:9s} {summary}")
    failed = [r.name for r in results if r.verdict == VERDICT_FAIL]
    findings = [r.name for r in results if r.verdict == VERDICT_FINDING]
    lines.append("")
    if failed:
        lines.append(f"FAILED scenarios (unsafe behaviour observed): {', '.join(failed)}")
    else:
        lines.append("No scenario produced corrupting or misleading behaviour.")
    if findings:
        lines.append(f"Scenarios with findings to report: {', '.join(findings)}")
    lines.append("")
    lines.append("Common limits of this drill (applies to every scenario):")
    for item in COMMON_NOT_SIMULATED:
        lines.append(f"  - {item}")
    lines.append("")
    lines.append(NOT_DISASTER_RECOVERY)
    lines.append("=" * 78)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Infrastructure: docker, HTTP, API process, database access
# ---------------------------------------------------------------------------


def _run(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    result = subprocess.run(command, check=False, capture_output=True, text=True, **kwargs)
    if result.returncode != 0:
        sys.stderr.write((result.stdout or "") + (result.stderr or ""))
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(command)}")
    return result


def _try_run(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(command, check=False, capture_output=True, text=True, **kwargs)


def _run_to_file(command: list[str], handle, **kwargs) -> subprocess.CompletedProcess:
    """Run a command whose stdout is a file (a dump) instead of a pipe."""
    result = subprocess.run(
        command, check=False, stdout=handle, stderr=subprocess.PIPE, text=True, **kwargs
    )
    if result.returncode != 0:
        sys.stderr.write(result.stderr or "")
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(command)}")
    return result


def _run_with_stdin(command: list[str], handle, **kwargs) -> subprocess.CompletedProcess:
    """Run a command that reads its input from a file (a restore)."""
    result = subprocess.run(
        command,
        check=False,
        stdin=handle,
        capture_output=True,
        text=True,
        **kwargs,
    )
    if result.returncode != 0:
        sys.stderr.write((result.stdout or "") + (result.stderr or ""))
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(command)}")
    return result


def _docker(*args: str) -> subprocess.CompletedProcess:
    return _run(["docker", *args])


def database_url(port: int = PORT, database: str = DATABASE) -> str:
    """The URL the *application* uses: no client-side timeout, as in production."""
    return f"postgresql+psycopg://{USER}:{PASSWORD}@127.0.0.1:{port}/{database}"


def drill_url(port: int = PORT, database: str = DATABASE) -> str:
    """The URL the *drill* uses for its own queries: bounded, so it cannot hang.

    The application's own URL deliberately keeps no connect/statement timeout;
    the drill measures that behaviour instead of hiding it.
    """
    return (
        f"postgresql+psycopg://{USER}:{PASSWORD}@127.0.0.1:{port}/{database}"
        "?connect_timeout=5&options=-c%20statement_timeout%3D10000"
    )


def redacted_url(port: int = PORT, database: str = DATABASE) -> str:
    return f"postgresql+psycopg://{USER}:***@127.0.0.1:{port}/{database}"


def _container_exists(name: str) -> bool:
    result = _try_run(
        ["docker", "ps", "-a", "--filter", f"name=^{name}$", "--format", "{{.Names}}"]
    )
    return name in result.stdout.split()


def _remove_container(name: str) -> None:
    if _container_exists(name):
        _docker("rm", "-f", name)
        print(f"removed container {name}")


def _wait_container_ready(name: str, timeout: int = 90) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = _try_run(["docker", "exec", name, "pg_isready", "-U", USER, "-d", "postgres"])
        if result.returncode == 0:
            return
        time.sleep(1)
    raise RuntimeError(f"container {name} did not become ready within {timeout}s")


def _psql(container: str, sql: str, database: str = "postgres") -> subprocess.CompletedProcess:
    return _docker("exec", container, "psql", "-U", USER, "-d", database, "-c", sql)


def _alembic(url: str, *args: str) -> None:
    env = dict(os.environ)
    env["DATABASE_URL"] = url
    result = _try_run([sys.executable, "-m", "alembic", *args], cwd=REPO_ROOT, env=env)
    if result.returncode != 0:
        sys.stderr.write(result.stdout + result.stderr)
        raise RuntimeError(f"alembic {' '.join(args)} failed")


def http_call(
    url: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Perform one HTTP request and never raise for an HTTP error status."""
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read().decode("utf-8", "replace")
            status = response.status
            response_headers = dict(response.headers)
            error = None
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode("utf-8", "replace")
        status = exc.code
        response_headers = dict(exc.headers)
        error = None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        payload = ""
        status = None
        response_headers = {}
        error = f"{type(exc).__name__}: {reason}"
    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
    hung = status is None and elapsed_ms >= timeout * 1000 * 0.95
    return {
        "status": status,
        "body": payload,
        "headers": response_headers,
        "error": error,
        "elapsed_ms": elapsed_ms,
        "hung": hung,
    }


def _json_body(response: dict[str, Any]) -> dict[str, Any]:
    try:
        parsed = json.loads(response["body"])
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_payload(response: dict[str, Any]) -> Any:
    """Parse any JSON body (object or array); None when it is not JSON."""
    try:
        return json.loads(response["body"])
    except (TypeError, ValueError):
        return None


def _json_items(response: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse a JSON array body into a list of objects."""
    payload = _json_payload(response)
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict)]


class ApiProcess:
    """A real API server process that the drill can kill."""

    def __init__(self, url: str, port: int, log_path: Path) -> None:
        self.url = url
        self.port = port
        self.log_path = log_path
        self.process: subprocess.Popen | None = None
        self.base = f"http://127.0.0.1:{port}"

    def start(self) -> None:
        env = dict(os.environ)
        env.update(
            {
                "DATABASE_URL": self.url,
                "ADVISORY_DEPLOYMENT_MODE": "local",
                "ADVISORY_DEMO_MODE": "0",
                "ADVISORY_REQUESTS_PER_MINUTE": "10000",
                "ADVISORY_REVIEW_TOKEN": "local_recovery_drill_review_token",
                "PYTHONPATH": str(REPO_ROOT),
                "PYTHONUNBUFFERED": "1",
            }
        )
        handle = self.log_path.open("ab")
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
                "--log-level",
                "info",
            ],
            cwd=REPO_ROOT,
            env=env,
            stdout=handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    def alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def wait_ready(self, timeout: int = 60) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            response = http_call(f"{self.base}/ready", timeout=3.0)
            if response["status"] == 200:
                return True
            if not self.alive():
                return False
            time.sleep(0.3)
        return False

    def kill(self) -> None:
        """SIGKILL the whole process group: an ungraceful crash, no cleanup."""
        if self.process is None:
            return
        try:
            os.killpg(os.getpgid(self.process.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            try:
                self.process.kill()
            except ProcessLookupError:
                pass
        try:
            self.process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            pass

    def stop(self) -> None:
        if self.process is None:
            return
        if self.process.poll() is None:
            try:
                os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                self.process.terminate()
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.kill()
        try:
            self.process.stdout and self.process.stdout.close()
        except Exception:  # pragma: no cover - defensive
            pass

    def log_text(self) -> str:
        try:
            return self.log_path.read_text(errors="replace")
        except OSError:
            return ""


def log_contains_secret(text: str, secret: str) -> bool:
    return bool(secret) and secret in text


@dataclass
class DrillContext:
    """Shared state for the drill scenarios."""

    url: str
    """The application URL: no client-side timeout, exactly as an operator sets it."""

    db_url: str
    """The drill's own URL: bounded, so the drill can never hang on a query."""

    api: ApiProcess
    headers: dict[str, str] = field(default_factory=dict)
    token: str = ""
    organization_id: str = ""
    workdir: Path = field(default_factory=lambda: Path(tempfile.gettempdir()))


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------


def _engine(url: str):
    from sqlalchemy import create_engine

    return create_engine(url, pool_pre_ping=True)


def _query(url: str, sql: str, **params) -> list[tuple]:
    from sqlalchemy import text

    engine = _engine(url)
    try:
        with engine.connect() as conn:
            return list(conn.execute(text(sql), params).all())
    finally:
        engine.dispose()


def _scalar(url: str, sql: str, **params):
    rows = _query(url, sql, **params)
    return rows[0][0] if rows else None


def _assessment_state(url: str, assessment_id: str) -> dict[str, Any]:
    rows = _query(
        url,
        "select title, revision, status from assessments where id = :id",
        id=assessment_id,
    )
    title, revision, status = rows[0] if rows else (None, None, None)
    workflow_count = _scalar(
        url, "select count(*) from workflows where assessment_id = :id", id=assessment_id
    )
    audit = _query(
        url,
        "select id, event_type, actor, created_at from audit_events "
        "where assessment_id = :id order by created_at, id",
        id=assessment_id,
    )
    return {
        "title": title,
        "revision": revision,
        "status": status,
        "workflow_count": int(workflow_count or 0),
        "audit_event_count": len(audit),
        "audit_events": [
            {
                "id": row[0],
                "event_type": row[1],
                "actor": row[2],
                "created_at": row[3].isoformat() if hasattr(row[3], "isoformat") else str(row[3]),
                "assessment_id": assessment_id,
            }
            for row in audit
        ],
    }


#: Child table -> (foreign key column, parent table) used for orphan checks.
ORPHAN_CHECKS: tuple[tuple[str, str, str], ...] = (
    ("workflows", "assessment_id", "assessments"),
    ("workflow_steps", "workflow_id", "workflows"),
    ("tasks", "step_id", "workflow_steps"),
    ("answers", "assessment_id", "assessments"),
    ("reviews", "assessment_id", "assessments"),
    ("audit_events", "assessment_id", "assessments"),
    ("ai_runs", "assessment_id", "assessments"),
    ("assessment_comments", "assessment_id", "assessments"),
    ("approval_votes", "assessment_id", "assessments"),
    ("reviewer_assignments", "assessment_id", "assessments"),
    ("opportunity_profiles", "assessment_id", "assessments"),
    ("opportunity_evidence", "assessment_id", "assessments"),
    ("value_scenarios", "assessment_id", "assessments"),
    ("poc_plans", "assessment_id", "assessments"),
    ("poc_observations", "poc_id", "poc_plans"),
    ("inbox_notifications", "assessment_id", "assessments"),
    ("api_credentials", "organization_id", "organizations"),
    ("assessments", "organization_id", "organizations"),
)


def orphaned_rows(url: str) -> list[str]:
    """Return every foreign key whose parent row is missing."""
    problems: list[str] = []
    for child, column, parent in ORPHAN_CHECKS:
        count = _scalar(
            url,
            f"select count(*) from {child} c left join {parent} p "
            f"on c.{column} = p.id where c.{column} is not null and p.id is null",
        )
        if count:
            problems.append(f"{child}.{column} -> {parent}.id ({count} orphaned row(s))")
    return problems


def table_names(url: str) -> list[str]:
    rows = _query(
        url,
        "select table_name from information_schema.tables "
        "where table_schema = 'public' and table_type = 'BASE TABLE'",
    )
    return sorted(row[0] for row in rows)


def expected_table_names() -> list[str]:
    from app.db.models import Base

    return sorted(Base.metadata.tables)


def alembic_version(url: str) -> str | None:
    return _scalar(url, "select version_num from alembic_version")


def row_counts(url: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in (
        "assessments",
        "workflows",
        "workflow_steps",
        "tasks",
        "answers",
        "audit_events",
        "reviews",
    ):
        counts[table] = int(_scalar(url, f"select count(*) from {table}") or 0)
    return counts


class TableLock:
    """Hold an EXCLUSIVE table lock from a separate session.

    This is what makes a write genuinely *in flight*: the API's INSERT into
    the locked table blocks inside PostgreSQL while its earlier statements in
    the same transaction are already applied (but uncommitted).
    """

    def __init__(self, url: str, table: str) -> None:
        self.url = url
        self.table = table
        self._engine = _engine(url)
        self._acquired = threading.Event()
        self._release = threading.Event()
        self._thread: threading.Thread | None = None
        self.error: Exception | None = None

    def _run(self) -> None:
        from sqlalchemy import text

        try:
            with self._engine.connect() as conn:
                conn.execute(text(f"LOCK TABLE {self.table} IN EXCLUSIVE MODE"))
                self._acquired.set()
                self._release.wait(timeout=180)
                conn.rollback()
        except Exception as exc:  # pragma: no cover - defensive
            self.error = exc
            self._acquired.set()

    def acquire(self, timeout: float = 30.0) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        if not self._acquired.wait(timeout=timeout):
            raise RuntimeError(f"could not take the {self.table} lock in {timeout}s")
        if self.error is not None:
            raise self.error

    def close(self) -> None:
        self._release.set()
        if self._thread is not None:
            self._thread.join(timeout=30)
        self._engine.dispose()


def wait_for_lock_wait(url: str, timeout: float = 30.0) -> dict[str, Any] | None:
    """Return the backend that is waiting on a lock, with its statement."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        rows = _query(
            url,
            "select pid, left(query, 200) from pg_stat_activity "
            "where datname = :db and wait_event_type = 'Lock' "
            "and pid <> pg_backend_pid() order by query_start",
            db=DATABASE,
        )
        if rows:
            return {"pid": rows[0][0], "query": str(rows[0][1])}
        time.sleep(0.2)
    return None


def backend_alive(url: str, pid: int) -> bool:
    """True while the given backend is still connected to the database."""
    rows = _query(
        url,
        "select count(*) from pg_stat_activity where pid = :pid",
        pid=pid,
    )
    return bool(rows and rows[0][0])


def backends_for_database(url: str) -> list[dict[str, Any]]:
    """Every backend currently connected to the drill database."""
    rows = _query(
        url,
        "select pid, state, wait_event_type, left(query, 120) from pg_stat_activity "
        "where datname = :db and pid <> pg_backend_pid()",
        db=DATABASE,
    )
    return [
        {"pid": row[0], "state": row[1], "wait_event_type": row[2], "query": row[3]} for row in rows
    ]


# ---------------------------------------------------------------------------
# Tenant provisioning
# ---------------------------------------------------------------------------


def provision_tenant(url: str) -> tuple[str, str, dict[str, str]]:
    """Create a throwaway organization and API credential. Returns the token."""
    from datetime import UTC, datetime, timedelta

    from sqlalchemy.orm import Session

    from app.db.models import ApiCredentialRow, OrganizationRow
    from app.security import digest_token, make_token

    token = make_token()
    engine = _engine(url)
    try:
        with Session(engine) as db:
            organization = OrganizationRow(name=f"recovery-drill-{token[-10:]}")
            db.add(organization)
            db.flush()
            db.add(
                ApiCredentialRow(
                    organization_id=organization.id,
                    subject="drill-analyst",
                    role="admin",
                    token_hash=digest_token(token),
                    expires_at=datetime.now(UTC) + timedelta(days=1),
                )
            )
            db.commit()
            organization_id = organization.id
    finally:
        engine.dispose()
    return token, organization_id, {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Container lifecycle
# ---------------------------------------------------------------------------


def start_container(
    name: str,
    port: int,
    tmpfs_mb: int | None = None,
    tablespace_mb: int | None = None,
    tablespace_path: str = "/drillspace",
) -> None:
    if _container_exists(name):
        _docker("rm", "-f", name)
    command = [
        "docker",
        "run",
        "--name",
        name,
        "-e",
        f"POSTGRES_PASSWORD={PASSWORD}",
        "-e",
        f"POSTGRES_USER={USER}",
        "-e",
        f"POSTGRES_DB={DATABASE}",
        "-p",
        f"{port}:5432",
        "-d",
    ]
    if tmpfs_mb is not None:
        command += ["--tmpfs", f"/var/lib/postgresql/data:size={tmpfs_mb}m"]
    if tablespace_mb is not None:
        command += ["--tmpfs", f"{tablespace_path}:size={tablespace_mb}m"]
    command.append(IMAGE)
    last_error = ""
    for _attempt in range(3):
        result = _try_run(command)
        if result.returncode == 0:
            _wait_container_ready(name)
            return
        last_error = (result.stderr or "") + (result.stdout or "")
        # A container that was left behind by an interrupted run can still hold
        # the published port for a moment; remove it and try again.
        _try_run(["docker", "rm", "-f", name])
        time.sleep(3)
    raise RuntimeError(f"could not start {name}: {last_error.strip()}")


def up() -> None:
    """Start the disposable instances and migrate them."""
    start_container(CONTAINER, PORT)
    _psql(CONTAINER, f'DROP DATABASE IF EXISTS "{DATABASE}"')
    _psql(CONTAINER, f'CREATE DATABASE "{DATABASE}"')
    print(f"started {CONTAINER} ({IMAGE}) on 127.0.0.1:{PORT} -> {redacted_url()}")
    _alembic(drill_url(), "upgrade", "head")
    print("migration chain applied")


def down() -> None:
    """Remove every container and file the drill created."""
    for container in (CONTAINER, FULL_CONTAINER):
        _remove_container(container)


def teardown(workdir: Path | None = None) -> None:
    down()
    if workdir is not None and not KEEP:
        for path in sorted(workdir.glob("*"), reverse=True):
            try:
                if path.is_dir():
                    path.rmdir()
                else:
                    path.unlink()
            except OSError:
                pass
        try:
            workdir.rmdir()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------

SIMULATED_CRASH = (
    "A real API process was SIGKILLed (kill -9, no cleanup, no graceful "
    "shutdown) while a multi-row write transaction was blocked inside "
    "PostgreSQL.",
    "The database rolled the in-flight transaction back, exactly as it would "
    "after a client process died.",
)
NOT_SIMULATED_CRASH = (
    "A real power loss, kernel panic, OOM kill or node failure. The storage "
    "stack underneath PostgreSQL was never interrupted, so no torn write, "
    "no lost fsync and no storage-level crash consistency was exercised.",
    "A crash of the PostgreSQL server itself. Only the *client* was killed; "
    "the database never had to replay its own WAL after an unclean stop.",
    "Crash recovery of the API process: no supervisor, no restart policy, no "
    "readiness gate and no rolling replacement of a crashed replica.",
)


def scenario_crash_mid_write(ctx: DrillContext) -> ScenarioResult:
    """Kill the API while a write is in flight and check the database."""
    print("== scenario: crash mid-write ==")
    obs: dict[str, Any] = {}

    created = http_call(
        f"{ctx.api.base}/api/v1/assessments",
        "POST",
        ctx.headers,
        {"title": "Recovery drill crash target"},
    )
    if created["status"] != 201:
        raise RuntimeError(f"could not create the crash target: {created}")
    assessment = _json_body(created)
    assessment_id = str(assessment["id"])
    obs["assessment_id"] = assessment_id

    before = _assessment_state(ctx.db_url, assessment_id)
    obs["assessment_title_before"] = before["title"]
    obs["assessment_revision_before"] = before["revision"]
    obs["workflow_count_before"] = before["workflow_count"]
    obs["audit_event_count_before"] = before["audit_event_count"]

    lock = TableLock(ctx.db_url, "audit_events")
    lock.acquire()
    response_box: dict[str, Any] = {}

    def in_flight_write() -> None:
        response_box["response"] = http_call(
            f"{ctx.api.base}/api/v1/assessments/{assessment_id}/workflows",
            "POST",
            ctx.headers,
            {"name": "Drill workflow written mid-crash"},
            timeout=60.0,
        )

    worker = threading.Thread(target=in_flight_write, daemon=True)
    worker.start()
    try:
        blocked = wait_for_lock_wait(ctx.db_url, timeout=30)
        obs["write_in_flight"] = blocked is not None
        obs["blocked_statement"] = blocked["query"] if blocked else None
        obs["blocked_backend_pid"] = blocked["pid"] if blocked else None
        print(f"   write is in flight, blocked on: {obs['blocked_statement']}")

        # The ungraceful crash: no signal handler, no rollback, no cleanup.
        ctx.api.kill()
        obs["api_killed"] = True
        print("   API process killed with SIGKILL")

        # Did the database notice the dead client by itself?
        reaped = False
        reaped_after: float | None = None
        if blocked is not None:
            started = time.time()
            deadline = started + 60
            while time.time() < deadline:
                if not backend_alive(ctx.db_url, blocked["pid"]):
                    reaped = True
                    reaped_after = round(time.time() - started, 1)
                    break
                time.sleep(0.5)
        obs["backend_reaped_naturally"] = reaped
        obs["backend_reaped_after_seconds"] = reaped_after
        obs["backends_still_connected_after_the_kill"] = backends_for_database(ctx.db_url)
        if not reaped and blocked is not None:
            _try_run(
                [
                    "docker",
                    "exec",
                    CONTAINER,
                    "psql",
                    "-U",
                    USER,
                    "-d",
                    DATABASE,
                    "-c",
                    f"select pg_terminate_backend({blocked['pid']})",
                ]
            )
    finally:
        lock.close()
        worker.join(timeout=30)

    after = _assessment_state(ctx.db_url, assessment_id)
    obs["assessment_title_after"] = after["title"]
    obs["assessment_revision_after"] = after["revision"]
    obs["workflow_count_after"] = after["workflow_count"]
    obs["audit_event_count_after"] = after["audit_event_count"]
    obs["orphaned_rows"] = orphaned_rows(ctx.db_url)
    obs["alembic_version"] = alembic_version(ctx.db_url)
    obs["expected_alembic_version"] = HEAD_REVISION
    present = set(table_names(ctx.db_url))
    obs["missing_tables"] = sorted(set(expected_table_names()) - present)
    obs["client_observed_error"] = (response_box.get("response") or {}).get("error")

    # The service must come back and read the pre-crash state correctly.
    ctx.api = ApiProcess(ctx.url, API_PORT, ctx.api.log_path)
    ctx.api.start()
    obs["restart_ok"] = ctx.api.wait_ready(timeout=60)
    read = http_call(
        f"{ctx.api.base}/api/v1/assessments/{assessment_id}",
        headers=ctx.headers,
        timeout=10.0,
    )
    obs["read_after_restart_ok"] = read["status"] == 200
    obs["read_after_restart_title"] = _json_body(read).get("title")
    obs["read_after_restart_status"] = read["status"]
    audit_read = http_call(
        f"{ctx.api.base}/api/v1/assessments/{assessment_id}/audit", headers=ctx.headers
    )
    obs["audit_read_after_restart_status"] = audit_read["status"]
    obs["audit_events_after_restart"] = len(_json_items(audit_read))

    verdict = classify_crash_mid_write(obs)
    return ScenarioResult(
        name="crash-mid-write",
        title="Kill the API during an in-flight multi-row write",
        verdict=verdict.verdict,
        summary=(
            "The API was SIGKILLed while a workflow+audit write was blocked in "
            "PostgreSQL; the database rolled the transaction back and the "
            "restarted service read the pre-crash state."
        ),
        observations=obs,
        findings=verdict.findings,
        notes=verdict.notes,
        simulated=list(SIMULATED_CRASH),
        not_simulated=list(NOT_SIMULATED_CRASH) + list(COMMON_NOT_SIMULATED),
    )


SIMULATED_RESTORE = (
    "A `pg_dump -Fc` backup was restored with `pg_restore --clean --if-exists` "
    "into the *same* database while the API was serving live traffic.",
    "Requests were issued continuously during the restore to observe what a "
    "running service does when the schema underneath it is dropped and "
    "recreated.",
)
NOT_SIMULATED_RESTORE = (
    "A managed snapshot restore or a point-in-time recovery. Those replace or "
    "replay a whole cluster from a managed service; here a dump is replayed "
    "into a live database with DDL that drops and recreates every table.",
    "A restore into a *different* database (the usual, safe case) and a "
    "cross-region or cross-account restore.",
    "Connection poolers (pgbouncer), read replicas, a load balancer draining "
    "connections, or a fleet of API instances that restart as a fleet.",
)


def scenario_restore_into_running_service(ctx: DrillContext) -> ScenarioResult:
    """Restore a backup underneath a running API and observe the behaviour."""
    print("== scenario: restore into a running service ==")
    obs: dict[str, Any] = {}
    workdir = ctx.workdir
    dump_path = workdir / "recovery-drill.dump"

    # Seed a little real data so the restore has something to restore.
    seeded_ids: list[str] = []
    for index in range(3):
        response = http_call(
            f"{ctx.api.base}/api/v1/assessments",
            "POST",
            ctx.headers,
            {"title": f"Pre-backup assessment {index}"},
        )
        if response["status"] == 201:
            seeded_ids.append(str(_json_body(response).get("id")))

    before_dump = http_call(f"{ctx.api.base}/api/v1/assessments", timeout=10.0, headers=ctx.headers)
    ids_before_dump = [str(item["id"]) for item in _json_items(before_dump)]
    obs["assessments_before_dump"] = len(ids_before_dump)

    with dump_path.open("wb") as handle:
        _run_to_file(
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
            handle,
        )
    obs["dump_bytes"] = dump_path.stat().st_size
    print(f"   backup written ({obs['dump_bytes']} bytes)")

    # Data written *after* the backup: it must not survive the restore.
    created = http_call(
        f"{ctx.api.base}/api/v1/assessments",
        "POST",
        ctx.headers,
        {"title": "Created after the backup (must not survive the restore)"},
    )
    post_backup_id = str(_json_body(created).get("id"))
    obs["post_backup_assessment_id"] = post_backup_id

    # Keep the service busy while the schema is dropped and recreated.
    statuses: Counter[str] = Counter()
    error_bodies: list[str] = []
    stop_poller = threading.Event()

    def poller() -> None:
        while not stop_poller.is_set():
            response = http_call(
                f"{ctx.api.base}/api/v1/assessments", timeout=5.0, headers=ctx.headers
            )
            code = str(response["status"]) if response["status"] is not None else "error"
            statuses[code] += 1
            if response["status"] is not None and response["status"] >= 400:
                error_bodies.append(f"HTTP {code}: {response['body']}")
            time.sleep(0.05)

    poll_thread = threading.Thread(target=poller, daemon=True)
    poll_thread.start()
    time.sleep(0.3)
    started = time.perf_counter()
    with dump_path.open("rb") as handle:
        _run_with_stdin(
            [
                "docker",
                "exec",
                "-i",
                CONTAINER,
                "pg_restore",
                "-U",
                USER,
                "-d",
                DATABASE,
                "--no-owner",
                "--no-privileges",
                "--clean",
                "--if-exists",
            ],
            handle,
        )
    obs["restore_seconds"] = round(time.perf_counter() - started, 2)
    time.sleep(0.5)
    stop_poller.set()
    poll_thread.join(timeout=30)

    obs["requests_during_restore"] = sum(statuses.values())
    obs["statuses_during_restore"] = dict(statuses)
    obs["error_bodies_during_restore"] = error_bodies[:10]

    after = http_call(f"{ctx.api.base}/api/v1/assessments", timeout=15.0, headers=ctx.headers)
    obs["post_restore_status"] = after["status"]
    obs["post_restore_body"] = after["body"]
    ids_after = [str(item["id"]) for item in _json_items(after)]
    ids_in_database = [str(row) for row in _ids_from_api(ctx)]
    obs["post_restore_matches_backup"] = set(ids_after) == set(ids_before_dump)
    obs["api_matches_database_after_restore"] = set(ids_after) == set(ids_in_database)
    obs["served_removed_rows"] = [row for row in ids_after if row not in set(ids_before_dump)] or (
        [post_backup_id] if post_backup_id in ids_after else []
    )
    obs["stack_trace_in_body"] = "Traceback" in after["body"]
    log_text = ctx.api.log_text()
    obs["credentials_in_log"] = log_contains_secret(log_text, PASSWORD)

    verdict = classify_restore_into_running_service(obs)
    return ScenarioResult(
        name="restore-into-running-service",
        title="Restore a backup while the API is serving traffic",
        verdict=verdict.verdict,
        summary=(
            "A pg_dump backup was restored over the live database while the API "
            "was serving; the service was observed during and after the restore."
        ),
        observations=obs,
        findings=verdict.findings,
        notes=verdict.notes,
        simulated=list(SIMULATED_RESTORE),
        not_simulated=list(NOT_SIMULATED_RESTORE) + list(COMMON_NOT_SIMULATED),
    )


def _ids_from_api(ctx: DrillContext) -> list[str]:
    """Read every assessment id straight from the database."""
    rows = _query(ctx.db_url, "select id from assessments order by created_at")
    return [row[0] for row in rows]


SIMULATED_AUDIT = (
    "The audit trail of a live assessment was captured before a crash, after "
    "the crash and after a restore, and the three were compared event by "
    "event.",
    "The restored trail was re-checked for the evidence chain (creation event "
    "first, a recorded review before any human final decision, monotonic "
    "ordering).",
)
NOT_SIMULATED_AUDIT = (
    "An append-only or WORM audit store. The trail lives in the same database "
    "as the data it describes, so anything that can roll the database back "
    "can roll the trail back.",
    "An external, immutable log sink, a hash chain, a signing key or any "
    "independent copy of the evidence.",
    "Legal-hold checks, retention enforcement and the operator process for "
    "disclosing that a trail was rewound.",
)


def scenario_audit_history_survival(ctx: DrillContext) -> ScenarioResult:
    """Check that the audit trail survives a crash and survives (or reports) a restore."""
    print("== scenario: audit-history survival ==")
    obs: dict[str, Any] = {}
    workdir = ctx.workdir
    dump_path = workdir / "recovery-drill-audit.dump"

    created = http_call(
        f"{ctx.api.base}/api/v1/assessments",
        "POST",
        ctx.headers,
        {"title": "Recovery drill audit target"},
    )
    assessment_id = str(_json_body(created).get("id"))
    obs["assessment_id"] = assessment_id

    # Give the assessment a decision-shaped history before the backup.
    workflow = http_call(
        f"{ctx.api.base}/api/v1/assessments/{assessment_id}/workflows",
        "POST",
        ctx.headers,
        {"name": "Drill workflow"},
    )
    step = http_call(
        f"{ctx.api.base}/api/v1/workflows/{_json_body(workflow).get('id')}/steps",
        "POST",
        ctx.headers,
        {"name": "Drill step", "order": 0},
    )
    http_call(
        f"{ctx.api.base}/api/v1/workflow-steps/{_json_body(step).get('id')}/tasks",
        "POST",
        ctx.headers,
        {"title": "Drill task"},
    )
    http_call(
        f"{ctx.api.base}/api/v1/assessments/{assessment_id}/answers",
        "POST",
        ctx.headers,
        {"key": "business_problem", "value": "Drill answer", "kind": "fact"},
    )

    # A human review, so the trail carries the "a human decided" evidence.
    analysis = http_call(
        f"{ctx.api.base}/api/v1/assessments/{assessment_id}/analyze",
        "POST",
        ctx.headers,
    )
    obs["analyze_status"] = analysis["status"]
    categories = [
        task.get("recommendation", {}).get("category")
        for task in _json_body(analysis).get("tasks") or []
    ]
    categories = [c for c in categories if c]
    review = http_call(
        f"{ctx.api.base}/api/v1/assessments/{assessment_id}/fde-review",
        "POST",
        ctx.headers,
        {
            "reviewer_id": "drill-analyst",
            "selected_category": categories[0] if categories else "human_led",
            "rationale": "Drill review: the evidence for a human decision.",
            "override_reason": None
            if categories
            else "Drill review: no analysis category was available.",
        },
    )
    obs["review_status"] = review["status"]
    trail_before_backup = _assessment_state(ctx.db_url, assessment_id)["audit_events"]
    obs["events_before_backup"] = len(trail_before_backup)

    with dump_path.open("wb") as handle:
        _run_to_file(
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
            handle,
        )

    # Write *after* the backup: a human final decision and another answer.
    finalize = http_call(
        f"{ctx.api.base}/api/v1/assessments/{assessment_id}/final-decision",
        "POST",
        {**ctx.headers, "X-Reviewer-Token": "local_recovery_drill_review_token"},
    )
    obs["finalize_status"] = finalize["status"]
    obs["finalize_body"] = _short(finalize["body"], 200)
    status_before_restore = _scalar(
        ctx.db_url, "select status from assessments where id = :id", id=assessment_id
    )
    obs["assessment_status_before_restore"] = status_before_restore
    http_call(
        f"{ctx.api.base}/api/v1/assessments/{assessment_id}/answers",
        "POST",
        ctx.headers,
        {"key": "risk", "value": "Answered after the backup", "kind": "fact"},
    )
    trail_before_restore = _assessment_state(ctx.db_url, assessment_id)["audit_events"]
    obs["events_before_restore"] = len(trail_before_restore)

    # A crash of the API must not change the audit trail by a single event.
    ctx.api.kill()
    obs["api_killed"] = True
    ctx.api = ApiProcess(ctx.url, API_PORT, ctx.api.log_path)
    ctx.api.start()
    obs["restart_ok"] = ctx.api.wait_ready(timeout=60)
    trail_after_crash = _assessment_state(ctx.db_url, assessment_id)["audit_events"]
    obs["events_after_crash"] = len(trail_after_crash)
    obs["lost_after_crash"] = trail_missing_events(trail_before_restore, trail_after_crash)
    obs["trail_changed_by_crash"] = trail_signature(trail_before_restore) != trail_signature(
        trail_after_crash
    )

    with dump_path.open("rb") as handle:
        _run_with_stdin(
            [
                "docker",
                "exec",
                "-i",
                CONTAINER,
                "pg_restore",
                "-U",
                USER,
                "-d",
                DATABASE,
                "--no-owner",
                "--no-privileges",
                "--clean",
                "--if-exists",
            ],
            handle,
        )

    trail_after_restore = _assessment_state(ctx.db_url, assessment_id)["audit_events"]
    obs["events_after_restore"] = len(trail_after_restore)
    obs["lost_after_restore"] = trail_missing_events(trail_before_restore, trail_after_restore)
    obs["trail_is_prefix_after_restore"] = trail_is_prefix(
        trail_before_restore, trail_after_restore
    )
    obs["trail_inconsistencies"] = audit_trail_inconsistencies(trail_after_restore)
    obs["affected_assessments"] = sorted(
        {event["assessment_id"] for event in obs["lost_after_restore"]}
    )

    api_read = http_call(
        f"{ctx.api.base}/api/v1/assessments/{assessment_id}/audit", headers=ctx.headers
    )
    obs["api_status_after_restore"] = api_read["status"]
    served = _json_items(api_read)
    obs["api_served_events"] = len(served)
    obs["trail_identical_to_backup"] = trail_is_prefix(
        trail_before_backup, trail_after_restore
    ) and len(trail_after_restore) == len(trail_before_backup)
    obs["assessment_status_after_restore"] = _scalar(
        ctx.db_url, "select status from assessments where id = :id", id=assessment_id
    )
    obs["decision_evidence_lost"] = any(
        event.get("event_type") == "fde.finalized" for event in obs["lost_after_restore"]
    )
    obs["finalized_claims_after_restore"] = [
        event.get("event_type")
        for event in trail_after_restore
        if event.get("event_type") == "fde.finalized"
    ]

    verdict = classify_audit_history_survival(obs)
    return ScenarioResult(
        name="audit-history-survival",
        title="Does the audit trail survive a crash and a restore?",
        verdict=verdict.verdict,
        summary=(
            "The audit trail of one assessment was compared before and after a "
            "restore. Events written after the backup were lost silently."
        ),
        observations=obs,
        findings=verdict.findings,
        notes=verdict.notes,
        simulated=list(SIMULATED_AUDIT),
        not_simulated=list(NOT_SIMULATED_AUDIT) + list(COMMON_NOT_SIMULATED),
    )


SIMULATED_DEPENDENCY = (
    "The database was stopped completely (connection refused) and, separately, "
    "frozen with `docker pause` (packets accepted, no answer: the local "
    "equivalent of a silently dropped network path).",
    "The real API process was asked for data and the status code, the body, "
    "the response headers and the server log were inspected.",
)
NOT_SIMULATED_DEPENDENCY = (
    "A managed-database failover, an AZ failure, a DNS failure, a security "
    "group change, a firewall drop or a connection-pooler outage. The "
    "application talks to a single local PostgreSQL container here.",
    "A real network partition with a client that keeps its TCP session "
    "established. The freeze case approximates it, but the operating system "
    "underneath still acknowledges the packets.",
    "DNS caching, TLS renegotiation, a proxy in the path, or a cloud load "
    "balancer that keeps sending traffic to a dead backend.",
)


def scenario_dependency_failure(ctx: DrillContext) -> ScenarioResult:
    """Make the database unreachable and check that the service fails closed."""
    print("== scenario: network partition / database unreachable ==")
    obs: dict[str, Any] = {}
    cases: dict[str, dict[str, Any]] = {}

    def probe(label: str, timeout: float) -> dict[str, Any]:
        response = http_call(
            f"{ctx.api.base}/api/v1/assessments", timeout=timeout, headers=ctx.headers
        )
        log_text = ctx.api.log_text()
        served_items = _json_items(response)
        case = {
            "label": label,
            "status": response["status"],
            "error": response["error"],
            "elapsed_ms": response["elapsed_ms"],
            "hung": response["hung"],
            "body": _short(response["body"], 400),
            "stack_trace_in_body": "Traceback" in response["body"],
            "served_stale_data": bool(served_items),
            "served_items": len(served_items),
            "credentials_in_log": log_contains_secret(log_text, PASSWORD),
            "security_headers_present": all(
                key in response["headers"]
                for key in ("X-Request-ID", "Cache-Control", "X-Content-Type-Options")
            ),
        }
        return case

    # Case 1: the database is completely gone (connection refused).
    print("   stopping the database container (connection refused)")
    _docker("stop", CONTAINER)
    try:
        cases["database-unreachable"] = probe("database unreachable", timeout=15.0)
    finally:
        _docker("start", CONTAINER)
        _wait_container_ready(CONTAINER)
    obs["cases"] = cases

    # Case 2: the database is frozen (packets accepted, nothing answered).
    print("   freezing the database container (silent network partition)")
    _docker("pause", CONTAINER)
    try:
        cases["network-partition"] = probe("network partition (frozen database)", timeout=8.0)
    finally:
        _docker("unpause", CONTAINER)
    obs["cases"] = cases

    # The service must recover once the dependency is back.
    time.sleep(1.0)
    recovered = http_call(f"{ctx.api.base}/api/v1/assessments", timeout=20.0, headers=ctx.headers)
    obs["recovered_status"] = recovered["status"]
    obs["recovered_without_restart"] = recovered["status"] == 200
    log_text = ctx.api.log_text()
    obs["credentials_in_log"] = log_contains_secret(log_text, PASSWORD)

    verdict = classify_dependency_failure(obs)
    return ScenarioResult(
        name="dependency-failure",
        title="Database unreachable / network partition",
        verdict=verdict.verdict,
        summary=(
            "The database was stopped and then frozen while the API was asked "
            "for data; the service's failure response was inspected."
        ),
        observations=obs,
        findings=verdict.findings,
        notes=verdict.notes,
        simulated=list(SIMULATED_DEPENDENCY),
        not_simulated=list(NOT_SIMULATED_DEPENDENCY) + list(COMMON_NOT_SIMULATED),
    )


SIMULATED_DISK_FULL = (
    "A second throwaway PostgreSQL container was given a size-limited tmpfs "
    "used as a tablespace, the `assessments` table was moved into it and the "
    "tablespace was filled until the write path failed with ENOSPC. The API "
    "was pointed at that instance and a real write was attempted; the error, "
    "the committed rows, the foreign keys and the recovery were inspected.",
    "This is a SIMULATED storage exhaustion (a quota on one tablespace), not a "
    "real full disk: the WAL and the rest of the cluster kept working, so the "
    "drill could observe the write-failure path instead of a dead server.",
)
NOT_SIMULATED_DISK_FULL = (
    "A real managed volume reaching 100% (EBS/RDS storage) or an inode "
    "exhaustion, which a size-limited tmpfs does not reproduce.",
    "A real disk exhaustion also hits the WAL, the WAL archive, the backup "
    "tooling, the log volume and the application host's own filesystem. Here "
    "only one tablespace was constrained; a genuinely full volume takes the "
    "database server down with it, which this simulation deliberately avoids.",
    "Storage that fails slowly (write latency, degraded IOPS, a failing "
    "device) instead of failing fast with ENOSPC.",
)


def scenario_disk_full(ctx: DrillContext) -> ScenarioResult:
    """Exhaust the database's storage and check the write failure path."""
    print("== scenario: disk full (simulated storage exhaustion) ==")
    obs: dict[str, Any] = {}
    tablespace_mb = int(os.getenv("RECOVERY_DRILL_FULL_TABLESPACE_MB", "16"))
    tablespace_path = "/drillspace"

    # The application tables are moved into a size-limited tablespace so that a
    # write really hits ENOSPC while the WAL (in the main data directory) still
    # has room. Filling the whole data directory would instead take the
    # database server down, which is a different (and less informative) fault.
    start_container(FULL_CONTAINER, FULL_PORT, tablespace_mb=tablespace_mb)
    full_url = database_url(port=FULL_PORT)
    full_db_url = drill_url(port=FULL_PORT)
    obs["simulation"] = (
        f"a {tablespace_mb} MB tmpfs tablespace on {FULL_CONTAINER}; the "
        "assessments table was moved into it and the tablespace was filled"
    )
    obs["full_url"] = redacted_url(FULL_PORT)
    # The tmpfs is mounted as root; the database user must own it to use it as
    # a tablespace.
    _try_run(
        [
            "docker",
            "exec",
            "-u",
            "root",
            FULL_CONTAINER,
            "chown",
            "-R",
            "postgres",
            tablespace_path,
        ]
    )
    _psql(
        FULL_CONTAINER,
        f"CREATE TABLESPACE drillspace LOCATION '{tablespace_path}'",
        database=DATABASE,
    )
    _alembic(full_db_url, "upgrade", "head")
    _psql(FULL_CONTAINER, "ALTER TABLE assessments SET TABLESPACE drillspace", database=DATABASE)

    token, organization_id, headers = provision_tenant(full_db_url)
    full_api = ApiProcess(full_url, FULL_API_PORT, ctx.api.log_path)
    full_api.start()
    try:
        if not full_api.wait_ready(timeout=60):
            raise RuntimeError("the API against the constrained instance did not start")
        created = http_call(
            f"{full_api.base}/api/v1/assessments",
            "POST",
            headers,
            {"title": "Disk full drill target"},
        )
        obs["write_before_fill_status"] = created["status"]
        obs["rows_before"] = row_counts(full_db_url)

        # Fill the tablespace.
        fill = _try_run(
            [
                "docker",
                "exec",
                FULL_CONTAINER,
                "sh",
                "-c",
                f"dd if=/dev/zero of={tablespace_path}/drill-filler bs=1M count=1024",
            ]
        )
        obs["fill_exit_code"] = fill.returncode
        obs["fill_error"] = _short((fill.stderr or "") + (fill.stdout or ""), 300)
        obs["storage_full"] = "No space left on device" in (
            (fill.stderr or "") + (fill.stdout or "")
        )

        # The write that must fail. PostgreSQL pre-allocates relation files, so
        # the first write into a full tablespace can still succeed; the drill
        # therefore writes until a write really fails.
        attempts = 0
        succeeded = 0
        write = {"status": None, "error": None, "body": "", "headers": {}, "elapsed_ms": 0.0}
        for attempts in range(1, 201):
            write = http_call(
                f"{full_api.base}/api/v1/assessments",
                "POST",
                headers,
                {"title": f"Written while the storage was full ({attempts})"},
                timeout=30.0,
            )
            if write["status"] in (200, 201):
                succeeded += 1
                continue
            break
        obs["write_attempts"] = attempts
        obs["writes_succeeded_before_failure"] = succeeded
        obs["write_status"] = write["status"]
        obs["write_error"] = write["error"]
        obs["write_body"] = _short(write["body"], 400)
        obs["stack_trace_in_body"] = "Traceback" in write["body"]
        obs["write_succeeded_despite_full_storage"] = write["status"] in (200, 201)
        obs["write_response_headers"] = sorted(write["headers"])
        obs["security_headers_present"] = all(
            key in write["headers"]
            for key in ("X-Request-ID", "Cache-Control", "X-Content-Type-Options")
        )

        log_text = full_api.log_text()
        obs["credentials_in_log"] = log_contains_secret(log_text, PASSWORD)
        obs["database_error_in_log"] = _short(
            "\n".join(
                line
                for line in log_text.splitlines()
                if "No space left" in line or "could not extend" in line
            ),
            400,
        )
        server_log = _try_run(["docker", "logs", "--tail", "40", FULL_CONTAINER])
        obs["postgres_server_log"] = _short(
            "\n".join(
                line
                for line in (server_log.stdout or "").splitlines()
                if "No space left" in line or "could not extend" in line
            ),
            400,
        )
        obs["database_crashed"] = not _container_running(FULL_CONTAINER)
        obs["database_reachable_after_failure"] = _container_running(FULL_CONTAINER)

        # The database must still be consistent after the failed write.
        try:
            obs["rows_after"] = row_counts(full_db_url)
        except Exception as exc:
            obs["rows_after"] = {}
            obs["rows_after_error"] = str(exc)
        obs["partial_rows"] = []
        obs["row_count_changes"] = []
        write_succeeded = write["status"] in (200, 201)
        for table, before in obs["rows_before"].items():
            after = obs["rows_after"].get(table, 0)
            if after != before:
                obs["row_count_changes"].append(f"{table}: {before} -> {after}")
                if not write_succeeded:
                    # The failed write must not have committed anything: the
                    # only allowed change is the rows that were written
                    # successfully before the failure.
                    expected = before + (succeeded if table == "assessments" else succeeded)
                    if after > expected:
                        obs["partial_rows"].append(f"{table}: {before} -> {after}")
        try:
            obs["data_readable_after_failure"] = (
                _scalar(full_db_url, "select count(*) from assessments") is not None
            )
        except Exception as exc:  # pragma: no cover - defensive
            obs["data_readable_after_failure"] = False
            obs["read_error"] = str(exc)
        try:
            obs["orphaned_rows"] = orphaned_rows(full_db_url)
            obs["alembic_version"] = alembic_version(full_db_url)
        except Exception as exc:  # pragma: no cover - defensive
            obs["orphaned_rows"] = [f"could not be checked: {exc}"]
            obs["alembic_version"] = None
        obs["data_intact_after_restart"] = bool(
            obs["data_readable_after_failure"]
            and not obs["partial_rows"]
            and not obs["orphaned_rows"]
            and obs["alembic_version"] == HEAD_REVISION
        )

        # Free the space: the service must accept writes again.
        _try_run(
            [
                "docker",
                "exec",
                FULL_CONTAINER,
                "rm",
                "-f",
                f"{tablespace_path}/drill-filler",
            ]
        )
        time.sleep(0.5)
        after_write = http_call(
            f"{full_api.base}/api/v1/assessments",
            "POST",
            headers,
            {"title": "Written after the storage was freed"},
            timeout=30.0,
        )
        obs["write_after_recovery_status"] = after_write["status"]
        try:
            obs["rows_after_recovery"] = row_counts(full_db_url)
        except Exception as exc:
            obs["rows_after_recovery"] = {}
            obs["rows_after_recovery_error"] = str(exc)
    finally:
        full_api.stop()
        _remove_container(FULL_CONTAINER)
    obs["organization_id"] = organization_id
    obs["token_used"] = bool(token)

    verdict = classify_disk_full(obs)
    return ScenarioResult(
        name="disk-full",
        title="Storage exhaustion (simulated) during a write",
        verdict=verdict.verdict,
        summary=(
            "A size-limited database volume was filled until writes failed; "
            "the API's error, the committed rows and the recovery were checked."
        ),
        observations=obs,
        findings=verdict.findings,
        notes=verdict.notes,
        simulated=list(SIMULATED_DISK_FULL),
        not_simulated=list(NOT_SIMULATED_DISK_FULL) + list(COMMON_NOT_SIMULATED),
    )


def _container_running(name: str) -> bool:
    result = _try_run(["docker", "ps", "--filter", f"name=^{name}$", "--format", "{{.Names}}"])
    return name in result.stdout.split()


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def run_drill() -> tuple[list[ScenarioResult], dict[str, Any]]:
    """Run every scenario. Always tears the drill down, even on failure."""
    workdir = Path(tempfile.mkdtemp(prefix="aiadvisory-recovery-drill-"))
    meta: dict[str, Any] = {"started": datetime.now(UTC).isoformat()}
    results: list[ScenarioResult] = []
    api: ApiProcess | None = None
    try:
        up()
        url = database_url()
        db_url = drill_url()
        token, organization_id, headers = provision_tenant(db_url)
        api = ApiProcess(url, API_PORT, workdir / "api.log")
        api.start()
        if not api.wait_ready(timeout=60):
            raise RuntimeError("the drill API did not become ready")
        ctx = DrillContext(
            url=url,
            db_url=db_url,
            api=api,
            headers=headers,
            token=token,
            organization_id=organization_id,
            workdir=workdir,
        )
        meta["environment"] = (
            f"image={IMAGE} container={CONTAINER} port={PORT} api_port={API_PORT} "
            f"python={sys.version.split()[0]}"
        )
        for scenario in (
            scenario_crash_mid_write,
            scenario_restore_into_running_service,
            scenario_audit_history_survival,
            scenario_dependency_failure,
            scenario_disk_full,
        ):
            try:
                results.append(scenario(ctx))
            except Exception as exc:  # a broken step must not hide the others
                results.append(
                    ScenarioResult(
                        name=scenario.__name__.replace("scenario_", "").replace("_", "-"),
                        title="(the drill step raised an exception)",
                        verdict=VERDICT_FAIL,
                        summary=f"The step could not be completed: {exc}",
                        findings=[f"{type(exc).__name__}: {exc}"],
                    )
                )
                print(f"   step failed: {exc}")
    finally:
        if api is not None:
            api.stop()
        teardown(workdir)
    meta["finished"] = datetime.now(UTC).isoformat()
    return results, meta


def _interrupt(signum, _frame) -> None:  # pragma: no cover - signal path
    raise KeyboardInterrupt(f"interrupted by signal {signum}")


def main() -> int:
    # Progress must survive a redirected log, and an interrupted run must still
    # tear the disposable containers down.
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, ValueError):  # pragma: no cover - defensive
        pass
    signal.signal(signal.SIGTERM, _interrupt)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--output", help="write the JSON report to this path")
    parser = argparse.ArgumentParser(
        description="Disposable local failure-recovery drill (P5-03 follow-up)",
        parents=[common],
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("up", "down", "run", "all"):
        sub.add_parser(name, parents=[common])
    args = parser.parse_args()

    if args.command == "up":
        up()
        return 0
    if args.command == "down":
        down()
        return 0

    results: list[ScenarioResult] = []
    meta: dict[str, Any] = {"started": datetime.now(UTC).isoformat()}
    if args.command == "all":
        try:
            results, meta = run_drill()
        except Exception as exc:
            sys.stderr.write(f"the drill could not run: {exc}\n")
            teardown()
            return 2
    else:
        results, meta = run_drill()

    report = render_report(results, meta)
    print()
    print(report)
    if args.output:
        payload = {
            "meta": meta,
            "not_disaster_recovery": NOT_DISASTER_RECOVERY,
            "scenarios": [
                {
                    "name": result.name,
                    "title": result.title,
                    "verdict": result.verdict,
                    "summary": result.summary,
                    "observations": result.observations,
                    "findings": result.findings,
                    "notes": result.notes,
                    "simulated": result.simulated,
                    "not_simulated": result.not_simulated,
                }
                for result in results
            ],
        }
        Path(args.output).write_text(json.dumps(payload, indent=2, default=str))
        print(f"\nJSON report written to {args.output}")

    failed = [r.name for r in results if r.verdict == VERDICT_FAIL]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
