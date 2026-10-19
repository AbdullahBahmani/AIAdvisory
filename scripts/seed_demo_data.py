"""Seed a running local API with synthetic demo data for documentation screenshots.

This helper only calls the public HTTP API of a locally running instance. It
never touches the database directly, never calls an AI provider and never
authorizes a rollout. All content is invented synthetic data.

Usage (local only):

    ADVISORY_REVIEW_TOKEN=<local demo token> \
      uv run --no-sync python -m scripts.seed_demo_data --base-url http://127.0.0.1:8000

The API must already be running in local demo mode:

    ADVISORY_DEMO_MODE=1 ADVISORY_REVIEW_TOKEN=<local demo token> \
      uvicorn app.main:app --port 8000
"""

import argparse
import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

REVIEWER = "synthetic-fde-01"
ORG = "Demo Retail Group (synthetic)"


def call(base_url: str, method: str, path: str, payload=None, *, token: str | None = None):
    """Perform one JSON API call against the local API and fail loudly."""
    headers = {"Accept": "application/json"}
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    if token:
        headers["X-Reviewer-Token"] = token
    request = Request(f"{base_url.rstrip('/')}{path}", data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=30) as response:  # noqa: S310 - configured local API
            body = response.read()
            if not body:
                return None
            return json.loads(body)
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise SystemExit(f"{method} {path} failed with HTTP {exc.code}: {detail}") from None
    except URLError as exc:
        raise SystemExit(f"{method} {path} could not reach the API: {exc.reason}") from None


def add_workflow(base_url: str, assessment_id: str, steps: list[dict]) -> None:
    """Create one workflow with ordered steps and atomic tasks."""
    workflow = call(
        base_url,
        "POST",
        f"/api/v1/assessments/{assessment_id}/workflows",
        {"name": "Synthetic support workflow"},
    )
    workflow_id = workflow["id"]
    for step in steps:
        created = call(
            base_url,
            "POST",
            f"/api/v1/workflows/{workflow_id}/steps",
            {
                "name": step["name"],
                "order": step["order"],
                "owner": step.get("owner"),
                "system": step.get("system"),
                "input_description": step.get("input_description"),
                "output_description": step.get("output_description"),
            },
        )
        for task in step["tasks"]:
            call(
                base_url,
                "POST",
                f"/api/v1/workflow-steps/{created['id']}/tasks",
                {
                    "title": task["title"],
                    "description": task.get("description", ""),
                    "decision_type": task.get("decision_type", "unknown"),
                    "signals": task.get("signals", {}),
                    "actions": task.get("actions", []),
                    "risks": task.get("risks", []),
                },
            )


def add_answers(base_url: str, assessment_id: str, answers: list[dict]) -> None:
    for answer in answers:
        call(base_url, "POST", f"/api/v1/assessments/{assessment_id}/answers", answer)


