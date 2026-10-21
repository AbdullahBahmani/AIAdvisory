"""Pure, reproducible, explicitly provisional portfolio and value calculations."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.domain.portfolio import FinancialModelView, ValueScenarioIn
from app.services.npv import financial_model


def value_projection(values: ValueScenarioIn) -> tuple[Decimal, Decimal, Decimal | None]:
    """One currency, one period. No assumed adoption, taxes or discount rate."""
    saved_hours = (
        values.cases_per_period * (values.baseline_minutes - values.target_minutes) / Decimal("60")
    )
    gross = saved_hours * values.hourly_cost
    net = gross - values.recurring_cost
    payback = values.initial_cost / net if net > 0 else None
    return gross, net, payback


def scenario_financial_model(values: ValueScenarioIn) -> FinancialModelView:
    """The advisory financial model of one value scenario.

    The model is derived from the scenario's own numbers, so it can never
    state a net present value for a scenario that has no horizon or no
    discount rate: the result is *unavailable* with a reason instead.
    """
    _gross, net, _payback = value_projection(values)
    result = financial_model(
        initial_cost=values.initial_cost,
        net_savings_per_period=net,
        period=values.period,
        horizon_periods=values.horizon_periods,
        discount_rate_annual=values.discount_rate_annual,
    )
    return FinancialModelView(
        status=result.status,  # type: ignore[arg-type]
        reason=result.reason,
        net_present_value=result.net_present_value,
        internal_rate_of_return=result.internal_rate_of_return,
        internal_rate_of_return_reason=result.internal_rate_of_return_reason,
        discounted_payback_periods=result.discounted_payback_periods,
        discounted_payback_reason=result.discounted_payback_reason,
        periodic_discount_rate=result.periodic_discount_rate,
        horizon_periods=result.horizon_periods,
        warnings=result.warnings,
    )


def priority_result(
    *,
    alignment: int | None,
    urgency: int | None,
    effort: int | None,
    verified_sources: int,
    analysis_fresh: bool,
    policy_gated: bool = False,
) -> dict[str, Any]:
    """A relative *planning heuristic*, never an AI-suitability verdict or ROI."""
    missing = []
    if alignment is None:
        missing.append("strategic_alignment")
    if urgency is None:
        missing.append("urgency")
    if effort is None:
        missing.append("delivery_effort")
    if verified_sources < 1:
        missing.append("verified_current_revision_evidence")
    if not analysis_fresh:
        missing.append("current_revision_analysis")
    if policy_gated:
        missing.append("deterministic_policy_requires_discovery_or_human_decision")
    if missing:
        return {"priority_score": None, "status": "unranked", "missing": missing}
    assert alignment is not None and urgency is not None and effort is not None
    # Explicit, versioned default weights (40/35/25). Review with customers.
    score = 40 * alignment / 5 + 35 * urgency / 5 + 25 * (6 - effort) / 5
    return {"priority_score": int(score), "status": "provisional", "missing": []}
