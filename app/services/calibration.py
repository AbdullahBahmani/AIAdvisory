"""Ranking calibration harness for the portfolio priority heuristic.

Scope and honesty
-----------------
This module *measures* a versioned ranking heuristic against an independently
supplied expected ordering. It does not create truth.

The labelled data required to make this meaningful does not exist yet: it needs
consented real assessments and blinded expert review (P8-09). Until then, any
number produced over a synthetic corpus is a **harness self-test**, not
calibration against reality. The runner in :mod:`app.portfolio_calibration`
says so loudly on every run, and the report carries the label source, so no
downstream reader can mistake one for the other.

Correlation method
------------------
Kendall's tau-b is used as the primary rank correlation.

Rationale:

* The heuristic produces **integer** priority scores (``portfolio-v1`` rounds to
  ``int``), so ties in the produced ordering are common, not exceptional.
* The expected ordering supplied by a reviewer may also contain ties (two
  opportunities judged equally urgent). Kendall's tau-b has an exact tie
  correction for ties in *either* sequence; Spearman's rho would need average
  ranks as a pre-step and its tie handling is easy to get wrong silently.
* tau-b is defined directly over *pairs* of items, which maps onto the
  requirement to name the specific portfolios that are ordered incorrectly: a
  discordant pair is exactly "these two are ordered the wrong way round".
* It is cheap to implement exactly, with no new dependency, and it is
  reproducible by hand from the concordance/discordance counts that the report
  prints.

Spearman's rho is computed alongside as a **secondary** diagnostic and is
cross-checked against tau-b; it is never the headline number.

Ties are handled explicitly, never silently: the report lists every tie group
on both sides of the comparison, states the tie correction actually used, and
records how many pairs were excluded from the concordance count because of
ties. Pairs that one side ties and the other separates are reported
separately, because tau-b deliberately excludes them from the correlation.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from app.services.portfolio import priority_result

#: The ranking version whose behaviour this harness measures. This string is
#: carried in every report so a reader can always tell which version produced
#: an ordering.
RANKING_VERSION = "portfolio-v1"

#: Reason string used by ``priority_result`` for a deterministic policy gate.
POLICY_GATE_REASON = "deterministic_policy_requires_discovery_or_human_decision"

SYNTHETIC_WARNING = (
    "SYNTHETIC LABELS ONLY: this run is a HARNESS SELF-TEST. It is NOT "
    "calibration against reality, NOT evidence of expert agreement, and NOT a "
    "measure of ROI, safety or business impact. Real calibration requires a "
    "consented, blinded-expert labelled set (P8-09)."
)

FIELD_WARNING = (
    "Labelled set supplied by an external source. Read its provenance note "
    "before quoting any number: the harness reports agreement with the "
    "supplied ordering only, and does not establish ROI or safety."
)


class CalibrationInputError(ValueError):
    """Raised when a supplied labelled set is malformed, empty or unusable.

    The runner turns this into a loud failure. A malformed or empty labelled
    set must never silently pass as "no calibration needed".
    """


@dataclass(frozen=True)
class LabelledCase:
    """One labelled portfolio: heuristic inputs plus an expected ordering slot.

    ``expected_rank`` is the reviewer's position, 1 = highest priority. Two
    cases may deliberately carry the same ``expected_rank``; that is how a
    reviewer says "these two are equally important" and it is preserved as a
    tie rather than being broken arbitrarily. ``expected_rank`` may be absent,
    which means the reviewer did not place the case in the ordering at all.
    """

    id: str
    title: str
    strategic_alignment: int | None
    urgency: int | None
    delivery_effort: int | None
    verified_sources: int
    analysis_fresh: bool
    policy_gated: bool
    expected_rank: int | None
    note: str = ""
    #: Free-text kind of the deterministic gate, e.g. ``policy_blocked`` or
    #: ``discovery_only``. Reporting metadata only: both kinds must stay
    #: unranked, which is what ``policy_gated`` expresses to the heuristic.
    gate_kind: str | None = None


@dataclass(frozen=True)
class LabelledSet:
    """A versioned labelled set of portfolios."""

    dataset_version: str
    label_source: Literal["synthetic", "field"]
    description: str
    provenance_note: str
    cases: tuple[LabelledCase, ...]

    @property
    def synthetic(self) -> bool:
        return self.label_source == "synthetic"


@dataclass
class CorrelationResult:
    """Rank correlation between an expected ordering and a produced ordering."""

    method: str
    n: int
    value: float | None
    concordant_pairs: int
    discordant_pairs: int
    tied_expected_pairs: int
    tied_produced_pairs: int
    tied_both_pairs: int
    comparable_pairs: int
    note: str = ""


@dataclass
class MisRankedCase:
    """A case the heuristic orders differently from the expected ordering."""

    id: str
    title: str
    expected_rank: int | None
    produced_rank: int | None
    produced_score: int | None
    reason: str
    conflicting_with: list[str] = field(default_factory=list)


@dataclass
class TieDisagreement:
    """A pair one side ties and the other separates.

    Kendall's tau-b excludes these pairs from the concordance count, so they
    are reported separately rather than being silently dropped.
    """

    first: str
    second: str
    kind: Literal["produced_tie", "expected_tie"]
    expected_ranks: tuple[int | None, int | None]
    produced_scores: tuple[int | None, int | None]


@dataclass
class CalibrationReport:
    """The full outcome of one calibration run."""

    ranking_version: str
    dataset_version: str
    label_source: str
    synthetic: bool
    description: str
    provenance_note: str
    total_cases: int
    ranked_cases: int
    unranked_cases: int
    abstentions: int
    policy_refusals: int
    abstention_reasons: dict[str, int]
    policy_blocked_cases: int
    policy_blocked_ranked: list[str]
    policy_blocked_unranked: list[str]
    expected_ranked_count: int
    expected_ranked_left_unranked: list[str]
    expected_ranked_left_unranked_by_policy: list[str]
    expected_ranked_left_unranked_by_abstention: list[str]
    expected_unranked_but_ranked_by_heuristic: list[str]
    correlation: CorrelationResult
    secondary_correlation: CorrelationResult
    mis_ranked: list[MisRankedCase]
    tie_disagreements: list[TieDisagreement]
    expected_tie_groups: list[list[str]]
    produced_tie_groups: list[list[str]]
    invariant_holds: bool
    invariant_violations: list[str]
    warning: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "ranking_version": self.ranking_version,
            "dataset_version": self.dataset_version,
            "label_source": self.label_source,
            "synthetic": self.synthetic,
            "description": self.description,
            "provenance_note": self.provenance_note,
            "totals": {
                "cases": self.total_cases,
                "ranked": self.ranked_cases,
                "unranked": self.unranked_cases,
                "abstentions": self.abstentions,
                "policy_refusals": self.policy_refusals,
            },
            "abstention_reasons": self.abstention_reasons,
            "policy_guard": {
                "policy_blocked_cases": self.policy_blocked_cases,
                "policy_blocked_ranked": self.policy_blocked_ranked,
                "policy_blocked_unranked": self.policy_blocked_unranked,
                "invariant_holds": self.invariant_holds,
                "invariant_violations": self.invariant_violations,
            },
            "expected_ranked_count": self.expected_ranked_count,
            "expected_ranked_left_unranked": self.expected_ranked_left_unranked,
            "expected_ranked_left_unranked_by_policy": (
                self.expected_ranked_left_unranked_by_policy
            ),
            "expected_ranked_left_unranked_by_abstention": (
                self.expected_ranked_left_unranked_by_abstention
            ),
            "expected_unranked_but_ranked_by_heuristic": (
                self.expected_unranked_but_ranked_by_heuristic
            ),
            "correlation": {
                "method": self.correlation.method,
                "value": self.correlation.value,
                "n": self.correlation.n,
                "concordant_pairs": self.correlation.concordant_pairs,
                "discordant_pairs": self.correlation.discordant_pairs,
                "tied_expected_pairs": self.correlation.tied_expected_pairs,
                "tied_produced_pairs": self.correlation.tied_produced_pairs,
                "tied_both_pairs": self.correlation.tied_both_pairs,
                "comparable_pairs": self.correlation.comparable_pairs,
                "note": self.correlation.note,
            },
            "secondary_correlation": {
                "method": self.secondary_correlation.method,
                "value": self.secondary_correlation.value,
                "n": self.secondary_correlation.n,
            },
            "mis_ranked": [
                {
                    "id": case.id,
                    "title": case.title,
                    "expected_rank": case.expected_rank,
                    "produced_rank": case.produced_rank,
                    "produced_score": case.produced_score,
                    "reason": case.reason,
                    "conflicting_with": case.conflicting_with,
                }
                for case in self.mis_ranked
            ],
            "tie_disagreements": [
                {
                    "first": item.first,
                    "second": item.second,
                    "kind": item.kind,
                    "expected_ranks": list(item.expected_ranks),
                    "produced_scores": list(item.produced_scores),
                }
                for item in self.tie_disagreements
            ],
            "expected_tie_groups": self.expected_tie_groups,
            "produced_tie_groups": self.produced_tie_groups,
            "invariant_holds": self.invariant_holds,
            "invariant_violations": self.invariant_violations,
            "warning": self.warning,
        }


# ---------------------------------------------------------------------------
# Scoring versions
# ---------------------------------------------------------------------------

#: A scorer turns one labelled case into the shipped heuristic's output shape:
#: ``{"priority_score": int | None, "status": str, "missing": list[str]}``.
Scorer = Callable[[LabelledCase], dict[str, Any]]


def score_portfolio_v1(case: LabelledCase) -> dict[str, Any]:
    """Score a case with the shipped ``portfolio-v1`` heuristic.

    This calls :func:`app.services.portfolio.priority_result` directly, so the
    harness measures the *shipped* behaviour and cannot drift away from it.
    """
    return priority_result(
        alignment=case.strategic_alignment,
        urgency=case.urgency,
        effort=case.delivery_effort,
        verified_sources=case.verified_sources,
        analysis_fresh=case.analysis_fresh,
        policy_gated=case.policy_gated,
    )


#: Registry of ranking versions this harness can measure. A new version is
#: only added together with an ADR that records the calibration evidence
#: justifying it; ``portfolio-v1`` remains available for comparison.
RANKING_VERSIONS: dict[str, Scorer] = {
    "portfolio-v1": score_portfolio_v1,
}


# ---------------------------------------------------------------------------
# Rank correlation
# ---------------------------------------------------------------------------


def _average_ranks(values: Sequence[float]) -> list[float]:
    """Rank ``values`` with the *average* method for ties (1 = smallest value).

    A tie group of size ``t`` occupying positions ``p .. p+t-1`` (1-based)
    receives the mean rank ``p + (t-1)/2``.
    """
    n = len(values)
    order = sorted(range(n), key=lambda i: values[i])
    ranks = [0.0] * n
    index = 0
    while index < n:
        end = index
        while end + 1 < n and values[order[end + 1]] == values[order[index]]:
            end += 1
        mean_rank = index + 1 + (end - index) / 2.0
        for position in range(index, end + 1):
            ranks[order[position]] = mean_rank
        index = end + 1
    return ranks


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    n = len(xs)
    if n < 2:
        return None
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    if var_x <= 0 or var_y <= 0:
        return None
    return cov / math.sqrt(var_x * var_y)


def spearman_rho(expected: Sequence[float], produced: Sequence[float]) -> CorrelationResult:
    """Spearman's rho with average ranks for ties (secondary diagnostic only)."""
    if len(expected) != len(produced):
        raise ValueError("expected and produced sequences must be the same length")
    n = len(expected)
    if n < 2:
        return CorrelationResult(
            method="spearman_rho",
            n=n,
            value=None,
            concordant_pairs=0,
            discordant_pairs=0,
            tied_expected_pairs=0,
            tied_produced_pairs=0,
            tied_both_pairs=0,
            comparable_pairs=0,
            note="fewer than two comparable cases; correlation undefined",
        )
    expected_ranks = _average_ranks(expected)
    produced_ranks = _average_ranks(produced)
    value = _pearson(expected_ranks, produced_ranks)
    concordant = discordant = tied_e = tied_p = tied_both = 0
    for i in range(n):
        for j in range(i + 1, n):
            de = expected[i] - expected[j]
            dp = produced[i] - produced[j]
            if de == 0 and dp == 0:
                tied_both += 1
            elif de == 0:
                tied_e += 1
            elif dp == 0:
                tied_p += 1
            elif (de > 0) == (dp > 0):
                concordant += 1
            else:
                discordant += 1
    return CorrelationResult(
        method="spearman_rho",
        n=n,
        value=value,
        concordant_pairs=concordant,
        discordant_pairs=discordant,
        tied_expected_pairs=tied_e,
        tied_produced_pairs=tied_p,
        tied_both_pairs=tied_both,
        comparable_pairs=concordant + discordant,
        note="secondary diagnostic; ties resolved to average ranks",
    )


