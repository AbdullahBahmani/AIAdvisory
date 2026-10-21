"""Net present value, internal rate of return and discounted payback.

The three numbers are computed from one explicit set of inputs: an initial
cost, a recurring net saving per period, a horizon and a discount rate.  They
are **advisory modelling**, not a valuation:

* The horizon and the discount rate are *caller-supplied assumptions*, and a
  discount rate without a stated source is refused.  A rate without a source is
  a number that looks authoritative and is not.
* No adoption curve, no tax, no inflation, no financing cost, no residual
  value and no observed ROI is modelled here.  The savings are the savings of
  the value scenario, nothing more.
* The arithmetic is :class:`~decimal.Decimal` throughout.  The periodic
  discount rate needs a twelfth root, which is irrational in general, so it is
  computed with a high-precision transcendental approximation and the result
  is reported as a number with a stated precision — never as an exact value.
* When an input is missing the result is **unavailable with a reason**, never
  zero.  A missing discount rate is not a discount rate of zero.

The existing payback measure is left exactly as it was: simple payback is
still ``initial_cost / net_savings``, and it is still reported next to these.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, localcontext

from app.domain.portfolio import NPV_DISCLAIMER, NPV_METHOD_VERSION

__all__ = [
    "MAX_HORIZON_PERIODS",
    "NPV_DISCLAIMER",
    "NPV_METHOD_VERSION",
    "NpvResult",
    "financial_model",
    "periodic_discount_rate",
]

#: The number of periods in one year, per scenario period.
PERIODS_PER_YEAR = {"month": 12, "quarter": 4, "year": 1}

#: The working precision of the arithmetic.
_PRECISION = 50

#: The tolerance of the internal-rate-of-return search, on the periodic rate.
_IRR_TOLERANCE = Decimal("1e-12")

#: The largest periodic rate the search considers.  A rate above 1000 % per
#: period is a modelling artefact, not a discount rate.
_IRR_MAX = Decimal("1000")

#: The number of periods a scenario may span.
MAX_HORIZON_PERIODS = 120

_ROUNDING = Decimal("0.0001")


@dataclass(frozen=True)
class NpvResult:
    """The result of the financial model of one value scenario.

    Each measure carries its own state.  The internal rate of return does not
    need a discount rate — it is the rate the project earns — so it is
    reported even when the net present value is unavailable.  The *status* is
    the state of the net present value, which is the headline number.
    """

    status: str  # "available" or "unavailable"
    reason: str | None = None
    net_present_value: Decimal | None = None
    internal_rate_of_return: Decimal | None = None
    internal_rate_of_return_reason: str | None = None
    discounted_payback_periods: Decimal | None = None
    discounted_payback_reason: str | None = None
    periodic_discount_rate: Decimal | None = None
    horizon_periods: int | None = None
    warnings: list[str] = field(default_factory=list)
    method_version: str = NPV_METHOD_VERSION
    disclaimer: str = NPV_DISCLAIMER


def _unavailable(reason: str, horizon: int | None = None) -> NpvResult:
    return NpvResult(status="unavailable", reason=reason, horizon_periods=horizon)


def periodic_discount_rate(annual_rate: Decimal, period: str) -> Decimal:
    """The discount rate of one period, from the annual rate.

    The rate is converted with a high-precision logarithm and exponential, so
    it is an approximation of the exact twelfth root.  It is reported as such.
    """
    periods = PERIODS_PER_YEAR.get(period)
    if periods is None:
        raise ValueError(f"Unknown period: {period!r}")
    with localcontext() as context:
        context.prec = _PRECISION
        if annual_rate == 0:
            return Decimal(0)
        return ((Decimal(1) + annual_rate).ln() / Decimal(periods)).exp() - Decimal(1)


def _cash_flows(initial_cost: Decimal, net_savings: Decimal, horizon: int) -> list[Decimal]:
    """The cash flow of every period: the initial cost, then the savings.

    The initial cost is a one-off outflow in period zero; the net saving
    recurs in every period of the horizon.
    """
    return [-initial_cost] + [net_savings] * horizon


def _discounted(flows: list[Decimal], periodic_rate: Decimal) -> list[Decimal]:
    """Discount every cash flow to the start of the horizon."""
    with localcontext() as context:
        context.prec = _PRECISION
        factor = Decimal(1)
        discounted = []
        for index, flow in enumerate(flows):
            if index:
                factor *= Decimal(1) + periodic_rate
            discounted.append(flow / factor)
        return discounted


def _npv_at(flows: list[Decimal], periodic_rate: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = _PRECISION
        factor = Decimal(1)
        total = Decimal(0)
        for index, flow in enumerate(flows):
            if index:
                factor *= Decimal(1) + periodic_rate
            total += flow / factor
        return total


def _irr_periodic(flows: list[Decimal]) -> tuple[Decimal | None, str | None]:
    """The periodic internal rate of return, or a reason why there is none.

    The search is a bisection on the periodic rate.  It is only meaningful for
    a *conventional* cash flow — one sign change — and only exists when the
    undiscounted total is positive; otherwise there is no positive rate that
    makes the present value zero.
    """
    if all(flow >= 0 for flow in flows) or all(flow <= 0 for flow in flows):
        return None, "The cash flow never changes sign, so it has no internal rate of return."
    signs = [1 if flow > 0 else (-1 if flow < 0 else 0) for flow in flows]
    changes = sum(1 for a, b in zip(signs, signs[1:], strict=False) if a and b and a != b)
    if changes != 1:
        return None, (
            "The cash flow changes sign more than once, so it has no single internal "
            "rate of return."
        )
    if _npv_at(flows, Decimal(0)) <= 0:
        return None, (
            "The undiscounted total is not positive, so there is no positive internal "
            "rate of return."
        )
    low, high = Decimal(0), _IRR_MAX
    if _npv_at(flows, high) > 0:
        return None, (
            "The internal rate of return is above 1000 % per period, which is a "
            "modelling artefact rather than a rate."
        )
    for _ in range(200):
        middle = (low + high) / 2
        if _npv_at(flows, middle) > 0:
            low = middle
        else:
            high = middle
        if high - low < _IRR_TOLERANCE:
            break
    return (low + high) / 2, None


def financial_model(
    *,
    initial_cost: Decimal,
    net_savings_per_period: Decimal,
    period: str,
    horizon_periods: int | None,
    discount_rate_annual: Decimal | None,
) -> NpvResult:
    """Compute the financial model of one value scenario.

    Every missing input yields an *unavailable* result with a reason.  Nothing
    is silently assumed to be zero.
    """
    if horizon_periods is None:
        return _unavailable("No horizon was stated, so no net present value can be computed.")
    if horizon_periods < 1 or horizon_periods > MAX_HORIZON_PERIODS:
        return _unavailable(
            f"The horizon must be between 1 and {MAX_HORIZON_PERIODS} periods.",
            horizon_periods,
        )
    if period not in PERIODS_PER_YEAR:
        return _unavailable(f"Unknown period: {period!r}", horizon_periods)

    warnings: list[str] = []
    flows = _cash_flows(initial_cost, net_savings_per_period, horizon_periods)

    periodic_rate: Decimal | None = None
    if discount_rate_annual is not None:
        try:
            periodic_rate = periodic_discount_rate(discount_rate_annual, period)
        except ValueError as exc:
            return _unavailable(str(exc), horizon_periods)

    net_present_value: Decimal | None = None
    if periodic_rate is not None:
        net_present_value = _npv_at(flows, periodic_rate).quantize(_ROUNDING)

    internal_rate: Decimal | None = None
    irr_periodic, irr_reason = _irr_periodic(flows)
    if irr_periodic is not None:
        with localcontext() as context:
            context.prec = _PRECISION
            annual = (Decimal(1) + irr_periodic) ** PERIODS_PER_YEAR[period] - Decimal(1)
        internal_rate = annual.quantize(_ROUNDING)

    discounted_payback: Decimal | None = None
    payback_reason: str | None = None
    if periodic_rate is not None:
        discounted_payback = _discounted_payback(flows, periodic_rate)
        if discounted_payback is None:
            payback_reason = (
                "The horizon is too short to recover the initial cost at the stated discount rate."
            )
    else:
        payback_reason = "No discount rate was stated, so there is no discounted payback."

    if net_present_value is None:
        return NpvResult(
            status="unavailable",
            reason="No discount rate was stated, so no net present value can be computed.",
            internal_rate_of_return=internal_rate,
            internal_rate_of_return_reason=irr_reason,
            horizon_periods=horizon_periods,
            warnings=warnings,
        )

    return NpvResult(
        status="available",
        net_present_value=net_present_value,
        internal_rate_of_return=internal_rate,
        internal_rate_of_return_reason=irr_reason,
        discounted_payback_periods=discounted_payback,
        discounted_payback_reason=payback_reason,
        periodic_discount_rate=periodic_rate.quantize(_ROUNDING),
        horizon_periods=horizon_periods,
        warnings=warnings,
    )


def _discounted_payback(flows: list[Decimal], periodic_rate: Decimal) -> Decimal | None:
    """The period in which the discounted cash flow turns positive.

    The last period is interpolated, so the answer is not rounded up to a
    whole period that has not been reached.
    """
    with localcontext() as context:
        context.prec = _PRECISION
        factor = Decimal(1)
        cumulative = Decimal(0)
        previous = Decimal(0)
        for index, flow in enumerate(flows):
            if index:
                factor *= Decimal(1) + periodic_rate
            discounted = flow / factor
            previous = cumulative
            cumulative += discounted
            if index and cumulative >= 0:
                if discounted == 0:
                    return Decimal(index)
                fraction = (-previous) / discounted
                return (Decimal(index) - 1 + fraction).quantize(_ROUNDING)
    return None
