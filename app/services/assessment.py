"""Pure service orchestration. No LLM, database, side effects or irreversible actions."""

from app.domain.schemas import (
    AssessmentPreviewRequest,
    AssessmentPreviewResponse,
    TaskAnalysis,
)
from app.services.recommendation import recommend
from app.services.risk import evaluate_risks
from app.services.rule_engine import evaluate_rules
from app.services.scoring import POLICY_VERSION, data_readiness, score_ai_suitability, technical_feasibility
from app.services.value import calculate_value


def analyze(request: AssessmentPreviewRequest) -> AssessmentPreviewResponse:
    value = calculate_value(request.value_inputs) if request.value_inputs is not None else None
    analyses: list[TaskAnalysis] = []
    for step in sorted(request.steps, key=lambda item: item.order):
        for task in step.tasks:
            score = score_ai_suitability(task.signals)
            risks = evaluate_risks(task.risks)
            rules = evaluate_rules(task, value, request.minimum_net_value_per_period)
            recommendation = recommend(task, score, rules, risks)
            analyses.append(
                TaskAnalysis(
                    task_id=task.id,
                    step_id=step.id,
                    ai_suitability=score,
                    technical_feasibility=technical_feasibility(task.signals),
                    data_readiness=data_readiness(task.signals),
                    risks=risks,
                    rule_hits=rules,
                    recommendation=recommendation,
                )
            )
    warnings = ["Rules-only preview; all conclusions are advisory and require final FDE review."]
    if value is None or value.status == "insufficient_evidence":
        warnings.append("Business-value baseline incomplete; ROI cannot be claimed.")
    if any(a.ai_suitability.coverage < 1 for a in analyses):
        warnings.append("Some suitability signals are unknown; do not treat scores as confidence.")
    return AssessmentPreviewResponse(
        title=request.title, policy_version=POLICY_VERSION, value=value, tasks=analyses,
        warnings=warnings,
    )