def kendall_tau_b(expected: Sequence[float], produced: Sequence[float]) -> CorrelationResult:
    """Kendall's tau-b between an expected ordering and a produced ordering.

    Both sequences are "position-like" values where **larger means higher
    priority** (the caller converts the reviewer's "1 = best" into that
    convention). Ties are handled explicitly with the standard tau-b
    correction:

    ``tau_b = (C - D) / sqrt((n0 - n1) * (n0 - n2))``

    where ``n0`` is the number of pairs, ``n1`` the number of pairs tied in the
    expected sequence, and ``n2`` the number of pairs tied in the produced
    sequence. Pairs tied in both sequences count towards both ``n1`` and ``n2``
    and contribute to neither ``C`` nor ``D``.

    Implemented directly over pairs (O(n^2)) so the arithmetic can be checked
    by hand; no third-party dependency is introduced.
    """
    if len(expected) != len(produced):
        raise ValueError("expected and produced sequences must be the same length")
    n = len(expected)
    if n < 2:
        return CorrelationResult(
            method="kendall_tau_b",
            n=n,
            value=None,
            concordant_pairs=0,
            discordant_pairs=0,
            tied_expected_pairs=0,
            tied_produced_pairs=0,
            tied_both_pairs=0,
            comparable_pairs=0,
            note="fewer than two comparable cases; correlation undefined",
        )
    concordant = discordant = 0
    tied_e = tied_p = tied_both = 0
    for i in range(n):
        for j in range(i + 1, n):
            de = expected[i] - expected[j]
            dp = produced[i] - produced[j]
            if de == 0 and dp == 0:
                tied_both += 1
            elif de == 0:
                tied_e += 1
            elif dp == 0:
                tied_p += 1
            elif (de > 0) == (dp > 0):
                concordant += 1
            else:
                discordant += 1
    n0 = n * (n - 1) // 2
    n1 = tied_e + tied_both
    n2 = tied_p + tied_both
    denominator = math.sqrt((n0 - n1) * (n0 - n2))
    value = (concordant - discordant) / denominator if denominator > 0 else None
    return CorrelationResult(
        method="kendall_tau_b",
        n=n,
        value=value,
        concordant_pairs=concordant,
        discordant_pairs=discordant,
        tied_expected_pairs=tied_e,
        tied_produced_pairs=tied_p,
        tied_both_pairs=tied_both,
        comparable_pairs=concordant + discordant,
        note=(
            "tie-corrected tau-b; larger means higher priority on both sides; "
            f"n0={n0}, n1={n1}, n2={n2}"
        ),
    )


