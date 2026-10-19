"""Synthetic regression invariants for Phase 8, NOT empirical model calibration."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from app.domain.portfolio import ValueScenarioIn
from app.services.portfolio import priority_result, value_projection


def run(path: str | Path | None = None) -> int:
    source = (
        Path(path)
        if path
        else Path(__file__).resolve().parent.parent / "evals/portfolio_golden_cases.json"
    )
    fixture = json.loads(source.read_text(encoding="utf-8"))
    failures: list[str] = []
    for case in fixture["priorities"]:
        actual = priority_result(
            alignment=case["alignment"],
            urgency=case["urgency"],
            effort=case["effort"],
            verified_sources=case["verified_sources"],
            analysis_fresh=case["analysis_fresh"],
            policy_gated=case["policy_gated"],
        )
        if actual["status"] != case["status"] or actual["priority_score"] != case["expected_score"]:
            failures.append(f"Priority case {case['id']} failed")
    for case in fixture["scenarios"]:
        inputs = ValueScenarioIn.model_validate(
            {
                key: val
                for key, val in case.items()
                if key not in {"id", "expected_gross", "expected_net", "expected_payback"}
            }
        )
        gross, net, payback = value_projection(inputs)
        expected = (
            Decimal(case["expected_gross"]),
            Decimal(case["expected_net"]),
            Decimal(case["expected_payback"]) if case["expected_payback"] is not None else None,
        )
        if (gross, net, payback) != expected:
            failures.append(f"Value case {case['id']} failed")
    if failures:
        print("\n".join(failures))
        return 1
    print(
        f"Portfolio synthetic regressions: "
        f"{len(fixture['priorities']) + len(fixture['scenarios'])} passed; "
        f"no field calibration claimed"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
