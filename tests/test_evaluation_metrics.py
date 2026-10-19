"""Tests for the offline evaluation metrics (P4-03).

These tests check that the *metrics* are computed correctly, including a deliberately
mutated fixture that must be detected as a false positive. They say nothing about real
world accuracy: the corpus is synthetic.
"""

import json
from pathlib import Path

import pytest

from app.domain.enums import RecommendationCategory
from app.evaluation import (
    AI_CATEGORIES,
    ALL_CATEGORIES,
    DEFAULT_DATASET,
    EXPECTED_RULE_IDS,
    evaluate_corpus,
    evaluate_dataset,
    main,
)

DOCS = Path(__file__).resolve().parent.parent / "docs" / "evaluation.md"


@pytest.fixture(scope="module")
def corpus() -> dict:
    return evaluate_corpus()


@pytest.fixture()
def dataset() -> dict:
    return json.loads(DEFAULT_DATASET.read_text(encoding="utf-8"))


def _write(dataset: dict, tmp_path: Path) -> Path:
    path = tmp_path / "mutated_goldens.json"
    path.write_text(json.dumps(dataset), encoding="utf-8")
    return path


def _case(dataset: dict, case_id: str) -> dict:
    return next(case for case in dataset["cases"] if case["id"] == case_id)


# --------------------------------------------------------------------------- dataset shape
def test_dataset_is_large_enough_and_covers_every_category_twice(corpus, dataset):
    assert corpus["total"] >= 14
    counts = {category: 0 for category in ALL_CATEGORIES}
    for case in dataset["cases"]:
        counts[case["expected_category"]] += 1
    for category, count in counts.items():
        assert count >= 2, f"{category} is covered by only {count} case(s)"
    assert set(counts) == set(ALL_CATEGORIES)


def test_every_case_carries_provenance_and_a_known_category(dataset):
    assert dataset["dataset_version"] != "4.0"
    for case in dataset["cases"]:
        assert case["expected_category"] in set(ALL_CATEGORIES)
        assert case.get("provenance"), f"{case['id']} has no provenance note"
        assert case["scenario"]["steps"]


def test_core_scope_keeps_the_legacy_four_fixture_contract():
    core = evaluate_dataset(scope="core")
    assert core["scope"] == "core"
    assert core["total"] == 4
    assert core["failed"] == 0
    assert {case["id"]: case["actual"][0] for case in core["cases"]} == {
        "ai-support": "strong_ai_candidate",
        "exact-pricing": "traditional_software",
        "hybrid-refund": "ai_human_approval",
        "privacy-blocked": "more_discovery",
    }


def test_default_scope_evaluates_the_whole_corpus():
    """The default must not silently under-cover; 'core' has to be requested."""
    default = evaluate_dataset()
    assert default["scope"] == "full"
    assert default["total"] > 4
    assert default["total"] == len(evaluate_corpus()["cases"])


def test_full_corpus_has_no_unexpected_failures(corpus):
    assert corpus["failed"] == 0
    assert corpus["unexpected_failures"] == []
    assert corpus["total"] == len(corpus["cases"])


# --------------------------------------------------------------------------- category metrics
def test_per_category_accuracy_and_confusion_matrix_are_consistent(corpus):
    metrics = corpus["metrics"]
    accuracy = metrics["per_category_accuracy"]
    matrix = metrics["confusion_matrix"]
    assert set(accuracy) == set(ALL_CATEGORIES)
    assert set(matrix) == set(ALL_CATEGORIES)
    for category in ALL_CATEGORIES:
        assert set(matrix[category]) == set(ALL_CATEGORIES)
        assert accuracy[category]["cases"] == sum(matrix[category].values())
        assert accuracy[category]["correct"] == matrix[category][category]
        if accuracy[category]["cases"] == 0:
            assert accuracy[category]["accuracy"] is None  # unmeasured is not perfect
        else:
            assert accuracy[category]["accuracy"] == pytest.approx(
                accuracy[category]["correct"] / accuracy[category]["cases"], abs=1e-4
            )
    assert corpus["category_accuracy_overall"] == pytest.approx(
        sum(stats["correct"] for stats in accuracy.values()) / corpus["total"], abs=1e-4
    )


def test_confusion_matrix_off_diagonal_entries_are_mismatches(corpus):
    matrix = corpus["metrics"]["confusion_matrix"]
    for expected, row in matrix.items():
        for actual, count in row.items():
            if expected == actual:
                continue
            if count:
                offender = next(
                    case
                    for case in corpus["cases"]
                    if case["expected"] == expected and actual in case["actual"]
                )
                assert offender["known_discrepancy"] is True


