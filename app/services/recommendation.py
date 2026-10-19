"""Conservative recommendation policy; no LLM or action execution involved."""

from app.domain.enums import RecommendationCategory as Category
from app.domain.schemas import Recommendation, RiskResult, RuleHit, ScoreResult, Task
from app.services.scoring import POLICY_VERSION


def recommend(
    task: Task, score: ScoreResult, hits: list[RuleHit], risks: list[RiskResult]
) -> Recommendation:
    flags = {hit.id for hit in hits}
    s = task.signals
    rationale: list[str] = []
    missing: list[str] = []
    controls = sorted(
        {hit.required_control for hit in hits if hit.required_control}
        | {item for risk in risks for item in risk.required_controls}
    )
    if not risks:
        missing.append("Risk likelihood/impact not assessed")
    if score.status == "not_assessable":
        missing.append("AI suitability signal evidence")
    if s.output_verifiable is None:
        missing.append("Independent output verification method")
    if s.data_available is None:
        missing.append("Source data availability")
    if s.high_consequence and s.human_review_available is None:
        missing.append("Human reviewer and approval process")

    if "HUM-001" in flags:
        category = Category.HUMAN_LED
        rationale.append("Final professional/accountability decision must remain with a human.")
    elif "PRIV-002" in flags:
        category = Category.MORE_DISCOVERY
        rationale.append("Processing data is unauthorized until permission is established.")
    elif "SAFE-001" in flags and not s.human_review_available:
        category = Category.HUMAN_LED
        rationale.append("Cannot safely automate a zero-tolerance, unverified outcome.")
    elif "EVID-001" in flags or "EVID-002" in flags or "EVID-003" in flags:
        category = Category.MORE_DISCOVERY
        rationale.append("Missing critical evidence prevents a defensible automation decision.")
    elif "PRIV-001" in flags:
        category = Category.MORE_DISCOVERY
        rationale.append(
            "Sensitive data cannot be sent to an external AI provider without permission."
        )
    elif (
        "ACT-001" in flags or "ACT-002" in flags or s.high_consequence
    ) and s.human_review_available is False:
        category = Category.HUMAN_LED
        rationale.append(
            "Consequential actions lack a qualified human reviewer; no automatic action allowed."
        )
    elif ("ACT-001" in flags or "ACT-002" in flags) and s.human_review_available is None:
        category = Category.MORE_DISCOVERY
        rationale.append("Required approval policy and human approver have not been confirmed.")
    elif any(r.score >= 17 for r in risks) and s.human_review_available is False:
        category = Category.HUMAN_LED
        rationale.append("Critical risk cannot be delegated without an available reviewer.")
    elif any(r.score >= 17 for r in risks) and s.human_review_available is None:
        category = Category.MORE_DISCOVERY
        rationale.append("Critical risk requires a named human review and mitigation process.")
    elif "VALUE-001" in flags:
        category = Category.HUMAN_LED
        rationale.append("The proposed project does not meet the evidenced value threshold.")
    elif "MATH-001" in flags or "DET-001" in flags:
        category = Category.TRADITIONAL_SOFTWARE
        rationale.append("Explicit business rules or exact arithmetic favor deterministic code.")
    elif (
        s.high_consequence
        or "ACT-001" in flags
        or "ACT-002" in flags
        or any(r.score >= 17 for r in risks)
    ):
        category = Category.AI_HUMAN_APPROVAL
        rationale.append("Any AI-generated suggestion must be separately reviewed and authorized.")
    elif s.requires_authoritative_lookup and (
        s.unstructured_content or s.classification_or_extraction or s.natural_language_generation
    ):
        category = Category.HYBRID
        rationale.append(
            "AI can interpret language; deterministic integrations must retrieve facts."
        )
    elif (
        s.data_available is True
        and score.value is not None
        and score.value >= 65
        and (s.output_verifiable is True or s.human_review_available is True)
    ):
        category = Category.STRONG_AI_CANDIDATE
        rationale.append(
            "Observed language-oriented signals and a review path support a bounded PoC."
        )
    else:
        category = Category.MORE_DISCOVERY
        rationale.append("Need a verifiable benefit and evidence before selecting an AI design.")

    rationale.append(
        f"Advisory suitability: {score.value if score.value is not None else 'N/A'} / 100."
    )
    if score.coverage < 1:
        rationale.append(
            f"Signal coverage is incomplete ({score.coverage:.0%}); "
            f"do not treat score as certainty."
        )

    if category == Category.TRADITIONAL_SOFTWARE:
        ai_role = "No LLM needed for this task."
    elif category in {Category.MORE_DISCOVERY, Category.HUMAN_LED}:
        ai_role = "No autonomous AI action; advisory drafting only if data policy permits."
    else:
        ai_role = (
            "Bounded interpretation, classification, extraction or drafting; verify before use."
        )
    deterministic_role = (
        "Validate inputs, calculate exact figures, enforce business policy "
        "and retrieve system-of-record facts."
    )
    human_role = (
        "An FDE makes the final deployment decision; required approvals remain human-controlled."
    )
    alternatives = ["Traditional rules/workflow automation", "Human-led process improvement"]
    if category != Category.HYBRID:
        alternatives.append("Hybrid AI extraction with deterministic policy gates")
    return Recommendation(
        category=category,
        rationale=rationale,
        ai_role=ai_role,
        deterministic_role=deterministic_role,
        human_role=human_role,
        required_controls=controls,
        missing_evidence=missing,
        alternatives=alternatives,
        policy_version=POLICY_VERSION,
    )
