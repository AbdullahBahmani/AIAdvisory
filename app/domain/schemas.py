"""Domain contracts. Unknown, zero and false are intentionally distinct."""

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.enums import (
    ActionType,
    DecisionType,
    FindingKind,
    RecommendationCategory,
    RiskBand,
    RiskCategory,
    RuleSeverity,
)


class DomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Organization(DomainModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    industry: str | None = None
    region: str | None = None


class EvidenceRecord(DomainModel):
    id: str = Field(min_length=1)
    task_id: str | None = None
    description: str = Field(min_length=1)
    source: str | None = None
    level: int = Field(ge=0, le=5)
    reviewed: bool = False


class RequiredControl(DomainModel):
    id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    mandatory: bool = True
    satisfied: bool = False


class FDEDecisionDraft(DomainModel):
    # A record *shape* for Phase 2, not a signoff operation or authorization.
    assessment_id: str = Field(min_length=1)
    reviewer_id: str = Field(min_length=1)
    selected_category: RecommendationCategory
    rationale: str = Field(min_length=1)
    override_reason: str | None = None
    is_final: bool = False


class TaskSignals(DomainModel):
    # Leave unknown signals at None; negative evidence is explicitly False.
    unstructured_content: bool | None = None
    classification_or_extraction: bool | None = None
    semantic_search: bool | None = None
    natural_language_generation: bool | None = None
    high_repetitive_volume: bool | None = None
    slow_manual_task: bool | None = None
    output_verifiable: bool | None = None
    data_available: bool | None = None
    clear_kpi: bool | None = None
    api_available: bool | None = None
    human_review_available: bool | None = None
    deterministic_rules: bool | None = None
    exact_math: bool | None = None
    zero_error_tolerance: bool | None = None
    no_reliable_data: bool | None = None
    very_low_volume: bool | None = None
    high_consequence: bool | None = None
    hard_to_evaluate: bool | None = None
    no_integration_path: bool | None = None
    requires_authoritative_lookup: bool | None = None
    action_irreversible: bool | None = None
    sensitive_data: bool | None = None
    provider_data_permitted: bool | None = None
    permission_to_use_data: bool | None = None
    auth_ready: bool | None = None
    sandbox_available: bool | None = None
    integration_path: bool | None = None
    data_quality_adequate: bool | None = None
    governance_ready: bool | None = None


class Task(DomainModel):
    id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=300)
    description: str = ""
    decision_type: DecisionType = DecisionType.UNKNOWN
    signals: TaskSignals = Field(default_factory=TaskSignals)
    actions: list[ActionType] = Field(default_factory=list)
    risks: list["RiskInput"] = Field(default_factory=list)


class WorkflowStep(DomainModel):
    id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=300)
    order: int = Field(ge=0)
    owner: str | None = None
    system: str | None = None
    input_description: str | None = None
    output_description: str | None = None
    tasks: list[Task] = Field(min_length=1)


class ValueInputs(DomainModel):
    cases_per_period: Decimal | None = Field(default=None, ge=0)
    baseline_minutes_per_case: Decimal | None = Field(default=None, ge=0)
    target_minutes_per_case: Decimal | None = Field(default=None, ge=0)
    loaded_hourly_cost: Decimal | None = Field(default=None, ge=0)
    proposed_operating_cost_per_period: Decimal | None = Field(default=None, ge=0)
    period: str = Field(default="month", min_length=1, max_length=50)

    @field_validator(
        "cases_per_period",
        "baseline_minutes_per_case",
        "target_minutes_per_case",
        "loaded_hourly_cost",
        "proposed_operating_cost_per_period",
    )
    @classmethod
    def finite_decimal(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and not value.is_finite():
            raise ValueError("Non-finite financial inputs are not allowed")
        return value


class RiskInput(DomainModel):
    category: RiskCategory
    description: str = Field(min_length=1, max_length=500)
    impact: int = Field(ge=1, le=5)
    likelihood: int = Field(ge=1, le=5)


class Answer(DomainModel):
    key: str = Field(min_length=1)
    value: str | float | bool | None = None
    kind: FindingKind = FindingKind.FACT
    source: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)


class DiscoveryRequest(DomainModel):
    answers: list[Answer] = Field(default_factory=list)


class Finding(DomainModel):
    key: str
    kind: FindingKind
    detail: str
    sources: list[str] = Field(default_factory=list)


class DiscoveryResult(DomainModel):
    next_question: "Question | None" = None
    findings: list[Finding] = Field(default_factory=list)
    missing_question_keys: list[str] = Field(default_factory=list)


class Question(DomainModel):
    key: str
    category: str
    text: str
    required: bool = True
    reason: str
    depends_on_key: str | None = None
    depends_on_value: bool | None = None


class AssessmentPreviewRequest(DomainModel):
    title: str = Field(min_length=1, max_length=300)
    steps: list[WorkflowStep] = Field(min_length=1)
    value_inputs: ValueInputs | None = None
    minimum_net_value_per_period: Decimal | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def require_unique_ids(self) -> "AssessmentPreviewRequest":
        step_ids = [s.id for s in self.steps]
        task_ids = [t.id for s in self.steps for t in s.tasks]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("Workflow step IDs must be unique")
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("Task IDs must be unique across the assessment")
        return self


class RuleHit(DomainModel):
    id: str
    severity: RuleSeverity
    reason: str
    required_control: str | None = None


class ScoreFactor(DomainModel):
    signal: str
    contribution: int
    observed: bool


class ScoreResult(DomainModel):
    value: int | None
    coverage: float = Field(ge=0, le=1)
    status: str
    factors: list[ScoreFactor] = Field(default_factory=list)
    policy_version: str


class DimensionScore(DomainModel):
    value: int | None
    coverage: float = Field(ge=0, le=1)
    status: str
    observed_signals: list[str] = Field(default_factory=list)


class ValueResult(DomainModel):
    period: str
    baseline_hours: Decimal | None
    potential_hours_saved: Decimal | None
    potential_gross_labor_savings: Decimal | None
    proposed_operating_cost: Decimal | None
    potential_net_savings: Decimal | None
    status: str
    assumptions: list[str]


class RiskResult(DomainModel):
    category: RiskCategory
    description: str
    impact: int
    likelihood: int
    score: int
    band: RiskBand
    required_controls: list[str]


class Recommendation(DomainModel):
    category: RecommendationCategory
    rationale: list[str]
    ai_role: str
    deterministic_role: str
    human_role: str
    required_controls: list[str]
    missing_evidence: list[str]
    alternatives: list[str]
    policy_version: str


class TaskAnalysis(DomainModel):
    task_id: str
    step_id: str
    ai_suitability: ScoreResult
    technical_feasibility: DimensionScore
    data_readiness: DimensionScore
    risks: list[RiskResult]
    rule_hits: list[RuleHit]
    recommendation: Recommendation


class AssessmentPreviewResponse(DomainModel):
    title: str
    policy_version: str
    advisory_only: bool = True
    value: ValueResult | None
    tasks: list[TaskAnalysis]
    warnings: list[str] = Field(default_factory=list)


Task.model_rebuild()
DiscoveryResult.model_rebuild()
