"""Failure-recovery drill verification (P5-03 follow-up, planner P5-06).

The drill itself (`scripts/recovery_drill.py`) needs Docker: it starts a
throwaway PostgreSQL container, runs a real API process against it and breaks
things on purpose. The part that matters for review — the **analysis and the
reporting** — is pure and lives in the same module, so it is tested here
offline, without Docker and without a database.

    python -m pytest -q tests/test_recovery_drill.py

The container-driven part is opt-in and skips cleanly when Docker is missing:

    ADVISORY_RECOVERY_DRILL=1 python -m pytest -q tests/test_recovery_drill.py

A pass here is a *local drill* result. It is not evidence of disaster-recovery
readiness, and it does not measure an RPO or an RTO.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts.recovery_drill import (  # noqa: E402
    COMMON_NOT_SIMULATED,
    NOT_DISASTER_RECOVERY,
    VERDICT_FAIL,
    VERDICT_FINDING,
    VERDICT_PASS,
    ScenarioResult,
    audit_trail_inconsistencies,
    classify_audit_history_survival,
    classify_crash_mid_write,
    classify_dependency_case,
    classify_dependency_failure,
    classify_disk_full,
    classify_restore_into_running_service,
    render_report,
    trail_is_prefix,
    trail_missing_events,
    trail_signature,
)

# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


def _event(event_id: str, event_type: str, created_at: str, actor: str = "analyst"):
    return {
        "id": event_id,
        "event_type": event_type,
        "actor": actor,
        "created_at": created_at,
        "assessment_id": "assessment-1",
    }


def _clean_crash_observations() -> dict:
    return {
        "write_in_flight": True,
        "blocked_statement": "INSERT INTO audit_events ...",
        "api_killed": True,
        "assessment_title_before": "Target",
        "assessment_title_after": "Target",
        "assessment_revision_before": 1,
        "assessment_revision_after": 1,
        "workflow_count_before": 0,
        "workflow_count_after": 0,
        "audit_event_count_before": 1,
        "audit_event_count_after": 1,
        "orphaned_rows": [],
        "alembic_version": "0008_portfolio",
        "expected_alembic_version": "0008_portfolio",
        "missing_tables": [],
        "restart_ok": True,
        "read_after_restart_ok": True,
        "read_after_restart_title": "Target",
        "backend_reaped_naturally": True,
        "backend_reaped_after_seconds": 1.0,
    }


def _clean_restore_observations() -> dict:
    return {
        "dump_bytes": 1024,
        "restore_seconds": 0.2,
        "requests_during_restore": 20,
        "statuses_during_restore": {"200": 20},
        "error_bodies_during_restore": [],
        "post_restore_status": 200,
        "post_restore_body": "[]",
        "post_restore_matches_backup": True,
        "served_removed_rows": [],
        "stack_trace_in_body": False,
        "credentials_in_log": False,
    }


def _clean_audit_observations() -> dict:
    before = [
        _event("1", "assessment.created", "2026-01-01T00:00:00+00:00"),
        _event("2", "fde.review_recorded", "2026-01-01T00:01:00+00:00"),
        _event("3", "fde.finalized", "2026-01-01T00:02:00+00:00"),
    ]
    return {
        "lost_after_crash": [],
        "lost_after_restore": [],
        "trail_is_prefix_after_restore": True,
        "trail_inconsistencies": [],
        "api_status_after_restore": 200,
        "affected_assessments": [],
        "events_before_restore": before,
        "events_after_restore": before,
    }


def _clean_disk_observations() -> dict:
    return {
        "storage_full": True,
        "write_status": 500,
        "write_error": None,
        "write_body": "Internal Server Error",
        "write_attempts": 41,
        "writes_succeeded_before_failure": 40,
        "rows_before": {"assessments": 1, "audit_events": 1},
        "rows_after": {"assessments": 41, "audit_events": 41},
        "partial_rows": [],
        "data_readable_after_failure": True,
        "data_intact_after_restart": True,
        "stack_trace_in_body": False,
        "credentials_in_log": False,
        "security_headers_present": False,
        "database_crashed": False,
        "write_after_recovery_status": 201,
    }


# ---------------------------------------------------------------------------
# 1. Crash mid-write
# ---------------------------------------------------------------------------


def test_clean_crash_is_a_pass():
    verdict = classify_crash_mid_write(_clean_crash_observations())
    assert verdict.verdict == VERDICT_PASS
    assert verdict.findings == []


def test_crash_that_never_reached_a_write_is_not_a_pass():
    observations = _clean_crash_observations()
    observations["write_in_flight"] = False
    verdict = classify_crash_mid_write(observations)
    assert verdict.verdict == VERDICT_FAIL
    assert "did not test what it claims" in verdict.findings[0]


def test_partial_write_surviving_the_crash_is_a_fail():
    observations = _clean_crash_observations()
    observations["workflow_count_after"] = 1
    verdict = classify_crash_mid_write(observations)
    assert verdict.verdict == VERDICT_FAIL
    assert any("partial write survived" in f for f in verdict.findings)


def test_orphaned_child_row_surviving_the_crash_is_a_fail():
    observations = _clean_crash_observations()
    observations["orphaned_rows"] = ["tasks.step_id -> workflow_steps.id (1 orphaned row(s))"]
    verdict = classify_crash_mid_write(observations)
    assert verdict.verdict == VERDICT_FAIL
    assert any("Orphaned rows" in f for f in verdict.findings)


def test_half_applied_migration_is_a_fail():
    observations = _clean_crash_observations()
    observations["alembic_version"] = "0007_collaboration"
    verdict = classify_crash_mid_write(observations)
    assert verdict.verdict == VERDICT_FAIL
    assert any("half-applied migration" in f for f in verdict.findings)


def test_missing_table_after_the_crash_is_a_fail():
    observations = _clean_crash_observations()
    observations["missing_tables"] = ["audit_events"]
    verdict = classify_crash_mid_write(observations)
    assert verdict.verdict == VERDICT_FAIL
    assert any("Tables are missing" in f for f in verdict.findings)


def test_service_that_does_not_come_back_is_a_fail():
    observations = _clean_crash_observations()
    observations["restart_ok"] = False
    verdict = classify_crash_mid_write(observations)
    assert verdict.verdict == VERDICT_FAIL
    assert any("did not come back up" in f for f in verdict.findings)


def test_service_that_reads_the_wrong_row_after_the_crash_is_a_fail():
    observations = _clean_crash_observations()
    observations["read_after_restart_title"] = "Something else"
    verdict = classify_crash_mid_write(observations)
    assert verdict.verdict == VERDICT_FAIL
    assert any("served a different title" in f for f in verdict.findings)


def test_unreaped_backend_is_reported_as_a_note_not_a_failure():
    observations = _clean_crash_observations()
    observations["backend_reaped_naturally"] = False
    observations["backend_reaped_after_seconds"] = None
    verdict = classify_crash_mid_write(observations)
    assert verdict.verdict == VERDICT_PASS
    assert any("did not notice the dead client" in note for note in verdict.notes)


# ---------------------------------------------------------------------------
# 2. Restore into a running service
# ---------------------------------------------------------------------------


def test_clean_restore_is_a_pass():
    verdict = classify_restore_into_running_service(_clean_restore_observations())
    assert verdict.verdict == VERDICT_PASS
    assert verdict.findings == []


def test_restore_that_leaves_the_service_broken_is_a_fail():
    observations = _clean_restore_observations()
    observations["post_restore_status"] = 500
    verdict = classify_restore_into_running_service(observations)
    assert verdict.verdict == VERDICT_FAIL
    assert any("did not return 200" in f for f in verdict.findings)


def test_service_serving_rows_the_restore_removed_is_a_fail():
    observations = _clean_restore_observations()
    observations["served_removed_rows"] = ["assessment-42"]
    verdict = classify_restore_into_running_service(observations)
    assert verdict.verdict == VERDICT_FAIL
    assert any("kept serving rows" in f for f in verdict.findings)


def test_restore_serving_a_different_row_set_is_a_fail():
    observations = _clean_restore_observations()
    observations["post_restore_matches_backup"] = False
    verdict = classify_restore_into_running_service(observations)
    assert verdict.verdict == VERDICT_FAIL
    assert any("did not serve the restored" in f for f in verdict.findings)


def test_stack_trace_in_the_error_body_is_a_fail():
    observations = _clean_restore_observations()
    observations["stack_trace_in_body"] = True
    verdict = classify_restore_into_running_service(observations)
    assert verdict.verdict == VERDICT_FAIL


def test_credentials_in_the_server_log_is_a_fail():
    observations = _clean_restore_observations()
    observations["credentials_in_log"] = True
    verdict = classify_restore_into_running_service(observations)
    assert verdict.verdict == VERDICT_FAIL
    assert any("credentials" in f for f in verdict.findings)


def test_a_401_during_the_restore_is_reported_as_a_finding():
    observations = _clean_restore_observations()
    observations["statuses_during_restore"] = {"200": 18, "401": 1, "503": 1}
    observations["error_bodies_during_restore"] = [
        'HTTP 401: {"detail":"Invalid or revoked API credential"}',
        'HTTP 503: {"detail":"Rate limiter unavailable"}',
    ]
    verdict = classify_restore_into_running_service(observations)
    assert verdict.verdict == VERDICT_FINDING
    assert any("HTTP 401" in f for f in verdict.findings)
    # A 503 is the safe answer and must not be reported as a 5xx defect.
    assert not any("5xx" in f for f in verdict.findings)


def test_a_5xx_other_than_503_during_the_restore_is_reported():
    observations = _clean_restore_observations()
    observations["statuses_during_restore"] = {"200": 18, "500": 2}
    observations["error_bodies_during_restore"] = ["HTTP 500: Internal Server Error"]
    verdict = classify_restore_into_running_service(observations)
    assert verdict.verdict == VERDICT_FINDING
    assert any("5xx" in f for f in verdict.findings)


# ---------------------------------------------------------------------------
# 3. Audit-history survival
# ---------------------------------------------------------------------------


def test_intact_audit_trail_after_a_crash_and_a_restore_is_a_pass():
    verdict = classify_audit_history_survival(_clean_audit_observations())
    assert verdict.verdict == VERDICT_PASS
    assert verdict.findings == []


def test_crash_that_changes_the_audit_trail_is_a_fail():
    observations = _clean_audit_observations()
    observations["lost_after_crash"] = [_event("9", "fde.finalized", "2026-01-01T00:03:00+00:00")]
    verdict = classify_audit_history_survival(observations)
    assert verdict.verdict == VERDICT_FAIL
    assert any("changed the audit trail" in f for f in verdict.findings)


def test_silently_rewound_audit_trail_is_reported_loudly():
    observations = _clean_audit_observations()
    observations["lost_after_restore"] = [_event("3", "fde.finalized", "2026-01-01T00:02:00+00:00")]
    observations["affected_assessments"] = ["assessment-1"]
    observations["decision_evidence_lost"] = True
    verdict = classify_audit_history_survival(observations)
    assert verdict.verdict == VERDICT_FINDING
    assert any("not append-only" in f for f in verdict.findings)
    assert any("fde.finalized" in f for f in verdict.findings)
    assert any("LOUD FINDING" in note for note in verdict.notes)


def test_mid_trail_truncation_is_a_fail():
    observations = _clean_audit_observations()
    observations["trail_is_prefix_after_restore"] = False
    verdict = classify_audit_history_survival(observations)
    assert verdict.verdict == VERDICT_FAIL
    assert any("not a prefix" in f.lower() for f in verdict.findings)


def test_inconsistent_trail_is_a_fail():
    observations = _clean_audit_observations()
    observations["trail_inconsistencies"] = ["a final decision has no preceding review"]
    verdict = classify_audit_history_survival(observations)
    assert verdict.verdict == VERDICT_FAIL
    assert any("internally inconsistent" in f for f in verdict.findings)


def test_audit_trail_helpers_detect_a_rewound_trail():
    before = [
        _event("1", "assessment.created", "2026-01-01T00:00:00+00:00"),
        _event("2", "fde.review_recorded", "2026-01-01T00:01:00+00:00"),
        _event("3", "fde.finalized", "2026-01-01T00:02:00+00:00"),
    ]
    after = before[:2]
    assert trail_signature(after) == ["1", "2"]
    assert trail_is_prefix(before, after) is True
    assert [e["id"] for e in trail_missing_events(before, after)] == ["3"]
    # A hole in the middle is not a prefix.
    assert trail_is_prefix(before, [before[0], before[2]]) is False


def test_audit_trail_consistency_requires_a_review_before_a_final_decision():
    good = [
        _event("1", "assessment.created", "2026-01-01T00:00:00+00:00"),
        _event("2", "fde.review_recorded", "2026-01-01T00:01:00+00:00"),
        _event("3", "fde.finalized", "2026-01-01T00:02:00+00:00"),
    ]
    assert audit_trail_inconsistencies(good) == []
    bad = [
        _event("1", "assessment.created", "2026-01-01T00:00:00+00:00"),
        _event("3", "fde.finalized", "2026-01-01T00:02:00+00:00"),
    ]
    problems = audit_trail_inconsistencies(bad)
    assert any("no preceding recorded review" in p for p in problems)


def test_audit_trail_consistency_detects_out_of_order_events():
    events = [
        _event("1", "assessment.created", "2026-01-01T00:00:00+00:00"),
        _event("2", "fde.review_recorded", "2026-01-01T00:05:00+00:00"),
        _event("3", "answer.recorded", "2026-01-01T00:01:00+00:00"),
    ]
    assert any("non-decreasing" in p for p in audit_trail_inconsistencies(events))


# ---------------------------------------------------------------------------
# 4. Dependency failure (database unreachable / network partition)
# ---------------------------------------------------------------------------


def test_503_is_the_expected_safe_answer():
    verdict = classify_dependency_case({"status": 503, "security_headers_present": True})
    assert verdict.verdict == VERDICT_PASS
    assert verdict.findings == []


def test_500_instead_of_503_is_reported_as_a_finding():
    verdict = classify_dependency_case({"status": 500, "security_headers_present": True})
    assert verdict.verdict == VERDICT_FINDING
    assert any("HTTP 500" in f for f in verdict.findings)


def test_a_hung_request_is_reported_as_a_finding():
    verdict = classify_dependency_case(
        {
            "status": None,
            "hung": True,
            "error": "TimeoutError: timed out",
            "security_headers_present": True,
        }
    )
    assert verdict.verdict == VERDICT_FINDING
    assert any("hang" in f for f in verdict.findings)


def test_answering_200_with_a_broken_dependency_is_a_fail():
    verdict = classify_dependency_case(
        {"status": 200, "served_stale_data": True, "security_headers_present": True}
    )
    assert verdict.verdict == VERDICT_FAIL
    assert any("misleading success" in f for f in verdict.findings)


def test_stack_trace_in_a_dependency_error_is_a_fail():
    verdict = classify_dependency_case(
        {"status": 500, "stack_trace_in_body": True, "security_headers_present": True}
    )
    assert verdict.verdict == VERDICT_FAIL


def test_credentials_in_the_log_are_a_fail():
    verdict = classify_dependency_case(
        {"status": 500, "credentials_in_log": True, "security_headers_present": True}
    )
    assert verdict.verdict == VERDICT_FAIL


def test_missing_security_headers_are_noted():
    verdict = classify_dependency_case({"status": 503, "security_headers_present": False})
    assert any("security headers" in note for note in verdict.notes)


def test_dependency_failure_combines_both_cases():
    verdict = classify_dependency_failure(
        {
            "cases": {
                "database-unreachable": {"status": 500, "security_headers_present": True},
                "network-partition": {
                    "status": None,
                    "hung": True,
                    "security_headers_present": True,
                },
            }
        }
    )
    assert verdict.verdict == VERDICT_FINDING
    assert any(f.startswith("[database-unreachable]") for f in verdict.findings)
    assert any(f.startswith("[network-partition]") for f in verdict.findings)


# ---------------------------------------------------------------------------
# 5. Disk full (simulated storage exhaustion)
# ---------------------------------------------------------------------------


def test_clean_disk_full_is_a_finding_about_the_status_code_only():
    """A failed write that committed nothing is safe; only the 500 is reported."""
    observations = _clean_disk_observations()
    observations["security_headers_present"] = True
    verdict = classify_disk_full(observations)
    assert verdict.verdict == VERDICT_FINDING
    assert [f for f in verdict.findings if "500" in f]
    assert not any("partial" in f for f in verdict.findings)
    assert not any("corrupt" in f for f in verdict.findings)


def test_a_write_that_succeeds_on_a_full_volume_is_reported():
    observations = _clean_disk_observations()
    observations["write_status"] = 201
    verdict = classify_disk_full(observations)
    assert verdict.verdict == VERDICT_FINDING
    assert any("still succeeded" in f for f in verdict.findings)


def test_partial_rows_after_a_failed_write_are_a_fail():
    observations = _clean_disk_observations()
    observations["partial_rows"] = ["assessments: 41 -> 42"]
    verdict = classify_disk_full(observations)
    assert verdict.verdict == VERDICT_FAIL
    assert any("partial rows" in f for f in verdict.findings)


def test_unreadable_database_after_the_storage_failure_is_a_fail():
    observations = _clean_disk_observations()
    observations["data_readable_after_failure"] = False
    verdict = classify_disk_full(observations)
    assert verdict.verdict == VERDICT_FAIL


def test_corrupted_data_after_the_restart_is_a_fail():
    observations = _clean_disk_observations()
    observations["data_intact_after_restart"] = False
    verdict = classify_disk_full(observations)
    assert verdict.verdict == VERDICT_FAIL
    assert any("lost or corrupted" in f for f in verdict.findings)


def test_a_storage_failure_is_not_reported_as_a_misleading_success():
    verdict = classify_disk_full(_clean_disk_observations())
    assert not any("misleading success" in f for f in verdict.findings)
    assert any("HTTP 500" in f for f in verdict.findings)


# ---------------------------------------------------------------------------
# 6. The report itself
# ---------------------------------------------------------------------------


def _sample_results() -> list[ScenarioResult]:
    return [
        ScenarioResult(
            name="crash-mid-write",
            title="Kill the API during an in-flight write",
            verdict=VERDICT_PASS,
            summary="The transaction was rolled back.",
            observations={"write_in_flight": True},
            notes=["The database rolled the transaction back."],
            simulated=["A SIGKILL of a real API process."],
            not_simulated=["A real power loss."],
        ),
        ScenarioResult(
            name="audit-history-survival",
            title="Does the audit trail survive?",
            verdict=VERDICT_FINDING,
            summary="The trail was rewound.",
            observations={"lost_after_restore": 1},
            findings=["The audit trail is not append-only across a restore."],
            simulated=["A pg_dump/pg_restore round trip."],
            not_simulated=["An append-only or WORM audit store."],
        ),
    ]


def test_report_states_plainly_that_this_is_not_disaster_recovery():
    report = render_report(_sample_results(), {"started": "now"})
    assert NOT_DISASTER_RECOVERY in report
    assert "NOT disaster-recovery readiness" in report
    assert report.count(NOT_DISASTER_RECOVERY) >= 2


def test_report_contains_every_scenario_with_its_verdict():
    report = render_report(_sample_results())
    assert "crash-mid-write" in report
    assert "audit-history-survival" in report
    assert "VERDICT: PASS" in report
    assert "VERDICT: FINDING" in report


def test_report_separates_what_was_simulated_from_what_was_not():
    report = render_report(_sample_results())
    assert "What was actually simulated:" in report
    assert "What was NOT simulated:" in report
    assert "A SIGKILL of a real API process." in report
    assert "A real power loss." in report


def test_report_lists_the_findings_and_the_common_limits():
    report = render_report(_sample_results())
    assert "The audit trail is not append-only across a restore." in report
    for limit in COMMON_NOT_SIMULATED:
        assert limit in report


def test_report_summary_separates_failures_from_findings():
    results = _sample_results()
    results.append(
        ScenarioResult(
            name="dependency-failure",
            title="Database unreachable",
            verdict=VERDICT_FAIL,
            summary="Unsafe behaviour.",
        )
    )
    report = render_report(results)
    assert "FAILED scenarios (unsafe behaviour observed): dependency-failure" in report
    assert "Scenarios with findings to report: audit-history-survival" in report


def test_report_never_contains_the_drill_password():
    from scripts.recovery_drill import PASSWORD

    report = render_report(_sample_results())
    assert PASSWORD not in report


def test_drill_default_password_is_a_documented_throwaway():
    from scripts.recovery_drill import PASSWORD

    assert PASSWORD == "local_recovery_drill_only_pw"
    assert "local_recovery_drill_only_pw" not in (REPO_ROOT / ".env.example").read_text()


# ---------------------------------------------------------------------------
# 7. The container-driven drill (opt-in)
# ---------------------------------------------------------------------------


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        completed = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


DOCKER_AVAILABLE = _docker_available()
OPT_IN = os.getenv("ADVISORY_RECOVERY_DRILL") == "1"

requires_docker = pytest.mark.skipif(
    not DOCKER_AVAILABLE or not OPT_IN,
    reason=(
        "Container-driven recovery drill skipped: it needs a working Docker daemon "
        "and ADVISORY_RECOVERY_DRILL=1 (it starts a throwaway PostgreSQL container "
        "and kills a real API process)"
    ),
)


@requires_docker
def test_crash_mid_write_scenario_passes_against_a_real_database():
    """The crash scenario must leave the database consistent and restart cleanly."""
    from scripts import recovery_drill as drill

    workdir = Path(tempfile.mkdtemp(prefix="aiadvisory-recovery-test-"))
    api = None
    try:
        drill.up()
        url = drill.database_url()
        db_url = drill.drill_url()
        token, organization_id, headers = drill.provision_tenant(db_url)
        api = drill.ApiProcess(url, drill.API_PORT, workdir / "api.log")
        api.start()
        assert api.wait_ready(timeout=60), "the drill API did not become ready"
        context = drill.DrillContext(
            url=url,
            db_url=db_url,
            api=api,
            headers=headers,
            token=token,
            organization_id=organization_id,
            workdir=workdir,
        )
        result = drill.scenario_crash_mid_write(context)
        assert result.verdict == VERDICT_PASS, result.findings
        assert result.observations["write_in_flight"] is True
        assert result.observations["orphaned_rows"] == []
        assert result.observations["read_after_restart_ok"] is True
    finally:
        if api is not None:
            api.stop()
        drill.teardown(workdir)
        shutil.rmtree(workdir, ignore_errors=True)


@requires_docker
def test_the_drill_leaves_no_container_behind():
    """Every container the drill creates must be removed again."""
    from scripts import recovery_drill as drill

    drill.up()
    try:
        existing = subprocess.run(
            ["docker", "ps", "-a", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            timeout=60,
        ).stdout.split()
        assert drill.CONTAINER in existing
    finally:
        drill.down()
    remaining = subprocess.run(
        ["docker", "ps", "-a", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        timeout=60,
    ).stdout.split()
    assert drill.CONTAINER not in remaining
    assert drill.FULL_CONTAINER not in remaining
