"""Local, dependency-free metrics and alert check for a running API.

Reads the public health/readiness probes and the Prometheus exposition of a
running instance, evaluates a small set of failure-mode checks and prints a
PASS/WARN/FAIL summary. It is meant to be run from cron (or by hand) on the
host that runs the API, so an operator can notice a misbehaving system without
deploying a monitoring stack.

    python -m scripts.metrics_check --base-url http://127.0.0.1:8000

The thresholds below are **suggestions for an operator to tune**, not
validated SLOs: this repository has no measured traffic baseline, so no
number here is a promise. See ``docs/metrics.md``.

Exit status: 0 when no check failed, 1 when at least one check failed.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_BASE_URL = "http://127.0.0.1:8000"

# --- Suggested thresholds. Tune these for the deployment; they are not SLOs. ---
SUGGESTED_5XX_FAIL_RATIO = 0.05
SUGGESTED_5XX_WARN_RATIO = 0.01
SUGGESTED_4XX_WARN_RATIO = 0.25
SUGGESTED_MIN_REQUESTS_FOR_RATIO = 20
SUGGESTED_UNPRICED_COST_WARN_RATIO = 0.50
SUGGESTED_UNPRICED_COST_GROWTH = 0.25
SUGGESTED_OVERRIDE_RATE_DELTA = 0.20
SUGGESTED_OVERRIDE_RATE_HIGH = 0.50
SUGGESTED_MIN_REVIEWS_FOR_RATE = 5

#: Rules whose disappearance is a safety-relevant silent failure.
SAFETY_RULE_IDS: tuple[str, ...] = (
    "SAFE-001",
    "PRIV-001",
    "PRIV-002",
    "ACT-001",
    "ACT-002",
    "HUM-001",
)

LABEL_PATTERN = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:[^"\\]|\\.)*)"')
SAMPLE_PATTERN = re.compile(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(\{.*\})?\s+(\S+)\s*$")

PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"


@dataclass
class Sample:
    name: str
    labels: dict[str, str]
    value: float


@dataclass
class Check:
    name: str
    status: str
    detail: str


@dataclass
class Baseline:
    recorded_at: str = ""
    rule_firing_analyses: dict[str, float] = field(default_factory=dict)
    unpriced_cost_ratio: float | None = None
    override_rate: float | None = None
    http_5xx_ratio: float | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "recorded_at": self.recorded_at,
            "rule_firing_analyses": self.rule_firing_analyses,
            "unpriced_cost_ratio": self.unpriced_cost_ratio,
            "override_rate": self.override_rate,
            "http_5xx_ratio": self.http_5xx_ratio,
        }

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> Baseline:
        return cls(
            recorded_at=str(payload.get("recorded_at", "")),
            rule_firing_analyses={
                str(key): float(value)
                for key, value in (payload.get("rule_firing_analyses") or {}).items()
            },
            unpriced_cost_ratio=_optional_float(payload.get("unpriced_cost_ratio")),
            override_rate=_optional_float(payload.get("override_rate")),
            http_5xx_ratio=_optional_float(payload.get("http_5xx_ratio")),
        )


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def unescape_label(value: str) -> str:
    return value.replace("\\n", "\n").replace('\\"', '"').replace("\\\\", "\\")


def parse_exposition(text: str) -> list[Sample]:
    """Parse a Prometheus text exposition into samples.

    Deliberately small: this project has no metrics client dependency, and the
    exposition format is simple enough to read directly.
    """
    samples: list[Sample] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = SAMPLE_PATTERN.match(line)
        if match is None:
            continue
        name, label_text, raw_value = match.groups()
        labels: dict[str, str] = {}
        if label_text:
            for key, value in LABEL_PATTERN.findall(label_text):
                labels[key] = unescape_label(value)
        try:
            value = float(raw_value)
        except ValueError:
            continue
        samples.append(Sample(name=name, labels=labels, value=value))
    return samples


def sample_value(samples: list[Sample], name: str, **labels: str) -> float | None:
    """Return the value of the sample with exactly these labels."""
    for sample in samples:
        if sample.name == name and sample.labels == labels:
            return sample.value
    return None


def sum_metric(samples: list[Sample], name: str) -> float:
    return sum(sample.value for sample in samples if sample.name == name)


def status_class_ratio(samples: list[Sample], wanted: str) -> tuple[float, float]:
    """Return (share of requests with this status class, total requests)."""
    total = 0.0
    matched = 0.0
    for sample in samples:
        if sample.name != "aiadvisory_http_requests_total":
            continue
        total += sample.value
        if sample.labels.get("status_class") == wanted:
            matched += sample.value
    return (matched / total if total else 0.0), total


def fetch(base_url: str, path: str, token: str | None, timeout: float) -> tuple[int, str]:
    """Return (status, body). HTTP error responses are returned, not raised."""
    headers = {"Accept": "text/plain"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(  # noqa: S310 - operator supplied local URL
        f"{base_url}{path}", headers=headers, method="GET"
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as error:
        return 0, str(error)


def evaluate(
    samples: list[Sample],
    health_status: int,
    health_body: str,
    ready_status: int,
    baseline: Baseline | None,
) -> list[Check]:
    """Evaluate the alert conditions against one scrape."""
    checks: list[Check] = []

    if health_status == 0:
        checks.append(Check("health_endpoint", FAIL, "could not be reached"))
    elif health_status != 200:
        checks.append(Check("health_endpoint", FAIL, f"HTTP {health_status}"))
    else:
        try:
            payload = json.loads(health_body)
        except ValueError:
            payload = {}
        state = payload.get("status")
        if state == "ok":
            checks.append(Check("health_endpoint", PASS, "HTTP 200, status=ok"))
        else:
            checks.append(Check("health_endpoint", FAIL, f"HTTP 200 but status={state!r}"))

    if ready_status == 0:
        checks.append(Check("readiness_endpoint", FAIL, "could not be reached"))
    elif ready_status == 200:
        checks.append(Check("readiness_endpoint", PASS, "HTTP 200"))
    else:
        checks.append(
            Check(
                "readiness_endpoint",
                FAIL,
                f"HTTP {ready_status} (database or migrations are not ready)",
            )
        )

    database_up = sample_value(samples, "aiadvisory_metrics_database_up")
    if database_up is None:
        checks.append(
            Check(
                "database_metrics",
                FAIL,
                "the metrics exposition did not report database availability",
            )
        )
    elif database_up == 1:
        checks.append(Check("database_metrics", PASS, "database-derived metrics present"))
    else:
        checks.append(
            Check(
                "database_metrics",
                FAIL,
                "database-derived metrics are unavailable (aiadvisory_metrics_database_up 0)",
            )
        )

    fivexx_ratio, total = status_class_ratio(samples, "5xx")
    if total < SUGGESTED_MIN_REQUESTS_FOR_RATIO:
        checks.append(
            Check(
                "http_5xx_rate",
                WARN,
                f"only {total:.0f} requests observed; 5xx rate {fivexx_ratio:.2%} is not "
                f"meaningful yet (suggested minimum {SUGGESTED_MIN_REQUESTS_FOR_RATIO})",
            )
        )
    elif fivexx_ratio > SUGGESTED_5XX_FAIL_RATIO:
        checks.append(
            Check(
                "http_5xx_rate",
                FAIL,
                f"5xx rate {fivexx_ratio:.2%} above the suggested {SUGGESTED_5XX_FAIL_RATIO:.0%} "
                f"over {total:.0f} requests",
            )
        )
    elif fivexx_ratio > SUGGESTED_5XX_WARN_RATIO:
        checks.append(
            Check(
                "http_5xx_rate",
                WARN,
                f"5xx rate {fivexx_ratio:.2%} above the suggested {SUGGESTED_5XX_WARN_RATIO:.0%} "
                f"over {total:.0f} requests",
            )
        )
    else:
        checks.append(
            Check("http_5xx_rate", PASS, f"5xx rate {fivexx_ratio:.2%} over {total:.0f} requests")
        )

    fourxx_ratio, _ = status_class_ratio(samples, "4xx")
    if fourxx_ratio > SUGGESTED_4XX_WARN_RATIO:
        checks.append(
            Check(
                "http_4xx_rate",
                WARN,
                f"4xx rate {fourxx_ratio:.2%} above the suggested {SUGGESTED_4XX_WARN_RATIO:.0%} "
                "(often credential or client errors, not a service fault)",
            )
        )
    else:
        checks.append(Check("http_4xx_rate", PASS, f"4xx rate {fourxx_ratio:.2%}"))

    current_rules = {
        sample.labels["rule_id"]: sample.value
        for sample in samples
        if sample.name == "aiadvisory_rule_firing_analyses" and "rule_id" in sample.labels
    }
    unrecognised = current_rules.get("other", 0.0)
    if unrecognised > 0:
        checks.append(
            Check(
                "rule_id_vocabulary",
                FAIL,
                f"{unrecognised:.0f} analyses reported a rule id outside the documented rule-id "
                "format (bucket 'other'); a safety rule could be invisible to this check",
            )
        )
    else:
        checks.append(Check("rule_id_vocabulary", PASS, "all rule ids are in the known format"))
    if baseline is None:
        checks.append(
            Check(
                "safety_rule_firing",
                WARN,
                "no baseline available; a rule that stops firing cannot be detected without one",
            )
        )
    else:
        regressed = [
            rule_id
            for rule_id in SAFETY_RULE_IDS
            if baseline.rule_firing_analyses.get(rule_id, 0) > 0
            and current_rules.get(rule_id, 0) == 0
        ]
        if regressed:
            checks.append(
                Check(
                    "safety_rule_firing",
                    FAIL,
                    f"rule(s) {', '.join(sorted(regressed))} used to fire and no longer do",
                )
            )
        else:
            checks.append(
                Check("safety_rule_firing", PASS, "no watched safety rule stopped firing")
            )

    priced = sum_metric(samples, "aiadvisory_ai_cost_priced_runs_total")
    unpriced = sum_metric(samples, "aiadvisory_ai_cost_unpriced_runs_total")
    runs = priced + unpriced
    if runs == 0:
        checks.append(Check("cost_visibility", PASS, "no AI runs recorded"))
        unpriced_ratio = None
    else:
        unpriced_ratio = unpriced / runs
        if unpriced_ratio > SUGGESTED_UNPRICED_COST_WARN_RATIO:
            checks.append(
                Check(
                    "cost_visibility",
                    WARN,
                    f"{unpriced:.0f} of {runs:.0f} AI runs have no operator-supplied cost "
                    f"({unpriced_ratio:.0%} unpriced; suggested limit "
                    f"{SUGGESTED_UNPRICED_COST_WARN_RATIO:.0%})",
                )
            )
        else:
            checks.append(
                Check(
                    "cost_visibility",
                    PASS,
                    f"{priced:.0f} of {runs:.0f} AI runs are priced "
                    f"({unpriced_ratio:.0%} unpriced)",
                )
            )
        if (
            baseline is not None
            and baseline.unpriced_cost_ratio is not None
            and unpriced_ratio - baseline.unpriced_cost_ratio > SUGGESTED_UNPRICED_COST_GROWTH
        ):
            checks.append(
                Check(
                    "cost_visibility_growth",
                    WARN,
                    f"unpriced cost share grew from {baseline.unpriced_cost_ratio:.0%} to "
                    f"{unpriced_ratio:.0%} (suggested growth limit "
                    f"{SUGGESTED_UNPRICED_COST_GROWTH:.0%})",
                )
            )

    reviews = sum_metric(samples, "aiadvisory_reviews_total")
    overrides = sum_metric(samples, "aiadvisory_fde_overrides_total")
    if reviews == 0:
        checks.append(Check("override_rate", PASS, "no FDE reviews recorded"))
        override_rate = None
    elif reviews < SUGGESTED_MIN_REVIEWS_FOR_RATE:
        checks.append(
            Check(
                "override_rate",
                WARN,
                f"only {reviews:.0f} reviews recorded; the override rate is not judged "
                f"(suggested minimum {SUGGESTED_MIN_REVIEWS_FOR_RATE})",
            )
        )
        override_rate = None
    else:
        override_rate = overrides / reviews
        if override_rate > SUGGESTED_OVERRIDE_RATE_HIGH:
            checks.append(
                Check(
                    "override_rate",
                    WARN,
                    f"override rate {override_rate:.0%} above the suggested "
                    f"{SUGGESTED_OVERRIDE_RATE_HIGH:.0%}",
                )
            )
        elif (
            baseline is not None
            and baseline.override_rate is not None
            and abs(override_rate - baseline.override_rate) > SUGGESTED_OVERRIDE_RATE_DELTA
        ):
            checks.append(
                Check(
                    "override_rate",
                    WARN,
                    f"override rate changed from {baseline.override_rate:.0%} to "
                    f"{override_rate:.0%} (suggested change limit "
                    f"{SUGGESTED_OVERRIDE_RATE_DELTA:.0%})",
                )
            )
        else:
            checks.append(
                Check(
                    "override_rate",
                    PASS,
                    f"override rate {override_rate:.0%} over {reviews:.0f} reviews",
                )
            )
    return checks


def build_baseline(samples: list[Sample]) -> Baseline:
    priced = sum_metric(samples, "aiadvisory_ai_cost_priced_runs_total")
    unpriced = sum_metric(samples, "aiadvisory_ai_cost_unpriced_runs_total")
    runs = priced + unpriced
    reviews = sum_metric(samples, "aiadvisory_reviews_total")
    overrides = sum_metric(samples, "aiadvisory_fde_overrides_total")
    fivexx_ratio, _ = status_class_ratio(samples, "5xx")
    return Baseline(
        recorded_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        rule_firing_analyses={
            sample.labels["rule_id"]: sample.value
            for sample in samples
            if sample.name == "aiadvisory_rule_firing_analyses" and "rule_id" in sample.labels
        },
        unpriced_cost_ratio=(unpriced / runs) if runs else None,
        override_rate=(overrides / reviews) if reviews else None,
        http_5xx_ratio=fivexx_ratio,
    )


def load_baseline(path: Path) -> Baseline | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    return Baseline.from_json(payload)


def default_baseline_path() -> Path:
    state_home = Path.home() / ".local" / "state"
    return state_home / "aiadvisory" / "metrics-baseline.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument(
        "--token",
        default=None,
        help="API credential for the /metrics endpoint (not needed in demo mode)",
    )
    parser.add_argument("--baseline", type=Path, default=None)
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="write the current values as the baseline for the next run",
    )
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--json", action="store_true", help="print the checks as JSON")
    arguments = parser.parse_args(argv)

    base_url = arguments.base_url.rstrip("/")
    baseline_path = arguments.baseline or default_baseline_path()
    baseline = load_baseline(baseline_path)

    health_status, health_body = fetch(base_url, "/health", arguments.token, arguments.timeout)
    ready_status, _ = fetch(base_url, "/ready", arguments.token, arguments.timeout)
    metrics_status, metrics_body = fetch(base_url, "/metrics", arguments.token, arguments.timeout)

    if metrics_status == 0:
        checks = [Check("metrics_endpoint", FAIL, f"could not be reached: {metrics_body[:120]}")]
        samples: list[Sample] = []
    elif metrics_status == 401 or metrics_status == 403:
        checks = [
            Check(
                "metrics_endpoint",
                FAIL,
                f"HTTP {metrics_status}: the metrics endpoint requires a credential; pass --token",
            )
        ]
        samples = []
    elif metrics_status != 200:
        checks = [Check("metrics_endpoint", FAIL, f"HTTP {metrics_status}")]
        samples = []
    else:
        samples = parse_exposition(metrics_body)
        if not samples:
            checks = [
                Check(
                    "metrics_endpoint",
                    FAIL,
                    "HTTP 200 but the exposition contained no parsable samples",
                )
            ]
        else:
            checks = [Check("metrics_endpoint", PASS, f"HTTP 200, {len(samples)} samples")]

    if samples:
        checks.extend(evaluate(samples, health_status, health_body, ready_status, baseline))

    failures = [check for check in checks if check.status == FAIL]
    warnings = [check for check in checks if check.status == WARN]

    if arguments.update_baseline and samples:
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        baseline_path.write_text(
            json.dumps(build_baseline(samples).to_json(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    if arguments.json:
        print(
            json.dumps(
                {
                    "base_url": base_url,
                    "checks": [
                        {"name": c.name, "status": c.status, "detail": c.detail} for c in checks
                    ],
                    "failures": len(failures),
                    "warnings": len(warnings),
                },
                indent=2,
            )
        )
    else:
        print("AI Opportunity Advisor - local metrics check")
        print(f"base-url: {base_url}")
        for check in checks:
            print(f"[{check.status}] {check.name}: {check.detail}")
        print(
            f"SUMMARY: {len(checks)} checks, {len(checks) - len(failures) - len(warnings)} PASS, "
            f"{len(warnings)} WARN, {len(failures)} FAIL"
        )
        print("RESULT:", "FAIL" if failures else ("WARN" if warnings else "PASS"))
        if baseline is None and not arguments.update_baseline:
            print(
                "note: no baseline yet; run again with --update-baseline to enable the "
                "change-detection checks."
            )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
