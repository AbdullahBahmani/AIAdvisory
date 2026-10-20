"""Phase 9 P9-04: value sensitivity envelope and scenario comparison.

These checks prove exact boundary arithmetic, reproducibility, refusal of
incomparable scenarios and the absence of floating point. They are regression
evidence about *arithmetic and contracts only*: no field accuracy, no measured
ROI and no validated business case is claimed anywhere.
"""

from __future__ import annotations

import ast
import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.domain.portfolio import (
    SensitivityRangeIn,
    SensitivityScenarioIn,
    SensitivityThresholdIn,
    ValueScenarioIn,
)
from app.services.portfolio import value_projection
from app.services.sensitivity import (
    ASSUMPTIONS,
    IncomparableScenariosError,
    ScenarioForComparison,
    compare_scenarios,
    sensitivity_envelope,
    sensitivity_scenario_from,
)
from tests.test_security import secure_client  # noqa: F401 - shared pytest fixture re-used here

REPO_ROOT = Path(__file__).resolve().parent.parent


def _scenario(**overrides) -> dict:
    payload = {
        "name": "Synthetic manual case",
        "currency": "NOK",
        "period": "month",
        "cases_per_period": "100",
        "baseline_minutes": "30",
        "target_minutes": "10",
        "hourly_cost": "600",
        "recurring_cost": "5000",
        "initial_cost": "15000",
        "assumption_source": "Synthetic discovery interview",
    }
    payload.update(overrides)
    return payload


def _values(**overrides) -> ValueScenarioIn:
    return ValueScenarioIn.model_validate(_scenario(**overrides))


def _dimension(envelope, assumption: str):
    return next(d for d in envelope.dimensions if d.assumption == assumption)


def _threshold(level: str = "5000", period: str | None = "month") -> SensitivityThresholdIn:
    return SensitivityThresholdIn(
        minimum_net_value=Decimal(level),
        basis="assessment_minimum_net_value_per_period",
        period=period,
        currency_basis="applied in the scenario currency NOK",
    )


# --------------------------------------------------------------------------- #
# Arithmetic: the envelope must agree with the Phase 8 projection
# --------------------------------------------------------------------------- #


def test_envelope_base_case_agrees_with_value_projection():
    values = _values()
    envelope = sensitivity_envelope(sensitivity_scenario_from(values))
    gross, net, payback = value_projection(values)
    assert envelope.base.status == "available"
    assert envelope.base.gross_savings == gross
    assert envelope.base.net_savings == net
    assert envelope.base.payback_periods == payback
    # The base point is inside every band and reproduces the same net savings.
    for assumption in ASSUMPTIONS:
        dimension = _dimension(envelope, assumption)
        assert dimension.status == "available"
        assert dimension.base_value == getattr(values, assumption)
        assert dimension.net_savings_range is not None
        probe = values.model_copy(update={assumption: dimension.base_value})
        assert value_projection(probe)[1] == net


def test_net_savings_range_endpoints_match_value_projection():
    values = _values()
    envelope = sensitivity_envelope(sensitivity_scenario_from(values))
    for assumption in ASSUMPTIONS:
        dimension = _dimension(envelope, assumption)
        band = dimension.band
        net_low = value_projection(values.model_copy(update={assumption: band.low}))[1]
        net_high = value_projection(values.model_copy(update={assumption: band.high}))[1]
        assert {dimension.net_savings_range.low, dimension.net_savings_range.high} == {
            net_low,
            net_high,
        }


# --------------------------------------------------------------------------- #
# Exactness at the boundaries
# --------------------------------------------------------------------------- #


def test_breakeven_lands_exactly_where_net_savings_reach_zero():
    values = _values()
    envelope = sensitivity_envelope(sensitivity_scenario_from(values))
    cases = Decimal(values.cases_per_period)
    baseline = Decimal(values.baseline_minutes)
    target = Decimal(values.target_minutes)
    hourly = Decimal(values.hourly_cost)
    recurring = Decimal(values.recurring_cost)

    expected = {
        # 60 * recurring / ((baseline - target) * hourly)
        "cases_per_period": Decimal(60) * recurring / ((baseline - target) * hourly),
        # target + 60 * recurring / (cases * hourly)
        "baseline_minutes": target + Decimal(60) * recurring / (cases * hourly),
        # baseline - 60 * recurring / (cases * hourly)
        "target_minutes": baseline - Decimal(60) * recurring / (cases * hourly),
        # 60 * recurring / (cases * (baseline - target))
        "hourly_cost": Decimal(60) * recurring / (cases * (baseline - target)),
    }
    for assumption, breakeven_value in expected.items():
        dimension = _dimension(envelope, assumption)
        breakeven = dimension.breakeven
        assert breakeven.status == "available", assumption
        assert breakeven.value == breakeven_value, assumption
        assert breakeven.value_is_exact is True, assumption
        # The reported breakeven really is the point where net savings reach zero.
        probe = values.model_copy(update={assumption: breakeven.value})
        assert value_projection(probe)[1] == Decimal(0), assumption


