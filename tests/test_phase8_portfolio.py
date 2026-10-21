"""Phase 8 offline checks: financial arithmetic, provenance, tenant scoping and PoC workflow."""

from __future__ import annotations

from decimal import Decimal

from app.domain.portfolio import ValueScenarioIn
from app.services.portfolio import priority_result, value_projection
from tests.test_phase7_collaboration import _assessment
from tests.test_security import secure_client  # noqa: F401 - shared pytest fixture re-used here


def _scenario(**overrides):
    payload = {
        "name": "Optimistic manual case",
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


def test_financial_projection_has_explicit_units_and_negative_case():
    vals = ValueScenarioIn.model_validate(_scenario())
    gross, net, payback = value_projection(vals)
    assert gross == Decimal("20000")
    assert net == Decimal("15000")
    assert payback == Decimal("1")
    negative = ValueScenarioIn.model_validate(_scenario(target_minutes="40", recurring_cost="2000"))
    gross, net, payback = value_projection(negative)
    assert gross == Decimal("-10000")
    assert net == Decimal("-12000")
    assert payback is None
    incomplete = priority_result(
        alignment=5,
        urgency=5,
        effort=1,
        verified_sources=0,
        analysis_fresh=True,
    )
    assert incomplete["status"] == "unranked"
    assert incomplete["priority_score"] is None
    assert (
        priority_result(
            alignment=5,
            urgency=5,
            effort=1,
            verified_sources=1,
            analysis_fresh=True,
        )["priority_score"]
        == 100
    )


def test_evidence_verification_scoped_and_independent(secure_client):
    client, provision, _ = secure_client
    owner, _, _ = provision("A", "analyst-a", "analyst")
    reviewer, _, _ = provision("A", "reviewer-a", "reviewer")
    other, _, _ = provision("B", "other-b", "admin")
    aid = _assessment(client, reviewer)
    root = f"/api/v1/assessments/{aid}"
    posted = client.post(
        root + "/evidence",
        headers=owner,
        json={
            "kind": "baseline",
            "description": "Synthetic baseline observation",
            "source_reference": "Pilot worksheet 2026-01",
        },
    )
    assert posted.status_code == 201, posted.text
    evid = posted.json()
    assert evid["verified_by"] is None
    assert client.get(root + "/evidence", headers=other).status_code == 404
    assert (
        client.post(root + "/evidence/" + evid["id"] + "/verify", headers=other).status_code == 404
    )
    assert (
        client.post(root + "/evidence/" + evid["id"] + "/verify", headers=owner).status_code == 403
    )
    verified = client.post(root + "/evidence/" + evid["id"] + "/verify", headers=reviewer)
    assert verified.status_code == 200, verified.text
    assert verified.json()["verified_by"] == "reviewer-a"
    assert (
        client.post(root + "/evidence/" + evid["id"] + "/verify", headers=reviewer).status_code
        == 409
    )
    assert client.get("/api/v1/portfolio/opportunities", headers=other).json()["items"] == []


def test_rank_stays_unranked_until_real_assessment_and_verified_evidence(secure_client):
    client, provision, _ = secure_client
    analyst, _, _ = provision("X", "analyst", "analyst")
    reviewer, _, _ = provision("X", "reviewer", "reviewer")
    aid = _assessment(client, reviewer)
    root = f"/api/v1/assessments/{aid}"
    profile = client.put(
        root + "/portfolio-profile",
        headers=analyst,
        json={
            "strategic_alignment": 4,
            "urgency": 5,
            "delivery_effort": 2,
        },
    )
    assert profile.status_code == 200, profile.text
    before = client.get("/api/v1/portfolio/opportunities", headers=analyst).json()
    assert before["items"][0]["priority"]["status"] == "unranked"
    entry = client.post(
        root + "/evidence",
        headers=analyst,
        json={
            "kind": "technical",
            "description": "Test harness access confirmed",
            "source_reference": "Synthetic mock fixture",
        },
    ).json()
    verify = client.post(root + "/evidence/" + entry["id"] + "/verify", headers=reviewer)
    assert verify.status_code == 200
    ranked = client.get("/api/v1/portfolio/opportunities", headers=analyst).json()
    assert ranked["items"][0]["priority"]["status"] == "provisional"
    assert ranked["items"][0]["priority"]["priority_score"] == 32 + 35 + 20
    assert ranked["items"][0]["verified_evidence_count"] == 1
    assert (
        client.patch(root, json={"title": "Requirements changed"}, headers=analyst).status_code
        == 200
    )
    stale = client.get("/api/v1/portfolio/opportunities", headers=analyst).json()
    assert stale["items"][0]["priority"]["status"] == "unranked"
    assert client.get(root + "/evidence", headers=analyst).json()[0]["stale"] is True


def test_a_scenario_without_a_horizon_has_no_financial_model(secure_client):
    """A scenario without a horizon reports that it has no net present value."""
    client, provision, _ = secure_client
    owner, _, _ = provision("Money", "owner", "analyst")
    aid = client.post("/api/v1/assessments", headers=owner, json={"title": "Savings"}).json()["id"]
    root = f"/api/v1/assessments/{aid}"
    created = client.post(root + "/value-scenarios", headers=owner, json=_scenario())
    assert created.status_code == 201, created.text
    model = created.json()["financial_model"]
    assert model["status"] == "unavailable"
    assert model["net_present_value"] is None
    assert model["reason"]
    assert model["advisory_only"] is True


def test_a_scenario_with_a_horizon_and_a_sourced_rate_gets_a_financial_model(secure_client):
    """The financial model is computed from the scenario's own inputs."""
    client, provision, _ = secure_client
    owner, _, _ = provision("Money", "owner", "analyst")
    aid = client.post("/api/v1/assessments", headers=owner, json={"title": "Savings"}).json()["id"]
    root = f"/api/v1/assessments/{aid}"
    created = client.post(
        root + "/value-scenarios",
        headers=owner,
        json=_scenario(
            horizon_periods=12,
            discount_rate_annual="0.05",
            discount_rate_source="The central bank's published rate for 2026",
        ),
    )
    assert created.status_code == 201, created.text
    model = created.json()["financial_model"]
    assert model["status"] == "available"
    assert model["net_present_value"] is not None
    assert model["internal_rate_of_return"] is not None
    assert model["horizon_periods"] == 12
    assert model["method_version"] == "npv-v1"
    assert model["disclaimer"]


def test_a_discount_rate_without_a_source_is_refused_by_the_api(secure_client):
    """The API refuses a rate that does not say where it came from."""
    client, provision, _ = secure_client
    owner, _, _ = provision("Money", "owner", "analyst")
    aid = client.post("/api/v1/assessments", headers=owner, json={"title": "Savings"}).json()["id"]
    root = f"/api/v1/assessments/{aid}"
    refused = client.post(
        root + "/value-scenarios",
        headers=owner,
        json=_scenario(horizon_periods=12, discount_rate_annual="0.05"),
    )
    assert refused.status_code == 422
    assert "source" in refused.text.lower()


def test_explicit_illustrative_scenarios_and_staleness(secure_client):
    client, provision, _ = secure_client
    owner, _, _ = provision("Money", "owner", "analyst")
    outsider, _, _ = provision("Other", "visitor", "admin")
    aid = client.post("/api/v1/assessments", headers=owner, json={"title": "Savings"}).json()["id"]
    root = f"/api/v1/assessments/{aid}"
    assert (
        client.post(root + "/value-scenarios", headers=outsider, json=_scenario()).status_code
        == 404
    )
    assert (
        client.post(
            root + "/value-scenarios", headers=owner, json=_scenario(currency="EURR")
        ).status_code
        == 422
    )
    created = client.post(root + "/value-scenarios", headers=owner, json=_scenario())
    assert created.status_code == 201, created.text
    result = created.json()
    assert result["advisory_only"] is True
    assert Decimal(result["gross_savings"]) == Decimal("20000")
    assert Decimal(result["net_savings"]) == Decimal("15000")
    assert Decimal(result["payback_periods"]) == Decimal("1")
    assert result["stale"] is False
    assert client.patch(root, headers=owner, json={"title": "New scope"}).status_code == 200
    assert client.get(root + "/value-scenarios", headers=owner).json()[0]["stale"] is True


def test_poc_tracking_is_human_reviewed_and_revision_bound(secure_client):
    client, provision, _ = secure_client
    analyst, _, _ = provision("PoC", "owner", "analyst")
    reviewer, _, _ = provision("PoC", "reviewer", "reviewer")
    outsider, _, _ = provision("External", "external", "admin")
    aid = _assessment(client, reviewer)
    root = f"/api/v1/assessments/{aid}"
    create = client.post(
        root + "/pocs",
        headers=analyst,
        json={
            "kpi_name": "Review errors",
            "unit": "errors per 100",
            "direction": "lower",
            "baseline": "10",
            "target": "3",
            "success_criteria": "Measure sample errors before considering rollout",
        },
    )
    assert create.status_code == 201, create.text
    poc_id = create.json()["id"]
    uri = root + "/pocs/" + poc_id
    assert client.post(uri + "/start", headers=analyst).status_code == 403
    assert (
        client.post(
            uri + "/observations",
            headers=reviewer,
            json={
                "actual": "2",
                "source_reference": "Synthetic evaluation set",
            },
        ).status_code
        == 409
    )
    assert client.post(uri + "/start", headers=outsider).status_code == 404
    assert client.post(uri + "/start", headers=reviewer).status_code == 200
    measured = client.post(
        uri + "/observations",
        headers=reviewer,
        json={
            "actual": "2",
            "source_reference": "Synthetic evaluation set",
        },
    )
    assert measured.status_code == 201, measured.text
    assert measured.json()["target_met"] is True
    assert client.get(root + "/pocs", headers=analyst).json()[0]["status"] == "measured"
    assert (
        client.patch(root, headers=analyst, json={"title": "Revised hypothesis"}).status_code == 200
    )
    assert client.get(root + "/pocs", headers=analyst).json()[0]["stale"] is True
    assert (
        client.post(
            uri + "/observations",
            headers=reviewer,
            json={
                "actual": "1",
                "source_reference": "Stale synthetic result",
            },
        ).status_code
        == 409
    )


def test_invalid_auth_and_missing_assessment_fail_closed(secure_client):
    client, provision, _ = secure_client
    owner, _, _ = provision("Org", "owner", "analyst")
    for path in (
        "/api/v1/portfolio/opportunities",
        "/api/v1/assessments/fake/pocs",
        "/api/v1/assessments/fake/value-scenarios",
        "/api/v1/assessments/fake/portfolio-profile",
    ):
        assert client.get(path).status_code == 401
    assert client.get("/api/v1/assessments/fake/evidence", headers=owner).status_code == 404


def test_policy_gated_opportunity_is_not_ranked_by_business_weights():
    result = priority_result(
        alignment=5,
        urgency=5,
        effort=1,
        verified_sources=3,
        analysis_fresh=True,
        policy_gated=True,
    )
    assert result["priority_score"] is None
    assert "deterministic_policy_requires_discovery_or_human_decision" in result["missing"]


def test_portfolio_rolls_up_only_current_poc_outcomes(secure_client):
    client, provision, _ = secure_client
    admin, _, _ = provision("Outcomes", "admin", "admin")
    aid = _assessment(client, admin)
    root = f"/api/v1/assessments/{aid}"
    plan = client.post(
        root + "/pocs",
        headers=admin,
        json={
            "kpi_name": "Cases resolved",
            "unit": "cases",
            "direction": "higher",
            "baseline": "5",
            "target": "10",
            "success_criteria": "Demonstrate at least ten resolved synthetic cases",
        },
    )
    assert plan.status_code == 201, plan.text
    poc = plan.json()["id"]
    assert client.post(root + f"/pocs/{poc}/start", headers=admin).status_code == 200
    assert (
        client.post(
            root + f"/pocs/{poc}/observations",
            headers=admin,
            json={"actual": "12", "source_reference": "Synthetic sample"},
        ).status_code
        == 201
    )
    entries = client.get("/api/v1/portfolio/opportunities", headers=admin).json()["items"]
    assert entries[0]["poc_outcomes"]["measured"] == 1
    assert entries[0]["poc_outcomes"]["targets_met"] == 1
    assert entries[0]["poc_outcomes"]["self_reported"] is True
    assert client.patch(root, headers=admin, json={"title": "Change"}).status_code == 200
    entries = client.get("/api/v1/portfolio/opportunities", headers=admin).json()["items"]
    assert entries[0]["poc_outcomes"]["measured"] == 0


def test_poc_goals_must_agree_with_improvement_direction():
    import pytest
    from pydantic import ValidationError

    from app.domain.portfolio import PocPlanIn

    base = {
        "kpi_name": "Case duration",
        "unit": "minutes",
        "direction": "lower",
        "baseline": "10",
        "target": "3",
        "success_criteria": "Validate time savings on a synthetic test set",
    }
    assert PocPlanIn.model_validate(base).target == Decimal("3")
    with pytest.raises(ValidationError):
        PocPlanIn.model_validate({**base, "target": "12"})
    with pytest.raises(ValidationError):
        PocPlanIn.model_validate({**base, "direction": "higher"})


def test_synthetic_portfolio_fixture_runner():
    from app.portfolio_evaluation import run

    assert run() == 0