# --------------------------------------------------------------------------- false positives
def test_false_positive_metric_reports_only_documented_offenders(corpus):
    false_positive = corpus["metrics"]["false_positive_ai_recommendation"]
    assert false_positive["non_ai_cases"] == sum(
        1 for case in corpus["cases"] if case["expected"] not in AI_CATEGORIES
    )
    assert false_positive["false_positives"] == len(false_positive["offending_case_ids"])
    assert false_positive["rate"] == pytest.approx(
        false_positive["false_positives"] / false_positive["non_ai_cases"], abs=1e-4
    )
    # Every offender must be a documented known discrepancy; an undocumented one is a
    # regression that must fail the run.
    assert false_positive["unexpected_false_positives"] == []
    for offender in false_positive["offenders"]:
        assert offender["known_discrepancy"] is True
        assert offender["expected"] not in AI_CATEGORIES
        assert any(actual in AI_CATEGORIES for actual in offender["actual"])


def test_mutated_expectation_is_detected_as_a_false_positive(dataset, tmp_path):
    baseline = evaluate_corpus()
    baseline_count = baseline["metrics"]["false_positive_ai_recommendation"]["false_positives"]

    # ai-support is genuinely an AI category; pretending it should be more_discovery turns
    # the same actual result into a false positive AI recommendation.
    _case(dataset, "ai-support")["expected_category"] = "more_discovery"
    result = evaluate_corpus(_write(dataset, tmp_path))

    false_positive = result["metrics"]["false_positive_ai_recommendation"]
    assert false_positive["false_positives"] == baseline_count + 1
    assert "ai-support" in false_positive["offending_case_ids"]
    assert "ai-support" in false_positive["unexpected_false_positives"]
    assert false_positive["rate"] > baseline["metrics"]["false_positive_ai_recommendation"]["rate"]
    # The mismatch is also a failed golden expectation, so the run must fail.
    assert result["failed"] == 1
    assert result["unexpected_failures"] == ["ai-support"]
    assert main(["--dataset", str(_write(dataset, tmp_path))]) == 1


def test_false_positive_guard_cases_do_not_leak_into_ai_categories(corpus):
    false_positive = corpus["metrics"]["false_positive_ai_recommendation"]
    assert len(false_positive["guard_cases"]) >= 4
    for case_id in false_positive["guard_cases"]:
        case = next(case for case in corpus["cases"] if case["id"] == case_id)
        assert not any(actual in AI_CATEGORIES for actual in case["actual"])


# --------------------------------------------------------------------------- unknown data
def test_unknown_data_handling_rate_is_complete(corpus):
    unknown_data = corpus["metrics"]["unknown_data_handling"]
    assert unknown_data["cases_requiring_unknown"] > 0
    assert unknown_data["rate"] == pytest.approx(1.0)
    assert unknown_data["violations"] == []
    assert unknown_data["roi"]["violations"] == []
    assert unknown_data["roi"]["fabricated_roi_cases"] == []
    assert unknown_data["feasibility"]["violations"] == []
    assert unknown_data["roi"]["rate"] == pytest.approx(1.0)
    assert unknown_data["feasibility"]["rate"] == pytest.approx(1.0)


def test_partial_baseline_keeps_roi_unavailable(dataset, tmp_path):
    case = _case(dataset, "ai-support")
    case["scenario"]["value_inputs"] = {"cases_per_period": "100", "loaded_hourly_cost": "50"}
    result = evaluate_corpus(_write(dataset, tmp_path))
    unknown_data = result["metrics"]["unknown_data_handling"]
    assert unknown_data["violations"] == []
    evaluated = next(c for c in result["cases"] if c["id"] == "ai-support")
    assert evaluated["roi"]["available"] is False
    assert evaluated["roi"]["status"] == "insufficient_evidence"


def test_fabricated_roi_expectation_is_detected(dataset, tmp_path):
    # value-below-threshold has a complete, evidenced baseline, so ROI is legitimately
    # available. Declaring the opposite must be reported as a violation.
    _case(dataset, "value-below-threshold")["expect_roi_missing"] = True
    result = evaluate_corpus(_write(dataset, tmp_path))
    unknown_data = result["metrics"]["unknown_data_handling"]
    assert "value-below-threshold" in unknown_data["expectation_violations"]
    assert result["failed"] == 1


# --------------------------------------------------------------------------- rule metrics
def test_rule_firing_rates_cover_the_whole_rule_vocabulary(corpus):
    rules = corpus["metrics"]["rule_firing_rates"]
    assert set(rules) == EXPECTED_RULE_IDS
    assert corpus["metrics"]["unexpected_rule_ids"] == []
    for _rule, stats in rules.items():
        assert stats["cases"] == corpus["total"]
        assert 0 <= stats["fired"] <= corpus["total"]
        if stats["fired"]:
            assert stats["rate"] == pytest.approx(stats["fired"] / corpus["total"], abs=1e-4)
    # A rule that silently stops firing becomes visible as a rate of 0.0.
    assert corpus["metrics"]["rules_never_fired"] == []
    assert all(stats["fired"] > 0 for stats in rules.values())