def _tie_groups(ids: Sequence[str], values: Sequence[float]) -> list[list[str]]:
    """Groups of ids sharing the same value, for values shared by more than one id."""
    buckets: dict[float, list[str]] = {}
    for case_id, value in zip(ids, values, strict=True):
        buckets.setdefault(value, []).append(case_id)
    return [sorted(group) for _, group in sorted(buckets.items()) if len(group) > 1]


def _canonical_pair(
    case_i: LabelledCase,
    result_i: dict[str, Any],
    case_j: LabelledCase,
    result_j: dict[str, Any],
) -> tuple[str, str, tuple[int | None, int | None], tuple[int | None, int | None]]:
    """Order a pair of cases canonically by id and align their ranks/scores."""
    if case_i.id <= case_j.id:
        first, second = case_i, case_j
        first_result, second_result = result_i, result_j
    else:
        first, second = case_j, case_i
        first_result, second_result = result_j, result_i
    return (
        first.id,
        second.id,
        (first.expected_rank, second.expected_rank),
        (first_result["priority_score"], second_result["priority_score"]),
    )


# ---------------------------------------------------------------------------
# Loading and validating a labelled set
# ---------------------------------------------------------------------------


def _optional_int(payload: dict[str, Any], key: str, case_id: str) -> int | None:
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise CalibrationInputError(
            f"case {case_id!r}: field {key!r} must be an integer or null, got {value!r}"
        )
    return value