def seed_support_triage(base_url: str) -> str:
    """Strong AI candidate: unstructured, verifiable, repetitive support work."""
    created = call(
        base_url,
        "POST",
        "/api/v1/assessments",
        {
            "title": "Knowledge-base support triage",
            "organization": ORG,
            "owner": REVIEWER,
            "business_problem": "Cut first-response time on inbound support tickets without "
            "changing what the customer receives.",
        },
    )
    aid = created["id"]
    add_answers(
        base_url,
        aid,
        [
            {
                "key": "business_problem",
                "value": "Reduce inbound first-response time",
                "kind": "fact",
                "source": "Synthetic service baseline report",
                "confidence": 0.9,
            },
            {
                "key": "department_owner",
                "value": "Customer Support Operations",
                "kind": "fact",
                "source": "Synthetic org chart",
                "confidence": 0.9,
            },
            {
                "key": "decision_owner",
                "value": "Head of Customer Support",
                "kind": "fact",
                "source": "Synthetic org chart",
                "confidence": 0.9,
            },
            {
                "key": "cases_per_period",
                "value": 1200,
                "kind": "fact",
                "source": "Synthetic ticket export",
                "confidence": 0.8,
            },
            {
                "key": "baseline_minutes",
                "value": 9,
                "kind": "fact",
                "source": "Synthetic time study",
                "confidence": 0.7,
            },
            {
                "key": "data_owner",
                "value": "Knowledge Platform Team",
                "kind": "fact",
                "source": "Synthetic data catalogue",
                "confidence": 0.9,
            },
            {
                "key": "data_permission",
                "value": True,
                "kind": "fact",
                "source": "Synthetic processing agreement",
                "confidence": 0.9,
            },
            {
                "key": "verification",
                "value": "Weekly sampling of suggested replies by a team lead",
                "kind": "assumption",
                "source": "Unconfirmed - synthetic draft process",
                "confidence": 0.3,
            },
            {
                "key": "human_review",
                "value": "Support team leads approve replies before sending",
                "kind": "fact",
                "source": "Synthetic team process",
                "confidence": 0.8,
            },
            {
                "key": "success_metric",
                "value": "First response under 6 minutes for 90% of tickets",
                "kind": "fact",
                "source": "Synthetic service target",
                "confidence": 0.8,
            },
            {
                "key": "external_ai_use",
                "value": False,
                "kind": "fact",
                "source": "Synthetic policy decision",
                "confidence": 0.9,
            },
            {
                "key": "consequential_action",
                "value": False,
                "kind": "fact",
                "source": "Synthetic process description",
                "confidence": 0.8,
            },
        ],
    )
    add_workflow(
        base_url,
        aid,
        [
            {
                "name": "Classify inbound ticket",
                "order": 1,
                "owner": "Support Operations",
                "system": "Synthetic ticket queue",
                "input_description": "Customer email or chat transcript",
                "output_description": "Intent label and destination queue",
                "tasks": [
                    {
                        "title": "Extract intent and route ticket",
                        "description": "Read the incoming message and assign a routing label.",
                        "decision_type": "unknown",
                        "signals": {
                            "unstructured_content": True,
                            "classification_or_extraction": True,
                            "high_repetitive_volume": True,
                            "output_verifiable": True,
                            "data_available": True,
                            "human_review_available": True,
                            "clear_kpi": True,
                            "api_available": True,
                        },
                        "risks": [
                            {
                                "category": "business",
                                "description": "Misrouted ticket",
                                "impact": 2,
                                "likelihood": 2,
                            }
                        ],
                    }
                ],
            },
            {
                "name": "Draft suggested reply",
                "order": 2,
                "owner": "Support Operations",
                "system": "Synthetic knowledge base",
                "input_description": "Routed ticket plus retrieved articles",
                "output_description": "Draft reply for human approval",
                "tasks": [
                    {
                        "title": "Draft reply from retrieved knowledge articles",
                        "description": "Summarise retrieved articles into a reply draft.",
                        "decision_type": "unknown",
                        "signals": {
                            "natural_language_generation": True,
                            "semantic_search": True,
                            "slow_manual_task": True,
                            "high_repetitive_volume": True,
                            "output_verifiable": True,
                            "data_available": True,
                            "human_review_available": True,
                            "clear_kpi": True,
                        },
                        "risks": [
                            {
                                "category": "business",
                                "description": "Incorrect answer reaches customer",
                                "impact": 3,
                                "likelihood": 2,
                            }
                        ],
                    }
                ],
            },
        ],
    )
    analysis = call(base_url, "POST", f"/api/v1/assessments/{aid}/analyze")
    category = analysis["tasks"][0]["recommendation"]["category"]
    call(
        base_url,
        "POST",
        f"/api/v1/assessments/{aid}/fde-review",
        {
            "reviewer_id": REVIEWER,
            "selected_category": category,
            "rationale": "Synthetic demo review: outputs are verifiable and a human approves every "
            "reply, so a drafting assistant is acceptable to pilot.",
        },
    )
    call(
        base_url,
        "PUT",
        f"/api/v1/assessments/{aid}/portfolio-profile",
        {"strategic_alignment": 4, "urgency": 3, "delivery_effort": 2},
    )
    evidence = call(
        base_url,
        "POST",
        f"/api/v1/assessments/{aid}/evidence",
        {
            "kind": "baseline",
            "description": "Synthetic time study: 1200 tickets per month, "
            "9 minutes median handling time.",
            "source_reference": "synthetic-time-study-2026-10",
        },
    )
    call(base_url, "POST", f"/api/v1/assessments/{aid}/evidence/{evidence['id']}/verify")
    call(
        base_url,
        "POST",
        f"/api/v1/assessments/{aid}/value-scenarios",
        {
            "name": "Conservative pilot scenario",
            "currency": "NOK",
            "period": "month",
            "cases_per_period": 1200,
            "baseline_minutes": 9,
            "target_minutes": 6,
            "hourly_cost": 45,
            "recurring_cost": 900,
            "initial_cost": 6000,
            "assumption_source": "Synthetic finance worksheet v1",
        },
    )
    poc = call(
        base_url,
        "POST",
        f"/api/v1/assessments/{aid}/pocs",
        {
            "kpi_name": "Median first-response time",
            "unit": "minutes",
            "direction": "lower",
            "baseline": 9,
            "target": 6,
            "success_criteria": "Median first response at or below 6 minutes "
            "over four measured weeks.",
        },
    )
    call(base_url, "POST", f"/api/v1/assessments/{aid}/pocs/{poc['id']}/start")
    call(
        base_url,
        "POST",
        f"/api/v1/assessments/{aid}/pocs/{poc['id']}/observations",
        {
            "actual": 5.4,
            "source_reference": "synthetic-pilot-measurement-week-4",
        },
    )
    return aid


