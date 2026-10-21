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

#: The version of the financial-model arithmetic.  A result states the method
#: that produced it, so a change of method is visible in an old record.
NPV_METHOD_VERSION = "npv-v1"

#: The wording that keeps a modelled number from being read as a measurement.
NPV_DISCLAIMER = (
    "Advisory modelling from the stated inputs. No tax, inflation, adoption curve, "
    "financing cost or residual value is modelled, and this is not an observed ROI."
)


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


class ObservedMetricIn(BaseModel):
    """One measurement taken from a production system.

    An observed metric is a *measurement*, not a conclusion.  It records what
    a source system reported, when it reported it and where the report came
    from, so that a modelled assumption can later be checked against it.  It
    never changes a score, a rule hit or a recommendation by itself.
    """

    source_system: str = Field(min_length=2, max_length=120)
    metric_key: str = Field(min_length=2, max_length=120)
    value: Decimal = Field(ge=0, max_digits=18, decimal_places=4)
    unit: str = Field(min_length=1, max_length=60)
    observed_at: datetime
    period: Period | None = None
    source_reference: str = Field(min_length=5, max_length=300)
    sample_size: int | None = Field(default=None, ge=1, le=10_000_000)

    @field_validator("source_system", "metric_key", "unit", "source_reference")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A metric field cannot be blank")
        return value.strip()

    @field_validator("observed_at")
    @classmethod
    def observed_at_is_timezone_aware(cls, value: datetime) -> datetime:
        """A measurement without a timezone cannot be placed in time.

        A naive timestamp is rejected rather than silently read as UTC, so a
        source system cannot quietly shift every measurement by an offset.
        """
        if value.tzinfo is None:
            raise ValueError("observed_at must carry a timezone offset")
        return value


class ObservedMetricBatchIn(BaseModel):
    """A batch of measurements from one source system."""

    metrics: list[ObservedMetricIn] = Field(min_length=1, max_length=200)


class ObservedMetricView(ObservedMetricIn):
    id: str
    assessment_id: str
    assessment_revision: int
    created_by: str
    created_at: datetime
    stale: bool
    advisory_only: bool = True


class ObservedMetricBatchResult(BaseModel):
    """What an ingestion request did.

    ``already_recorded`` counts the measurements that were already stored.
    The count is reported so a caller can see that a repeated delivery was
    recognised instead of silently adding a second record.
    """

    stored: list[ObservedMetricView] = Field(default_factory=list)
    already_recorded: int = 0
    advisory_only: bool = True


#: The value-scenario inputs an observed metric may substantiate.  A metric
#: whose key is one of these names the quantity it measures, so a scenario
#: that cites it can be checked against it.
SCENARIO_INPUT_FIELDS = (
    "cases_per_period",
    "baseline_minutes",
    "target_minutes",
    "hourly_cost",
    "recurring_cost",
    "initial_cost",
)


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
    # The horizon and the discount rate are the two inputs a net present
    # value needs.  Both are optional: a scenario without them is perfectly
    # valid, and the financial model is then reported as *unavailable* with a
    # reason instead of being computed from an invented rate.
    horizon_periods: int | None = Field(default=None, ge=1, le=120)
    discount_rate_annual: Decimal | None = Field(
        default=None, ge=0, le=1, max_digits=6, decimal_places=6
    )
    discount_rate_source: str | None = Field(default=None, min_length=5, max_length=300)
    # The measurements that inform this scenario.  A citation is a claim by
    # the author that the scenario is based on a measurement; the API checks
    # that the cited measurements exist, belong to this assessment and are
    # not stale, and it reports a value that does not match.
    observed_metric_ids: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("observed_metric_ids")
    @classmethod
    def unique_metric_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("The same observed metric cannot be cited twice")
        return value

    @field_validator("name", "assumption_source")
    @classmethod
    def valid_source(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Name and assumption source cannot be blank")
        return value.strip()

    @model_validator(mode="after")
    def a_discount_rate_must_state_its_source(self) -> ValueScenarioIn:
        """A rate without a source looks authoritative and is not."""
        if self.discount_rate_annual is not None and not self.discount_rate_source:
            raise ValueError("A discount rate must state its source")
        if self.discount_rate_source is not None and self.discount_rate_annual is None:
            raise ValueError("A discount rate source needs a discount rate")
        if self.discount_rate_source is not None and not self.discount_rate_source.strip():
            raise ValueError("The discount rate source cannot be blank")
        return self


class FinancialModelView(BaseModel):
    """The advisory financial model of one value scenario.

    The status is *available* only when every input the measure needs was
    stated.  A missing input is reported as unavailable with a reason; it is
    never reported as zero.
    """

    status: Literal["available", "unavailable"]
    reason: str | None = None
    net_present_value: Decimal | None = None
    internal_rate_of_return: Decimal | None = None
    internal_rate_of_return_reason: str | None = None
    discounted_payback_periods: Decimal | None = None
    discounted_payback_reason: str | None = None
    periodic_discount_rate: Decimal | None = None
    horizon_periods: int | None = None
    warnings: list[str] = Field(default_factory=list)
    method_version: str = NPV_METHOD_VERSION
    disclaimer: str = NPV_DISCLAIMER
    advisory_only: bool = True


class ValueScenarioView(ValueScenarioIn):
    id: str
    assessment_revision: int
    created_by: str
    created_at: datetime
    stale: bool
    gross_savings: Decimal
    net_savings: Decimal
    payback_periods: Decimal | None
    financial_model: FinancialModelView
    citation_warnings: list[str] = Field(default_factory=list)
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


# --- Phase 9 P9-04: value sensitivity and scenario comparison contracts ---
#
# These contracts describe an *advisory* sensitivity envelope and a same-currency
# side-by-side comparison. They never carry a recommendation category and are never
# consumed by the rules engine, so a sensitivity result can never change an
# assessment outcome. Unknown stays unknown: an unavailable section is reported as
# unavailable, never as zero or an estimate.

SensitivityAssumption = Literal[
    "cases_per_period",
    "baseline_minutes",
    "target_minutes",
    "hourly_cost",
]


class SensitivityRangeIn(BaseModel):
    """An explicit, caller-supplied range for one assumption (e.g. from evidence)."""

    low: Decimal = Field(ge=0, max_digits=18, decimal_places=4)
    high: Decimal = Field(ge=0, max_digits=18, decimal_places=4)

    @model_validator(mode="after")
    def ordered(self):
        if self.high < self.low:
            raise ValueError("Range high must not be below low")
        return self


class SensitivityThresholdIn(BaseModel):
    """The minimum net value threshold applied to a sensitivity envelope.

    ``period`` and ``currency`` record the *basis* of the threshold. The assessment
    stores the threshold as a bare per-period amount without a currency, so the
    basis is always stated explicitly instead of being assumed silently.
    """

    minimum_net_value: Decimal = Field(ge=0, max_digits=18, decimal_places=4)
    basis: str = Field(min_length=3, max_length=300)
    period: Period | None = None
    currency_basis: str = Field(min_length=3, max_length=300)


class SensitivityScenarioIn(BaseModel):
    """A scenario whose assumptions may be unknown.

    Unlike :class:`ValueScenarioIn` every assumption is optional here so that a
    missing assumption can be reported as *unavailable* instead of being silently
    replaced by zero.
    """

    name: str = Field(min_length=1, max_length=120)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    period: Period
    cases_per_period: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=4)
    baseline_minutes: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=4)
    target_minutes: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=4)
    hourly_cost: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=4)
    recurring_cost: Decimal = Field(default=Decimal("0"), ge=0, max_digits=18, decimal_places=4)
    initial_cost: Decimal = Field(default=Decimal("0"), ge=0, max_digits=18, decimal_places=4)