def _require_int(payload: dict[str, Any], key: str, case_id: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise CalibrationInputError(
            f"case {case_id!r}: field {key!r} must be an integer, got {value!r}"
        )
    return value


def _require_bool(payload: dict[str, Any], key: str, case_id: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise CalibrationInputError(
            f"case {case_id!r}: field {key!r} must be a boolean, got {value!r}"
        )
    return value


def parse_labelled_set(payload: Any) -> LabelledSet:
    """Validate a decoded labelled-set document and return a :class:`LabelledSet`.

    Raises :class:`CalibrationInputError` for anything malformed, empty or
    ambiguous. The point of raising rather than defaulting is that a broken
    labelled set must never be reported as "no calibration problem found".
    """
    if not isinstance(payload, dict):
        raise CalibrationInputError("labelled set must be a JSON object")
    for key in ("dataset_version", "label_source", "description", "cases"):
        if key not in payload:
            raise CalibrationInputError(f"labelled set is missing required field {key!r}")
    label_source = payload["label_source"]
    if label_source not in ("synthetic", "field"):
        raise CalibrationInputError(
            "labelled set field 'label_source' must be 'synthetic' or 'field', "
            f"got {label_source!r}"
        )
    dataset_version = payload["dataset_version"]
    if not isinstance(dataset_version, str) or not dataset_version.strip():
        raise CalibrationInputError(
            "labelled set field 'dataset_version' must be a non-empty string"
        )
    description = payload["description"]
    if not isinstance(description, str) or not description.strip():
        raise CalibrationInputError("labelled set field 'description' must be a non-empty string")
    provenance_note = payload.get("provenance_note", "")
    if not isinstance(provenance_note, str):
        raise CalibrationInputError("labelled set field 'provenance_note' must be a string")
    raw_cases = payload["cases"]
    if not isinstance(raw_cases, list):
        raise CalibrationInputError("labelled set field 'cases' must be a list")
    if not raw_cases:
        raise CalibrationInputError(
            "labelled set contains no cases; an empty labelled set cannot be calibrated "
            "and must not be reported as a pass"
        )
    cases: list[LabelledCase] = []
    seen_ids: set[str] = set()
    for raw in raw_cases:
        if not isinstance(raw, dict):
            raise CalibrationInputError("every entry in 'cases' must be an object")
        case_id = raw.get("id")
        if not isinstance(case_id, str) or not case_id.strip():
            raise CalibrationInputError("every case needs a non-empty string 'id'")
        if case_id in seen_ids:
            raise CalibrationInputError(f"duplicate case id {case_id!r} in labelled set")
        seen_ids.add(case_id)
        title = raw.get("title", case_id)
        if not isinstance(title, str) or not title.strip():
            raise CalibrationInputError(f"case {case_id!r}: 'title' must be a non-empty string")
        dimensions: dict[str, int | None] = {}
        for key in ("strategic_alignment", "urgency", "delivery_effort"):
            value = _optional_int(raw, key, case_id)
            if value is not None and not 1 <= value <= 5:
                raise CalibrationInputError(
                    f"case {case_id!r}: field {key!r} must be between 1 and 5, got {value!r}"
                )
            dimensions[key] = value
        verified_sources = _require_int(raw, "verified_sources", case_id)
        if verified_sources < 0:
            raise CalibrationInputError(
                f"case {case_id!r}: 'verified_sources' must not be negative, "
                f"got {verified_sources!r}"
            )
        analysis_fresh = _require_bool(raw, "analysis_fresh", case_id)
        policy_gated = _require_bool(raw, "policy_gated", case_id)
        expected_rank = _optional_int(raw, "expected_rank", case_id)
        if expected_rank is not None and expected_rank < 1:
            raise CalibrationInputError(
                f"case {case_id!r}: 'expected_rank' must be >= 1 when present, "
                f"got {expected_rank!r}"
            )
        note = raw.get("note", "")
        if not isinstance(note, str):
            raise CalibrationInputError(f"case {case_id!r}: 'note' must be a string")
        gate_kind = raw.get("gate_kind")
        if gate_kind is not None and not isinstance(gate_kind, str):
            raise CalibrationInputError(f"case {case_id!r}: 'gate_kind' must be a string or null")
        if gate_kind and not policy_gated:
            raise CalibrationInputError(
                f"case {case_id!r}: 'gate_kind' is set but 'policy_gated' is false; "
                "a gated case must be marked policy_gated so the guard can check it"
            )
        cases.append(
            LabelledCase(
                id=case_id,
                title=title,
                strategic_alignment=dimensions["strategic_alignment"],
                urgency=dimensions["urgency"],
                delivery_effort=dimensions["delivery_effort"],
                verified_sources=verified_sources,
                analysis_fresh=analysis_fresh,
                policy_gated=policy_gated,
                expected_rank=expected_rank,
                note=note,
                gate_kind=gate_kind,
            )
        )
    if not any(case.expected_rank is not None for case in cases):
        raise CalibrationInputError(
            "labelled set contains no expected ordering; at least one case needs "
            "'expected_rank' for a rank correlation to exist"
        )
    return LabelledSet(
        dataset_version=dataset_version,
        label_source=label_source,
        description=description,
        provenance_note=provenance_note,
        cases=tuple(cases),
    )


def load_labelled_set(path: str | Path) -> LabelledSet:
    """Load and validate a labelled set from disk.

    Raises :class:`CalibrationInputError` when the file is missing, is not
    valid JSON, or does not satisfy the documented format.
    """
    source = Path(path)
    try:
        raw = source.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise CalibrationInputError(f"labelled set not found: {source}") from exc
    except OSError as exc:
        raise CalibrationInputError(f"labelled set could not be read: {source}: {exc}") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CalibrationInputError(f"labelled set is not valid JSON: {source}: {exc}") from exc
    return parse_labelled_set(payload)


# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------


def calibrate(
    labelled: LabelledSet,
    ranking_version: str = RANKING_VERSION,
) -> CalibrationReport:
    """Measure ``ranking_version`` against the expected ordering in ``labelled``."""
    if ranking_version not in RANKING_VERSIONS:
        raise CalibrationInputError(
            f"unknown ranking version {ranking_version!r}; known versions: "
            f"{', '.join(sorted(RANKING_VERSIONS))}"
        )
    scorer = RANKING_VERSIONS[ranking_version]

    scored: list[tuple[LabelledCase, dict[str, Any]]] = [
        (case, scorer(case)) for case in labelled.cases
    ]
    scores_by_id = {case.id: result for case, result in scored}

    ranked = [(case, result) for case, result in scored if result["priority_score"] is not None]
    unranked = [(case, result) for case, result in scored if result["priority_score"] is None]

    # --- Policy guard -----------------------------------------------------
    # A policy-blocked or discovery-only proposal must never be ranked, and it
    # must stay visible as unranked rather than disappearing from the report.
    policy_blocked = [(case, result) for case, result in scored if case.policy_gated]
    policy_blocked_ranked = [
        case.id for case, result in policy_blocked if result["priority_score"] is not None
    ]
    policy_blocked_unranked = [
        case.id for case, result in policy_blocked if result["priority_score"] is None
    ]
    invariant_violations: list[str] = []
    for case_id in policy_blocked_ranked:
        result = scores_by_id[case_id]
        invariant_violations.append(
            f"policy-blocked case {case_id!r} was ranked with score "
            f"{result['priority_score']!r}; the Phase 8 invariant requires it to stay unranked"
        )
    for case, result in policy_blocked:
        if result["status"] != "unranked":
            invariant_violations.append(
                f"policy-blocked case {case.id!r} status is {result['status']!r}, "
                "expected 'unranked'"
            )
    invariant_holds = not invariant_violations

    # --- Abstentions -------------------------------------------------------
    # An *abstention* is a genuine "I cannot tell" (missing evidence, stale
    # analysis, an unknown dimension). A *policy refusal* is a deliberate,
    # deterministic "this must not be ranked at all". They are counted apart,
    # because only the second one is a policy decision.
    abstention_reasons: dict[str, int] = {}
    abstentions = 0
    policy_refusals = 0
    for _case, result in unranked:
        is_policy = POLICY_GATE_REASON in result["missing"]
        if is_policy:
            policy_refusals += 1
        else:
            abstentions += 1
        for reason in result["missing"]:
            abstention_reasons[reason] = abstention_reasons.get(reason, 0) + 1

    # --- Expected vs produced coverage -------------------------------------
    expected_ranked = [case for case in labelled.cases if case.expected_rank is not None]
    expected_ranked_ids = {case.id for case in expected_ranked}
    ranked_ids = {case.id for case, _ in ranked}
    expected_ranked_left_unranked = sorted(expected_ranked_ids - ranked_ids)
    policy_gated_ids = {case.id for case in labelled.cases if case.policy_gated}
    expected_ranked_left_unranked_by_policy = sorted(
        set(expected_ranked_left_unranked) & policy_gated_ids
    )
    expected_ranked_left_unranked_by_abstention = sorted(
        set(expected_ranked_left_unranked) - policy_gated_ids
    )
    expected_unranked_but_ranked = sorted(ranked_ids - expected_ranked_ids)

    # Comparable set: the heuristic ranked it *and* the reviewer ranked it.
    comparable = [(case, result) for case, result in ranked if case.expected_rank is not None]
    comparable.sort(key=lambda item: (-(item[0].expected_rank or 0), item[0].id))
    # Convert "rank 1 = best" into "larger = higher priority" so both sides of
    # the correlation point the same way.
    expected_priority = [float(-(case.expected_rank or 0)) for case, _ in comparable]
    produced_priority = [float(result["priority_score"] or 0) for _, result in comparable]

    correlation = kendall_tau_b(expected_priority, produced_priority)
    secondary = spearman_rho(expected_priority, produced_priority)

    # --- Mis-ranked cases and one-sided ties -------------------------------
    mis_ranked: dict[str, MisRankedCase] = {}
    tie_disagreements: list[TieDisagreement] = []
    for i in range(len(comparable)):
        for j in range(i + 1, len(comparable)):
            case_i, result_i = comparable[i]
            case_j, result_j = comparable[j]
            de = expected_priority[i] - expected_priority[j]
            dp = produced_priority[i] - produced_priority[j]
            if de == 0 and dp == 0:
                continue
            if dp == 0:
                first, second, ranks, scores = _canonical_pair(case_i, result_i, case_j, result_j)
                tie_disagreements.append(
                    TieDisagreement(
                        first=first,
                        second=second,
                        kind="produced_tie",
                        expected_ranks=ranks,
                        produced_scores=scores,
                    )
                )
                continue
            if de == 0:
                first, second, ranks, scores = _canonical_pair(case_i, result_i, case_j, result_j)
                tie_disagreements.append(
                    TieDisagreement(
                        first=first,
                        second=second,
                        kind="expected_tie",
                        expected_ranks=ranks,
                        produced_scores=scores,
                    )
                )
                continue
            if (de > 0) == (dp > 0):
                continue
            winner = case_i if dp > 0 else case_j
            loser = case_j if dp > 0 else case_i
            for first, second in ((winner, loser), (loser, winner)):
                entry = mis_ranked.get(first.id)
                if entry is None:
                    entry = MisRankedCase(
                        id=first.id,
                        title=first.title,
                        expected_rank=first.expected_rank,
                        produced_rank=None,
                        produced_score=result_i["priority_score"]
                        if first is case_i
                        else result_j["priority_score"],
                        reason="",
                    )
                    mis_ranked[first.id] = entry
                entry.conflicting_with.append(second.id)
    for entry in mis_ranked.values():
        entry.conflicting_with = sorted(set(entry.conflicting_with))

    # Produced ranking: score descending; items with an equal score share a
    # competition rank (1, 2, 2, 4) and are ordered by id for stability.
    produced_order = sorted(
        ranked, key=lambda item: (-(item[1]["priority_score"] or 0), item[0].id)
    )
    produced_rank_by_id: dict[str, int] = {
        case.id: 1
        + sum(
            1
            for _, other in ranked
            if (other["priority_score"] or 0) > (result["priority_score"] or 0)
        )
        for case, result in produced_order
    }
    for entry in mis_ranked.values():
        entry.produced_rank = produced_rank_by_id.get(entry.id)
        entry.reason = (
            f"ordered against the expected ordering "
            f"(expected rank {entry.expected_rank}, heuristic rank {entry.produced_rank})"
        )

    expected_tie_groups = _tie_groups(
        [case.id for case in expected_ranked],
        [float(case.expected_rank or 0) for case in expected_ranked],
    )
    produced_tie_groups = _tie_groups(
        [case.id for case, _ in ranked],
        [float(result["priority_score"] or 0) for case, result in ranked],
    )

    warning = SYNTHETIC_WARNING if labelled.synthetic else FIELD_WARNING

    return CalibrationReport(
        ranking_version=ranking_version,
        dataset_version=labelled.dataset_version,
        label_source=labelled.label_source,
        synthetic=labelled.synthetic,
        description=labelled.description,
        provenance_note=labelled.provenance_note,
        total_cases=len(labelled.cases),
        ranked_cases=len(ranked),
        unranked_cases=len(unranked),
        abstentions=abstentions,
        policy_refusals=policy_refusals,
        abstention_reasons=abstention_reasons,
        policy_blocked_cases=len(policy_blocked),
        policy_blocked_ranked=policy_blocked_ranked,
        policy_blocked_unranked=policy_blocked_unranked,
        expected_ranked_count=len(expected_ranked),
        expected_ranked_left_unranked=expected_ranked_left_unranked,
        expected_ranked_left_unranked_by_policy=expected_ranked_left_unranked_by_policy,
        expected_ranked_left_unranked_by_abstention=(expected_ranked_left_unranked_by_abstention),
        expected_unranked_but_ranked_by_heuristic=expected_unranked_but_ranked,
        correlation=correlation,
        secondary_correlation=secondary,
        mis_ranked=sorted(mis_ranked.values(), key=lambda case: case.id),
        tie_disagreements=sorted(
            tie_disagreements, key=lambda item: (item.kind, item.first, item.second)
        ),
        expected_tie_groups=expected_tie_groups,
        produced_tie_groups=produced_tie_groups,
        invariant_holds=invariant_holds,
        invariant_violations=invariant_violations,
        warning=warning,
    )


def compare_versions(labelled: LabelledSet) -> dict[str, Any]:
    """Measure every registered ranking version against the same labelled set.

    This is the regression comparison a future version bump must present: the
    candidate version's correlation is reported *next to* ``portfolio-v1``'s on
    the identical labelled set, so a change in agreement is visible rather
    than asserted.

    Only ``portfolio-v1`` is registered today. No candidate version is shipped,
    because no labelled set exists that could justify one (see
    ``docs/calibration.md``).
    """
    reports = {
        version: calibrate(labelled, ranking_version=version)
        for version in sorted(RANKING_VERSIONS)
    }
    baseline = reports.get(RANKING_VERSION)
    baseline_value = baseline.correlation.value if baseline else None
    comparison: dict[str, Any] = {
        "ranking_versions": sorted(RANKING_VERSIONS),
        "baseline_version": RANKING_VERSION,
        "versions": {},
        "synthetic": labelled.synthetic,
        "label_source": labelled.label_source,
        "warning": SYNTHETIC_WARNING if labelled.synthetic else FIELD_WARNING,
    }
    for version, report in reports.items():
        value = report.correlation.value
        comparison["versions"][version] = {
            "tau_b": value,
            "spearman_rho": report.secondary_correlation.value,
            "comparable_cases": report.correlation.n,
            "discordant_pairs": report.correlation.discordant_pairs,
            "mis_ranked_ids": [case.id for case in report.mis_ranked],
            "invariant_holds": report.invariant_holds,
            "delta_tau_b_vs_baseline": (
                None if value is None or baseline_value is None else value - baseline_value
            ),
        }
    return comparison


def format_report(report: CalibrationReport) -> str:
    """Render a calibration report as plain text for the CLI."""
    correlation = report.correlation
    value = "n/a" if correlation.value is None else f"{correlation.value:.4f}"
    lines = [
        "=" * 78,
        "RANKING CALIBRATION REPORT",
        "=" * 78,
        f"WARNING: {report.warning}",
        "",
        f"ranking version:   {report.ranking_version}",
        f"dataset version:   {report.dataset_version}",
        f"label source:      {report.label_source} "
        f"({'SYNTHETIC SELF-TEST' if report.synthetic else 'externally labelled'})",
        f"dataset:           {report.description}",
    ]
    if report.provenance_note:
        lines.append(f"provenance note:   {report.provenance_note}")
    lines += [
        "",
        "COUNTS",
        f"  cases in labelled set:            {report.total_cases}",
        f"  ranked by heuristic:               {report.ranked_cases}",
        f"  unranked by heuristic:             {report.unranked_cases}",
        f"  heuristic abstentions:             {report.abstentions} "
        "(cannot tell: missing evidence / stale analysis / unknown dimension)",
        f"  deterministic policy refusals:     {report.policy_refusals} "
        "(must not be ranked at all)",
    ]
    if report.abstention_reasons:
        for reason, count in sorted(report.abstention_reasons.items()):
            lines.append(f"    - {reason}: {count}")
    lines += [
        f"  expected-ranked items:             {report.expected_ranked_count}",
        "  expected-ranked but LEFT UNRANKED: "
        f"{len(report.expected_ranked_left_unranked)}"
        + (
            f" ({', '.join(report.expected_ranked_left_unranked)})"
            if report.expected_ranked_left_unranked
            else ""
        ),
        "    - because a policy gate forbids ranking (correct per Phase 8): "
        f"{len(report.expected_ranked_left_unranked_by_policy)}"
        + (
            f" ({', '.join(report.expected_ranked_left_unranked_by_policy)})"
            if report.expected_ranked_left_unranked_by_policy
            else ""
        ),
        "    - because the heuristic abstained (inputs unknown): "
        f"{len(report.expected_ranked_left_unranked_by_abstention)}"
        + (
            f" ({', '.join(report.expected_ranked_left_unranked_by_abstention)})"
            if report.expected_ranked_left_unranked_by_abstention
            else ""
        ),
        "  ranked but NOT expected-ranked:   "
        f"{len(report.expected_unranked_but_ranked_by_heuristic)}"
        + (
            f" ({', '.join(report.expected_unranked_but_ranked_by_heuristic)})"
            if report.expected_unranked_but_ranked_by_heuristic
            else ""
        ),
        "",
        "POLICY GUARD (Phase 8 invariant: policy-blocked / discovery-only stay unranked)",
        f"  policy-blocked cases:              {report.policy_blocked_cases}",
        f"  policy-blocked ranked (must be 0): {len(report.policy_blocked_ranked)}",
        f"  policy-blocked unranked:           {len(report.policy_blocked_unranked)}",
        f"  invariant holds:                   {report.invariant_holds}",
    ]
    for violation in report.invariant_violations:
        lines.append(f"    VIOLATION: {violation}")
    lines += [
        "",
        f"RANK CORRELATION ({correlation.method})",
        f"  value:                             {value}",
        f"  comparable cases:                  {correlation.n}",
        f"  concordant pairs:                  {correlation.concordant_pairs}",
        f"  discordant pairs:                  {correlation.discordant_pairs}",
        f"  pairs tied in expected ordering:   {correlation.tied_expected_pairs}",
        f"  pairs tied in produced ordering:   {correlation.tied_produced_pairs}",
        f"  pairs tied in both:                {correlation.tied_both_pairs}",
        f"  comparable pairs (C + D):          {correlation.comparable_pairs}",
        f"  note:                              {correlation.note}",
        "",
        f"  secondary check ({report.secondary_correlation.method}): "
        + (
            "n/a"
            if report.secondary_correlation.value is None
            else f"{report.secondary_correlation.value:.4f}"
        ),
        "",
        "TIE GROUPS (explicit, never silently broken)",
        "  expected ordering ties:",
    ]
    if report.expected_tie_groups:
        for group in report.expected_tie_groups:
            lines.append(f"    - {', '.join(group)}")
    else:
        lines.append("    none")
    lines.append("  produced ordering ties:")
    if report.produced_tie_groups:
        for group in report.produced_tie_groups:
            lines.append(f"    - {', '.join(group)}")
    else:
        lines.append("    none")
    lines += ["", f"MIS-RANKED PORTFOLIOS ({len(report.mis_ranked)})"]
    if report.mis_ranked:
        for case in report.mis_ranked:
            lines.append(
                f"  - {case.id} ({case.title}): expected rank {case.expected_rank}, "
                f"heuristic rank {case.produced_rank}, score {case.produced_score}; "
                f"conflicts with {', '.join(case.conflicting_with)}"
            )
    else:
        lines.append("  none")
    lines += ["", "TIE DISAGREEMENTS (excluded from the correlation by construction)"]
    if report.tie_disagreements:
        for item in report.tie_disagreements:
            lines.append(
                f"  - {item.kind}: {item.first} vs {item.second} "
                f"(expected ranks {item.expected_ranks[0]}/{item.expected_ranks[1]}, "
                f"produced scores {item.produced_scores[0]}/{item.produced_scores[1]})"
            )
    else:
        lines.append("  none")
    lines += ["", "=" * 78]
    return "\n".join(lines)
