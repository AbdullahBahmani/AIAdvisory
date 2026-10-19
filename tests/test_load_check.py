"""Tests for the local load / distributed rate-limit harness.

The harness itself needs containers and multiple processes, so these tests cover the parts
that can be verified offline: the latency percentile helper and, most importantly, the
verdict logic. The verdict is the safety-relevant part — it is what decides whether the
per-principal rate limit was enforced *globally* across replicas or only per process.
"""

from __future__ import annotations

import pytest

from scripts.load_check import _percentile, evaluate_report


def _report(**overrides) -> dict:
    base = {
        "instances": 3,
        "configured_capacity": 25,
        "total_requests": 90,
        "admitted_2xx": 25,
        "rate_limited_429": 65,
        "other_statuses": {},
        "connection_errors": 0,
        "elapsed_seconds": 0.24,
        "throughput_rps": 378.72,
        "latency_ms": {"min": 1.0, "p50": 2.0, "p95": 3.0, "p99": 4.0, "max": 5.0, "mean": 2.5},
    }
    base.update(overrides)
    return base


def test_percentile_selects_the_expected_element():
    values = [10.0, 20.0, 30.0, 40.0, 50.0]
    assert _percentile(values, 0.0) == 10.0
    assert _percentile(values, 1.0) == 50.0
    assert _percentile(values, 0.5) == 30.0


def test_percentile_handles_an_empty_sample():
    assert _percentile([], 0.95) == 0.0


def test_percentile_handles_a_single_sample():
    assert _percentile([7.5], 0.99) == 7.5


def test_global_limit_enforced_passes():
    assert evaluate_report(_report()) == []


def test_per_process_limiter_is_detected():
    """Admitting capacity*instances is the signature of a per-process limiter."""
    failures = evaluate_report(_report(admitted_2xx=75, rate_limited_429=15))
    assert failures
    assert "NOT enforced globally" in failures[0]
    assert "per-process limiter would admit up to 75" in failures[0]


def test_exceeding_capacity_is_detected():
    failures = evaluate_report(_report(admitted_2xx=26))
    assert failures
    assert "NOT enforced globally" in failures[0]


def test_connection_errors_fail_the_check():
    failures = evaluate_report(_report(connection_errors=3))
    assert failures
    assert "failed to connect" in failures[0]


def test_unexpected_status_codes_fail_the_check():
    failures = evaluate_report(_report(other_statuses={500: 2}))
    assert failures
    assert "unexpected status codes" in failures[0]


def test_missing_rate_limiting_is_detected():
    failures = evaluate_report(_report(rate_limited_429=0, admitted_2xx=25))
    assert failures
    assert "no request was rate limited" in failures[0]


def test_no_rate_limiting_needed_when_under_capacity():
    """Under capacity, no 429 is correct and must not be reported as a failure."""
    report = _report(
        configured_capacity=100, total_requests=10, admitted_2xx=10, rate_limited_429=0
    )
    assert evaluate_report(report) == []


def test_single_instance_still_checks_the_limit():
    failures = evaluate_report(
        _report(instances=1, admitted_2xx=30, rate_limited_429=0, total_requests=30)
    )
    assert failures
    assert "NOT enforced globally" in failures[0]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__]))
