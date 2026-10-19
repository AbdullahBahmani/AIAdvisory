"""Reproducible offline evaluation of recommendation policy against curated fixtures.

Scope and honesty
-----------------
The dataset in ``evals/golden_cases.json`` is **synthetic**. It is a golden-expectation
regression suite for the deterministic policy engine. It is **not** field accuracy, not
model quality, not evidence of ROI, and not a safety assurance. A false-positive rate
measured over this corpus is a *regression guard*, nothing more: it can only show that a
hand-written expectation still holds, never that a deployed system is safe.

The runner is importable (``from app.evaluation import evaluate_dataset``) and callable
programmatically; ``python -m app.evaluation`` is a thin wrapper around it.
"""

from __future__ import annotations

import argparse
import json
import sys
import types
from pathlib import Path
from typing import Any

from app.domain.enums import RecommendationCategory
from app.domain.schemas import AssessmentPreviewRequest
from app.services.assessment import analyze

DEFAULT_DATASET = Path(__file__).resolve().parent.parent / "evals" / "golden_cases.json"

#: Categories in which an AI component is proposed to the FDE. A case that is expected to
#: land outside this set and still lands inside it is a *false positive AI recommendation*.
AI_CATEGORIES: frozenset[str] = frozenset(
    {
        RecommendationCategory.STRONG_AI_CANDIDATE.value,
        RecommendationCategory.HYBRID.value,
        RecommendationCategory.AI_HUMAN_APPROVAL.value,
    }
)

ALL_CATEGORIES: tuple[str, ...] = tuple(category.value for category in RecommendationCategory)

#: Rule vocabulary declared by the policy engine. Rates are reported for every id, so a rule
#: that silently stops firing shows up as a rate of 0.0 instead of disappearing.
EXPECTED_RULE_IDS: frozenset[str] = frozenset(
    {
        "DET-001",
        "MATH-001",
        "DATA-001",
        "SAFE-001",
        "ACT-001",
        "ACT-002",
        "HUM-001",
        "PRIV-001",
        "PRIV-002",
        "VALUE-001",
        "EVID-001",
        "EVID-002",
        "EVID-003",
    }
)

#: Scopes: ``core`` is the four original contrastive fixtures kept for the historical
#: four-fixture contract; ``full`` is the whole corpus and is what the CLI runs.
SCOPES: tuple[str, ...] = ("core", "full")

#: Value inputs that must be observed before any net saving may be stated.
VALUE_BASELINE_FIELDS: tuple[str, ...] = (
    "cases_per_period",
    "baseline_minutes_per_case",
    "target_minutes_per_case",
    "loaded_hourly_cost",
)

#: Dimension scores that must stay unavailable when none of their inputs are known.
FEASIBILITY_DIMENSIONS: dict[str, tuple[str, ...]] = {
    "data_readiness": (
        "data_available",
        "data_quality_adequate",
        "permission_to_use_data",
        "governance_ready",
    ),
    "technical_feasibility": (
        "api_available",
        "auth_ready",
        "sandbox_available",
        "integration_path",
    ),
}

#: Payload keys that would indicate an approval or execution side effect.
APPROVAL_SIDE_EFFECT_KEYS: frozenset[str] = frozenset(
    {"is_final", "approved", "approval_granted", "signed_off", "executed", "committed"}
)

#: Modules that must never be reachable from the analysis path.
FORBIDDEN_ANALYSIS_MODULES: tuple[str, ...] = ("app.db", "app.ai", "app.api")


def _load_dataset(path: Path) -> dict:
    dataset = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(dataset, dict) or "cases" not in dataset:
        raise ValueError(f"{path} is not an evaluation dataset")
    if not dataset["cases"]:
        raise ValueError("An empty evaluation dataset cannot pass")
    return dataset


