"""Tests for the Prometheus metrics endpoint and the local metrics check command.

The exposition format is parsed with a tiny in-test parser: this project has
no metrics client dependency and the format is simple enough to read
directly. The parser is intentionally independent of ``scripts/metrics_check``
so a bug in one does not hide a bug in the other.
"""

import json
import re
import subprocess
import sys
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.db.models import (
    AIRunRow,
    ApiCredentialRow,
    Base,
    OrganizationRow,
)
from app.db.session import get_db
from app.main import app
from app.security import digest_token, make_token

REPO_ROOT = Path(__file__).resolve().parents[1]

SAMPLE_PATTERN = re.compile(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(.*)\})?\s+(\S+)$")
LABEL_PATTERN = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:[^"\\]|\\.)*)"')


def parse_prometheus(text: str) -> dict[str, list[tuple[dict[str, str], float]]]:
    """Parse a Prometheus text exposition into ``name -> [(labels, value)]``."""
    parsed: dict[str, list[tuple[dict[str, str], float]]] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = SAMPLE_PATTERN.match(line)
        assert match is not None, f"unparsable exposition line: {line!r}"
        name, label_text, raw_value = match.groups()
        labels: dict[str, str] = {}
        if label_text:
            for key, value in LABEL_PATTERN.findall(label_text):
                labels[key] = value.replace("\\n", "\n").replace('\\"', '"').replace("\\\\", "\\")
        parsed.setdefault(name, []).append((labels, float(raw_value)))
    return parsed


def sample_value(
    parsed: dict[str, list[tuple[dict[str, str], float]]], name: str, **labels: str
) -> float | None:
    """Return the first sample of ``name`` whose labels contain ``labels``."""
    for found_labels, value in parsed.get(name, []):
        if all(found_labels.get(key) == value for key, value in labels.items()):
            return value
    return None


def label_values(parsed: dict[str, list[tuple[dict[str, str], float]]], name: str) -> set[str]:
    values: set[str] = set()
    for labels, _ in parsed.get(name, []):
        values.update(labels.values())
    return values


@dataclass
class Api:
    """A test client plus the isolated database engine behind it."""

    client: TestClient
    engine: object


def _make_engine(path: Path):
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def api(tmp_path: Path):
    """A demo-mode API client backed by an isolated SQLite database."""
    engine = _make_engine(tmp_path / "metrics.sqlite3")

    def override_db():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as test_client:
        yield Api(client=test_client, engine=engine)
    app.dependency_overrides.clear()
    engine.dispose()


@pytest.fixture
def secure_api(monkeypatch, tmp_path: Path):
    """An authenticated (non-demo) API client backed by an isolated database."""
    monkeypatch.delenv("ADVISORY_DEMO_MODE", raising=False)
    engine = _make_engine(tmp_path / "secure.sqlite3")

    def override_db():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as test_client:
        yield Api(client=test_client, engine=engine)
    app.dependency_overrides.clear()
    engine.dispose()


def provision(engine, organization: str, subject: str, role: str = "viewer") -> str:
    """Create an organization and a credential for it; return the raw token."""
    with Session(engine) as session:
        row = OrganizationRow(name=organization)
        session.add(row)
        session.flush()
        token = make_token()
        session.add(
            ApiCredentialRow(
                organization_id=row.id,
                subject=subject,
                role=role,
                token_hash=digest_token(token),
                expires_at=datetime.now(UTC) + timedelta(days=1),
            )
        )
        session.commit()
    return token


def create_assessment(client: TestClient, title: str = "Customer workflow") -> str:
    response = client.post(
        "/api/v1/assessments",
        json={
            "title": title,
            "owner": "FDE Person",
            "business_problem": "Reduce manual work",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def add_task_and_analyze(client: TestClient, assessment_id: str) -> None:
    workflow = client.post(
        f"/api/v1/assessments/{assessment_id}/workflows", json={"name": "Workflow"}
    ).json()
    step = client.post(
        f"/api/v1/workflows/{workflow['id']}/steps", json={"name": "Step", "order": 1}
    ).json()
    created = client.post(
        f"/api/v1/workflow-steps/{step['id']}/tasks",
        json={
            "title": "Decide a regulated refund",
            "decision_type": "judgment",
            "signals": {"zero_error_tolerance": True, "sensitive_data": True},
            "actions": ["issue_refund"],
        },
    )
    assert created.status_code == 201, created.text
    analyzed = client.post(f"/api/v1/assessments/{assessment_id}/analyze")
    assert analyzed.status_code == 200, analyzed.text


def test_metrics_endpoint_returns_prometheus_text(api: Api) -> None:
    # One request first, so the in-process counter has a sample to expose.
    api.client.get("/health")
    response = api.client.get("/metrics")
    assert response.status_code == 200
    content_type = response.headers["content-type"]
    assert content_type.startswith("text/plain")
    assert "version=0.0.4" in content_type
    assert "charset=utf-8" in content_type

    for line in response.text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        assert SAMPLE_PATTERN.match(stripped), f"unparsable line: {stripped!r}"

    parsed = parse_prometheus(response.text)
    assert "aiadvisory_http_requests_total" in parsed
    assert "aiadvisory_http_request_duration_seconds_bucket" in parsed
    assert "aiadvisory_metrics_database_up" in parsed
    assert sample_value(parsed, "aiadvisory_metrics_database_up") == 1
    assert sample_value(parsed, "aiadvisory_build_info", version="0.8.0") == 1
    assert sample_value(parsed, "aiadvisory_policy_version_info", policy_version="rules-v2") == 1


def test_metrics_expose_no_content_identifiers_or_tokens(api: Api) -> None:
    assessment_id = create_assessment(api.client, title="SENSITIVE-TITLE-abc123")
    add_task_and_analyze(api.client, assessment_id)
    review = api.client.post(
        f"/api/v1/assessments/{assessment_id}/fde-review",
        json={
            "reviewer_id": "SENSITIVE-REVIEWER-xyz",
            "selected_category": "human_led",
            "rationale": "SENSITIVE-RATIONALE-42",
            "override_reason": "SENSITIVE-OVERRIDE-REASON-7",
        },
    )
    assert review.status_code == 201, review.text

    request_id = api.client.get("/health").headers["X-Request-ID"]

    body = api.client.get("/metrics").text
    for secret in (
        "SENSITIVE-TITLE-abc123",
        "SENSITIVE-REVIEWER-xyz",
        "SENSITIVE-RATIONALE-42",
        "SENSITIVE-OVERRIDE-REASON-7",
        "Reduce manual work",
        "FDE Person",
        assessment_id,
        request_id,
        "aia_",
        "Bearer",
        "customer workflow",
    ):
        assert secret not in body, f"metrics output leaked {secret!r}"


def test_metric_labels_stay_low_cardinality(api: Api) -> None:
    assessment_id = create_assessment(api.client)
    add_task_and_analyze(api.client, assessment_id)

    def database_labels() -> dict[str, set[str]]:
        parsed = parse_prometheus(api.client.get("/metrics").text)
        return {
            name: label_values(parsed, name)
            for name in parsed
            if not name.startswith("aiadvisory_http_")
        }

    before = database_labels()
    for index in range(15):
        extra = create_assessment(api.client, title=f"Customer workflow {index}")
        add_task_and_analyze(api.client, extra)

    after = database_labels()
    assert set(before) == set(after)
    for name in before:
        assert before[name] == after[name], f"label values of {name} grew with the data"
    assert after["aiadvisory_rule_firing_analyses"] == {"SAFE-001", "ACT-002", "PRIV-001"}

    body = api.client.get("/metrics").text
    for index in range(15):
        assert f"Customer workflow {index}" not in body


def test_rule_hits_are_counted_per_rule_id(api: Api) -> None:
    assessment_id = create_assessment(api.client)
    add_task_and_analyze(api.client, assessment_id)

    parsed = parse_prometheus(api.client.get("/metrics").text)
    assert sample_value(parsed, "aiadvisory_analyses_total") == 1
    assert sample_value(parsed, "aiadvisory_rule_firing_analyses", rule_id="SAFE-001") == 1
    assert sample_value(parsed, "aiadvisory_rule_firing_analyses", rule_id="PRIV-001") == 1
    assert sample_value(parsed, "aiadvisory_rule_firing_analyses", rule_id="ACT-002") == 1
    assert sample_value(parsed, "aiadvisory_rule_hits_total", rule_id="SAFE-001") == 1
    assert (
        sample_value(parsed, "aiadvisory_analyses_policy_version", policy_version="rules-v2") == 1
    )
    # No rule outside the documented vocabulary may appear.
    assert "other" not in label_values(parsed, "aiadvisory_rule_firing_analyses")


def test_unknown_cost_is_reported_as_unpriced_and_never_estimated(api: Api) -> None:
    assessment_id = create_assessment(api.client)
    add_task_and_analyze(api.client, assessment_id)

    with Session(api.engine) as session:
        priced = AIRunRow(
            assessment_id=assessment_id,
            assessment_revision=1,
            feature="workflow",
            proposal={},
            provider="openai",
            model="synthetic-model",
            prompt_version="3.0",
            schema_version="3.0",
            elapsed_ms=10,
            input_tokens=1200,
            output_tokens=300,
            estimated_cost_usd="0.004500",
            pricing_source="operator supplied price list",
        )
        unpriced = AIRunRow(
            assessment_id=assessment_id,
            assessment_revision=1,
            feature="risk",
            proposal={},
            provider="openai",
            model="synthetic-model",
            prompt_version="3.0",
            schema_version="3.0",
            elapsed_ms=20,
            input_tokens=None,
            output_tokens=None,
            estimated_cost_usd=None,
            pricing_source=None,
        )
        session.add_all([priced, unpriced])
        session.commit()

    parsed = parse_prometheus(api.client.get("/metrics").text)
    assert sample_value(parsed, "aiadvisory_ai_cost_priced_runs_total") == 1
    assert sample_value(parsed, "aiadvisory_ai_cost_unpriced_runs_total") == 1
    assert sample_value(parsed, "aiadvisory_ai_cost_usd_total") == pytest.approx(0.0045)
    assert sample_value(parsed, "aiadvisory_ai_input_tokens_total") == 1200
    assert sample_value(parsed, "aiadvisory_ai_output_tokens_total") == 300
    assert sample_value(parsed, "aiadvisory_ai_runs_tokens_unknown_total") == 1
    # The unpriced run is counted, not silently dropped and not estimated.
    assert sample_value(parsed, "aiadvisory_ai_runs_total", feature="risk") == 1
    assert sample_value(parsed, "aiadvisory_ai_runs_total", feature="workflow") == 1


def test_override_counts_are_reported(api: Api) -> None:
    assessment_id = create_assessment(api.client)
    add_task_and_analyze(api.client, assessment_id)
    response = api.client.post(
        f"/api/v1/assessments/{assessment_id}/fde-review",
        json={
            "reviewer_id": "FDE Person",
            "selected_category": "human_led",
            "rationale": "Regulated decision",
            "override_reason": "Different from the recommendation",
        },
    )
    assert response.status_code == 201, response.text
    parsed = parse_prometheus(api.client.get("/metrics").text)
    assert sample_value(parsed, "aiadvisory_reviews_total") == 1
    assert sample_value(parsed, "aiadvisory_fde_overrides_total") == 1


def test_metrics_require_a_credential_outside_demo_mode(secure_api: Api) -> None:
    assert secure_api.client.get("/metrics").status_code == 401
    token = provision(secure_api.engine, "Example", "viewer@example")
    authorized = secure_api.client.get("/metrics", headers={"Authorization": f"Bearer {token}"})
    assert authorized.status_code == 200
    assert "aiadvisory_http_requests_total" in authorized.text


def test_metrics_are_scoped_to_the_calling_organization(secure_api: Api) -> None:
    # An analyst credential may create assessments and may also read metrics.
    first = provision(secure_api.engine, "First Org", "a@example", role="analyst")
    second = provision(secure_api.engine, "Second Org", "b@example", role="analyst")

    created = secure_api.client.post(
        "/api/v1/assessments",
        json={"title": "First org assessment", "owner": "FDE Person"},
        headers={"Authorization": f"Bearer {first}"},
    )
    assert created.status_code == 201, created.text
    other = secure_api.client.post(
        "/api/v1/assessments",
        json={"title": "Second org assessment", "owner": "FDE Person"},
        headers={"Authorization": f"Bearer {second}"},
    )
    assert other.status_code == 201, other.text

    body = secure_api.client.get("/metrics", headers={"Authorization": f"Bearer {first}"}).text
    parsed = parse_prometheus(body)
    assert sample_value(parsed, "aiadvisory_assessments_total", status="draft") == 1
    assert "Second org assessment" not in body


HEALTHY_METRICS = """\
# HELP aiadvisory_http_requests_total HTTP requests.
# TYPE aiadvisory_http_requests_total counter
aiadvisory_http_requests_total{method="GET",route="/api/v1/assessments",status_class="2xx"} 100
aiadvisory_http_requests_total{method="GET",route="/api/v1/assessments",status_class="5xx"} 0
# HELP aiadvisory_metrics_database_up Database availability.
# TYPE aiadvisory_metrics_database_up gauge
aiadvisory_metrics_database_up 1
aiadvisory_rule_firing_analyses{rule_id="SAFE-001"} 3
aiadvisory_rule_firing_analyses{rule_id="PRIV-001"} 2
aiadvisory_ai_cost_priced_runs_total 5
aiadvisory_ai_cost_unpriced_runs_total 5
aiadvisory_ai_cost_usd_total 0.0125
aiadvisory_reviews_total 10
aiadvisory_fde_overrides_total 1
"""

DEGRADED_METRICS = """\
# HELP aiadvisory_http_requests_total HTTP requests.
# TYPE aiadvisory_http_requests_total counter
aiadvisory_http_requests_total{method="GET",route="/api/v1/assessments",status_class="2xx"} 80
aiadvisory_http_requests_total{method="GET",route="/api/v1/assessments",status_class="5xx"} 20
# HELP aiadvisory_metrics_database_up Database availability.
# TYPE aiadvisory_metrics_database_up gauge
aiadvisory_metrics_database_up 1
aiadvisory_rule_firing_analyses{rule_id="PRIV-001"} 2
aiadvisory_ai_cost_priced_runs_total 0
aiadvisory_ai_cost_unpriced_runs_total 6
aiadvisory_reviews_total 10
aiadvisory_fde_overrides_total 8
"""


class _StubHandler(BaseHTTPRequestHandler):
    routes: dict[str, tuple[int, str]] = {}

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        status, body = self.routes.get(self.path, (404, "not found"))
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args) -> None:
        return


@pytest.fixture
def stub_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _StubHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


def run_check(base_url: str, baseline: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.metrics_check",
            "--base-url",
            base_url,
            "--baseline",
            str(baseline),
            *extra,
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_metrics_check_reports_a_healthy_system(stub_server, tmp_path: Path) -> None:
    _StubHandler.routes = {
        "/health": (200, json.dumps({"status": "ok"})),
        "/ready": (200, json.dumps({"status": "ready"})),
        "/metrics": (200, HEALTHY_METRICS),
    }
    baseline = tmp_path / "baseline.json"
    result = run_check(
        f"http://127.0.0.1:{stub_server.server_address[1]}", baseline, "--update-baseline"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "[FAIL]" not in result.stdout
    assert "[PASS] health_endpoint" in result.stdout
    assert "RESULT: WARN" in result.stdout or "RESULT: PASS" in result.stdout
    assert baseline.exists()


def test_metrics_check_reports_a_degraded_system(stub_server, tmp_path: Path) -> None:
    _StubHandler.routes = {
        "/health": (200, json.dumps({"status": "ok"})),
        "/ready": (200, json.dumps({"status": "ready"})),
        "/metrics": (200, HEALTHY_METRICS),
    }
    baseline = tmp_path / "baseline.json"
    healthy = run_check(
        f"http://127.0.0.1:{stub_server.server_address[1]}", baseline, "--update-baseline"
    )
    assert healthy.returncode == 0, healthy.stdout

    _StubHandler.routes = {
        "/health": (503, json.dumps({"detail": "not ready"})),
        "/ready": (503, json.dumps({"detail": "Database not ready"})),
        "/metrics": (200, DEGRADED_METRICS),
    }
    degraded = run_check(f"http://127.0.0.1:{stub_server.server_address[1]}", baseline)
    assert degraded.returncode == 1, degraded.stdout
    assert "[FAIL] health_endpoint" in degraded.stdout
    assert "[FAIL] readiness_endpoint" in degraded.stdout
    assert "[FAIL] safety_rule_firing" in degraded.stdout
    assert "[FAIL] http_5xx_rate" in degraded.stdout
    assert "RESULT: FAIL" in degraded.stdout


def test_metrics_check_fails_when_the_endpoint_requires_a_credential(
    stub_server, tmp_path: Path
) -> None:
    _StubHandler.routes = {
        "/health": (200, json.dumps({"status": "ok"})),
        "/ready": (200, json.dumps({"status": "ready"})),
        "/metrics": (401, json.dumps({"detail": "Bearer credential required"})),
    }
    result = run_check(f"http://127.0.0.1:{stub_server.server_address[1]}", tmp_path / "b.json")
    assert result.returncode == 1
    assert "[FAIL] metrics_endpoint" in result.stdout
    assert "--token" in result.stdout