def seed_exact_pricing(base_url: str) -> str:
    """Traditional software: exact arithmetic stays in typed code."""
    created = call(
        base_url,
        "POST",
        "/api/v1/assessments",
        {
            "title": "Exact pricing calculation",
            "organization": ORG,
            "owner": REVIEWER,
            "business_problem": "Quote shipping, tax and discount exactly for every order.",
            "value_inputs": {
                "cases_per_period": 4000,
                "baseline_minutes_per_case": 3,
                "target_minutes_per_case": 1.2,
                "loaded_hourly_cost": 45,
                "proposed_operating_cost_per_period": 900,
                "period": "month",
            },
        },
    )
    aid = created["id"]
    add_answers(
        base_url,
        aid,
        [
            {
                "key": "business_problem",
                "value": "Produce exact order quotes",
                "kind": "fact",
                "source": "Synthetic pricing handbook",
                "confidence": 0.9,
            },
            {
                "key": "department_owner",
                "value": "Pricing Operations",
                "kind": "fact",
                "source": "Synthetic org chart",
                "confidence": 0.9,
            },
            {
                "key": "decision_owner",
                "value": "Pricing Operations Lead",
                "kind": "fact",
                "source": "Synthetic org chart",
                "confidence": 0.9,
            },
            {
                "key": "cases_per_period",
                "value": 4000,
                "kind": "fact",
                "source": "Synthetic order export",
                "confidence": 0.9,
            },
            {
                "key": "baseline_minutes",
                "value": 3,
                "kind": "fact",
                "source": "Synthetic time study",
                "confidence": 0.8,
            },
            {
                "key": "data_owner",
                "value": "Pricing Data Team",
                "kind": "fact",
                "source": "Synthetic data catalogue",
                "confidence": 0.9,
            },
            {
                "key": "data_permission",
                "value": True,
                "kind": "fact",
                "source": "Synthetic processing agreement",
                "confidence": 0.9,
            },
            {
                "key": "verification",
                "value": "Reconciliation against invoiced totals",
                "kind": "fact",
                "source": "Synthetic finance control",
                "confidence": 0.9,
            },
            {
                "key": "human_review",
                "value": "Pricing analyst reviews exception quotes",
                "kind": "fact",
                "source": "Synthetic team process",
                "confidence": 0.8,
            },
            {
                "key": "success_metric",
                "value": "Zero pricing variances in monthly reconciliation",
                "kind": "fact",
                "source": "Synthetic finance target",
                "confidence": 0.9,
            },
            {
                "key": "external_ai_use",
                "value": False,
                "kind": "fact",
                "source": "Synthetic policy decision",
                "confidence": 0.9,
            },
            {
                "key": "consequential_action",
                "value": False,
                "kind": "fact",
                "source": "Synthetic process description",
                "confidence": 0.9,
            },
        ],
    )
    add_workflow(
        base_url,
        aid,
        [
            {
                "name": "Compute fee",
                "order": 1,
                "owner": "Pricing Operations",
                "system": "Synthetic rating service",
                "input_description": "Cart lines, shipping address, tax region",
                "output_description": "Shipping fee, tax and discount amounts",
                "tasks": [
                    {
                        "title": "Apply shipping and tax rules",
                        "description": "Look up the rate table and apply tax rules exactly.",
                        "decision_type": "deterministic",
                        "signals": {
                            "deterministic_rules": True,
                            "exact_math": True,
                            "data_available": True,
                            "api_available": True,
                            "output_verifiable": True,
                            "zero_error_tolerance": True,
                            "clear_kpi": True,
                        },
                        "risks": [
                            {
                                "category": "business",
                                "description": "Incorrect invoice amount",
                                "impact": 3,
                                "likelihood": 1,
                            }
                        ],
                    }
                ],
            },
        ],
    )
    call(base_url, "POST", f"/api/v1/assessments/{aid}/analyze")
    return aid


