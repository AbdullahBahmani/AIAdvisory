"""Phase 9 P9-04: value sensitivity envelope and same-currency scenario comparison.

Advisory forecast arithmetic only. Nothing in this module writes, calls a model or
feeds the rules engine, so a sensitivity result can never change a rule hit, a
recommendation category or a decision. An envelope is a statement about *how wrong
the entered assumptions would have to be*, not a validated business case.

Method
------
One-at-a-time (OAT) variation. Each declared assumption is varied on its own while
every other assumption stays at its entered value. For every assumption the envelope
reports the range of net savings over the varied range, the exact breakeven point
where net savings reach zero, and the range of that assumption which keeps net
savings at or above the minimum net value threshold.

The default band is a +/-25 % relative band around the entered value. That band is a
**documented modelling convention**, not market data, not a distribution and not
customer evidence: this repository holds no sourced range for case volumes, handling
times or loaded hourly cost. A caller may supply an explicit range per assumption
(typically from a cited source); the envelope then records that the range was
caller-supplied instead of conventional.

Arithmetic
----------
* Net savings at every reported point are produced by the existing Phase 8 helper
  ``value_projection``, so the envelope can never disagree with the ROI simulator.
* Breakeven and threshold boundaries are solved in *exact* rational arithmetic over
  the same Decimal inputs (``Decimal.as_integer_ratio`` -> integers -> gcd), so a
  boundary is mathematically exact. A boundary with a terminating decimal expansion
  is reported exactly; a boundary without one is reported as an explicitly labelled
  rendering plus the exact integer ratio that defines it.
* No floating point is used: no ``float``, no ``round()``.

Unknown stays unknown
---------------------
A missing assumption is never replaced by zero or by an estimate. The affected
dimension is reported as ``unavailable`` with an explicit reason.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, localcontext
from math import gcd

from app.domain.portfolio import (
    ExactRatioView,
    ScenarioComparisonRowView,
    ScenarioComparisonView,
    SensitivityAssumption,
    SensitivityBandView,
    SensitivityBaseView,
    SensitivityBoundaryView,
    SensitivityDimensionView,
    SensitivityEnvelopeView,
    SensitivityMethodView,
    SensitivityNetRangeView,
    SensitivityRangeIn,
    SensitivityRangeView,
    SensitivityScenarioIn,
    SensitivityThresholdIn,
    SensitivityThresholdView,
    ValueScenarioIn,
)
from app.services.portfolio import value_projection

SENSITIVITY_METHOD = "one_at_a_time_relative_band"
SENSITIVITY_METHOD_VERSION = "sensitivity-v1"
BAND_FRACTION = Decimal("0.25")
SENSITIVITY_PRECISION = 28
MINUTES_PER_HOUR = Decimal("60")
ZERO = Decimal("0")
ASSUMPTIONS: tuple[SensitivityAssumption, ...] = (
    "cases_per_period",
    "baseline_minutes",
    "target_minutes",
    "hourly_cost",
)
DISCLAIMER = (
    "Advisory sensitivity envelope over entered assumptions. Not measured ROI, "
    "not a validated business case and not an approval to build."
)

# An exact rational number held as a reduced (numerator, denominator) integer pair.
_Ratio = tuple[int, int]


# --------------------------------------------------------------------------- #
# Exact rational helpers (integers only, never float)
# --------------------------------------------------------------------------- #


def _ratio(value: Decimal) -> _Ratio:
    """Exact integer ratio of a finite Decimal."""
    numerator, denominator = value.as_integer_ratio()
    return numerator, denominator


def _reduce(numerator: int, denominator: int) -> _Ratio:
    if denominator == 0:
        raise ZeroDivisionError("exact ratio with a zero denominator")
    if denominator < 0:
        numerator, denominator = -numerator, -denominator
    divisor = gcd(abs(numerator), denominator) or 1
    return numerator // divisor, denominator // divisor


def _ratio_neg(value: _Ratio) -> _Ratio:
    return -value[0], value[1]


def _ratio_div(left: _Ratio, right: _Ratio) -> _Ratio:
    if right[0] == 0:
        raise ZeroDivisionError("exact ratio division by zero")
    return _reduce(left[0] * right[1], left[1] * right[0])


def _ratio_le(left: _Ratio, right: _Ratio) -> bool:
    return left[0] * right[1] <= right[0] * left[1]


def _ratio_to_decimal(value: _Ratio) -> tuple[Decimal, bool]:
    """Render an exact ratio as a Decimal.

    Returns ``(decimal, is_exact)``. The value is exact whenever the reduced
    denominator only has factors 2 and 5, because such a ratio has a terminating
    decimal expansion. Otherwise the rendering is produced by the pinned decimal
    context and is explicitly reported as *not* exact; the integer ratio stays
    authoritative.
    """
    numerator, denominator = value
    if denominator == 1:
        return Decimal(numerator), True
    rest = denominator
    twos = 0
    fives = 0
    while rest % 2 == 0:
        rest //= 2
        twos += 1
    while rest % 5 == 0:
        rest //= 5
        fives += 1
    if rest != 1:
        return Decimal(numerator) / Decimal(denominator), False
    scale = max(twos, fives)
    scaled = numerator * (2 ** (scale - twos)) * (5 ** (scale - fives))
    digits = str(abs(scaled)).rjust(scale + 1, "0")
    sign = "-" if scaled < 0 else ""
    text = f"{sign}{digits[:-scale]}.{digits[-scale:]}"
    return Decimal(text), True


def _exact_ratio_view(value: _Ratio) -> ExactRatioView:
    return ExactRatioView(numerator=str(value[0]), denominator=str(value[1]))


# --------------------------------------------------------------------------- #
# Scenario helpers
# --------------------------------------------------------------------------- #


def sensitivity_scenario_from(values: ValueScenarioIn) -> SensitivityScenarioIn:
    """Adapt the stored Phase 8 scenario to the sensitivity contract."""
    return SensitivityScenarioIn(
        name=values.name,
        currency=values.currency,
        period=values.period,
        cases_per_period=values.cases_per_period,
        baseline_minutes=values.baseline_minutes,
        target_minutes=values.target_minutes,
        hourly_cost=values.hourly_cost,
        recurring_cost=values.recurring_cost,
        initial_cost=values.initial_cost,
    )


def _projection_scenario(scenario: SensitivityScenarioIn) -> ValueScenarioIn | None:
    """A projection input for the Phase 8 helper, or ``None`` if an input is unknown.

    The scenario is built with ``model_construct`` on purpose: a sensitivity probe
    point may carry more decimal places than the stored four-decimal contract
    allows, and a derived internal point must never be rounded back into that
    contract. Every value placed here was validated by
    :class:`SensitivityScenarioIn` before it reached the service.
    """
    if any(getattr(scenario, assumption) is None for assumption in ASSUMPTIONS):
        return None
    return ValueScenarioIn.model_construct(
        cases_per_period=scenario.cases_per_period,
        baseline_minutes=scenario.baseline_minutes,
        target_minutes=scenario.target_minutes,
        hourly_cost=scenario.hourly_cost,
        recurring_cost=scenario.recurring_cost,
        initial_cost=scenario.initial_cost,
        currency=scenario.currency,
        period=scenario.period,
    )


def _net_at(projection: ValueScenarioIn, assumption: str, value: Decimal) -> Decimal:
    """Net savings at one probe point, produced by the Phase 8 projection helper."""
    probe = projection.model_copy(update={assumption: value})
    _gross, net, _payback = value_projection(probe)
    return net


def _default_band(base: Decimal) -> tuple[Decimal, Decimal]:
    """The documented default band: +/-25 % around the entered value."""
    return base * (Decimal("1") - BAND_FRACTION), base * (Decimal("1") + BAND_FRACTION)


def _affine(
    scenario: SensitivityScenarioIn, assumption: SensitivityAssumption
) -> tuple[Decimal, Decimal] | None:
    """Return ``(M, N)`` with ``net(x) = (M * x + N) / 60`` for the varied assumption.

    The form is exact: every coefficient is a product of entered Decimal inputs
    and whole numbers, so no division happens while building it.
    """
    cases = scenario.cases_per_period
    baseline = scenario.baseline_minutes
    target = scenario.target_minutes
    hourly = scenario.hourly_cost
    recurring = scenario.recurring_cost
    if assumption == "cases_per_period":
        if baseline is None or target is None or hourly is None:
            return None
        return (baseline - target) * hourly, -MINUTES_PER_HOUR * recurring
    if assumption == "baseline_minutes":
        if cases is None or target is None or hourly is None:
            return None
        return cases * hourly, -(cases * target * hourly) - MINUTES_PER_HOUR * recurring
    if assumption == "target_minutes":
        if cases is None or baseline is None or hourly is None:
            return None
        return -(cases * hourly), (cases * hourly * baseline) - MINUTES_PER_HOUR * recurring
    if cases is None or baseline is None or target is None:
        return None
    return cases * (baseline - target), -MINUTES_PER_HOUR * recurring


# --------------------------------------------------------------------------- #
# Envelope
# --------------------------------------------------------------------------- #


def _unavailable_boundary(reason: str) -> SensitivityBoundaryView:
    return SensitivityBoundaryView(status="unavailable", reason=reason)


def _unavailable_threshold(
    threshold: SensitivityThresholdIn | None, reason: str
) -> SensitivityThresholdView:
    if threshold is None:
        return SensitivityThresholdView(
            status="unavailable",
            basis="not_set",
            currency_basis="not_applicable",
            reason=reason,
        )
    return SensitivityThresholdView(
        status="unavailable",
        minimum_net_value=threshold.minimum_net_value,
        basis=threshold.basis,
        currency_basis=threshold.currency_basis,
        reason=reason,
    )


def _breakeven(affine: tuple[Decimal, Decimal]) -> SensitivityBoundaryView:
    """The exact point where net savings reach zero for the varied assumption."""
    slope, offset = affine
    if slope == 0:
        if offset == 0:
            return SensitivityBoundaryView(
                status="unavailable",
                reason="net_savings_zero_for_every_value",
                note="Net savings are zero for every value of this assumption.",
            )
        return SensitivityBoundaryView(
            status="unavailable",
            reason="net_savings_independent_of_assumption",
            note="Net savings do not depend on this assumption, so no breakeven exists.",
        )
    ratio = _ratio_div(_ratio_neg(_ratio(offset)), _ratio(slope))
    value, is_exact = _ratio_to_decimal(ratio)
    direction = "below" if slope > 0 else "above"
    if value < 0:
        return SensitivityBoundaryView(
            status="unavailable",
            exact_ratio=_exact_ratio_view(ratio),
            direction=direction,
            reason="breakeven_outside_feasible_domain",
            note=(
                "The breakeven lies below zero, so no feasible (non-negative) value "
                "of this assumption turns net savings negative."
            ),
        )
    return SensitivityBoundaryView(
        status="available",
        value=value,
        value_is_exact=is_exact,
        exact_ratio=_exact_ratio_view(ratio),
        direction=direction,
        note=(
            "Net savings turn negative when this assumption falls below the breakeven."
            if direction == "below"
            else "Net savings turn negative when this assumption rises above the breakeven."
        ),
    )


def _threshold_view(
    scenario: SensitivityScenarioIn,
    threshold: SensitivityThresholdIn | None,
    affine: tuple[Decimal, Decimal] | None,
    band: tuple[Decimal, Decimal],
    net_at_base: Decimal | None,
) -> SensitivityThresholdView:
    """The assumption range that keeps net savings at or above the threshold."""
    if threshold is None:
        return _unavailable_threshold(None, "minimum_net_value_threshold_not_set")
    if threshold.period is None:
        return _unavailable_threshold(threshold, "minimum_net_value_period_basis_unknown")
    if threshold.period != scenario.period:
        return _unavailable_threshold(threshold, "minimum_net_value_period_basis_mismatch")
    if affine is None:
        return _unavailable_threshold(threshold, "assumption_inputs_unavailable")
    slope, offset = affine
    level = threshold.minimum_net_value
    scaled_level = MINUTES_PER_HOUR * level
    satisfied_at_base = None if net_at_base is None else net_at_base >= level
    if slope == 0:
        holds = offset >= scaled_level
        return SensitivityThresholdView(
            status="available",
            minimum_net_value=level,
            basis=threshold.basis,
            currency_basis=threshold.currency_basis,
            satisfied_at_base=satisfied_at_base,
            satisfied_range=SensitivityRangeView(low=band[0], high=band[1]) if holds else None,
            boundary=SensitivityBoundaryView(
                status="unavailable",
                reason="net_savings_independent_of_assumption",
            ),
        )
    ratio = _ratio_div(_ratio(scaled_level - offset), _ratio(slope))
    value, is_exact = _ratio_to_decimal(ratio)
    direction = "below" if slope > 0 else "above"
    band_low, band_high = _ratio(band[0]), _ratio(band[1])
    if slope > 0:
        low = band_low
        for candidate in (ratio, _ratio(ZERO)):
            if not _ratio_le(candidate, low):
                low = candidate
        high = band_high
    else:
        low = band_low
        high = band_high
        if _ratio_le(ratio, high):
            high = ratio
    if _ratio_le(low, high):
        low_value, _ = _ratio_to_decimal(low)
        high_value, _ = _ratio_to_decimal(high)
        satisfied_range: SensitivityRangeView | None = SensitivityRangeView(
            low=low_value, high=high_value
        )
    else:
        satisfied_range = None
    boundary = SensitivityBoundaryView(
        status="available" if value >= 0 else "unavailable",
        value=value if value >= 0 else None,
        value_is_exact=is_exact,
        exact_ratio=_exact_ratio_view(ratio),
        direction=direction,
        reason=None if value >= 0 else "boundary_outside_feasible_domain",
        note=(
            "Net savings stay at or above the threshold while this assumption is "
            f"{'at or above' if direction == 'below' else 'at or below'} the boundary, "
            "limited to the analysed range."
        ),
    )
    return SensitivityThresholdView(
        status="available",
        minimum_net_value=level,
        basis=threshold.basis,
        currency_basis=threshold.currency_basis,
        satisfied_at_base=satisfied_at_base,
        satisfied_range=satisfied_range,
        boundary=boundary,
    )


def _net_range(
    projection: ValueScenarioIn,
    assumption: SensitivityAssumption,
    low: Decimal,
    high: Decimal,
) -> SensitivityNetRangeView:
    """The range of net savings over the varied range (the projection is affine)."""
    net_low = _net_at(projection, assumption, low)
    net_high = _net_at(projection, assumption, high)
    if net_low <= net_high:
        return SensitivityNetRangeView(low=net_low, high=net_high, low_at=low, high_at=high)
    return SensitivityNetRangeView(low=net_high, high=net_low, low_at=high, high_at=low)


def _dimension(
    scenario: SensitivityScenarioIn,
    projection: ValueScenarioIn | None,
    assumption: SensitivityAssumption,
    ranges: Mapping[str, SensitivityRangeIn],
    threshold: SensitivityThresholdIn | None,
) -> SensitivityDimensionView:
    base_value = getattr(scenario, assumption)
    if base_value is None:
        return SensitivityDimensionView(
            assumption=assumption,
            base_value=None,
            status="unavailable",
            reason="assumption_not_provided",
            band=None,
            net_savings_range=None,
            breakeven=_unavailable_boundary("assumption_not_provided"),
            threshold=_unavailable_threshold(threshold, "assumption_not_provided"),
        )
    supplied = ranges.get(assumption)
    if supplied is not None:
        band_low, band_high = supplied.low, supplied.high
        band = SensitivityBandView(
            low=band_low,
            high=band_high,
            source="caller_supplied",
            note="Range supplied by the caller; record its source before relying on it.",
        )
    else:
        band_low, band_high = _default_band(base_value)
        band = SensitivityBandView(
            low=band_low,
            high=band_high,
            source="modelling_convention_relative_band",
            note=(
                "Default +/-25 % relative band around the entered value. This is a "
                "documented modelling convention, not market data or customer evidence."
            ),
        )
    affine = _affine(scenario, assumption)
    if band_low == band_high:
        band = band.model_copy(
            update={
                "note": band.note
                + " The entered value is zero, so the relative band is a single point."
            }
        )
    if projection is None or affine is None:
        reason = "assumption_inputs_unavailable"
        return SensitivityDimensionView(
            assumption=assumption,
            base_value=base_value,
            status="unavailable",
            reason=reason,
            band=band,
            net_savings_range=None,
            breakeven=_unavailable_boundary(reason),
            threshold=_unavailable_threshold(threshold, reason),
        )
    net_at_base = _net_at(projection, assumption, base_value)
    return SensitivityDimensionView(
        assumption=assumption,
        base_value=base_value,
        status="available",
        reason=None,
        band=band,
        net_savings_range=_net_range(projection, assumption, band_low, band_high),
        breakeven=_breakeven(affine),
        threshold=_threshold_view(scenario, threshold, affine, (band_low, band_high), net_at_base),
    )


def _method() -> SensitivityMethodView:
    return SensitivityMethodView(
        name=SENSITIVITY_METHOD,
        version=SENSITIVITY_METHOD_VERSION,
        band_fraction=BAND_FRACTION,
        description=(
            "One-at-a-time variation: each assumption is varied on its own while every "
            "other assumption stays at its entered value. For each assumption the "
            "envelope reports the range of net savings over the varied range, the exact "
            "breakeven point where net savings reach zero, and the range of that "
            "assumption that keeps net savings at or above the minimum net value "
            "threshold. The default band is a +/-25 % relative band, a documented "
            "modelling convention rather than market data."
        ),
        boundary_arithmetic=(
            "Breakeven and threshold boundaries are solved in exact rational arithmetic "
            "over the entered Decimal inputs (as_integer_ratio, integer arithmetic, gcd "
            "reduction). A boundary with a terminating decimal expansion is reported "
            "exactly; a non-terminating one is reported as a labelled rendering together "
            "with the exact integer ratio that defines it."
        ),
        rounding=(
            "No floating point and no round() are used. Net savings come from the Phase 8 "
            "value_projection helper under a pinned 28-significant-digit decimal context, "
            "so the envelope and the ROI simulator can never disagree."
        ),
    )


def sensitivity_envelope(
    scenario: SensitivityScenarioIn,
    *,
    ranges: Mapping[str, SensitivityRangeIn] | None = None,
    threshold: SensitivityThresholdIn | None = None,
    scenario_id: str | None = None,
    assessment_id: str | None = None,
    assessment_revision: int | None = None,
    stale: bool = False,
) -> SensitivityEnvelopeView:
    """Compute the advisory sensitivity envelope for one scenario.

    The result is a pure function of its arguments: the same inputs always produce
    byte-identical output. No state is read or written and no recommendation can
    be influenced by the result.
    """
    supplied_ranges = ranges or {}
    with localcontext() as context:
        context.prec = SENSITIVITY_PRECISION
        return _envelope(
            scenario,
            supplied_ranges,
            threshold,
            scenario_id,
            assessment_id,
            assessment_revision,
            stale,
        )


def _envelope(
    scenario: SensitivityScenarioIn,
    ranges: Mapping[str, SensitivityRangeIn],
    threshold: SensitivityThresholdIn | None,
    scenario_id: str | None,
    assessment_id: str | None,
    assessment_revision: int | None,
    stale: bool,
) -> SensitivityEnvelopeView:
    projection = _projection_scenario(scenario)
    missing = [assumption for assumption in ASSUMPTIONS if getattr(scenario, assumption) is None]
    if projection is None:
        base = SensitivityBaseView(
            status="unavailable",
            reason="assumption_inputs_unavailable: " + ", ".join(missing),
        )
    else:
        gross, net, payback = value_projection(projection)
        base = SensitivityBaseView(
            status="available",
            reason=None,
            gross_savings=gross,
            net_savings=net,
            payback_periods=payback,
        )
    dimensions = [
        _dimension(scenario, projection, assumption, ranges, threshold)
        for assumption in ASSUMPTIONS
    ]
    warnings: list[str] = []
    if missing:
        warnings.append(
            "Assumptions not provided (" + ", ".join(missing) + ") are reported as "
            "unavailable; they are never treated as zero or estimated."
        )
    if stale:
        warnings.append(
            "This scenario belongs to an older assessment revision; its assumptions may "
            "no longer describe the current scope."
        )
    if any(
        dimension.band and dimension.band.source == "modelling_convention_relative_band"
        for dimension in dimensions
    ):
        warnings.append(
            "Default ranges are a documented modelling convention (+/-25 %), not market "
            "data and not customer evidence."
        )
    if threshold is None:
        warnings.append(
            "No minimum net value threshold is set, so the headroom before the business "
            "case fails cannot be established."
        )
    return SensitivityEnvelopeView(
        scenario_id=scenario_id,
        scenario_name=scenario.name,
        currency=scenario.currency,
        period=scenario.period,
        assessment_id=assessment_id,
        assessment_revision=assessment_revision,
        stale=stale,
        base=base,
        method=_method(),
        dimensions=dimensions,
        warnings=warnings,
        advisory_only=True,
        disclaimer=DISCLAIMER,
    )


# --------------------------------------------------------------------------- #
# Comparison
# --------------------------------------------------------------------------- #


class IncomparableScenariosError(ValueError):
    """Raised when scenarios cannot be compared without an unsourced conversion."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