def test_exact_ratio_identifies_the_boundary_even_when_the_decimal_repeats():
    # hourly_cost 7 and recurring 1 make the breakeven 3/7: no terminating decimal.
    values = _values(hourly_cost="7", recurring_cost="1")
    envelope = sensitivity_envelope(sensitivity_scenario_from(values))
    breakeven = _dimension(envelope, "cases_per_period").breakeven
    assert breakeven.status == "available"
    assert breakeven.value_is_exact is False
    assert breakeven.exact_ratio is not None
    numerator = int(breakeven.exact_ratio.numerator)
    denominator = int(breakeven.exact_ratio.denominator)
    assert (numerator, denominator) == (3, 7)
    # The rendering is the context-precision decimal of that exact ratio, and the
    # exact ratio satisfies the boundary equation without any rounding.
    assert breakeven.value == Decimal(numerator) / Decimal(denominator)
    baseline = Decimal(values.baseline_minutes)
    target = Decimal(values.target_minutes)
    hourly = Decimal(values.hourly_cost)
    recurring = Decimal(values.recurring_cost)
    slope = (baseline - target) * hourly
    offset = -Decimal(60) * recurring
    assert slope * numerator + offset * denominator == 0


def test_threshold_headroom_is_reported_within_the_analysed_range():
    values = _values()
    envelope = sensitivity_envelope(sensitivity_scenario_from(values), threshold=_threshold())
    cases = _dimension(envelope, "cases_per_period")
    # 60 * 5000 / ((30 - 10) * 600) = 50 cases keep net savings at or above 5000.
    assert cases.threshold.status == "available"
    assert cases.threshold.boundary.value == Decimal(50)
    assert cases.threshold.boundary.value_is_exact is True
    assert cases.threshold.satisfied_at_base is True
    assert cases.threshold.satisfied_range is not None
    assert cases.threshold.satisfied_range.low == cases.band.low
    assert cases.threshold.satisfied_range.high == cases.band.high
    # A threshold nobody can reach is reported as an empty satisfying range.
    strict = sensitivity_envelope(
        sensitivity_scenario_from(values), threshold=_threshold(level="999999999")
    )
    strict_cases = _dimension(strict, "cases_per_period")
    assert strict_cases.threshold.status == "available"
    assert strict_cases.threshold.satisfied_at_base is False
    assert strict_cases.threshold.satisfied_range is None


def test_threshold_without_a_known_period_basis_is_unavailable():
    values = _values()
    envelope = sensitivity_envelope(
        sensitivity_scenario_from(values), threshold=_threshold(period=None)
    )
    threshold = _dimension(envelope, "cases_per_period").threshold
    assert threshold.status == "unavailable"
    assert threshold.reason == "minimum_net_value_period_basis_unknown"
    assert threshold.minimum_net_value == Decimal(5000)


def test_threshold_for_another_period_is_refused():
    values = _values()
    envelope = sensitivity_envelope(
        sensitivity_scenario_from(values), threshold=_threshold(period="year")
    )
    threshold = _dimension(envelope, "cases_per_period").threshold
    assert threshold.status == "unavailable"
    assert threshold.reason == "minimum_net_value_period_basis_mismatch"


# --------------------------------------------------------------------------- #
# Reproducibility and unknown handling
# --------------------------------------------------------------------------- #


def test_identical_inputs_produce_byte_identical_output():
    values = _values()
    first = sensitivity_envelope(sensitivity_scenario_from(values), threshold=_threshold())
    second = sensitivity_envelope(sensitivity_scenario_from(values), threshold=_threshold())
    assert first.model_dump_json() == second.model_dump_json()
    assert json.dumps(first.model_dump(mode="json")) == json.dumps(second.model_dump(mode="json"))


