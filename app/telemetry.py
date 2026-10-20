"""Sanitised request logging and dependency-free operator metrics.

Two responsibilities, one redaction policy:

* ``instrument_request`` keeps the existing sanitised JSON request log.
* ``render_metrics`` produces a Prometheus text exposition that an operator can
  scrape without reading logs, and that a local check command can evaluate.

Redaction rules applied to both:

* No request or response body, query string, header, credential, token, person
  name, free-text field, assessment/organization/reviewer identifier, prompt,
  provider output or error message is ever logged or exposed. The only
  request-scoped value in the log line is a random request id, and that id is
  never exposed through the metrics endpoint.
* Metric labels come from closed, code-controlled vocabularies only (route
  template, HTTP method, status class, rule id, AI feature/provider/model,
  policy and prompt version). Every label value is pattern-checked and the
  number of distinct values per label is capped, so a URL, identifier or
  message can never create unbounded label cardinality.
* Database-derived metrics are counts and sums only, and they are tenant
  scoped. There is no title, owner, reviewer, rationale, override reason,
  assessment id or organization id anywhere in the output.
* Cost is operator supplied. An unknown cost is reported as unpriced; it is
  never estimated and never silently dropped.
"""

import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import uuid4

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.ai.schemas import PROMPT_VERSION
from app.db.models import AIRunRow, AssessmentRow, ReviewRow
from app.services.scoring import POLICY_VERSION

logger = logging.getLogger("aiadvisory.requests")
if not logger.handlers:
    logger.addHandler(logging.StreamHandler())
logger.setLevel(logging.INFO)

#: Application version, reported as a low-cardinality build label.
APP_VERSION = "0.8.0"

METRICS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"

#: Upper bounds (seconds) for the request latency histogram.
DURATION_BUCKETS: tuple[float, ...] = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
)

#: Label vocabularies. ``(pattern, maximum number of distinct values)``.
#: A value that does not match, or that would exceed the cap, is replaced by
#: ``other`` so the exposition stays bounded and stays free of caller data.
LABEL_RULES: dict[str, tuple[re.Pattern[str], int]] = {
    "route": (re.compile(r"^(?:/[A-Za-z0-9_{}./-]{0,120}|unknown)$"), 64),
    "method": (re.compile(r"^[A-Z]{3,10}$"), 8),
    "status_class": (re.compile(r"^[1-5]xx$"), 8),
    "status": (re.compile(r"^[a-z][a-z_]{0,23}$"), 16),
    # Rule ids are code-controlled ("ACT-002", "PRIV-001", ...). The pattern is
    # deliberately generous so a newly added rule still gets its own label
    # instead of being hidden in the overflow bucket.
    "rule_id": (re.compile(r"^[A-Z]{2,8}-[0-9]{2,4}$"), 64),
    "feature": (re.compile(r"^[a-z][a-z_]{0,23}$"), 16),
    "provider": (re.compile(r"^[A-Za-z0-9._-]{1,40}$"), 16),
    "model": (re.compile(r"^[A-Za-z0-9._:/+-]{1,60}$"), 16),
    "policy_version": (re.compile(r"^[A-Za-z0-9._-]{1,32}$"), 16),
    "prompt_version": (re.compile(r"^[A-Za-z0-9._-]{1,32}$"), 16),
    "version": (re.compile(r"^[A-Za-z0-9.+-]{1,32}$"), 8),
}

#: Value used when a label is rejected or the cardinality cap is reached.
OVERFLOW_LABEL = "other"


