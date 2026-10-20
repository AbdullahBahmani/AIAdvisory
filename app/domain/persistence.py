"""Validated persistence API contracts for the local Phase 2 workflow."""

from datetime import datetime
from decimal import Decimal

from pydantic import Field

from app.domain.enums import ActionType, DecisionType, RecommendationCategory
from app.domain.schemas import (
    Answer,
    AssessmentPreviewResponse,
    DomainModel,
    RiskInput,
    TaskSignals,
    ValueInputs,
)


class AssessmentCreate(DomainModel):
    title: str = Field(min_length=1, max_length=300)
    organization: str | None = Field(default=None, max_length=200)
    owner: str | None = Field(default=None, max_length=200)
    business_problem: str | None = Field(default=None, max_length=4000)
    value_inputs: ValueInputs | None = None
    minimum_net_value_per_period: Decimal | None = Field(default=None, ge=0)


class AssessmentPatch(DomainModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    organization: str | None = Field(default=None, max_length=200)
    owner: str | None = Field(default=None, max_length=200)
    business_problem: str | None = Field(default=None, max_length=4000)
    value_inputs: ValueInputs | None = None
    minimum_net_value_per_period: Decimal | None = Field(default=None, ge=0)


class WorkflowCreate(DomainModel):
    name: str = Field(min_length=1, max_length=300)


class StepCreate(DomainModel):
    name: str = Field(min_length=1, max_length=300)
    order: int = Field(ge=0)
    owner: str | None = Field(default=None, max_length=200)
    system: str | None = Field(default=None, max_length=200)
    input_description: str | None = None
    output_description: str | None = None


class StepPatch(DomainModel):
    name: str | None = Field(default=None, min_length=1, max_length=300)
    order: int | None = Field(default=None, ge=0)
    owner: str | None = Field(default=None, max_length=200)
    system: str | None = Field(default=None, max_length=200)
    input_description: str | None = None
    output_description: str | None = None


class TaskCreate(DomainModel):
    title: str = Field(min_length=1, max_length=300)
    description: str = ""
    decision_type: DecisionType = DecisionType.UNKNOWN
    signals: TaskSignals = Field(default_factory=TaskSignals)
    actions: list[ActionType] = Field(default_factory=list)
    risks: list[RiskInput] = Field(default_factory=list)


class TaskPatch(DomainModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = None
    decision_type: DecisionType | None = None
    signals: TaskSignals | None = None
    actions: list[ActionType] | None = None
    risks: list[RiskInput] | None = None


class StoredTask(TaskCreate):
    id: str


class StoredStep(StepCreate):
    id: str
    tasks: list[StoredTask] = Field(default_factory=list)


class StoredWorkflow(WorkflowCreate):
    id: str
    steps: list[StoredStep] = Field(default_factory=list)


class StoredAnswer(Answer):
    id: str
    created_at: datetime


class ReviewRequest(DomainModel):
    reviewer_id: str = Field(min_length=1, max_length=200)
    selected_category: RecommendationCategory
    rationale: str = Field(min_length=1, max_length=4000)
    override_reason: str | None = Field(default=None, max_length=4000)


class StoredReview(ReviewRequest):
    id: str
    analysis_revision: int
    is_final: bool
    created_at: datetime
    identity_unverified: bool = True


class AuditEntry(DomainModel):
    id: str
    event_type: str
    actor: str
    details: dict
    created_at: datetime
    sequence: int = 0


class AssessmentDetail(AssessmentCreate):
    id: str
    status: str
    revision: int
    analysis_revision: int | None = None
    analysis: AssessmentPreviewResponse | None = None
    workflows: list[StoredWorkflow] = Field(default_factory=list)
    answers: list[StoredAnswer] = Field(default_factory=list)
    reviews: list[StoredReview] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    local_demo_only: bool = True


class AssessmentSummary(DomainModel):
    id: str
    title: str
    status: str
    revision: int
    organization: str | None
    owner: str | None
    updated_at: datetime