def test_missing_assumption_is_reported_as_unavailable_and_never_as_zero():
    partial = SensitivityScenarioIn(
        name="Partial synthetic case",
        currency="NOK",
        period="month",
        cases_per_period="100",
        baseline_minutes="30",
        target_minutes="10",
        hourly_cost=None,
        recurring_cost="5000",
    )
    envelope = sensitivity_envelope(partial)
    hourly = _dimension(envelope, "hourly_cost")
    assert hourly.status == "unavailable"
    assert hourly.reason == "assumption_not_provided"
    assert hourly.base_value is None
    assert hourly.band is None
    assert hourly.net_savings_range is None
    assert hourly.breakeven.status == "unavailable"
    assert hourly.breakeven.value is None
    assert hourly.threshold.status == "unavailable"
    # No dimension may present an unknown assumption as a number.
    assert envelope.base.status == "unavailable"
    assert envelope.base.net_savings is None
    for dimension in envelope.dimensions:
        if dimension.assumption != "hourly_cost":
            assert dimension.status == "unavailable"
            assert dimension.net_savings_range is None
    assert any("unavailable" in warning for warning in envelope.warnings)


def test_a_zero_assumption_gives_a_degenerate_band_but_a_real_breakeven():
    values = _values(cases_per_period="0")
    envelope = sensitivity_envelope(sensitivity_scenario_from(values))
    dimension = _dimension(envelope, "cases_per_period")
    assert dimension.band.low == Decimal(0)
    assert dimension.band.high == Decimal(0)
    assert "single point" in dimension.band.note
    # Zero volume still has a meaningful minimum volume for the case to hold.
    assert dimension.breakeven.status == "available"
    assert dimension.breakeven.value == Decimal(25)
    assert dimension.net_savings_range.low == dimension.net_savings_range.high


def test_an_assumption_the_case_does_not_depend_on_has_no_breakeven():
    values = _values(baseline_minutes="10")
    envelope = sensitivity_envelope(sensitivity_scenario_from(values))
    cases = _dimension(envelope, "cases_per_period")
    assert cases.breakeven.status == "unavailable"
    assert cases.breakeven.reason == "net_savings_independent_of_assumption"
    assert cases.breakeven.value is None
    # Varying the baseline still has a breakeven, because that dimension moves it.
    baseline = _dimension(envelope, "baseline_minutes")
    assert baseline.breakeven.status == "available"
    assert baseline.breakeven.value == Decimal(15)


def test_a_breakeven_below_zero_is_reported_as_outside_the_feasible_domain():
    values = _values(target_minutes="40")
    envelope = sensitivity_envelope(sensitivity_scenario_from(values))
    cases = _dimension(envelope, "cases_per_period")
    assert cases.breakeven.status == "unavailable"
    assert cases.breakeven.reason == "breakeven_outside_feasible_domain"
    assert cases.breakeven.direction == "above"
    assert cases.net_savings_range.high < 0


def test_caller_supplied_ranges_are_used_and_labelled():
    values = _values()
    envelope = sensitivity_envelope(
        sensitivity_scenario_from(values),
        ranges={"cases_per_period": SensitivityRangeIn(low=Decimal("40"), high=Decimal("80"))},
    )
    dimension = _dimension(envelope, "cases_per_period")
    assert dimension.band.source == "caller_supplied"
    assert dimension.band.low == Decimal(40)
    assert dimension.band.high == Decimal(80)
    default_dimension = _dimension(
        sensitivity_envelope(sensitivity_scenario_from(values)), "cases_per_period"
    )
    assert default_dimension.band.source == "modelling_convention_relative_band"
    assert default_dimension.band.low == Decimal(75)
    assert default_dimension.band.high == Decimal(125)


# --------------------------------------------------------------------------- #
# Comparison
# --------------------------------------------------------------------------- #


def test_mixed_currency_comparison_is_refused_with_a_clear_error():
    nok = ScenarioForComparison(scenario_id="a", scenario=_values(name="NOK case"), stale=False)
    eur = ScenarioForComparison(
        scenario_id="b", scenario=_values(name="EUR case", currency="EUR"), stale=False
    )
    with pytest.raises(IncomparableScenariosError) as raised:
        compare_scenarios([nok, eur])
    assert raised.value.reason == "mixed_currency"
    message = raised.value.message
    assert "mixed-currency" in message.lower()
    assert "EUR" in message and "NOK" in message
    assert "no sourced rate" in message.lower()


