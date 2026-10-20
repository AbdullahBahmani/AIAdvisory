"""Strict Phase 8 portfolio contracts; forecasts and priority are advisory."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

EvidenceKind = Literal["baseline", "technical", "business", "risk", "other"]
Period = Literal["month", "quarter", "year"]
Direction = Literal["higher", "lower"]
Money = Decimal


class PortfolioProfileIn(BaseModel):
    strategic_alignment: int | None = Field(default=None, ge=1, le=5)
    urgency: int | None = Field(default=None, ge=1, le=5)
    delivery_effort: int | None = Field(default=None, ge=1, le=5)


class PortfolioProfileView(PortfolioProfileIn):
    updated_by: str
    updated_at: datetime


class EvidenceIn(BaseModel):
    kind: EvidenceKind
    description: str = Field(min_length=5, max_length=1000)
    source_reference: str = Field(min_length=3, max_length=300)

    @field_validator("description", "source_reference")
    @classmethod
    def strip_nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Evidence description and source cannot be blank")
        return value.strip()


class EvidenceView(EvidenceIn):
    id: str
    assessment_revision: int
    created_by: str
    verified_by: str | None
    verified_at: datetime | None
    created_at: datetime
    stale: bool


class ValueScenarioIn(BaseModel):
    name: str = Field(min_length=3, max_length=120)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    period: Period
    cases_per_period: Decimal = Field(ge=0, max_digits=18, decimal_places=4)
    baseline_minutes: Decimal = Field(ge=0, max_digits=18, decimal_places=4)
    target_minutes: Decimal = Field(ge=0, max_digits=18, decimal_places=4)
    hourly_cost: Decimal = Field(ge=0, max_digits=18, decimal_places=4)
    recurring_cost: Decimal = Field(ge=0, max_digits=18, decimal_places=4)
    initial_cost: Decimal = Field(ge=0, max_digits=18, decimal_places=4)
    assumption_source: str = Field(min_length=5, max_length=300)

    @field_validator("name", "assumption_source")
    @classmethod
    def valid_source(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Name and assumption source cannot be blank")
        return value.strip()


class ValueScenarioView(ValueScenarioIn):
    id: str
    assessment_revision: int
    created_by: str
    created_at: datetime
    stale: bool
    gross_savings: Decimal
    net_savings: Decimal
    payback_periods: Decimal | None
    advisory_only: bool = True


PROVENANCE_SCHEMA_VERSION = "provenance-v1"

ProvenanceStaleReason = Literal[
    "analysis_changed",
    "analysis_revision_changed",
    "evidence_changed",
    "evidence_added",
    "value_scenario_changed",
    "value_scenario_added",
    "policy_version_changed",
]

PROVENANCE_NOTE = (
    "Records the inputs the system presented when the decision was finalized. "
    "It is not proof that the decision was correct, and it is neither an approval "
    "nor a validation of the decision."
)


class AiProposalVersion(BaseModel):
    """Prompt and schema versions of an optional, advisory AI proposal.

    An AI proposal never feeds the deterministic analysis; its presence in the
    ledger states that a proposal existed, not that it was correct or accepted.
    """

    feature: str
    provider: str
    model: str
    prompt_version: str
    schema_version: str


class ProvenanceSnapshot(BaseModel):
    """Immutable record of the inputs behind one finalized human decision.

    The snapshot states what the system knew at decision time. It never changes a
    recommendation category, an approval outcome or the human finalization gate.
    """

    schema_version: str = PROVENANCE_SCHEMA_VERSION
    policy_version: str
    scoring_weight_version: str
    analysis_revision: int
    assessment_revision: int
    analysis_digest: str
    evidence_ids: list[str] = Field(default_factory=list)
    verified_evidence_ids: list[str] = Field(default_factory=list)
    value_scenario_id: str | None = None
    value_scenario_ids: list[str] = Field(default_factory=list)
    reviewer_subject: str
    reviewer_identity_verified: bool = False
    decision_maker_subject: str
    decision_maker_identity_verified: bool = False
    ai_contributed: bool = False
    ai_proposals: list[AiProposalVersion] = Field(default_factory=list)
    recorded_at: datetime


class ProvenanceView(ProvenanceSnapshot):
    """A stored ledger entry together with the drift state of its basis.

    ``stale`` mirrors the ``stale`` convention of the Phase 8 portfolio views: the
    recorded basis is no longer the current basis of the assessment.
    """

    assessment_id: str
    stale: bool = False
    stale_reasons: list[ProvenanceStaleReason] = Field(default_factory=list)
    advisory_only: bool = True
    note: str = PROVENANCE_NOTE


class PocPlanIn(BaseModel):
    kpi_name: str = Field(min_length=3, max_length=160)
    unit: str = Field(min_length=1, max_length=60)
    direction: Direction
    baseline: Decimal = Field(ge=0, max_digits=18, decimal_places=4)
    target: Decimal = Field(ge=0, max_digits=18, decimal_places=4)
    success_criteria: str = Field(min_length=5, max_length=1000)

    @field_validator("kpi_name", "unit", "success_criteria")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Metric fields cannot be blank")
        return value.strip()

    @model_validator(mode="after")
    def target_direction_is_meaningful(self):
        if self.direction == "higher" and self.target < self.baseline:
            raise ValueError("Higher-is-better target must not be below baseline")
        if self.direction == "lower" and self.target > self.baseline:
            raise ValueError("Lower-is-better target must not exceed baseline")
        return self


class PocObservationIn(BaseModel):
    actual: Decimal = Field(ge=0, max_digits=18, decimal_places=4)
    source_reference: str = Field(min_length=5, max_length=300)

    @field_validator("source_reference")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Observation source is required")
        return value.strip()


class PocObservationView(PocObservationIn):
    id: str
    created_by: str
    created_at: datetime
    target_met: bool


class PocPlanView(PocPlanIn):
    id: str
    assessment_revision: int
    status: Literal["planned", "in_progress", "measured"]
    created_by: str
    created_at: datetime
    stale: bool
    observations: list[PocObservationView]
    advisory_only: bool = True


class AttachmentView(BaseModel):
    """Provenance of one stored evidence attachment.

    `stale` follows the portfolio convention: an attachment is stale when the
    assessment revision it was bound to is no longer the current revision.
    """

    id: str
    assessment_id: str
    assessment_revision: int
    evidence_id: str | None
    display_name: str
    download_name: str
    content_type: str
    size_bytes: int
    sha256: str
    uploaded_by: str
    uploaded_at: datetime
    stale: bool