def _select_cases(dataset: dict, scope: str) -> list[dict]:
    cases = list(dataset["cases"])
    if scope == "full":
        return cases
    if scope != "core":
        raise ValueError(f"Unknown scope {scope!r}; expected one of {SCOPES}")
    core_ids = dataset.get("core_case_ids")
    if not core_ids:
        return cases
    return [case for case in cases if case["id"] in set(core_ids)]


def _value_is_evidenced(scenario: dict) -> bool:
    """True when the scenario carries a complete observed labour baseline."""
    inputs = scenario.get("value_inputs") or {}
    return all(inputs.get(field) is not None for field in VALUE_BASELINE_FIELDS)


def _roi_state(response) -> dict:
    value = response.value
    if value is None:
        return {"status": "absent", "net": None, "available": False}
    net = value.potential_net_savings
    return {
        "status": value.status,
        "net": None if net is None else str(net),
        "available": net is not None,
    }


def _feasibility_state(scenario: dict) -> dict:
    """Which dimension scores must remain unavailable, and whether they did.

    A dimension whose inputs are all unknown must not be reported as a number: a
    fabricated feasibility number is the same failure as a fabricated ROI.
    """
    tasks = [task for step in scenario.get("steps", []) for task in step.get("tasks", [])]
    must_be_unavailable: set[str] = set()
    for dimension, fields in FEASIBILITY_DIMENSIONS.items():
        for task in tasks:
            signals = task.get("signals") or {}
            if all(signals.get(field) is None for field in fields):
                must_be_unavailable.add(dimension)
    return {"must_be_unavailable": sorted(must_be_unavailable)}


