"""Offline evaluation of policy precedence, data semantics and recommendations."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.domain.enums import RecommendationCategory as Category
from app.domain.schemas import AssessmentPreviewRequest, RiskInput, TaskSignals, ValueInputs
from app.services.assessment import analyze
from app.services.risk import band, evaluate_risks
from app.services.scoring import score_ai_suitability
from app.services.value import calculate_value
from tests.fixtures.cases import AI_SUPPORT, EXACT_PRICING, HYBRID_REFUND


def case(payload: dict):
    return analyze(AssessmentPreviewRequest.model_validate(payload)).tasks[0]


def test_ai_support_is_bounded_candidate():
    result = case(AI_SUPPORT)
    assert result.recommendation.category == Category.STRONG_AI_CANDIDATE
    assert result.ai_suitability.value is not None
    assert result.ai_suitability.value >= 65
    assert result.ai_suitability.coverage < 1


def test_pricing_always_prefers_code():
    result = case(EXACT_PRICING)
    assert result.recommendation.category == Category.TRADITIONAL_SOFTWARE
    assert {hit.id for hit in result.rule_hits} >= {"DET-001", "MATH-001"}


def test_refund_requires_explicit_approval():
    result = case(HYBRID_REFUND)
    assert result.recommendation.category == Category.AI_HUMAN_APPROVAL
    assert {hit.id for hit in result.rule_hits} >= {"ACT-001", "ACT-002", "DATA-001"}
    assert result.risks[0].score == 16
    assert "Human approval before consequential actions" in result.risks[0].required_controls


def test_unknown_signals_are_not_zero_confidence():
    score = score_ai_suitability(TaskSignals())
    assert score.value is None
    assert score.status == "not_assessable"
    assert score.coverage == 0


def test_no_fabricated_roi():
    value = calculate_value(ValueInputs(cases_per_period=Decimal(500)))
    assert value.potential_gross_labor_savings is None
    assert value.potential_net_savings is None
    assert value.status == "insufficient_evidence"


def test_decimal_labor_value_and_operating_cost():
    value = calculate_value(
        ValueInputs(
            cases_per_period=Decimal(500),
            baseline_minutes_per_case=Decimal(8),
            target_minutes_per_case=Decimal(3),
            loaded_hourly_cost=Decimal(60),
            proposed_operating_cost_per_period=Decimal(100),
        )
    )
    assert value.potential_gross_labor_savings == Decimal(2500)
    assert value.potential_net_savings == Decimal(2400)
    assert value.status == "illustrative_only"


def test_data_permission_blocks_external_provider_use():
    payload = {
        **AI_SUPPORT,
        "steps": [
            {
                "id": "x",
                "name": "sensitive ticket",
                "order": 0,
                "tasks": [
                    {
                        "id": "t",
                        "title": "extract",
                        "signals": {
                            "unstructured_content": True,
                            "sensitive_data": True,
                            "provider_data_permitted": False,
                            "output_verifiable": True,
                        },
                    }
                ],
            }
        ],
    }
    result = case(payload)
    assert result.recommendation.category == Category.MORE_DISCOVERY
    assert any(x.id == "PRIV-001" for x in result.rule_hits)


def test_human_only_cannot_be_overridden_by_high_suitability():
    payload = {
        **AI_SUPPORT,
        "steps": [
            {
                "id": "x",
                "name": "Legal",
                "order": 0,
                "tasks": [
                    {
                        "id": "t",
                        "title": "commit",
                        "actions": ["legal_commitment"],
                        "signals": {
                            "unstructured_content": True,
                            "classification_or_extraction": True,
                            "output_verifiable": True,
                        },
                    }
                ],
            }
        ],
    }
    result = case(payload)
    assert result.recommendation.category == Category.HUMAN_LED
    assert any(x.id == "HUM-001" for x in result.rule_hits)


def test_risk_bands_and_constraints():
    assert [band(x).value for x in (1, 4, 5, 9, 10, 16, 17, 25)] == [
        "low",
        "low",
        "medium",
        "medium",
        "high",
        "high",
        "critical",
        "critical",
    ]
    assert (
        evaluate_risks(
            [RiskInput(category="operational", description="unsafe retry", impact=5, likelihood=5)]
        )[0].score
        == 25
    )
    with pytest.raises(ValidationError):
        RiskInput(category="operational", description="bad", impact=6, likelihood=1)


def test_zero_error_unverifiable_is_human_led():
    payload = {
        **EXACT_PRICING,
        "steps": [
            {
                "id": "x",
                "name": "bad",
                "order": 0,
                "tasks": [
                    {
                        "id": "t",
                        "title": "unsafe",
                        "signals": {"zero_error_tolerance": True, "output_verifiable": False},
                    }
                ],
            }
        ],
    }
    result = case(payload)
    assert result.recommendation.category == Category.HUMAN_LED
    assert any(x.id == "SAFE-001" for x in result.rule_hits)


def test_duplicate_task_ids_rejected():
    payload = {**AI_SUPPORT, "steps": AI_SUPPORT["steps"] * 2}
    with pytest.raises(ValidationError, match="step IDs must be unique"):
        AssessmentPreviewRequest.model_validate(payload)


def test_high_consequence_without_reviewer_not_approved():
    payload = {
        **HYBRID_REFUND,
        "steps": [
            {
                "id": "s",
                "name": "Refund",
                "order": 0,
                "tasks": [
                    {
                        "id": "t",
                        "title": "Refund",
                        "actions": ["issue_refund"],
                        "signals": {
                            "high_consequence": True,
                            "human_review_available": False,
                            "data_available": True,
                        },
                    }
                ],
            }
        ],
    }
    result = case(payload)
    assert result.recommendation.category == Category.HUMAN_LED


def test_approval_with_unknown_reviewer_requires_discovery():
    payload = {
        **HYBRID_REFUND,
        "steps": [
            {
                "id": "s",
                "name": "Refund",
                "order": 0,
                "tasks": [
                    {
                        "id": "t",
                        "title": "Refund",
                        "actions": ["issue_refund"],
                        "signals": {"data_available": True},
                    }
                ],
            }
        ],
    }
    assert case(payload).recommendation.category == Category.MORE_DISCOVERY


def test_technical_scores_show_partial_coverage():
    result = case(EXACT_PRICING)
    assert result.technical_feasibility.coverage < 1
    assert result.technical_feasibility.status == "partial"


def test_hybrid_interpretation_with_authoritative_lookup():
    payload = {
        "title": "Ticket lookup",
        "steps": [
            {
                "id": "s",
                "name": "Lookup",
                "order": 0,
                "tasks": [
                    {
                        "id": "t",
                        "title": "Extract request and retrieve order",
                        "signals": {
                            "unstructured_content": True,
                            "requires_authoritative_lookup": True,
                            "data_available": True,
                            "output_verifiable": True,
                        },
                    }
                ],
            }
        ],
    }
    result = case(payload)
    assert result.recommendation.category == Category.HYBRID
    assert any(hit.id == "DATA-001" for hit in result.rule_hits)


def test_more_discovery_when_no_signals_observed():
    payload = {
        "title": "Unknown",
        "steps": [
            {"id": "s", "name": "Step", "order": 0, "tasks": [{"id": "t", "title": "Unknown task"}]}
        ],
    }
    result = case(payload)
    assert result.recommendation.category == Category.MORE_DISCOVERY
    assert result.ai_suitability.value is None


def test_low_value_threshold_requires_measured_net_value():
    payload = {
        **AI_SUPPORT,
        "value_inputs": {
            "cases_per_period": 100,
            "baseline_minutes_per_case": 4,
            "target_minutes_per_case": 3,
            "loaded_hourly_cost": 30,
            "proposed_operating_cost_per_period": 100,
        },
        "minimum_net_value_per_period": 200,
    }
    result = case(payload)
    assert result.recommendation.category == Category.HUMAN_LED
    assert any(x.id == "VALUE-001" for x in result.rule_hits)


def test_threshold_does_not_fire_on_missing_roi():
    payload = {
        **AI_SUPPORT,
        "value_inputs": {"cases_per_period": 100},
        "minimum_net_value_per_period": 1000,
    }
    assert not any(x.id == "VALUE-001" for x in case(payload).rule_hits)


def test_critical_risk_with_no_reviewer_not_approved():
    payload = {
        "title": "Danger",
        "steps": [
            {
                "id": "s",
                "name": "Step",
                "order": 0,
                "tasks": [
                    {
                        "id": "t",
                        "title": "Analyze",
                        "signals": {
                            "unstructured_content": True,
                            "data_available": True,
                            "human_review_available": False,
                        },
                        "risks": [
                            {
                                "category": "safety",
                                "description": "Irrecoverable failure",
                                "impact": 5,
                                "likelihood": 5,
                            }
                        ],
                    }
                ],
            }
        ],
    }
    assert case(payload).recommendation.category == Category.HUMAN_LED


def test_multiple_tasks_analyzed_independently():
    payload = {
        "title": "Mixed workflow",
        "steps": [
            {
                "id": "x",
                "name": "Semantic",
                "order": 0,
                "tasks": [AI_SUPPORT["steps"][0]["tasks"][0]],
            },
            {
                "id": "y",
                "name": "Compute",
                "order": 1,
                "tasks": [{**EXACT_PRICING["steps"][0]["tasks"][0], "id": "t2"}],
            },
        ],
    }
    results = analyze(AssessmentPreviewRequest.model_validate(payload)).tasks
    assert [r.recommendation.category for r in results] == [
        Category.STRONG_AI_CANDIDATE,
        Category.TRADITIONAL_SOFTWARE,
    ]