def test_mixed_period_comparison_is_refused():
    monthly = ScenarioForComparison(scenario_id="a", scenario=_values(), stale=False)
    yearly = ScenarioForComparison(
        scenario_id="b", scenario=_values(name="Yearly case", period="year"), stale=False
    )
    with pytest.raises(IncomparableScenariosError) as raised:
        compare_scenarios([monthly, yearly])
    assert raised.value.reason == "mixed_period"
    assert "mixed-period" in raised.value.message.lower()


def test_same_currency_and_period_scenarios_compare_side_by_side():
    first = ScenarioForComparison(
        scenario_id="a", scenario=_values(name="Optimistic case"), stale=False
    )
    second = ScenarioForComparison(
        scenario_id="b",
        scenario=_values(name="Pessimistic case", hourly_cost="300"),
        stale=True,
    )
    comparison = compare_scenarios([first, second])
    assert comparison.comparable is True
    assert comparison.conversion_applied is False
    assert comparison.currency == "NOK"
    assert comparison.period == "month"
    assert comparison.ranking == ["a", "b"]
    assert [row.rank for row in comparison.rows] == [1, 2]
    assert comparison.rows[1].stale is True
    assert comparison.advisory_only is True


# --------------------------------------------------------------------------- #
# No floating point
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "relative_path",
    ["app/services/sensitivity.py", "app/domain/portfolio.py"],
)
def test_no_floating_point_is_used_in_the_sensitivity_code(relative_path: str):
    source = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, float):
            raise AssertionError(f"float literal in {relative_path} line {node.lineno}")
        if isinstance(node, ast.Call):
            function = node.func
            name = function.id if isinstance(function, ast.Name) else None
            if name in {"float", "round"}:
                raise AssertionError(f"{name}() in {relative_path} line {node.lineno}")
            if name == "Decimal" and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, float):
                    raise AssertionError(
                        f"Decimal built from a float in {relative_path} line {node.lineno}"
                    )


