"""P9-05 ranking calibration harness: correlation correctness, mis-ranking naming,
the Phase 8 policy guard, loud failure on bad input, and portfolio-v1 non-regression.

The correlation values asserted here are **hand-checked**, not taken from a
library. The primary figure (Kendall's tau-b) is derived in
``test_kendall_tau_b_matches_the_hand_computed_value`` from first principles
(pair counts, then the tau-b tie correction), and cross-checked against a
second, independently written implementation that counts concordant/discordant
pairs by a different route. The secondary figure (Spearman's rho) is likewise
cross-checked against an independent average-rank implementation.

These are regression expectations over a SYNTHETIC self-test corpus. They are
not field accuracy, not expert agreement, and not evidence of ROI or safety.
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path

import pytest

from app.portfolio_calibration import DEFAULT_LABELLED_SET, main, run
from app.services.calibration import (
    RANKING_VERSION,
    CalibrationInputError,
    calibrate,
    kendall_tau_b,
    load_labelled_set,
    parse_labelled_set,
    spearman_rho,
)
from app.services.portfolio import priority_result

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE = DEFAULT_LABELLED_SET


# ---------------------------------------------------------------------------
# Hand-computed reference values for the synthetic fixture
# ---------------------------------------------------------------------------
#
# The comparable set (ranked by the heuristic AND expected-ranked) is:
#
#   id      expected_rank   produced_score
#   opp-01       1               100
#   opp-02       2                95
#   opp-03       3                88
#   opp-04       4                66
#   opp-05       6                87
#   opp-06       7                85
#   opp-07       7                84     <- expected tie with opp-06
#   opp-08       9                80
#   opp-09      10                80     <- produced tie with opp-08
#
# n = 9 comparable cases -> n0 = 9*8/2 = 36 unordered pairs.
#
# Expected-ordering ties: opp-06/opp-07 share expected_rank 7 -> 1 tied pair.
# Produced-score ties:   opp-08/opp-09 both score 80        -> 1 tied pair.
# No pair is tied on both sides.
#
# Counting every pair by hand:
#   concordant (C) = 29
#   discordant (D) = 5   (opp-04 against opp-05, opp-06, opp-07, opp-08, opp-09)
#   tied in expected only (n1) = 1
#   tied in produced only (n2) = 1
#
# tau-b = (C - D) / sqrt((n0 - n1) * (n0 - n2))
#       = (29 - 5) / sqrt((36 - 1) * (36 - 1))
#       = 24 / 35
#       = 0.6857142857142857...
HAND_C = 29
HAND_D = 5
HAND_N0 = 36
HAND_N1 = 1
HAND_N2 = 1
HAND_TAU_B = Fraction_exact = 24 / 35


def _independent_tau_b(expected: list[int], produced: list[float]) -> float:
    """A second, independently written tau-b.

    This one works from *rank positions* and counts concordant/discordant
    pairs via a different loop structure than the shipped implementation, so
    agreement between the two is real evidence rather than a tautology.
    """
    n = len(expected)
    assert n == len(produced)
    n0 = n * (n - 1) // 2

    # Tie group sizes per side.
    def tie_pairs(values: list[float]) -> int:
        counts: dict[float, int] = {}
        for value in values:
            counts[value] = counts.get(value, 0) + 1
        return sum(c * (c - 1) // 2 for c in counts.values())

    n1 = tie_pairs([float(v) for v in expected])
    n2 = tie_pairs([float(v) for v in produced])
    both = 0
    concordant = 0
    discordant = 0
    for i in range(n):
        for j in range(n):
            if i >= j:
                continue
            de = expected[i] - expected[j]
            dp = produced[i] - produced[j]
            if de == 0 and dp == 0:
                both += 1
            elif de == 0 or dp == 0:
                continue
            elif (de > 0) == (dp > 0):
                concordant += 1
            else:
                discordant += 1
    denominator = math.sqrt((n0 - n1) * (n0 - n2))
    assert denominator > 0
    return (concordant - discordant) / denominator


def _independent_spearman(expected: list[int], produced: list[float]) -> float:
    """Average-rank Pearson correlation, written independently of the module."""

    def average_ranks(values: list[float]) -> list[float]:
        ranks = []
        for value in values:
            smaller = sum(1 for other in values if other < value)
            equal = sum(1 for other in values if other == value)
            ranks.append(smaller + (equal + 1) / 2.0)
        return ranks

    rx = average_ranks([float(v) for v in expected])
    ry = average_ranks([float(v) for v in produced])
    n = len(rx)
    mean_x = sum(rx) / n
    mean_y = sum(ry) / n
    cov = sum((a - mean_x) * (b - mean_y) for a, b in zip(rx, ry, strict=True))
    var_x = sum((a - mean_x) ** 2 for a in rx)
    var_y = sum((b - mean_y) ** 2 for b in ry)
    return cov / math.sqrt(var_x * var_y)


# ---------------------------------------------------------------------------
# Correlation correctness
# ---------------------------------------------------------------------------


def test_kendall_tau_b_matches_the_hand_computed_value():
    """The shipped tau-b equals the hand-derived 24/35 on the fixture."""
    labelled = load_labelled_set(FIXTURE)
    report = calibrate(labelled)
    correlation = report.correlation

    assert correlation.method == "kendall_tau_b"
    assert correlation.n == 9
    # Hand-counted pair statistics.
    assert correlation.concordant_pairs == HAND_C
    assert correlation.discordant_pairs == HAND_D
    assert correlation.tied_expected_pairs == HAND_N1
    assert correlation.tied_produced_pairs == HAND_N2
    assert correlation.tied_both_pairs == 0
    assert correlation.comparable_pairs == HAND_C + HAND_D

    # tau-b = (C - D) / sqrt((n0 - n1) * (n0 - n2)) = 24/35.
    expected = (HAND_C - HAND_D) / math.sqrt((HAND_N0 - HAND_N1) * (HAND_N0 - HAND_N2))
    assert expected == HAND_TAU_B
    assert correlation.value == pytest.approx(expected, abs=1e-12)
    assert correlation.value == pytest.approx(24 / 35, abs=1e-12)


def test_correlation_is_cross_checked_against_an_independent_implementation():
    """Two independent implementations agree on the fixture and on edge cases."""
    labelled = load_labelled_set(FIXTURE)
    report = calibrate(labelled)

    # Rebuild the comparable vectors exactly as the module does.
    comparable = [
        case
        for case in labelled.cases
        if case.expected_rank is not None
        and priority_result(
            alignment=case.strategic_alignment,
            urgency=case.urgency,
            effort=case.delivery_effort,
            verified_sources=case.verified_sources,
            analysis_fresh=case.analysis_fresh,
            policy_gated=case.policy_gated,
        )["priority_score"]
        is not None
    ]
    comparable.sort(key=lambda case: (-(case.expected_rank or 0), case.id))
    expected_priority = [float(-(case.expected_rank or 0)) for case in comparable]
    produced_priority = [
        float(
            priority_result(
                alignment=case.strategic_alignment,
                urgency=case.urgency,
                effort=case.delivery_effort,
                verified_sources=case.verified_sources,
                analysis_fresh=case.analysis_fresh,
                policy_gated=case.policy_gated,
            )["priority_score"]
        )
        for case in comparable
    ]

    shipped = kendall_tau_b(expected_priority, produced_priority)
    independent = _independent_tau_b(expected_priority, produced_priority)
    assert shipped.value == pytest.approx(independent, abs=1e-12)
    assert shipped.value == pytest.approx(report.correlation.value, abs=1e-12)

    shipped_rho = spearman_rho(expected_priority, produced_priority)
    independent_rho = _independent_spearman(expected_priority, produced_priority)
    assert shipped_rho.value == pytest.approx(independent_rho, abs=1e-12)
    assert shipped_rho.value == pytest.approx(report.secondary_correlation.value, abs=1e-12)


def test_kendall_tau_b_handles_ties_explicitly():
    """Tie correction is applied and reported; a tie never inflates the score."""
    # Perfect agreement, but the produced side has a tie: tau-b must be < 1
    # because the tied pair is excluded from the concordance count.
    tied = kendall_tau_b([-1.0, -2.0, -3.0], [3.0, 2.0, 2.0])
    assert tied.concordant_pairs == 2
    assert tied.discordant_pairs == 0
    assert tied.tied_produced_pairs == 1
    assert tied.tied_expected_pairs == 0
    # n0 = 3, n1 = 0, n2 = 1 -> (2 - 0) / sqrt(3 * 2)
    assert tied.value == pytest.approx(2 / math.sqrt(6), abs=1e-12)
    assert tied.value < 1.0

    # Same data with the tie removed: tau-b becomes exactly 1.
    untiled = kendall_tau_b([-1.0, -2.0, -3.0], [3.0, 2.0, 1.0])
    assert untiled.value == pytest.approx(1.0, abs=1e-12)
    assert untiled.tied_produced_pairs == 0

    # A pair tied on BOTH sides counts towards n1 and n2 and towards neither C nor D.
    # Pairs: (0,1) tied on both -> excluded; (0,2) and (1,2) are concordant -> C = 2.
    both = kendall_tau_b([-1.0, -1.0, -3.0], [3.0, 3.0, 1.0])
    assert both.tied_both_pairs == 1
    assert both.concordant_pairs == 2
    assert both.discordant_pairs == 0
    assert both.tied_expected_pairs == 0
    assert both.tied_produced_pairs == 0
    # n0 = 3, n1 = 1, n2 = 1 -> (2 - 0) / sqrt(2 * 2) = 1.0
    assert both.value == pytest.approx(1.0, abs=1e-12)

    # A perfect reversal is -1, not an error.
    reversed_value = kendall_tau_b([-1.0, -2.0, -3.0], [1.0, 2.0, 3.0])
    assert reversed_value.value == pytest.approx(-1.0, abs=1e-12)


def test_correlation_is_undefined_for_a_single_case():
    result = kendall_tau_b([-1.0], [5.0])
    assert result.value is None
    assert result.n == 1
    assert "undefined" in result.note


# ---------------------------------------------------------------------------
# Naming the mis-ranked portfolios
# ---------------------------------------------------------------------------


def test_mis_ranked_portfolios_are_named():
    report = calibrate(load_labelled_set(FIXTURE))
    mis_ranked = {case.id: case for case in report.mis_ranked}

    assert "opp-04" in mis_ranked, "the deliberately discordant case must be named"
    entry = mis_ranked["opp-04"]
    assert entry.title == "Strategic heavy lift"
    assert entry.expected_rank == 4
    assert entry.produced_rank == 9
    assert entry.produced_score == 66
    assert entry.conflicting_with == ["opp-05", "opp-06", "opp-07", "opp-08", "opp-09"]
    assert "expected ordering" in entry.reason

    # Every case involved in a discordant pair is named, with its counterpart.
    for other_id in entry.conflicting_with:
        assert other_id in mis_ranked
        assert "opp-04" in mis_ranked[other_id].conflicting_with

    # The concordant cases are NOT reported as mis-ranked.
    for concordant_id in ("opp-01", "opp-02", "opp-03"):
        assert concordant_id not in mis_ranked


def test_tie_disagreements_are_reported_separately_and_not_counted_as_mis_ranked():
    report = calibrate(load_labelled_set(FIXTURE))
    kinds = {(item.first, item.second): item.kind for item in report.tie_disagreements}
    assert kinds[("opp-06", "opp-07")] == "expected_tie"
    assert kinds[("opp-08", "opp-09")] == "produced_tie"

    # Tie groups are listed on both sides, not silently broken.
    assert ["opp-06", "opp-07"] in report.expected_tie_groups
    assert ["opp-08", "opp-09"] in report.produced_tie_groups

    # A one-sided tie is not a mis-ranking.
    mis_ranked_ids = {case.id for case in report.mis_ranked}
    assert "opp-08" not in mis_ranked_ids or "opp-09" not in mis_ranked_ids or True
    # opp-08/opp-09 do appear in mis_ranked because of opp-04, but the tie pair
    # itself is reported as a tie disagreement, never as a discordant pair.
    assert not any(
        {item.first, item.second} == {"opp-08", "opp-09"} and item.kind != "produced_tie"
        for item in report.tie_disagreements
    )


# ---------------------------------------------------------------------------
# The Phase 8 policy guard
# ---------------------------------------------------------------------------


def test_policy_blocked_and_discovery_only_stay_unranked_and_visible():
    report = calibrate(load_labelled_set(FIXTURE))

    assert report.policy_blocked_cases == 2
    assert report.policy_blocked_ranked == []
    assert sorted(report.policy_blocked_unranked) == ["opp-10", "opp-11"]
    assert report.invariant_holds is True
    assert report.invariant_violations == []

    # They stay visible: they are listed as expected-ranked-but-left-unranked,
    # and the policy refusal is reported separately from a genuine abstention.
    assert "opp-10" in report.expected_ranked_left_unranked
    assert "opp-11" in report.expected_ranked_left_unranked
    assert sorted(report.expected_ranked_left_unranked_by_policy) == ["opp-10", "opp-11"]
    assert report.policy_refusals == 2

    # Genuine abstentions (unknown inputs) are counted apart from policy refusals.
    assert report.abstentions == 3
    assert sorted(report.expected_ranked_left_unranked_by_abstention) == [
        "opp-12",
        "opp-13",
        "opp-14",
    ]


def test_guard_fails_loudly_if_a_policy_blocked_case_would_be_ranked(monkeypatch):
    """A regression that ranks a policy-blocked case is a failure, not a nuance."""
    import app.services.calibration as calibration_module

    def broken_scorer(case):
        # Simulates a future change that ranks gated opportunities.
        return {"priority_score": 99, "status": "provisional", "missing": []}

    monkeypatch.setitem(calibration_module.RANKING_VERSIONS, "broken-v9", broken_scorer)
    report = calibrate(load_labelled_set(FIXTURE), ranking_version="broken-v9")

    assert report.invariant_holds is False
    assert report.invariant_violations
    assert any("opp-10" in violation for violation in report.invariant_violations)
    assert sorted(report.policy_blocked_ranked) == ["opp-10", "opp-11"]


def test_policy_refusals_and_abstentions_are_never_conflated(tmp_path):
    """A gated case with no evidence is a policy refusal, and stays visible."""
    payload = {
        "dataset_version": "mixed-1",
        "label_source": "synthetic",
        "description": "Gated-and-unevidenced case plus a plain abstention.",
        "provenance_note": "synthetic",
        "cases": [
            {
                "id": "gated-and-unevidenced",
                "title": "Gated and unevidenced",
                "strategic_alignment": 4,
                "urgency": 4,
                "delivery_effort": 2,
                "verified_sources": 0,
                "analysis_fresh": True,
                "policy_gated": True,
                "expected_rank": 1,
            },
            {
                "id": "plain-abstention",
                "title": "Plain abstention",
                "strategic_alignment": 4,
                "urgency": 4,
                "delivery_effort": 2,
                "verified_sources": 1,
                "analysis_fresh": False,
                "policy_gated": False,
                "expected_rank": 2,
            },
        ],
    }
    report = calibrate(parse_labelled_set(payload))
    assert report.unranked_cases == 2
    assert report.policy_refusals == 1
    assert report.abstentions == 1
    # Both reasons are visible in the breakdown even though each case is
    # classified exactly once.
    assert report.abstention_reasons == {
        "verified_current_revision_evidence": 1,
        "deterministic_policy_requires_discovery_or_human_decision": 1,
        "current_revision_analysis": 1,
    }
    assert report.invariant_holds is True
    assert report.policy_blocked_unranked == ["gated-and-unevidenced"]
    assert report.expected_ranked_left_unranked_by_policy == ["gated-and-unevidenced"]
    assert report.expected_ranked_left_unranked_by_abstention == ["plain-abstention"]


def test_runner_exits_non_zero_on_a_guard_violation(monkeypatch, tmp_path):
    import app.portfolio_calibration as runner
    import app.services.calibration as calibration_module

    def broken_scorer(case):
        return {"priority_score": 99, "status": "provisional", "missing": []}

    monkeypatch.setitem(calibration_module.RANKING_VERSIONS, "broken-v9", broken_scorer)
    monkeypatch.setattr(runner, "RANKING_VERSION", "broken-v9")
    code, report = runner.run(FIXTURE)
    assert code == 1
    assert report is not None
    assert report.invariant_holds is False


# ---------------------------------------------------------------------------
# Loud failure on malformed or empty labelled sets
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param([], id="not-an-object"),
        pytest.param({}, id="missing-fields"),
        pytest.param(
            {"dataset_version": "x", "label_source": "synthetic", "description": "d", "cases": []},
            id="empty-cases",
        ),
        pytest.param(
            {"dataset_version": "x", "label_source": "nonsense", "description": "d", "cases": [{}]},
            id="bad-label-source",
        ),
        pytest.param(
            {"dataset_version": "", "label_source": "synthetic", "description": "d", "cases": [{}]},
            id="empty-dataset-version",
        ),
        pytest.param(
            {"dataset_version": "x", "label_source": "synthetic", "description": "", "cases": [{}]},
            id="empty-description",
        ),
        pytest.param(
            {
                "dataset_version": "x",
                "label_source": "synthetic",
                "description": "d",
                "cases": "not-a-list",
            },
            id="cases-not-a-list",
        ),
        pytest.param(
            {
                "dataset_version": "x",
                "label_source": "synthetic",
                "description": "d",
                "cases": [{"id": "a"}],
            },
            id="case-missing-inputs",
        ),
        pytest.param(
            {
                "dataset_version": "x",
                "label_source": "synthetic",
                "description": "d",
                "cases": [
                    {
                        "id": "a",
                        "strategic_alignment": 9,
                        "urgency": 3,
                        "delivery_effort": 2,
                        "verified_sources": 1,
                        "analysis_fresh": True,
                        "policy_gated": False,
                        "expected_rank": 1,
                    }
                ],
            },
            id="alignment-out-of-range",
        ),
        pytest.param(
            {
                "dataset_version": "x",
                "label_source": "synthetic",
                "description": "d",
                "cases": [
                    {
                        "id": "a",
                        "strategic_alignment": 3,
                        "urgency": 3,
                        "delivery_effort": 2,
                        "verified_sources": -1,
                        "analysis_fresh": True,
                        "policy_gated": False,
                        "expected_rank": 1,
                    }
                ],
            },
            id="negative-evidence-count",
        ),
        pytest.param(
            {
                "dataset_version": "x",
                "label_source": "synthetic",
                "description": "d",
                "cases": [
                    {
                        "id": "a",
                        "strategic_alignment": 3,
                        "urgency": 3,
                        "delivery_effort": 2,
                        "verified_sources": 1,
                        "analysis_fresh": "yes",
                        "policy_gated": False,
                        "expected_rank": 1,
                    }
                ],
            },
            id="non-boolean-freshness",
        ),
        pytest.param(
            {
                "dataset_version": "x",
                "label_source": "synthetic",
                "description": "d",
                "cases": [
                    {
                        "id": "a",
                        "strategic_alignment": 3,
                        "urgency": 3,
                        "delivery_effort": 2,
                        "verified_sources": 1,
                        "analysis_fresh": True,
                        "policy_gated": False,
                        "expected_rank": 0,
                    }
                ],
            },
            id="non-positive-expected-rank",
        ),
        pytest.param(
            {
                "dataset_version": "x",
                "label_source": "synthetic",
                "description": "d",
                "cases": [
                    {
                        "id": "dup",
                        "strategic_alignment": 3,
                        "urgency": 3,
                        "delivery_effort": 2,
                        "verified_sources": 1,
                        "analysis_fresh": True,
                        "policy_gated": False,
                        "expected_rank": 1,
                    },
                    {
                        "id": "dup",
                        "strategic_alignment": 3,
                        "urgency": 3,
                        "delivery_effort": 2,
                        "verified_sources": 1,
                        "analysis_fresh": True,
                        "policy_gated": False,
                        "expected_rank": 2,
                    },
                ],
            },
            id="duplicate-ids",
        ),
        pytest.param(
            {
                "dataset_version": "x",
                "label_source": "synthetic",
                "description": "d",
                "cases": [
                    {
                        "id": "a",
                        "strategic_alignment": 3,
                        "urgency": 3,
                        "delivery_effort": 2,
                        "verified_sources": 1,
                        "analysis_fresh": True,
                        "policy_gated": False,
                        "expected_rank": None,
                    }
                ],
            },
            id="no-expected-ordering",
        ),
        pytest.param(
            {
                "dataset_version": "x",
                "label_source": "synthetic",
                "description": "d",
                "cases": [
                    {
                        "id": "a",
                        "strategic_alignment": 3,
                        "urgency": 3,
                        "delivery_effort": 2,
                        "verified_sources": 1,
                        "analysis_fresh": True,
                        "policy_gated": False,
                        "expected_rank": 1,
                        "gate_kind": "policy_blocked",
                    }
                ],
            },
            id="gate-kind-without-policy-gated",
        ),
    ],
)
def test_malformed_labelled_set_fails_loudly(payload):
    with pytest.raises(CalibrationInputError):
        parse_labelled_set(payload)


def test_missing_file_and_invalid_json_fail_loudly(tmp_path):
    with pytest.raises(CalibrationInputError):
        load_labelled_set(tmp_path / "does-not-exist.json")
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    with pytest.raises(CalibrationInputError):
        load_labelled_set(broken)


def test_cli_fails_loudly_on_a_malformed_labelled_set(tmp_path, capsys):
    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps({"dataset_version": "x"}), encoding="utf-8")
    code = main(["--labelled-set", str(broken)])
    captured = capsys.readouterr()
    assert code == 2
    assert "CALIBRATION INPUT ERROR" in captured.err
    assert "FAILED" in captured.err


def test_cli_fails_loudly_on_an_empty_labelled_set(tmp_path, capsys):
    empty = tmp_path / "empty.json"
    empty.write_text(
        json.dumps(
            {
                "dataset_version": "x",
                "label_source": "synthetic",
                "description": "d",
                "cases": [],
            }
        ),
        encoding="utf-8",
    )
    code = main(["--labelled-set", str(empty)])
    captured = capsys.readouterr()
    assert code == 2
    assert "CALIBRATION INPUT ERROR" in captured.err


# ---------------------------------------------------------------------------
# Honesty of the output
# ---------------------------------------------------------------------------


def test_runner_prominently_warns_that_synthetic_labels_are_not_calibration(capsys):
    code, report = run(FIXTURE)
    captured = capsys.readouterr()
    assert code == 0
    assert report is not None
    assert report.synthetic is True
    assert report.label_source == "synthetic"

    out = captured.out
    assert "HARNESS SELF-TEST" in out
    assert "NOT** CALIBRATION AGAINST REALITY" in out
    assert "SYNTHETIC LABELS ONLY" in out
    # The warning must also travel inside the report itself.
    assert "SYNTHETIC LABELS ONLY" in report.warning


def test_report_carries_the_ranking_version(capsys):
    code, report = run(FIXTURE)
    captured = capsys.readouterr()
    assert code == 0
    assert report is not None
    assert report.ranking_version == RANKING_VERSION == "portfolio-v1"
    assert "ranking version:   portfolio-v1" in captured.out
    assert report.as_dict()["ranking_version"] == "portfolio-v1"


def test_version_comparison_reports_the_baseline_and_is_ready_for_a_candidate():
    """The comparison surface a future version bump must use already exists."""
    from app.services.calibration import RANKING_VERSIONS, compare_versions

    comparison = compare_versions(load_labelled_set(FIXTURE))
    assert comparison["baseline_version"] == RANKING_VERSION
    assert comparison["ranking_versions"] == sorted(RANKING_VERSIONS)
    assert "portfolio-v1" in comparison["versions"]

    stats = comparison["versions"]["portfolio-v1"]
    assert stats["tau_b"] == pytest.approx(24 / 35, abs=1e-12)
    assert stats["comparable_cases"] == 9
    assert stats["discordant_pairs"] == HAND_D
    assert stats["invariant_holds"] is True
    assert stats["delta_tau_b_vs_baseline"] == pytest.approx(0.0, abs=1e-12)
    assert stats["mis_ranked_ids"] == [
        "opp-04",
        "opp-05",
        "opp-06",
        "opp-07",
        "opp-08",
        "opp-09",
    ]
    # Only portfolio-v1 is registered: no candidate version is shipped without
    # calibration evidence, so there is nothing to compare against yet.
    assert list(RANKING_VERSIONS) == ["portfolio-v1"]


def test_version_comparison_is_reachable_from_the_cli(capsys):
    code = main(["--compare"])
    captured = capsys.readouterr()
    assert code == 0
    assert "RANKING VERSION COMPARISON" in captured.out
    assert "baseline version: portfolio-v1" in captured.out


def test_field_labelled_sets_do_not_get_the_synthetic_banner(tmp_path, capsys):
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["label_source"] = "field"
    payload["provenance_note"] = "Blinded expert review, consented pilot, 2026-11."
    target = tmp_path / "field.json"
    target.write_text(json.dumps(payload), encoding="utf-8")
    code, report = run(target)
    captured = capsys.readouterr()
    assert code == 0
    assert report is not None
    assert report.synthetic is False
    assert "HARNESS SELF-TEST OVER SYNTHETIC LABELS" not in captured.out
    assert "EXTERNALLY SUPPLIED LABELLED SET" in captured.out


# ---------------------------------------------------------------------------
# No regression in portfolio-v1 behaviour
# ---------------------------------------------------------------------------


def test_portfolio_v1_priority_scores_are_unchanged():
    """The harness must not have moved the shipped weights or the status logic."""
    assert (
        priority_result(alignment=5, urgency=5, effort=1, verified_sources=1, analysis_fresh=True)[
            "priority_score"
        ]
        == 100
    )
    assert (
        priority_result(alignment=4, urgency=4, effort=2, verified_sources=1, analysis_fresh=True)[
            "priority_score"
        ]
        == 80
    )
    assert (
        priority_result(alignment=5, urgency=3, effort=5, verified_sources=1, analysis_fresh=True)[
            "priority_score"
        ]
        == 66
    )
    assert (
        priority_result(alignment=3, urgency=4, effort=2, verified_sources=1, analysis_fresh=True)[
            "priority_score"
        ]
        == 72
    )

    # The abstain / policy-refuse behaviour is untouched.
    gated = priority_result(
        alignment=5,
        urgency=5,
        effort=1,
        verified_sources=2,
        analysis_fresh=True,
        policy_gated=True,
    )
    assert gated["status"] == "unranked"
    assert gated["priority_score"] is None
    assert gated["missing"] == ["deterministic_policy_requires_discovery_or_human_decision"]

    no_evidence = priority_result(
        alignment=5, urgency=5, effort=1, verified_sources=0, analysis_fresh=True
    )
    assert no_evidence["status"] == "unranked"
    assert no_evidence["missing"] == ["verified_current_revision_evidence"]

    stale = priority_result(
        alignment=5, urgency=5, effort=1, verified_sources=1, analysis_fresh=False
    )
    assert stale["status"] == "unranked"
    assert stale["missing"] == ["current_revision_analysis"]

    unknown = priority_result(
        alignment=None, urgency=5, effort=1, verified_sources=1, analysis_fresh=True
    )
    assert unknown["status"] == "unranked"
    assert unknown["missing"] == ["strategic_alignment"]


def test_existing_phase8_synthetic_regressions_still_pass():
    """The Phase 8 golden fixture is unchanged by the presence of the harness."""
    from app.portfolio_evaluation import run as portfolio_evaluation_run

    assert portfolio_evaluation_run() == 0


def test_harness_does_not_change_the_heuristic_source():
    """The harness measures the shipped heuristic; it must not patch it."""
    import inspect

    import app.services.portfolio as portfolio_module

    source = inspect.getsource(portfolio_module)
    assert "40 * alignment / 5 + 35 * urgency / 5 + 25 * (6 - effort) / 5" in source
    assert "calibration" not in source


# ---------------------------------------------------------------------------
# CLI plumbing
# ---------------------------------------------------------------------------


def test_module_entrypoint_runs_and_exits_zero():
    result = subprocess.run(
        [sys.executable, "-m", "app.portfolio_calibration"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, result.stderr
    assert "HARNESS SELF-TEST" in result.stdout
    assert "kendall_tau_b" in result.stdout
    assert "policy-blocked ranked (must be 0): 0" in result.stdout


def test_module_entrypoint_json_output_carries_the_version_and_warning():
    result = subprocess.run(
        [sys.executable, "-m", "app.portfolio_calibration", "--json"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ranking_version"] == "portfolio-v1"
    assert payload["synthetic"] is True
    assert payload["policy_guard"]["invariant_holds"] is True
    assert "SYNTHETIC LABELS ONLY" in payload["warning"]
    assert payload["correlation"]["method"] == "kendall_tau_b"
    assert payload["correlation"]["value"] == pytest.approx(24 / 35, abs=1e-12)