class LabelGuard:
    """Pattern-check and cap every label value.

    Unbounded label cardinality is both a denial-of-service vector and a leak,
    so labels are only accepted from the closed vocabularies in
    ``LABEL_RULES`` and only up to a fixed number of distinct values.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._seen: dict[str, set[str]] = {}

    def check(self, name: str, value: Any) -> str:
        rule = LABEL_RULES.get(name)
        if rule is None:
            return OVERFLOW_LABEL
        pattern, limit = rule
        if not isinstance(value, str) or not pattern.fullmatch(value):
            return OVERFLOW_LABEL
        with self._lock:
            seen = self._seen.setdefault(name, set())
            if value in seen:
                return value
            if len(seen) >= limit:
                return OVERFLOW_LABEL
            seen.add(value)
            return value

    def labels(self, **values: Any) -> dict[str, str]:
        return {name: self.check(name, value) for name, value in values.items()}


LABEL_GUARD = LabelGuard()


@dataclass
class MetricFamily:
    """One Prometheus metric family."""

    name: str
    kind: str
    help: str
    samples: list[tuple[dict[str, str], Any, str]] = field(default_factory=list)

    def add(self, labels: dict[str, str], value: Any, suffix: str = "") -> None:
        """Add a sample. ``suffix`` is ``""``, ``"_bucket"``, ``"_sum"`` or ``"_count"``."""
        self.samples.append((labels, value, suffix))


class RequestMetrics:
    """In-process request counters and latency histogram.

    The counters live in the API process: they are reset on restart and are
    not shared between replicas. The durable, database-derived gauges in
    ``render_metrics`` are the ones an operator should alert on.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requests: dict[tuple[str, str, str], int] = {}
        self._durations: dict[tuple[str, str], dict[str, Any]] = {}

    def record(self, route: str, method: str, status_class: str, seconds: float) -> None:
        labels = LABEL_GUARD.labels(route=route, method=method, status_class=status_class)
        with self._lock:
            key = (labels["route"], labels["method"], labels["status_class"])
            self._requests[key] = self._requests.get(key, 0) + 1
            duration_key = (labels["route"], labels["method"])
            entry = self._durations.get(duration_key)
            if entry is None:
                entry = {"buckets": [0] * len(DURATION_BUCKETS), "sum": 0.0, "count": 0}
                self._durations[duration_key] = entry
            for index, edge in enumerate(DURATION_BUCKETS):
                if seconds <= edge:
                    # Only the first matching bucket is incremented; the
                    # exposition renders the cumulative counts.
                    entry["buckets"][index] += 1
                    break
            entry["sum"] += seconds
            entry["count"] += 1

    def snapshot(self) -> tuple[dict[tuple[str, str, str], int], dict[tuple[str, str], dict]]:
        with self._lock:
            return dict(self._requests), {
                key: dict(value) for key, value in self._durations.items()
            }


REQUEST_METRICS = RequestMetrics()


def status_class(status_code: int) -> str:
    """Map a status code to a bounded class label (never the raw code set)."""
    if 100 <= status_code < 200:
        return "1xx"
    if 200 <= status_code < 300:
        return "2xx"
    if 300 <= status_code < 400:
        return "3xx"
    if 400 <= status_code < 500:
        return "4xx"
    return "5xx"


def route_template(request: Request) -> str:
    """The matched route template, or ``unknown`` for an unmatched path.

    The raw path is never used: an unmatched URL must not become a label.
    """
    route = request.scope.get("route")
    template = getattr(route, "path", None)
    if not isinstance(template, str) or not template:
        return "unknown"
    return template


async def instrument_request(request: Request, call_next):
    started = time.perf_counter()
    identifier = str(uuid4())
    try:
        response: Response = await call_next(request)
    except Exception:
        # The failure-recovery drill found that an unhandled exception bypassed
        # this middleware entirely: the reply carried no X-Request-ID, no
        # nosniff and no Cache-Control, and no http.request line was written to
        # the log. A reply that leaves the application without its security
        # headers is worse than an ugly one, and a request that leaves no trace
        # is worse than a failed one.
        #
        # The exception is logged with its traceback for the operator, and the
        # client gets a clean 500 that still carries the standard headers. The
        # exception is NOT swallowed silently: it is logged at error level.
        elapsed = time.perf_counter() - started
        logger.exception(
            json.dumps(
                {
                    "event": "http.request",
                    "request_id": identifier,
                    "method": request.method,
                    "route": route_template(request),
                    "status": 500,
                    "duration_ms": round(elapsed * 1000, 1),
                    "outcome": "unhandled_exception",
                }
            )
        )
        response = JSONResponse(status_code=500, content={"detail": "Internal server error"})
    response.headers["X-Request-ID"] = identifier
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Frame-Options"] = "DENY"
    template = route_template(request)
    elapsed = time.perf_counter() - started
    logger.info(
        json.dumps(
            {
                "event": "http.request",
                "request_id": identifier,
                "method": request.method,
                "route": template,
                "status": response.status_code,
                "duration_ms": round(elapsed * 1000, 1),
            }
        )
    )
    try:
        REQUEST_METRICS.record(
            template, request.method, status_class(response.status_code), elapsed
        )
    except Exception:  # pragma: no cover - metrics must never break a request
        logger.warning('{"event": "http.metrics_record_failed"}')
    return response