def _contains_approval_side_effect(payload: Any) -> list[str]:
    found: list[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in APPROVAL_SIDE_EFFECT_KEYS and value:
                found.append(key)
            found.extend(_contains_approval_side_effect(value))
    elif isinstance(payload, list):
        for item in payload:
            found.extend(_contains_approval_side_effect(item))
    return found


def _analysis_path_modules() -> set[str]:
    """Import graph reachable from the analysis service (used for the no-side-effect check)."""
    module = sys.modules.get("app.services.assessment")
    if module is None:  # pragma: no cover - import side effect of this module
        import app.services.assessment as module  # type: ignore[no-redef]
    seen: set[str] = set()
    stack: list[types.ModuleType] = [module]
    while stack:
        current = stack.pop()
        for value in vars(current).values():
            if (
                isinstance(value, types.ModuleType)
                and value.__name__.startswith("app.")
                and value.__name__ not in seen
            ):
                seen.add(value.__name__)
                stack.append(value)
    return seen


def _purity_check() -> dict:
    reachable = _analysis_path_modules()
    forbidden = sorted(
        name for name in reachable if name.startswith(FORBIDDEN_ANALYSIS_MODULES)
    )
    return {
        "analysis_path_is_pure": not forbidden,
        "forbidden_modules_reachable": forbidden,
        "modules_reachable": sorted(reachable),
    }


def _evaluate_case(item: dict) -> dict:
    scenario = item["scenario"]
    response = analyze(AssessmentPreviewRequest.model_validate(scenario))
    actual = [task.recommendation.category.value for task in response.tasks]
    expected = item["expected_category"]
    fired = sorted({hit.id for task in response.tasks for hit in task.rule_hits})
    roi = _roi_state(response)

    checks: dict[str, bool] = {}
    checks["category"] = bool(actual) and all(category == expected for category in actual)
    checks["advisory_only"] = response.advisory_only is True
    checks["no_approval_side_effect"] = not _contains_approval_side_effect(response.model_dump())

    if item.get("expect_controls") or item.get("expected_controls"):
        checks["controls"] = bool(response.tasks) and all(
            bool(task.recommendation.required_controls) for task in response.tasks
        )

    required_rules = list(item.get("expected_rule_ids") or [])
    if item.get("required_rule"):
        required_rules.append(item["required_rule"])
    if required_rules:
        checks["expected_rules"] = all(rule in fired for rule in required_rules)

    if item.get("expect_roi_missing"):
        checks["roi_missing"] = not roi["available"]
    if item.get("expect_roi_available"):
        checks["roi_available"] = roi["available"]
    roi_expectation = None
    if item.get("expect_roi_missing"):
        roi_expectation = "missing"
    elif item.get("expect_roi_available"):
        roi_expectation = "available"

    known = bool(item.get("known_discrepancy"))
    feasibility = _feasibility_state(scenario)
    scenario_signals = {
        task["id"]: (task.get("signals") or {})
        for step in scenario.get("steps", [])
        for task in step.get("tasks", [])
    }
    feasibility_violations: list[str] = []
    for analysis in response.tasks:
        signals = scenario_signals.get(analysis.task_id, {})
        for dimension, fields in FEASIBILITY_DIMENSIONS.items():
            if all(signals.get(field) is None for field in fields):
                if getattr(analysis, dimension).value is not None:
                    feasibility_violations.append(f"{analysis.task_id}:{dimension}")
    if feasibility["must_be_unavailable"]:
        checks["feasibility_unavailable"] = not feasibility_violations

    case_ok = all(checks.values())
    # A documented known discrepancy exempts the *category* expectation only. Every other
    # invariant (controls, rule firing, ROI, advisory-only) must still hold.
    other_checks_ok = all(value for name, value in checks.items() if name != "category")
    if known and not checks["category"] and other_checks_ok:
        status = "known_discrepancy"
    elif known and checks["category"] and other_checks_ok:
        status = "known_discrepancy_resolved"
    else:
        status = "passed" if case_ok else "failed"

    return {
        "id": item["id"],
        "expected": expected,
        "actual": actual,
        "rules_fired": fired,
        "expected_rule_ids": required_rules,
        "roi": roi,
        "roi_evidenced": _value_is_evidenced(scenario),
        "roi_expectation": roi_expectation,
        "feasibility": {
            "must_be_unavailable": feasibility["must_be_unavailable"],
            "violations": feasibility_violations,
        },
        "false_positive_guard": bool(item.get("false_positive_guard")),
        "checks": checks,
        "passed": case_ok,
        "status": status,
        "known_discrepancy": known,
        "discrepancy_note": item.get("discrepancy_note"),
        "provenance": item.get("provenance"),
    }


def _category_metrics(results: list[dict]) -> dict:
    accuracy: dict[str, Any] = {}
    for category in ALL_CATEGORIES:
        subset = [r for r in results if r["expected"] == category]
        correct = sum(1 for r in subset if r["actual"] and all(c == category for c in r["actual"]))
        accuracy[category] = {
            "cases": len(subset),
            "correct": correct,
            # None (not 1.0) when the category has no cases: an unmeasured category is
            # not a perfect category.
            "accuracy": (round(correct / len(subset), 4) if subset else None),
        }
    matrix = {expected: {actual: 0 for actual in ALL_CATEGORIES} for expected in ALL_CATEGORIES}
    for result in results:
        expected = result["expected"]
        if expected not in matrix:
            matrix[expected] = {actual: 0 for actual in ALL_CATEGORIES}
        for actual in result["actual"]:
            matrix[expected][actual] = matrix[expected].get(actual, 0) + 1
    return {"per_category_accuracy": accuracy, "confusion_matrix": matrix}


def _false_positive_metrics(results: list[dict]) -> dict:
    non_ai = [r for r in results if r["expected"] not in AI_CATEGORIES]
    offenders = []
    for result in non_ai:
        if any(actual in AI_CATEGORIES for actual in result["actual"]):
            offenders.append(
                {
                    "id": result["id"],
                    "expected": result["expected"],
                    "actual": result["actual"],
                    "known_discrepancy": result["known_discrepancy"],
                }
            )
    count = len(offenders)
    guards = [r for r in results if r["false_positive_guard"]]
    guard_leaks = [
        r["id"] for r in guards if any(actual in AI_CATEGORIES for actual in r["actual"])
    ]
    return {
        "definition": (
            "Cases whose expected category is NOT an AI category (traditional_software, "
            "human_led, more_discovery) but which were classified as one (strong_ai_candidate, "
            "hybrid, ai_human_approval). Safety-relevant regression guard only."
        ),
        "non_ai_cases": len(non_ai),
        "false_positives": count,
        "rate": (round(count / len(non_ai), 4) if non_ai else None),
        "offending_case_ids": [offender["id"] for offender in offenders],
        "offenders": offenders,
        "unexpected_false_positives": [
            offender["id"] for offender in offenders if not offender["known_discrepancy"]
        ],
        "guard_cases": [r["id"] for r in guards],
        "guard_cases_leaking_into_ai": guard_leaks,
        "guard_note": (
            "Guard cases are a deliberately adversarial, hand-picked subset; their rate is not "
            "a population estimate."
        ),
    }


def _unknown_data_metrics(results: list[dict]) -> dict:
    must_stay_unavailable = [r for r in results if not r["roi_evidenced"]]
    violations = [r["id"] for r in must_stay_unavailable if r["roi"]["available"]]
    handled = len(must_stay_unavailable) - len(violations)
    evidenced = [r for r in results if r["roi_evidenced"]]
    withheld_when_evidenced = [r["id"] for r in evidenced if not r["roi"]["available"]]
    fabricated = [
        r["id"]
        for r in results
        if r["roi"]["available"] and not r["roi_evidenced"]
    ]
    feasibility_cases = [r for r in results if r["feasibility"]["must_be_unavailable"]]
    feasibility_violations = [
        r["id"] for r in feasibility_cases if r["feasibility"]["violations"]
    ]
    requiring_unknown = [
        r
        for r in results
        if (not r["roi_evidenced"]) or r["feasibility"]["must_be_unavailable"]
    ]
    expectation_violations = [
        r["id"] for r in results if not r["checks"].get("roi_missing", True) or
        not r["checks"].get("roi_available", True)
    ]
    unknown_violations = sorted(set(violations) | set(feasibility_violations))
    return {
        "definition": (
            "Cases without a complete observed baseline must keep ROI/net savings unavailable, "
            "and a dimension score whose inputs are all unknown must stay unavailable. This is "
            "the 'never fabricate ROI or feasibility' property."
        ),
        "cases_requiring_unknown": len(requiring_unknown),
        "handled": len(requiring_unknown) - len(unknown_violations),
        "rate": (
            round((len(requiring_unknown) - len(unknown_violations)) / len(requiring_unknown), 4)
            if requiring_unknown
            else None
        ),
        "violations": unknown_violations,
        "expectation_violations": expectation_violations,
        "roi": {
            "unknown_data_cases": len(must_stay_unavailable),
            "handled": handled,
            "rate": (
                round(handled / len(must_stay_unavailable), 4) if must_stay_unavailable else None
            ),
            "violations": violations,
            "fabricated_roi_cases": fabricated,
            "evidenced_cases": len(evidenced),
            "roi_withheld_when_evidenced": withheld_when_evidenced,
        },
        "feasibility": {
            "cases": len(feasibility_cases),
            "handled": len(feasibility_cases) - len(feasibility_violations),
            "rate": (
                round(
                    (len(feasibility_cases) - len(feasibility_violations))
                    / len(feasibility_cases),
                    4,
                )
                if feasibility_cases
                else None
            ),
            "violations": feasibility_violations,
        },
    }


def _rule_metrics(results: list[dict]) -> dict:
    total = len(results)
    observed = sorted({rule for result in results for rule in result["rules_fired"]})
    rates: dict[str, Any] = {}
    for rule in sorted(EXPECTED_RULE_IDS | set(observed)):
        fired = sum(1 for result in results if rule in result["rules_fired"])
        rates[rule] = {
            "fired": fired,
            "cases": total,
            "rate": (round(fired / total, 4) if total else None),
        }
    checked = 0
    satisfied = 0
    violations: list[str] = []
    for result in results:
        expected_rules = result.get("expected_rule_ids") or []
        if not expected_rules:
            continue
        checked += 1
        missing = [rule for rule in expected_rules if rule not in result["rules_fired"]]
        if missing:
            violations.append(f"{result['id']}: expected {missing}, fired {result['rules_fired']}")
        else:
            satisfied += 1
    return {
        "rule_firing_rates": rates,
        "rules_never_fired": sorted(rule for rule in EXPECTED_RULE_IDS if rule not in observed),
        "unexpected_rule_ids": sorted(set(observed) - EXPECTED_RULE_IDS),
        "expected_rule_checks": {
            "checked": checked,
            "satisfied": satisfied,
            "violations": violations,
        },
    }


def evaluate_dataset(path: Path = DEFAULT_DATASET, scope: str = "core") -> dict:
    """Evaluate a golden dataset and return pass/fail plus outcome metrics.

    ``scope`` selects which cases run:

    * ``"core"`` (default) evaluates only the four original contrastive fixtures, keeping the
      historical four-fixture contract that the existing reporting regression test asserts.
    * ``"full"`` evaluates every case in the dataset. ``python -m app.evaluation`` and
      :func:`evaluate_corpus` use the full corpus.
    """
    dataset = _load_dataset(Path(path))
    cases = _select_cases(dataset, scope)
    results = [_evaluate_case(item) for item in cases]

    unexpected_failures = [r for r in results if r["status"] == "failed"]
    known_discrepancies = [r for r in results if r["status"] == "known_discrepancy"]
    resolved = [r for r in results if r["status"] == "known_discrepancy_resolved"]

    category_metrics = _category_metrics(results)
    false_positive = _false_positive_metrics(results)
    unknown_data = _unknown_data_metrics(results)
    rules = _rule_metrics(results)

    purity = _purity_check()
    advisory_violations = [
        r["id"] for r in results if not r["checks"].get("advisory_only")
    ]
    side_effect_violations = [
        r["id"] for r in results if not r["checks"].get("no_approval_side_effect")
    ]

    correct = sum(1 for r in results if r["expected"] in r["actual"] and len(r["actual"]) == 1)
    return {
        "dataset_version": dataset.get("dataset_version"),
        "scope": scope,
        "total": len(results),
        "passed": len(results) - len(unexpected_failures) - len(known_discrepancies),
        "failed": len(unexpected_failures),
        "unexpected_failures": [r["id"] for r in unexpected_failures],
        "known_discrepancy_count": len(known_discrepancies),
        "known_discrepancies": [
            {
                "id": r["id"],
                "expected": r["expected"],
                "actual": r["actual"],
                "note": r["discrepancy_note"],
            }
            for r in known_discrepancies
        ],
        "resolved_discrepancies": [r["id"] for r in resolved],
        "category_accuracy_overall": (
            round(correct / len(results), 4) if results else None
        ),
        "metrics": {
            **category_metrics,
            "false_positive_ai_recommendation": false_positive,
            "unknown_data_handling": unknown_data,
            **rules,
            "advisory_only": {
                "cases": len(results),
                "compliant": len(results) - len(advisory_violations),
                "violations": advisory_violations,
                "approval_side_effect_violations": side_effect_violations,
                **purity,
            },
        },
        "cases": results,
        "note": (
            "scope='core' evaluates the four original contrastive fixtures only; use "
            "scope='full' (or evaluate_corpus()) for the whole synthetic corpus."
        ),
        "warning": (
            "Synthetic golden expectations only: not field accuracy, not model quality, not "
            "evidence of ROI, and not a safety assurance. A false-positive rate over this "
            "corpus is a regression guard, not proof of safety."
        ),
    }


def evaluate_corpus(path: Path = DEFAULT_DATASET) -> dict:
    """Evaluate every case in the dataset (the full corpus, not the legacy core subset)."""
    return evaluate_dataset(path, scope="full")


def _format_summary(result: dict) -> str:
    metrics = result["metrics"]
    false_positive = metrics["false_positive_ai_recommendation"]
    unknown_data = metrics["unknown_data_handling"]
    lines = [
        "Offline policy regression evaluation (synthetic golden expectations).",
        f"dataset_version: {result['dataset_version']}   scope: {result['scope']}   "
        f"cases: {result['total']}",
        f"passed: {result['passed']}   unexpected failures: {result['failed']}   "
        f"known discrepancies: {result['known_discrepancy_count']}",
        "",
        "FALSE POSITIVE AI RECOMMENDATIONS (safety-relevant regression guard):",
        f"  {false_positive['false_positives']} of {false_positive['non_ai_cases']} "
        f"non-AI cases (rate {false_positive['rate']})",
        f"  offending cases: {', '.join(false_positive['offending_case_ids']) or 'none'}",
        f"  unexpected (undocumented): "
        f"{', '.join(false_positive['unexpected_false_positives']) or 'none'}",
        f"  false-positive guard cases: {len(false_positive['guard_cases'])}, "
        f"leaking into an AI category: "
        f"{', '.join(false_positive['guard_cases_leaking_into_ai']) or 'none'}",
        "",
        "UNKNOWN-DATA HANDLING (never fabricate ROI or feasibility):",
        f"  {unknown_data['handled']} of {unknown_data['cases_requiring_unknown']} cases kept "
        f"unknown data unavailable (rate {unknown_data['rate']})",
        f"  ROI sub-metric: {unknown_data['roi']['handled']}/"
        f"{unknown_data['roi']['unknown_data_cases']} (rate {unknown_data['roi']['rate']}); "
        f"feasibility sub-metric: {unknown_data['feasibility']['handled']}/"
        f"{unknown_data['feasibility']['cases']} (rate {unknown_data['feasibility']['rate']})",
        f"  violations: {', '.join(unknown_data['violations']) or 'none'}",
        f"  declared-expectation violations: "
        f"{', '.join(unknown_data['expectation_violations']) or 'none'}",
        "",
        "PER-CATEGORY ACCURACY:",
    ]
    for category, stats in metrics["per_category_accuracy"].items():
        accuracy = "n/a" if stats["accuracy"] is None else f"{stats['accuracy']:.2%}"
        lines.append(f"  {category}: {stats['correct']}/{stats['cases']} ({accuracy})")
    lines.append("")
    lines.append("RULE FIRING RATES:")
    for rule, stats in metrics["rule_firing_rates"].items():
        lines.append(f"  {rule}: {stats['fired']}/{stats['cases']} ({stats['rate']:.2%})")
    checks = metrics["expected_rule_checks"]
    lines.append(
        f"  expected-rule checks: {checks['satisfied']}/{checks['checked']} satisfied"
    )
    advisory = metrics["advisory_only"]
    lines.append("")
    lines.append(
        f"ADVISORY ONLY: {advisory['compliant']}/{advisory['cases']} compliant; "
        f"analysis path pure: {advisory['analysis_path_is_pure']}; "
        f"approval side effects: {len(advisory['approval_side_effect_violations'])}"
    )
    if result["known_discrepancies"]:
        lines.append("")
        lines.append("KNOWN DISCREPANCIES (documented engine findings, not regressions):")
        for discrepancy in result["known_discrepancies"]:
            lines.append(
                f"  {discrepancy['id']}: expected {discrepancy['expected']}, "
                f"actual {discrepancy['actual']}"
            )
    lines.append("")
    lines.append(f"WARNING: {result['warning']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Offline, no-provider policy regression evaluation"
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--scope", choices=SCOPES, default="full")
    parser.add_argument("--json", action="store_true", help="print the raw metrics as JSON")
    args = parser.parse_args(argv)
    result = evaluate_dataset(args.dataset, scope=args.scope)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(_format_summary(result))
    return 0 if result["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