def test_api_module_uses_no_floating_point_for_the_new_routes():
    source = (REPO_ROOT / "app/api/portfolio.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, float):
            raise AssertionError(f"float literal in app/api/portfolio.py line {node.lineno}")
        if isinstance(node, ast.Call):
            function = node.func
            name = function.id if isinstance(function, ast.Name) else None
            if name in {"float", "round"}:
                raise AssertionError(f"{name}() in app/api/portfolio.py line {node.lineno}")


def test_every_reported_number_is_a_decimal():
    envelope = sensitivity_envelope(sensitivity_scenario_from(_values()), threshold=_threshold())
    assert isinstance(envelope.base.net_savings, Decimal)
    for dimension in envelope.dimensions:
        assert isinstance(dimension.base_value, Decimal)
        assert isinstance(dimension.net_savings_range.low, Decimal)
        assert isinstance(dimension.net_savings_range.high, Decimal)
        assert isinstance(dimension.breakeven.value, Decimal)
        assert isinstance(dimension.band.low, Decimal)
        assert isinstance(dimension.band.high, Decimal)


# --------------------------------------------------------------------------- #
# API surface
# --------------------------------------------------------------------------- #


def _create_scenario(client, headers, root, **overrides):
    response = client.post(root + "/value-scenarios", headers=headers, json=_scenario(**overrides))
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_sensitivity_endpoint_is_read_only_tenant_scoped_and_advisory(secure_client):
    client, provision, _ = secure_client
    owner, _, _ = provision("Sensitivity", "owner", "analyst")
    outsider, _, _ = provision("Elsewhere", "outsider", "admin")
    aid = client.post("/api/v1/assessments", headers=owner, json={"title": "Savings"}).json()["id"]
    root = f"/api/v1/assessments/{aid}"
    scenario_id = _create_scenario(client, owner, root)
    uri = f"{root}/value-scenarios/{scenario_id}/sensitivity"

    assert client.get(uri).status_code == 401
    assert client.get(uri, headers=outsider).status_code == 404

    response = client.get(uri, headers=owner)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["advisory_only"] is True
    assert "not measured roi" in body["disclaimer"].lower()
    assert body["base"]["net_savings"] == "15000.00000000000000000000000"
    cases = next(d for d in body["dimensions"] if d["assumption"] == "cases_per_period")
    assert cases["breakeven"]["value"] == "25"
    assert cases["breakeven"]["value_is_exact"] is True
    assert cases["breakeven"]["exact_ratio"] == {"numerator": "25", "denominator": "1"}
    assert cases["band"]["source"] == "modelling_convention_relative_band"

    # Reproducible: the same request returns byte-identical output.
    again = client.get(uri, headers=owner)
    assert again.status_code == 200
    assert again.content == response.content

    # A read-only analysis must not change the assessment or its recommendation.
    detail = client.get(root, headers=owner).json()
    assert detail["analysis"] is None or detail["analysis"]["tasks"] is not None


def test_sensitivity_endpoint_rejects_incomplete_or_inverted_ranges(secure_client):
    client, provision, _ = secure_client
    owner, _, _ = provision("Ranges", "owner", "analyst")
    aid = client.post("/api/v1/assessments", headers=owner, json={"title": "Savings"}).json()["id"]
    root = f"/api/v1/assessments/{aid}"
    scenario_id = _create_scenario(client, owner, root)
    uri = f"{root}/value-scenarios/{scenario_id}/sensitivity"

    assert client.get(uri + "?cases_per_period_low=10", headers=owner).status_code == 422
    inverted = uri + "?cases_per_period_low=90&cases_per_period_high=10"
    assert client.get(inverted, headers=owner).status_code == 422
    assert client.get(uri + "?cases_per_period_low=-5", headers=owner).status_code == 422
    supplied = client.get(uri + "?cases_per_period_low=40&cases_per_period_high=80", headers=owner)
    assert supplied.status_code == 200, supplied.text
    cases = next(d for d in supplied.json()["dimensions"] if d["assumption"] == "cases_per_period")
    assert cases["band"]["source"] == "caller_supplied"
    assert cases["band"]["low"] == "40"
    assert cases["band"]["high"] == "80"


def test_comparison_endpoint_refuses_mixed_currency_and_serves_equal_ones(secure_client):
    client, provision, _ = secure_client
    owner, _, _ = provision("Compare", "owner", "analyst")
    outsider, _, _ = provision("Other", "outsider", "admin")
    aid = client.post("/api/v1/assessments", headers=owner, json={"title": "Savings"}).json()["id"]
    root = f"/api/v1/assessments/{aid}"
    first = _create_scenario(client, owner, root)
    second = _create_scenario(client, owner, root, name="Alternative case", hourly_cost="300")
    third = _create_scenario(client, owner, root, name="Euro case", currency="EUR")

    uri = f"{root}/value-scenarios/comparison"
    assert client.get(uri, headers=owner).status_code == 422
    assert client.get(uri + f"?scenario_ids={first}", headers=outsider).status_code == 404

    refused = client.get(uri + f"?scenario_ids={first}&scenario_ids={third}", headers=owner)
    assert refused.status_code == 400, refused.text
    detail = refused.json()["detail"]
    assert "mixed-currency" in detail.lower()
    assert "EUR" in detail and "NOK" in detail
    assert "no sourced rate" in detail.lower()

    served = client.get(uri + f"?scenario_ids={first}&scenario_ids={second}", headers=owner)
    assert served.status_code == 200, served.text
    body = served.json()
    assert body["comparable"] is True
    assert body["conversion_applied"] is False
    assert body["currency"] == "NOK"
    assert body["period"] == "month"
    assert len(body["rows"]) == 2
    assert body["advisory_only"] is True


def test_sensitivity_output_cannot_change_a_recommendation_category(secure_client):
    from tests.test_phase7_collaboration import _assessment

    client, provision, _ = secure_client
    analyst, _, _ = provision("Advisory", "analyst", "analyst")
    reviewer, _, _ = provision("Advisory", "reviewer", "reviewer")
    aid = _assessment(client, reviewer)
    root = f"/api/v1/assessments/{aid}"
    scenario_id = _create_scenario(client, analyst, root)
    before = client.get(root, headers=analyst).json()
    categories_before = [
        task["recommendation"]["category"] for task in (before["analysis"] or {}).get("tasks", [])
    ]
    response = client.get(f"{root}/value-scenarios/{scenario_id}/sensitivity", headers=analyst)
    assert response.status_code == 200, response.text
    after = client.get(root, headers=analyst).json()
    categories_after = [
        task["recommendation"]["category"] for task in (after["analysis"] or {}).get("tasks", [])
    ]
    assert categories_after == categories_before
    assert after["revision"] == before["revision"]
    assert after["analysis"]["policy_version"] == before["analysis"]["policy_version"]
