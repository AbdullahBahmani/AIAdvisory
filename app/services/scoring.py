"""Versioned, deterministic, explainable AI suitability scoring."""

from app.domain.schemas import DimensionScore, ScoreFactor, ScoreResult, TaskSignals

POLICY_VERSION = "rules-v2"

# Blueprint weights are advisory, not calibrated probabilities. Only observed
# signals contribute. A None means 'unknown' rather than false.
WEIGHTS: dict[str, int] = {
    "unstructured_content": 15,
    "classification_or_extraction": 10,
    "semantic_search": 10,
    "natural_language_generation": 10,
    "high_repetitive_volume": 10,
    "slow_manual_task": 10,
    "output_verifiable": 10,
    "data_available": 10,
    "clear_kpi": 5,
    "api_available": 5,
    "human_review_available": 5,
    "deterministic_rules": -20,
    "exact_math": -15,
    "zero_error_tolerance": -20,
    "no_reliable_data": -15,
    "very_low_volume": -10,
    "hard_to_evaluate": -15,
    "no_integration_path": -10,
}


def score_ai_suitability(signals: TaskSignals) -> ScoreResult:
    factors: list[ScoreFactor] = []
    known = 0
    total = len(WEIGHTS) + 1  # high_consequence + no reviewer compound gate
    score = 50
    for key, weight in WEIGHTS.items():
        value = getattr(signals, key)
        if value is None:
            continue
        known += 1
        if value:
            score += weight
            factors.append(ScoreFactor(signal=key, contribution=weight, observed=True))
    if signals.high_consequence is not None and signals.human_review_available is not None:
        known += 1
        if signals.high_consequence and not signals.human_review_available:
            score -= 25
            factors.append(
                ScoreFactor(
                    signal="high_consequence_without_review", contribution=-25, observed=True
                )
            )
    return ScoreResult(
        value=max(0, min(100, score)) if known else None,
        coverage=round(known / total, 4),
        status="advisory" if known else "not_assessable",
        factors=factors,
        policy_version=POLICY_VERSION,
    )


def _completeness(signals: TaskSignals, fields: tuple[str, ...]) -> DimensionScore:
    known = [key for key in fields if getattr(signals, key) is not None]
    yes = [key for key in known if getattr(signals, key)]
    coverage = round(len(known) / len(fields), 4)
    # A partial rubric is a provisional score, not a claim of full knowledge.
    status = (
        "not_assessable" if not known else "complete" if len(known) == len(fields) else "partial"
    )
    return DimensionScore(
        value=round(len(yes) / len(known) * 100) if known else None,
        coverage=coverage,
        status=status,
        observed_signals=known,
    )


def technical_feasibility(signals: TaskSignals) -> DimensionScore:
    return _completeness(
        signals, ("api_available", "auth_ready", "sandbox_available", "integration_path")
    )


def data_readiness(signals: TaskSignals) -> DimensionScore:
    return _completeness(
        signals,
        ("data_available", "data_quality_adequate", "permission_to_use_data", "governance_ready"),
    )
