"""Deterministic risk scoring and mandatory baseline controls."""

from app.domain.enums import RiskBand
from app.domain.schemas import RiskInput, RiskResult


def band(score: int) -> RiskBand:
    if score <= 4:
        return RiskBand.LOW
    if score <= 9:
        return RiskBand.MEDIUM
    if score <= 16:
        return RiskBand.HIGH
    return RiskBand.CRITICAL


def evaluate_risks(inputs: list[RiskInput]) -> list[RiskResult]:
    results = []
    for risk in inputs:
        score = risk.impact * risk.likelihood
        classification = band(score)
        controls = ["Record accountable owner and evidence source"]
        if score >= 5:
            controls.append("Verify AI outputs against known ground truth")
        if score >= 10:
            controls.extend(["Human approval before consequential actions", "Audit and rollback plan"])
        if score >= 17:
            controls.append("No autonomous action until risk is explicitly mitigated")
        results.append(
            RiskResult(
                category=risk.category,
                description=risk.description,
                impact=risk.impact,
                likelihood=risk.likelihood,
                score=score,
                band=classification,
                required_controls=controls,
            )
        )
    return results