def seed_refund_workflow(base_url: str) -> str:
    """Consequential action: human approval is a hard rule, not a preference."""
    created = call(
        base_url,
        "POST",
        "/api/v1/assessments",
        {
            "title": "Refund email workflow",
            "organization": ORG,
            "owner": REVIEWER,
            "business_problem": "Answer refund requests faster while keeping "
            "every refund approved.",
        },
    )
    aid = created["id"]
    add_answers(
        base_url,
        aid,
        [
            {
                "key": "business_problem",
                "value": "Answer refund requests faster",
                "kind": "fact",
                "source": "Synthetic service baseline",
                "confidence": 0.9,
            },
            {
                "key": "department_owner",
                "value": "Service Operations",
                "kind": "fact",
                "source": "Synthetic org chart",
                "confidence": 0.9,
            },
            {
                "key": "decision_owner",
                "value": "Service Operations Lead",
                "kind": "fact",
                "source": "Synthetic org chart",
                "confidence": 0.9,
            },
            {
                "key": "cases_per_period",
                "value": 420,
                "kind": "fact",
                "source": "Synthetic case export",
                "confidence": 0.8,
            },
            {
                "key": "baseline_minutes",
                "value": 12,
                "kind": "fact",
                "source": "Synthetic time study",
                "confidence": 0.8,
            },
            {
                "key": "data_owner",
                "value": "Order Systems Team",
                "kind": "fact",
                "source": "Synthetic data catalogue",
                "confidence": 0.9,
            },
            {
                "key": "data_permission",
                "value": True,
                "kind": "fact",
                "source": "Synthetic processing agreement",
                "confidence": 0.9,
            },
            {
                "key": "verification",
                "value": "Order lookup before any refund recommendation",
                "kind": "fact",
                "source": "Synthetic control description",
                "confidence": 0.9,
            },
            {
                "key": "human_review",
                "value": "Service agent approves each refund before execution",
                "kind": "fact",
                "source": "Synthetic team process",
                "confidence": 0.9,
            },
            {
                "key": "success_metric",
                "value": "Refund error rate below 1% of processed refunds",
                "kind": "fact",
                "source": "Synthetic risk target",
                "confidence": 0.8,
            },
            {
                "key": "external_ai_use",
                "value": False,
                "kind": "fact",
                "source": "Synthetic policy decision",
                "confidence": 0.9,
            },
            {
                "key": "consequential_action",
                "value": True,
                "kind": "fact",
                "source": "Synthetic process description",
                "confidence": 0.9,
            },
            {
                "key": "approval_owner",
                "value": "Service Operations Lead",
                "kind": "fact",
                "source": "Synthetic delegation matrix",
                "confidence": 0.9,
            },
        ],
    )
    add_workflow(
        base_url,
        aid,
        [
            {
                "name": "Review refund request",
                "order": 1,
                "owner": "Service Operations",
                "system": "Synthetic order system",
                "input_description": "Customer email with order reference",
                "output_description": "Refund recommendation for human approval",
                "tasks": [
                    {
                        "title": "Extract request and prepare refund recommendation",
                        "description": "Read the request, look up the order and "
                        "prepare a recommendation.",
                        "decision_type": "mixed",
                        "actions": ["issue_refund"],
                        "signals": {
                            "unstructured_content": True,
                            "classification_or_extraction": True,
                            "requires_authoritative_lookup": True,
                            "high_consequence": True,
                            "action_irreversible": True,
                            "human_review_available": True,
                            "data_available": True,
                            "output_verifiable": True,
                        },
                        "risks": [
                            {
                                "category": "business",
                                "description": "Wrong refund issued",
                                "impact": 4,
                                "likelihood": 4,
                            }
                        ],
                    }
                ],
            },
        ],
    )
    analysis = call(base_url, "POST", f"/api/v1/assessments/{aid}/analyze")
    category = analysis["tasks"][0]["recommendation"]["category"]
    call(
        base_url,
        "POST",
        f"/api/v1/assessments/{aid}/fde-review",
        {
            "reviewer_id": REVIEWER,
            "selected_category": category,
            "rationale": "Synthetic demo review: the extraction step may be assisted, "
            "but the refund itself stays behind human approval, least privilege and audit.",
        },
    )
    poc = call(
        base_url,
        "POST",
        f"/api/v1/assessments/{aid}/pocs",
        {
            "kpi_name": "Refund error rate",
            "unit": "percent",
            "direction": "lower",
            "baseline": 3,
            "target": 1,
            "success_criteria": "Refund error rate at or below 1% across two measured months.",
        },
    )
    call(base_url, "POST", f"/api/v1/assessments/{aid}/pocs/{poc['id']}/start")
    call(
        base_url,
        "POST",
        f"/api/v1/assessments/{aid}/pocs/{poc['id']}/observations",
        {
            "actual": 1.4,
            "source_reference": "synthetic-refund-error-audit-month-1",
        },
    )
    return aid


