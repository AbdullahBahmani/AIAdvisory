"""Evidence-sensitive labor value calculation (not validated ROI)."""

from decimal import Decimal

from app.domain.schemas import ValueInputs, ValueResult

NEEDED_FIELDS = (
    "cases_per_period",
    "baseline_minutes_per_case",
    "target_minutes_per_case",
    "loaded_hourly_cost",
)


def calculate_value(inputs: ValueInputs) -> ValueResult:
    missing = [key for key in NEEDED_FIELDS if getattr(inputs, key) is None]
    if missing:
        return ValueResult(
            period=inputs.period,
            baseline_hours=None,
            potential_hours_saved=None,
            potential_gross_labor_savings=None,
            proposed_operating_cost=inputs.proposed_operating_cost_per_period,
            potential_net_savings=None,
            status="insufficient_evidence",
            assumptions=[f"Missing observed input: {key}" for key in missing],
        )
    assert inputs.cases_per_period is not None
    assert inputs.baseline_minutes_per_case is not None
    assert inputs.target_minutes_per_case is not None
    assert inputs.loaded_hourly_cost is not None
    hours = inputs.cases_per_period * inputs.baseline_minutes_per_case / Decimal(60)
    saved = (
        inputs.cases_per_period
        * (inputs.baseline_minutes_per_case - inputs.target_minutes_per_case)
        / Decimal(60)
    )
    gross = saved * inputs.loaded_hourly_cost
    net = (
        gross - inputs.proposed_operating_cost_per_period
        if inputs.proposed_operating_cost_per_period is not None
        else None
    )
    notes = ["Illustrative labor savings only; validate actual adoption, quality and baseline."]
    if saved < 0:
        notes.append(
            "Target handling time is higher than baseline; estimated labor value is negative."
        )
    if net is None:
        notes.append("Operating cost unknown; net savings cannot be established.")
    return ValueResult(
        period=inputs.period,
        baseline_hours=hours,
        potential_hours_saved=saved,
        potential_gross_labor_savings=gross,
        proposed_operating_cost=inputs.proposed_operating_cost_per_period,
        potential_net_savings=net,
        status="illustrative_only",
        assumptions=notes,
    )