class ExactRatioView(BaseModel):
    """An exact integer ratio. Strings keep every digit through JSON round-trips."""

    numerator: str
    denominator: str


class SensitivityBandView(BaseModel):
    low: Decimal
    high: Decimal
    source: Literal["modelling_convention_relative_band", "caller_supplied"]
    note: str


class SensitivityRangeView(BaseModel):
    low: Decimal
    high: Decimal


class SensitivityNetRangeView(BaseModel):
    low: Decimal
    high: Decimal
    low_at: Decimal
    high_at: Decimal


class SensitivityBoundaryView(BaseModel):
    status: Literal["available", "unavailable"]
    value: Decimal | None = None
    value_is_exact: bool = False
    exact_ratio: ExactRatioView | None = None
    direction: Literal["below", "above"] | None = None
    reason: str | None = None
    note: str | None = None


class SensitivityThresholdView(BaseModel):
    status: Literal["available", "unavailable"]
    minimum_net_value: Decimal | None = None
    basis: str
    currency_basis: str
    reason: str | None = None
    satisfied_at_base: bool | None = None
    satisfied_range: SensitivityRangeView | None = None
    boundary: SensitivityBoundaryView | None = None


class SensitivityDimensionView(BaseModel):
    assumption: SensitivityAssumption
    base_value: Decimal | None = None
    status: Literal["available", "unavailable"]
    reason: str | None = None
    band: SensitivityBandView | None = None
    net_savings_range: SensitivityNetRangeView | None = None
    breakeven: SensitivityBoundaryView
    threshold: SensitivityThresholdView


class SensitivityMethodView(BaseModel):
    name: str
    version: str
    band_fraction: Decimal
    description: str
    boundary_arithmetic: str
    rounding: str


class SensitivityBaseView(BaseModel):
    status: Literal["available", "unavailable"]
    reason: str | None = None
    gross_savings: Decimal | None = None
    net_savings: Decimal | None = None
    payback_periods: Decimal | None = None


class SensitivityEnvelopeView(BaseModel):
    scenario_id: str | None = None
    scenario_name: str
    currency: str
    period: Period
    assessment_id: str | None = None
    assessment_revision: int | None = None
    stale: bool = False
    base: SensitivityBaseView
    method: SensitivityMethodView
    dimensions: list[SensitivityDimensionView]
    warnings: list[str] = Field(default_factory=list)
    advisory_only: bool = True
    disclaimer: str


class ScenarioComparisonRowView(BaseModel):
    scenario_id: str
    name: str
    currency: str
    period: Period
    gross_savings: Decimal
    net_savings: Decimal
    payback_periods: Decimal | None
    stale: bool
    rank: int


class ScenarioComparisonView(BaseModel):
    currency: str
    period: Period
    comparable: bool
    conversion_applied: bool = False
    rows: list[ScenarioComparisonRowView]
    ranking: list[str]
    warnings: list[str] = Field(default_factory=list)
    advisory_only: bool = True
    disclaimer: str
