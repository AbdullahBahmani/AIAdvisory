"""The financial model must be exact, explicit and never quietly wrong.

A net present value, an internal rate of return and a discounted payback are
the three numbers a reader is most likely to quote.  The tests therefore
check the arithmetic against an independent calculation, and check that a
missing input produces *unavailable with a reason* rather than a zero.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.portfolio import ValueScenarioIn
from app.services.npv import (
    MAX_HORIZON_PERIODS,
    NPV_METHOD_VERSION,
    financial_model,
    periodic_discount_rate,
)
from app.services.portfolio import scenario_financial_model


def _independent_npv(initial_cost, net, periods, rate):
    """A plain float calculation, written the obvious way."""
    total = -initial_cost
    for period in range(1, periods + 1):
        total += net / ((1 + rate) ** period)
    return total


def test_the_periodic_rate_is_the_twelfth_root_of_the_annual_rate():
    """A monthly rate is the monthly root of the annual rate."""
    annual = 0.05
    rate = periodic_discount_rate(Decimal("0.05"), "month")
    assert float((1 + rate) ** 12) == pytest.approx((1 + annual) ** 1, rel=1e-9)
    assert float((1 + rate) ** 12) - 1 == pytest.approx(annual, rel=1e-9)


def test_a_zero_annual_rate_gives_a_zero_periodic_rate():
    """A zero rate is a zero rate, not a division by zero."""
    assert periodic_discount_rate(Decimal("0"), "month") == Decimal(0)
    assert periodic_discount_rate(Decimal("0"), "year") == Decimal(0)


def test_the_net_present_value_matches_an_independent_calculation():
    """The arithmetic must agree with the obvious calculation."""
    result = financial_model(
        initial_cost=Decimal("10000"),
        net_savings_per_period=Decimal("2000"),
        period="month",
        horizon_periods=12,
        discount_rate_annual=Decimal("0.05"),
    )
    assert result.status == "available"
    monthly = (1.05) ** (1 / 12) - 1
    expected = _independent_npv(10000.0, 2000.0, 12, monthly)
    assert float(result.net_present_value) == pytest.approx(expected, rel=1e-6)


def test_the_internal_rate_of_return_is_the_rate_that_makes_the_value_zero():
    """The IRR must be the rate at which the present value is zero."""
    result = financial_model(
        initial_cost=Decimal("10000"),
        net_savings_per_period=Decimal("2000"),
        period="month",
        horizon_periods=12,
        discount_rate_annual=Decimal("0.05"),
    )
    annual = result.internal_rate_of_return
    assert annual is not None
    monthly = (1 + float(annual)) ** (1 / 12) - 1
    value = _independent_npv(10000.0, 2000.0, 12, monthly)
    assert value == pytest.approx(0.0, abs=0.5)


def test_the_discounted_payback_is_later_than_the_simple_payback():
    """Discounting can only make the payback later."""
    result = financial_model(
        initial_cost=Decimal("10000"),
        net_savings_per_period=Decimal("2000"),
        period="month",
        horizon_periods=12,
        discount_rate_annual=Decimal("0.05"),
    )
    assert result.discounted_payback_periods is not None
    assert float(result.discounted_payback_periods) > 5.0
    assert float(result.discounted_payback_periods) < 5.5


def test_a_missing_horizon_is_unavailable_and_never_zero():
    """No horizon means no net present value — not a net present value of zero."""
    result = financial_model(
        initial_cost=Decimal("10000"),
        net_savings_per_period=Decimal("2000"),
        period="month",
        horizon_periods=None,
        discount_rate_annual=Decimal("0.05"),
    )
    assert result.status == "unavailable"
    assert result.net_present_value is None
    assert result.reason


def test_a_missing_discount_rate_is_unavailable_and_never_zero():
    """No discount rate means no net present value."""
    result = financial_model(
        initial_cost=Decimal("10000"),
        net_savings_per_period=Decimal("2000"),
        period="month",
        horizon_periods=12,
        discount_rate_annual=None,
    )
    assert result.status == "unavailable"
    assert result.net_present_value is None
    assert result.reason
    # The internal rate of return does not need a discount rate.
    assert result.internal_rate_of_return is not None


def test_a_horizon_that_is_too_short_reports_no_discounted_payback():
    """The model says so instead of pretending the cost was recovered."""
    result = financial_model(
        initial_cost=Decimal("100000"),
        net_savings_per_period=Decimal("100"),
        period="month",
        horizon_periods=6,
        discount_rate_annual=Decimal("0.05"),
    )
    assert result.status == "available"
    assert result.discounted_payback_periods is None
    assert "horizon is too short" in (result.discounted_payback_reason or "")


def test_a_cash_flow_that_never_changes_sign_has_no_internal_rate_of_return():
    """There is no rate that makes a constant inflow worth zero."""
    result = financial_model(
        initial_cost=Decimal("0"),
        net_savings_per_period=Decimal("100"),
        period="month",
        horizon_periods=12,
        discount_rate_annual=Decimal("0.05"),
    )
    assert result.internal_rate_of_return is None
    assert result.internal_rate_of_return_reason


def test_a_cash_flow_with_two_sign_changes_has_no_single_rate():
    """A non-conventional cash flow has no single internal rate of return."""
    result = financial_model(
        initial_cost=Decimal("1000"),
        net_savings_per_period=Decimal("-100"),
        period="month",
        horizon_periods=12,
        discount_rate_annual=Decimal("0.05"),
    )
    assert result.internal_rate_of_return is None
    assert result.internal_rate_of_return_reason


def test_the_horizon_is_bounded():
    """A horizon beyond the bound is refused, not silently truncated."""
    result = financial_model(
        initial_cost=Decimal("1000"),
        net_savings_per_period=Decimal("100"),
        period="month",
        horizon_periods=MAX_HORIZON_PERIODS + 1,
        discount_rate_annual=Decimal("0.05"),
    )
    assert result.status == "unavailable"
    assert result.reason


def test_the_method_version_is_reported():
    """A result states which method produced it."""
    result = financial_model(
        initial_cost=Decimal("1000"),
        net_savings_per_period=Decimal("100"),
        period="month",
        horizon_periods=12,
        discount_rate_annual=Decimal("0.05"),
    )
    assert result.method_version == NPV_METHOD_VERSION
    assert result.disclaimer


def test_a_discount_rate_without_a_source_is_refused():
    """A rate that does not say where it came from looks authoritative and is not."""
    with pytest.raises(ValueError, match="source"):
        ValueScenarioIn(
            name="Scenario",
            currency="NOK",
            period="month",
            cases_per_period=Decimal("100"),
            baseline_minutes=Decimal("10"),
            target_minutes=Decimal("5"),
            hourly_cost=Decimal("500"),
            recurring_cost=Decimal("0"),
            initial_cost=Decimal("1000"),
            assumption_source="A measured baseline from the operations team",
            discount_rate_annual=Decimal("0.05"),
        )


def test_a_discount_rate_source_without_a_rate_is_refused():
    """A source without a rate is a dangling reference."""
    with pytest.raises(ValueError, match="needs a discount rate"):
        ValueScenarioIn(
            name="Scenario",
            currency="NOK",
            period="month",
            cases_per_period=Decimal("100"),
            baseline_minutes=Decimal("10"),
            target_minutes=Decimal("5"),
            hourly_cost=Decimal("500"),
            recurring_cost=Decimal("0"),
            initial_cost=Decimal("1000"),
            assumption_source="A measured baseline from the operations team",
            discount_rate_source="The central bank's published rate for 2026",
        )


def test_a_scenario_without_a_horizon_still_validates():
    """The horizon and the rate are optional; the scenario is still valid."""
    scenario = ValueScenarioIn(
        name="Scenario",
        currency="NOK",
        period="month",
        cases_per_period=Decimal("100"),
        baseline_minutes=Decimal("10"),
        target_minutes=Decimal("5"),
        hourly_cost=Decimal("500"),
        recurring_cost=Decimal("0"),
        initial_cost=Decimal("1000"),
        assumption_source="A measured baseline from the operations team",
    )
    model = scenario_financial_model(scenario)
    assert model.status == "unavailable"
    assert model.net_present_value is None


def test_the_model_of_a_scenario_uses_the_scenario_own_numbers():
    """The model is derived from the scenario, not from an independent guess."""
    scenario = ValueScenarioIn(
        name="Scenario",
        currency="NOK",
        period="quarter",
        cases_per_period=Decimal("100"),
        baseline_minutes=Decimal("10"),
        target_minutes=Decimal("5"),
        hourly_cost=Decimal("500"),
        recurring_cost=Decimal("0"),
        initial_cost=Decimal("1000"),
        assumption_source="A measured baseline from the operations team",
        horizon_periods=8,
        discount_rate_annual=Decimal("0.04"),
        discount_rate_source="The central bank's published rate for 2026",
    )
    model = scenario_financial_model(scenario)
    assert model.status == "available"
    # 100 cases * 5 saved minutes / 60 = 8.333 hours * 500 NOK = 4166.67 NOK
    net = Decimal("100") * (Decimal("10") - Decimal("5")) / Decimal("60") * Decimal("500")
    quarterly = (1.04) ** (1 / 4) - 1
    expected = _independent_npv(1000.0, float(net), 8, quarterly)
    assert float(model.net_present_value) == pytest.approx(expected, rel=1e-6)
    assert model.horizon_periods == 8


def test_the_model_is_reproducible():
    """The same inputs must give the same numbers."""
    arguments = {
        "initial_cost": Decimal("1000"),
        "net_savings_per_period": Decimal("100"),
        "period": "month",
        "horizon_periods": 12,
        "discount_rate_annual": Decimal("0.05"),
    }
    first = financial_model(**arguments)
    second = financial_model(**arguments)
    assert first == second
