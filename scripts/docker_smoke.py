"""Local container smoke test for the Docker/Compose deployment.

Exercises a running API container over HTTP only: liveness, readiness, the
OpenAPI document, a complete synthetic assessment flow (create, map, analyze,
report) and the "AI provider is off" switch. It creates no secrets, needs no
AI key and changes no repository state. A non-zero exit status means a check
failed, so it can be used as a release-gate smoke command.

Usage:
    python -m scripts.docker_smoke --base-url http://127.0.0.1:8000
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

DEFAULT_BASE_URL = "http://127.0.0.1:8000"

# Fully synthetic task: deterministic pricing, no real business data.
SYNTHETIC_TASK = {
    "title": "Apply known shipping rates and tax",
    "description": "Synthetic container smoke test",
    "decision_type": "deterministic",
    "signals": {
        "deterministic_rules": True,
        "exact_math": True,
        "data_available": True,
        "output_verifiable": True,
    },
    "actions": [],
    "risks": [],
}


def request(base_url: str, method: str, path: str, payload: dict | None = None) -> tuple[int, str]:
    """Return the HTTP status and body; HTTP error responses are returned too."""
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    call = urllib.request.Request(  # noqa: S310 - loopback local container only
        f"{base_url}{path}", data=data, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(call, timeout=30) as response:  # noqa: S310
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", "replace")


class Checks:
    """Collect named pass/fail results and print them as they happen."""

    def __init__(self) -> None:
        self.results: dict[str, bool] = {}

    def expect(self, name: str, status: int, body: str, expected: int = 200) -> None:
        passed = status == expected
        self.results[name] = passed
        marker = "PASS" if passed else "FAIL"
        print(f"[{marker}] {name}: HTTP {status} (expected {expected}) {body[:120]}")


def run(base_url: str) -> bool:
    checks = Checks()

    status, body = request(base_url, "GET", "/health")
    checks.expect("GET /health", status, body)
    status, body = request(base_url, "GET", "/ready")
    checks.expect("GET /ready", status, body)
    status, body = request(base_url, "GET", "/openapi.json")
    checks.expect("GET /openapi.json", status, body)

    status, body = request(
        base_url,
        "POST",
        "/api/v1/assessments",
        {
            "title": "Docker smoke assessment",
            "organization": "LocalDemo",
            "owner": "local_demo",
            "business_problem": "Synthetic container smoke test only.",
        },
    )
    checks.expect("POST /api/v1/assessments", status, body, 201)
    if status != 201:
        return False
    assessment_id = json.loads(body)["id"]

    status, body = request(
        base_url,
        "POST",
        f"/api/v1/assessments/{assessment_id}/workflows",
        {"name": "Synthetic workflow"},
    )
    checks.expect("POST assessments/{id}/workflows", status, body, 201)
    workflow_id = json.loads(body)["id"]

    status, body = request(
        base_url,
        "POST",
        f"/api/v1/workflows/{workflow_id}/steps",
        {"name": "Compute shipping fee", "order": 1},
    )
    checks.expect("POST workflows/{id}/steps", status, body, 201)
    step_id = json.loads(body)["id"]

    status, body = request(
        base_url, "POST", f"/api/v1/workflow-steps/{step_id}/tasks", SYNTHETIC_TASK
    )
    checks.expect("POST workflow-steps/{id}/tasks", status, body, 201)

    status, body = request(base_url, "POST", f"/api/v1/assessments/{assessment_id}/analyze")
    checks.expect("POST assessments/{id}/analyze", status, body)

    report = f"/api/v1/assessments/{assessment_id}/report"
    status, body = request(base_url, "GET", f"{report}?format=markdown")
    checks.expect("GET report (markdown)", status, body)
    status, body = request(base_url, "GET", f"{report}?format=html")
    checks.expect("GET report (html)", status, body)

    # The AI provider must stay off: no key is configured in the container.
    status, body = request(
        base_url,
        "POST",
        f"/api/v1/assessments/{assessment_id}/ai/discovery",
        {
            "send_to_provider": True,
            "approved_non_sensitive_data": True,
        },
    )
    checks.expect("POST ai/discovery is refused (AI disabled)", status, body, 503)

    return all(checks.results.values())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"API base URL of the running container (default {DEFAULT_BASE_URL})",
    )
    arguments = parser.parse_args()
    ok = run(arguments.base_url.rstrip("/"))
    print("SMOKE RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