def escape_label(value: str) -> str:
    """Escape a label value for the Prometheus text exposition format."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def format_value(value: Any) -> str:
    """Render a metric value in the Prometheus text format."""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, Decimal):
        return format(value, "f")
    number = float(value)
    if number != number:
        return "NaN"
    if number == float("inf"):
        return "+Inf"
    if number == float("-inf"):
        return "-Inf"
    return repr(number)


def render_families(families: list[MetricFamily]) -> str:
    """Render metric families as a Prometheus text exposition document."""
    lines: list[str] = []
    for family in families:
        lines.append(f"# HELP {family.name} {family.help}")
        lines.append(f"# TYPE {family.name} {family.kind}")
        for labels, value, suffix in family.samples:
            name = f"{family.name}{suffix}"
            if labels:
                rendered = ",".join(
                    f'{key}="{escape_label(labels[key])}"' for key in sorted(labels)
                )
                lines.append(f"{name}{{{rendered}}} {format_value(value)}")
            else:
                lines.append(f"{name} {format_value(value)}")
    return "\n".join(lines) + "\n"


def _http_families() -> list[MetricFamily]:
    requests, durations = REQUEST_METRICS.snapshot()
    counted = MetricFamily(
        "aiadvisory_http_requests_total",
        "counter",
        "HTTP requests served by this process, by route template, method and status class.",
    )
    for (route, method, status), value in sorted(requests.items()):
        counted.add(
            {"route": route, "method": method, "status_class": status},
            value,
        )
    latency = MetricFamily(
        "aiadvisory_http_request_duration_seconds",
        "histogram",
        "HTTP request latency in seconds, by route template and method.",
    )
    for (route, method), entry in sorted(durations.items()):
        cumulative = 0
        for index, edge in enumerate(DURATION_BUCKETS):
            cumulative += entry["buckets"][index]
            latency.add(
                {"route": route, "method": method, "le": format_value(edge)},
                cumulative,
                "_bucket",
            )
        latency.add({"route": route, "method": method, "le": "+Inf"}, entry["count"], "_bucket")
        latency.add({"route": route, "method": method}, round(entry["sum"], 6), "_sum")
        latency.add({"route": route, "method": method}, entry["count"], "_count")
    return [counted, latency]


def _assessment_scope(organization_id: str | None):
    """Restrict a query to one tenant, or to everything in demo mode."""
    if organization_id is None:
        return []
    return [AssessmentRow.organization_id == organization_id]


def _parse_cost(raw: Any) -> Decimal | None:
    """Parse an operator-supplied cost string. Anything unusable stays unknown."""
    if raw is None:
        return None
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return None
    if not value.is_finite() or value < 0:
        return None
    return value


def _database_families(db: Session, organization_id: str | None) -> list[MetricFamily]:
    """Counts and sums derived from stored records. No content, no identifiers."""
    families: list[MetricFamily] = []

    assessments = MetricFamily(
        "aiadvisory_assessments_total",
        "gauge",
        "Stored assessments by lifecycle status.",
    )
    assessment_query = select(AssessmentRow.status, func.count()).group_by(AssessmentRow.status)
    for condition in _assessment_scope(organization_id):
        assessment_query = assessment_query.where(condition)
    for status, count in db.execute(assessment_query).all():
        assessments.add({"status": LABEL_GUARD.check("status", status)}, int(count))
    families.append(assessments)

    current_analysis = [
        AssessmentRow.analysis_snapshot.is_not(None),
        AssessmentRow.analysis_revision == AssessmentRow.revision,
    ]
    analyses_query = select(func.count()).select_from(AssessmentRow).where(*current_analysis)
    for condition in _assessment_scope(organization_id):
        analyses_query = analyses_query.where(condition)
    analysis_total = int(db.execute(analyses_query).scalar_one())
    analyses = MetricFamily(
        "aiadvisory_analyses_total",
        "gauge",
        "Assessments with a current (non-stale) analysis snapshot.",
    )
    analyses.add({}, analysis_total)
    families.append(analyses)

    snapshots_query = select(AssessmentRow.analysis_snapshot).where(*current_analysis)
    for condition in _assessment_scope(organization_id):
        snapshots_query = snapshots_query.where(condition)
    rule_hits = MetricFamily(
        "aiadvisory_rule_hits_total",
        "gauge",
        "Policy rule hits recorded in current analyses, by rule id.",
    )
    rule_firing = MetricFamily(
        "aiadvisory_rule_firing_analyses",
        "gauge",
        "Current analyses in which a policy rule fired at least once, by rule id.",
    )
    policy_of_analysis = MetricFamily(
        "aiadvisory_analyses_policy_version",
        "gauge",
        "Current analyses by the policy version that produced them.",
    )
    hit_counts: dict[str, int] = {}
    firing_counts: dict[str, int] = {}
    policy_counts: dict[str, int] = {}
    for (snapshot,) in db.execute(snapshots_query).all():
        if not isinstance(snapshot, dict):
            continue
        tasks = snapshot.get("tasks")
        if not isinstance(tasks, list):
            continue
        seen_here: set[str] = set()
        for task in tasks:
            if not isinstance(task, dict):
                continue
            hits = task.get("rule_hits")
            if not isinstance(hits, list):
                continue
            for hit in hits:
                if not isinstance(hit, dict):
                    continue
                rule_id = LABEL_GUARD.check("rule_id", hit.get("id"))
                hit_counts[rule_id] = hit_counts.get(rule_id, 0) + 1
                seen_here.add(rule_id)
        for rule_id in seen_here:
            firing_counts[rule_id] = firing_counts.get(rule_id, 0) + 1
        version = snapshot.get("policy_version")
        if isinstance(version, str):
            key = LABEL_GUARD.check("policy_version", version)
            policy_counts[key] = policy_counts.get(key, 0) + 1
    for rule_id, count in sorted(hit_counts.items()):
        rule_hits.add({"rule_id": rule_id}, count)
    for rule_id, count in sorted(firing_counts.items()):
        rule_firing.add({"rule_id": rule_id}, count)
    for version, count in sorted(policy_counts.items()):
        policy_of_analysis.add({"policy_version": version}, count)
    families.extend([rule_hits, rule_firing, policy_of_analysis])

    reviews_query = select(func.count()).select_from(ReviewRow)
    if organization_id is not None:
        reviews_query = reviews_query.join(
            AssessmentRow, ReviewRow.assessment_id == AssessmentRow.id
        ).where(AssessmentRow.organization_id == organization_id)
    review_total = int(db.execute(reviews_query).scalar_one())
    overrides_query = (
        select(func.count())
        .select_from(ReviewRow)
        .where(
            ReviewRow.override_reason.is_not(None),
            func.length(func.trim(ReviewRow.override_reason)) > 0,
        )
    )
    if organization_id is not None:
        overrides_query = overrides_query.join(
            AssessmentRow, ReviewRow.assessment_id == AssessmentRow.id
        ).where(AssessmentRow.organization_id == organization_id)
    override_total = int(db.execute(overrides_query).scalar_one())
    reviews = MetricFamily(
        "aiadvisory_reviews_total", "gauge", "Stored FDE reviews (all revisions)."
    )
    reviews.add({}, review_total)
    families.append(reviews)
    overrides = MetricFamily(
        "aiadvisory_fde_overrides_total",
        "gauge",
        "FDE reviews that record an override of the deterministic recommendation.",
    )
    overrides.add({}, override_total)
    families.append(overrides)

    runs_query = select(AIRunRow)
    if organization_id is not None:
        runs_query = runs_query.join(
            AssessmentRow, AIRunRow.assessment_id == AssessmentRow.id
        ).where(AssessmentRow.organization_id == organization_id)
    run_counts: dict[tuple[str, str, str], int] = {}
    input_tokens = 0
    output_tokens = 0
    unknown_token_runs = 0
    priced_runs = 0
    unpriced_runs = 0
    cost_total = Decimal(0)
    for run in db.execute(runs_query).scalars().all():
        feature = LABEL_GUARD.check("feature", run.feature)
        provider = LABEL_GUARD.check("provider", run.provider)
        model = LABEL_GUARD.check("model", run.model)
        key = (feature, provider, model)
        run_counts[key] = run_counts.get(key, 0) + 1
        if run.input_tokens is None or run.output_tokens is None:
            unknown_token_runs += 1
        else:
            input_tokens += int(run.input_tokens)
            output_tokens += int(run.output_tokens)
        cost = _parse_cost(run.estimated_cost_usd)
        if cost is None:
            unpriced_runs += 1
        else:
            priced_runs += 1
            cost_total += cost
    runs_family = MetricFamily(
        "aiadvisory_ai_runs_total",
        "gauge",
        "Stored AI proposal runs, by feature, provider and model.",
    )
    for (feature, provider, model), count in sorted(run_counts.items()):
        runs_family.add({"feature": feature, "provider": provider, "model": model}, count)
    families.append(runs_family)
    input_token_family = MetricFamily(
        "aiadvisory_ai_input_tokens_total",
        "gauge",
        "Input tokens reported by the provider, summed over runs with known token counts.",
    )
    input_token_family.add({}, input_tokens)
    families.append(input_token_family)
    output_token_family = MetricFamily(
        "aiadvisory_ai_output_tokens_total",
        "gauge",
        "Output tokens reported by the provider, summed over runs with known token counts.",
    )
    output_token_family.add({}, output_tokens)
    families.append(output_token_family)
    unknown_token_family = MetricFamily(
        "aiadvisory_ai_runs_tokens_unknown_total",
        "gauge",
        "AI runs whose provider token counts are unknown (excluded from the totals).",
    )
    unknown_token_family.add({}, unknown_token_runs)
    families.append(unknown_token_family)
    cost_family = MetricFamily(
        "aiadvisory_ai_cost_usd_total",
        "gauge",
        "Operator-supplied cost total over priced AI runs. Unpriced runs are excluded, "
        "never estimated.",
    )
    cost_family.add({}, cost_total)
    families.append(cost_family)
    priced_family = MetricFamily(
        "aiadvisory_ai_cost_priced_runs_total",
        "gauge",
        "AI runs with an operator-supplied cost.",
    )
    priced_family.add({}, priced_runs)
    families.append(priced_family)
    unpriced_family = MetricFamily(
        "aiadvisory_ai_cost_unpriced_runs_total",
        "gauge",
        "AI runs without a usable operator-supplied cost (reported as unpriced).",
    )
    unpriced_family.add({}, unpriced_runs)
    families.append(unpriced_family)
    return families


def render_metrics(db: Session, organization_id: str | None) -> str:
    """Render the Prometheus exposition for the running application.

    ``organization_id`` scopes every database-derived gauge to one tenant.
    ``None`` means the caller is allowed to see the whole database, which is
    only the case in the explicit local demo mode.
    """
    families: list[MetricFamily] = []
    database_up = MetricFamily(
        "aiadvisory_metrics_database_up",
        "gauge",
        "1 when the database-derived metrics could be collected, 0 otherwise.",
    )
    try:
        families.extend(_database_families(db, organization_id))
        database_up.add({}, 1)
    except SQLAlchemyError:
        # A failing database must not hide the HTTP metrics; the missing
        # families are visible as this gauge reporting 0.
        database_up.add({}, 0)
    families.insert(0, database_up)

    build = MetricFamily(
        "aiadvisory_build_info",
        "gauge",
        "Build information of the running application.",
    )
    build.add({"version": LABEL_GUARD.check("version", APP_VERSION)}, 1)
    policy = MetricFamily(
        "aiadvisory_policy_version_info",
        "gauge",
        "Policy version of the running deterministic engine.",
    )
    policy.add({"policy_version": LABEL_GUARD.check("policy_version", POLICY_VERSION)}, 1)
    prompt = MetricFamily(
        "aiadvisory_ai_prompt_version_info",
        "gauge",
        "Prompt version of the running AI advisory prompts.",
    )
    prompt.add({"prompt_version": LABEL_GUARD.check("prompt_version", PROMPT_VERSION)}, 1)
    families.extend([build, policy, prompt])

    scrape = MetricFamily(
        "aiadvisory_metrics_scrape_timestamp_seconds",
        "gauge",
        "Unix timestamp of this scrape.",
    )
    scrape.add({}, round(time.time(), 3))
    families.append(scrape)
    families.extend(_http_families())
    return render_families(families)