def test_expected_rule_ids_must_actually_fire(dataset, tmp_path):
    _case(dataset, "ai-support")["expected_rule_ids"] = ["MATH-001"]
    result = evaluate_corpus(_write(dataset, tmp_path))
    checks = result["metrics"]["expected_rule_checks"]
    assert checks["violations"], "an expected rule that never fired must be reported"
    assert "ai-support" in checks["violations"][0]
    assert result["failed"] == 1


# --------------------------------------------------------------------------- advisory only
def test_every_case_stays_advisory_only(corpus):
    advisory = corpus["metrics"]["advisory_only"]
    assert advisory["cases"] == corpus["total"]
    assert advisory["compliant"] == corpus["total"]
    assert advisory["violations"] == []
    assert advisory["approval_side_effect_violations"] == []
    assert advisory["analysis_path_is_pure"] is True
    assert advisory["forbidden_modules_reachable"] == []
    for case in corpus["cases"]:
        assert case["checks"]["advisory_only"] is True
        assert case["checks"]["no_approval_side_effect"] is True


# --------------------------------------------------------------------------- known discrepancy
def test_no_known_discrepancies_remain_open(corpus):
    """Every documented engine finding must stay visible until it is genuinely fixed."""
    known = corpus["known_discrepancies"]
    assert known == [], f"open engine discrepancies: {known}"
    assert corpus["failed"] == 0


def test_resolved_lookup_discrepancy_now_strictly_enforced(corpus):
    """The pure-lookup case must fail hard if the language-signal gate regresses."""
    case = next(case for case in corpus["cases"] if case["id"] == "authoritative-lookup-only")
    assert case["expected"] == "more_discovery"
    assert case["actual"] == ["more_discovery"]
    assert case["known_discrepancy"] is False, "the exemption was removed once fixed"
    assert case["history_note"], "the resolution must stay recorded in the dataset"
    assert case["id"] in DOCS.read_text(encoding="utf-8")


def test_strong_ai_requires_an_observed_language_signal():
    """A high suitability score alone must never make a task an AI candidate.

    Policy rules-v2 requires a language-oriented signal in addition to the score
    threshold. Without it, a plain system-of-record integration scored 75 and was
    recommended as a strong AI candidate.
    """
    from app.domain.schemas import AssessmentPreviewRequest
    from app.services.assessment import analyze

    scenario = {
        "title": "Lookup-only task",
        "steps": [
            {
                "id": "s1",
                "name": "Synthetic workflow step",
                "order": 1,
                "tasks": [
                    {
                        "id": "t1",
                        "title": "Retrieve the authoritative customer record",
                        "description": "Synthetic regression fixture.",
                        "signals": {
                            "requires_authoritative_lookup": True,
                            "data_available": True,
                            "output_verifiable": True,
                            "api_available": True,
                        },
                    }
                ],
            }
        ],
    }
    response = analyze(AssessmentPreviewRequest.model_validate(scenario))
    assert response.tasks
    for task in response.tasks:
        assert task.recommendation.category.value != "strong_ai_candidate"
        assert task.recommendation.category.value == "more_discovery"


# --------------------------------------------------------------------------- contradiction path
def test_contradictory_answers_are_flagged_for_review():
    from app.domain.enums import FindingKind
    from app.domain.schemas import Answer
    from app.services.discovery import summarize_discovery

    result = summarize_discovery(
        [
            Answer(key="human_review", value="yes", kind=FindingKind.FACT, source="intake"),
            Answer(key="human_review", value="no", kind=FindingKind.FACT, source="email"),
        ]
    )
    assert any(finding.kind == FindingKind.CONTRADICTION for finding in result.findings)
    assert result.next_question is not None


# --------------------------------------------------------------------------- runner interface
def test_runner_is_callable_programmatically_and_exits_zero_on_the_shipped_dataset():
    result = evaluate_corpus()
    assert result["failed"] == 0
    assert main([]) == 0
    assert main(["--scope", "core"]) == 0
    assert main(["--json"]) == 0


def test_recommended_categories_cover_the_six_policy_families():
    assert set(ALL_CATEGORIES) == {category.value for category in RecommendationCategory}
    assert AI_CATEGORIES == {
        RecommendationCategory.STRONG_AI_CANDIDATE.value,
        RecommendationCategory.HYBRID.value,
        RecommendationCategory.AI_HUMAN_APPROVAL.value,
    }
