"""Strict, feature-scoped proposal contracts; AI never writes authoritative domain state."""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import DecisionType, RiskCategory


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Feature(StrEnum):
    DISCOVERY = "discovery"
    WORKFLOW = "workflow"
    RISK = "risk"
    ARCHITECTURE = "architecture"
    EVALUATION = "evaluation"
    CHALLENGE = "challenge"


class AIRequest(StrictModel):
    # These are affirmative per-call attestations, not implicit consent from an env variable.
    send_to_provider: bool = False
    approved_non_sensitive_data: bool = False


class DiscoveryProposal(StrictModel):
    question: str = Field(min_length=5, max_length=350)
    why_ask: str = Field(min_length=5, max_length=500)
    evidence_needed: list[str] = Field(default_factory=list, max_length=5)


class TaskDraft(StrictModel):
    step_id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=3, max_length=300)
    description: str = Field(min_length=5, max_length=800)
    decision_type: DecisionType = DecisionType.UNKNOWN


class WorkflowProposal(StrictModel):
    suggested_tasks: list[TaskDraft] = Field(min_length=1, max_length=12)
    open_questions: list[str] = Field(default_factory=list, max_length=8)


class RiskDraft(StrictModel):
    task_id: str = Field(min_length=1, max_length=100)
    category: RiskCategory
    description: str = Field(min_length=5, max_length=500)
    impact: int = Field(ge=1, le=5)
    likelihood: int = Field(ge=1, le=5)
    proposed_controls: list[str] = Field(min_length=1, max_length=6)


class RiskProposal(StrictModel):
    suggested_risks: list[RiskDraft] = Field(min_length=1, max_length=12)
    missing_evidence: list[str] = Field(default_factory=list, max_length=8)


Pattern = Literal[
    "traditional_workflow", "classification", "extraction", "rag_with_citations",
    "ai_plus_deterministic_rules", "ai_plus_human_approval", "tool_using_agent",
    "more_discovery", "no_project",
]


class ArchitectureDraft(StrictModel):
    task_id: str = Field(min_length=1, max_length=100)
    pattern: Pattern
    rationale: str = Field(min_length=10, max_length=1000)
    ai_role: str = Field(max_length=600)
    deterministic_role: str = Field(min_length=5, max_length=600)
    human_approval_required: bool
    simpler_alternative: str = Field(min_length=5, max_length=600)
    limitations: list[str] = Field(default_factory=list, max_length=6)


class ArchitectureProposal(StrictModel):
    patterns: list[ArchitectureDraft] = Field(min_length=1, max_length=12)


class EvaluationMetric(StrictModel):
    name: str = Field(min_length=3, max_length=200)
    measurement: str = Field(min_length=5, max_length=500)
    proposed_threshold: str | None = Field(default=None, max_length=150)
    source_needed: str = Field(min_length=5, max_length=300)


class EvaluationProposal(StrictModel):
    metrics: list[EvaluationMetric] = Field(min_length=1, max_length=10)
    poc_scope: str = Field(min_length=10, max_length=800)
    go_no_go_checks: list[str] = Field(min_length=1, max_length=8)


class ChallengeProposal(StrictModel):
    objections: list[str] = Field(min_length=1, max_length=8)
    weak_assumptions: list[str] = Field(default_factory=list, max_length=8)
    simpler_options: list[str] = Field(min_length=1, max_length=6)
    evidence_that_changes_decision: list[str] = Field(min_length=1, max_length=8)


SCHEMAS: dict[Feature, type[StrictModel]] = {
    Feature.DISCOVERY: DiscoveryProposal,
    Feature.WORKFLOW: WorkflowProposal,
    Feature.RISK: RiskProposal,
    Feature.ARCHITECTURE: ArchitectureProposal,
    Feature.EVALUATION: EvaluationProposal,
    Feature.CHALLENGE: ChallengeProposal,
}

SCHEMA_VERSION = "3.0"
PROMPT_VERSION = "3.0"


class AIRunRecord(StrictModel):
    id: str
    assessment_id: str
    assessment_revision: int
    feature: Feature
    proposal: dict
    provider: str
    model: str
    prompt_version: str
    schema_version: str
    elapsed_ms: int = Field(ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    created_at: str
    advisory_only: bool = True
    stale: bool = False