@dataclass(frozen=True)
class ScenarioForComparison:
    scenario_id: str
    scenario: ValueScenarioIn
    stale: bool = False


def compare_scenarios(
    scenarios: Sequence[ScenarioForComparison],
) -> ScenarioComparisonView:
    """Compare scenarios that share one currency and one period.

    A mixed currency or a mixed period is refused: this repository has no sourced
    exchange rate and rate date, and the Phase 8 projection is defined per declared
    period. Nothing is converted, approximated or silently dropped.
    """
    if not scenarios:
        raise ValueError("At least one scenario is required for a comparison")
    currencies = sorted({item.scenario.currency for item in scenarios})
    if len(currencies) > 1:
        raise IncomparableScenariosError(
            "mixed_currency",
            "Refused: mixed-currency comparison is not supported (scenarios use "
            + ", ".join(currencies)
            + "). No exchange rate is applied because this repository has no sourced "
            "rate and rate date; compare scenarios that share one currency.",
        )
    periods = sorted({item.scenario.period for item in scenarios})
    if len(periods) > 1:
        raise IncomparableScenariosError(
            "mixed_period",
            "Refused: mixed-period comparison is not supported (scenarios use "
            + ", ".join(periods)
            + "). Periods are never normalised or annualised: the Phase 8 projection is "
            "defined per declared period, so a monthly and a yearly amount are not "
            "comparable.",
        )
    rows: list[ScenarioComparisonRowView] = []
    for item in scenarios:
        gross, net, payback = value_projection(item.scenario)
        rows.append(
            ScenarioComparisonRowView(
                scenario_id=item.scenario_id,
                name=item.scenario.name,
                currency=item.scenario.currency,
                period=item.scenario.period,
                gross_savings=gross,
                net_savings=net,
                payback_periods=payback,
                stale=item.stale,
                rank=0,
            )
        )
    ordered = sorted(rows, key=lambda row: (-row.net_savings, row.name, row.scenario_id))
    for position, row in enumerate(ordered, start=1):
        row.rank = position
    warnings = [
        "Rows are ordered by modelled net savings within one currency and period; this "
        "is an advisory ordering, not a validated priority ranking."
    ]
    if any(row.stale for row in rows):
        warnings.append(
            "At least one scenario belongs to an older assessment revision and may no "
            "longer describe the current scope."
        )
    return ScenarioComparisonView(
        currency=currencies[0],
        period=periods[0],
        comparable=True,
        conversion_applied=False,
        rows=rows,
        ranking=[row.scenario_id for row in ordered],
        warnings=warnings,
        advisory_only=True,
        disclaimer=DISCLAIMER,
    )