def seed_override_review(base_url: str, token: str) -> str:
    """Deterministic engine result that the FDE deliberately overrides, then finalizes."""
    created = call(
        base_url,
        "POST",
        "/api/v1/assessments",
        {
            "title": "Returns policy exception review",
            "organization": ORG,
            "owner": REVIEWER,
            "business_problem": "Decide non-standard returns exceptions without leaking money.",
        },
    )
    aid = created["id"]
    add_answers(
        base_url,
        aid,
        [
            {
                "key": "business_problem",
                "value": "Decide returns exceptions consistently",
                "kind": "fact",
                "source": "Synthetic policy handbook",
                "confidence": 0.8,
            },
            {
                "key": "department_owner",
                "value": "Returns Operations",
                "kind": "fact",
                "source": "Synthetic org chart",
                "confidence": 0.9,
            },
            {
                "key": "decision_owner",
                "value": "Returns Operations Lead",
                "kind": "fact",
                "source": "Synthetic org chart",
                "confidence": 0.9,
            },
            {
                "key": "data_owner",
                "value": "Order Systems Team",
                "kind": "fact",
                "source": "Synthetic data catalogue",
                "confidence": 0.9,
            },
            {
                "key": "verification",
                "value": "Every exception is logged and reviewed",
                "kind": "fact",
                "source": "Synthetic control description",
                "confidence": 0.9,
            },
            {
                "key": "human_review",
                "value": "Returns Operations Lead approves exceptions",
                "kind": "fact",
                "source": "Synthetic team process",
                "confidence": 0.9,
            },
            {
                "key": "success_metric",
                "value": "No unapproved exception refunds",
                "kind": "fact",
                "source": "Synthetic risk target",
                "confidence": 0.9,
            },
            {
                "key": "external_ai_use",
                "value": False,
                "kind": "fact",
                "source": "Synthetic policy decision",
                "confidence": 0.9,
            },
            {
                "key": "consequential_action",
                "value": True,
                "kind": "fact",
                "source": "Synthetic process description",
                "confidence": 0.9,
            },
            {
                "key": "approval_owner",
                "value": "Returns Operations Lead",
                "kind": "fact",
                "source": "Synthetic delegation matrix",
                "confidence": 0.9,
            },
        ],
    )
    add_workflow(
        base_url,
        aid,
        [
            {
                "name": "Check returns eligibility",
                "order": 1,
                "owner": "Returns Operations",
                "system": "Synthetic policy table",
                "input_description": "Return request and order history",
                "output_description": "Eligibility result or exception for human decision",
                "tasks": [
                    {
                        "title": "Check standard returns eligibility against the policy table",
                        "description": "Look up the policy row for the product "
                        "and return category.",
                        "decision_type": "deterministic",
                        "signals": {
                            "deterministic_rules": True,
                            "data_available": True,
                            "api_available": True,
                            "output_verifiable": True,
                        },
                        "risks": [
                            {
                                "category": "business",
                                "description": "Exception approved without authority",
                                "impact": 4,
                                "likelihood": 2,
                            }
                        ],
                    }
                ],
            },
        ],
    )
    analysis = call(base_url, "POST", f"/api/v1/assessments/{aid}/analyze")
    engine_category = analysis["tasks"][0]["recommendation"]["category"]
    override_category = (
        "ai_human_approval" if engine_category != "ai_human_approval" else "human_led"
    )
    call(
        base_url,
        "POST",
        f"/api/v1/assessments/{aid}/fde-review",
        {
            "reviewer_id": REVIEWER,
            "selected_category": override_category,
            "rationale": "Synthetic demo review: the engine only sees the standard policy "
            "row, so the category below reflects the real exception workload.",
            "override_reason": "Synthetic override rationale: the deterministic policy "
            "table covers standard returns only. Non-standard exceptions are "
            "ambiguous and consequential, so an accountable human must decide each one.",
        },
    )
    call(base_url, "POST", f"/api/v1/assessments/{aid}/final-decision", token=token)
    return aid


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url", default=os.environ.get("API_BASE_URL", "http://127.0.0.1:8000")
    )
    args = parser.parse_args()
    token = os.environ.get("ADVISORY_REVIEW_TOKEN", "")
    if len(token) < 16:
        raise SystemExit(
            "ADVISORY_REVIEW_TOKEN (16+ chars) is required to finalize the demo review"
        )
    call(args.base_url, "GET", "/health")
    identifiers = {
        "support_triage": seed_support_triage(args.base_url),
        "exact_pricing": seed_exact_pricing(args.base_url),
        "refund_workflow": seed_refund_workflow(args.base_url),
        "returns_override": seed_override_review(args.base_url, token),
    }
    print(json.dumps(identifiers, indent=2))
    print("Synthetic local demo data only. No rollout approval and no external provider used.")


if __name__ == "__main__":
    sys.exit(main())
