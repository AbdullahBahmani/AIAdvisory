"""Reproducible offline evaluation of recommendation policy against curated fixtures."""

import argparse
import json
from pathlib import Path

from app.domain.schemas import AssessmentPreviewRequest
from app.services.assessment import analyze

DEFAULT_DATASET = Path(__file__).resolve().parent.parent / "evals" / "golden_cases.json"


def evaluate_dataset(path: Path = DEFAULT_DATASET) -> dict:
    dataset = json.loads(path.read_text(encoding="utf-8"))
    cases = dataset["cases"]
    if not cases:
        raise ValueError("An empty evaluation dataset cannot pass")
    outputs = []
    for item in cases:
        response = analyze(AssessmentPreviewRequest.model_validate(item["scenario"]))
        categories = [result.recommendation.category.value for result in response.tasks]
        expected = item["expected_category"]
        checks = {"recommendation": bool(categories) and all(category == expected for category in categories)}
        if item.get("expected_controls"):
            checks["controls"] = bool(response.tasks) and all(
                bool(task.recommendation.required_controls) or bool(task.rule_hits)
                for task in response.tasks
            )
        if item.get("required_rule"):
            checks["required_rule"] = any(
                hit.id == item["required_rule"] for task in response.tasks for hit in task.rule_hits
            )
        if item.get("expect_roi_missing"):
            checks["roi_missing"] = response.value is None or response.value.potential_net_savings is None
        checks["advisory_only"] = response.advisory_only is True
        outputs.append({"id": item["id"], "expected": expected, "actual": categories,
                        "passed": all(checks.values()), "checks": checks})
    passed = sum(1 for result in outputs if result["passed"])
    return {"dataset_version": dataset["dataset_version"], "total": len(outputs), "passed": passed,
            "failed": len(outputs)-passed, "cases": outputs,
            "warning": "Synthetic golden tests check regressions only, not real-world model quality."}


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline, no-provider policy regression evaluation")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    args = parser.parse_args()
    result = evaluate_dataset(args.dataset)
    print(json.dumps(result, indent=2))
    return 0 if result["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
