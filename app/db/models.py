"""Relational persistence models. Analysis snapshots stay distinct from user input."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def new_id() -> str:
    return str(uuid4())


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class AssessmentRow(Base):
    __tablename__ = "assessments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), index=True
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    organization: Mapped[str | None] = mapped_column(String(200))
    owner: Mapped[str | None] = mapped_column(String(200))
    business_problem: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="draft")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    write_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    __mapper_args__ = {"version_id_col": write_version}
    value_inputs: Mapped[dict | None] = mapped_column(JSON)
    minimum_net_value_per_period: Mapped[str | None] = mapped_column(String(50))
    analysis_snapshot: Mapped[dict | None] = mapped_column(JSON)
    analysis_revision: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    workflows: Mapped[list["WorkflowRow"]] = relationship(
        cascade="all, delete-orphan", back_populates="assessment", order_by="WorkflowRow.created_at"
    )
    answers: Mapped[list["AnswerRow"]] = relationship(
        cascade="all, delete-orphan", back_populates="assessment", order_by="AnswerRow.created_at"
    )
    reviews: Mapped[list["ReviewRow"]] = relationship(
        cascade="all, delete-orphan", back_populates="assessment", order_by="ReviewRow.created_at"
    )
    ai_runs: Mapped[list["AIRunRow"]] = relationship(
        cascade="all, delete-orphan", back_populates="assessment", order_by="AIRunRow.created_at"
    )
    audit_events: Mapped[list["AuditRow"]] = relationship(
        cascade="all, delete-orphan", back_populates="assessment", order_by="AuditRow.created_at"
    )


class WorkflowRow(Base):
    __tablename__ = "workflows"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    assessment_id: Mapped[str] = mapped_column(
        ForeignKey("assessments.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    assessment: Mapped[AssessmentRow] = relationship(back_populates="workflows")
    steps: Mapped[list["StepRow"]] = relationship(
        cascade="all, delete-orphan", back_populates="workflow", order_by="StepRow.position"
    )


class StepRow(Base):
    __tablename__ = "workflow_steps"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workflow_id: Mapped[str] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(300))
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    owner: Mapped[str | None] = mapped_column(String(200))
    system: Mapped[str | None] = mapped_column(String(200))
    input_description: Mapped[str | None] = mapped_column(Text)
    output_description: Mapped[str | None] = mapped_column(Text)
    workflow: Mapped[WorkflowRow] = relationship(back_populates="steps")
    tasks: Mapped[list["TaskRow"]] = relationship(
        cascade="all, delete-orphan", back_populates="step", order_by="TaskRow.created_at"
    )


class TaskRow(Base):
    __tablename__ = "tasks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    step_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_steps.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    decision_type: Mapped[str] = mapped_column(String(24), default="unknown")
    signals: Mapped[dict] = mapped_column(JSON, default=dict)
    actions: Mapped[list] = mapped_column(JSON, default=list)
    risks: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    step: Mapped[StepRow] = relationship(back_populates="tasks")


class AnswerRow(Base):
    __tablename__ = "answers"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    assessment_id: Mapped[str] = mapped_column(
        ForeignKey("assessments.id", ondelete="CASCADE"), index=True
    )
    key: Mapped[str] = mapped_column(String(120), index=True)
    value: Mapped[dict] = mapped_column(JSON)
    kind: Mapped[str] = mapped_column(String(24))
    source: Mapped[str | None] = mapped_column(String(200))
    confidence: Mapped[float | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    assessment: Mapped[AssessmentRow] = relationship(back_populates="answers")


class ReviewRow(Base):
    __tablename__ = "reviews"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    assessment_id: Mapped[str] = mapped_column(
        ForeignKey("assessments.id", ondelete="CASCADE"), index=True
    )
    analysis_revision: Mapped[int] = mapped_column(Integer)
    reviewer_id: Mapped[str] = mapped_column(String(200))
    selected_category: Mapped[str] = mapped_column(String(60))
    rationale: Mapped[str] = mapped_column(Text)
    override_reason: Mapped[str | None] = mapped_column(Text)
    is_final: Mapped[bool] = mapped_column(default=False)
    identity_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    assessment: Mapped[AssessmentRow] = relationship(back_populates="reviews")


class AuditRow(Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    assessment_id: Mapped[str] = mapped_column(
        ForeignKey("assessments.id", ondelete="CASCADE"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(70))
    actor: Mapped[str] = mapped_column(String(200), default="local_demo")
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    assessment: Mapped[AssessmentRow] = relationship(back_populates="audit_events")


class OrganizationRow(Base):
    __tablename__ = "organizations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class ApiCredentialRow(Base):
    __tablename__ = "api_credentials"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class AIRunRow(Base):
    __tablename__ = "ai_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    assessment_id: Mapped[str] = mapped_column(
        ForeignKey("assessments.id", ondelete="CASCADE"), index=True
    )
    assessment_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    feature: Mapped[str] = mapped_column(String(30), nullable=False)
    proposal: Mapped[dict] = mapped_column(JSON, nullable=False)
    provider: Mapped[str] = mapped_column(String(60), nullable=False)
    model: Mapped[str] = mapped_column(String(150), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(30), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(30), nullable=False)
    elapsed_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    estimated_cost_usd: Mapped[str | None] = mapped_column(String(60))
    pricing_source: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    assessment: Mapped[AssessmentRow] = relationship(back_populates="ai_runs")


class OidcMembershipRow(Base):
    """Trusted operator-provisioned identity-to-organization mapping."""

    __tablename__ = "oidc_memberships"
    __table_args__ = (
        UniqueConstraint(
            "issuer", "subject", "organization_id", name="uq_oidc_member_identity_org"
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    issuer: Mapped[str] = mapped_column(String(400), nullable=False)
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class RequestLimitRow(Base):
    __tablename__ = "request_limits"
    identity_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    minute_bucket: Mapped[int] = mapped_column(Integer, primary_key=True)
    requests: Mapped[int] = mapped_column(Integer, nullable=False)


class ReviewerAssignmentRow(Base):
    """One assigned reviewer per assessment; organization derives from assessment."""

    __tablename__ = "reviewer_assignments"
    __table_args__ = (UniqueConstraint("assessment_id", name="uq_reviewer_assignment_assessment"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    assessment_id: Mapped[str] = mapped_column(
        ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False
    )
    reviewer_subject: Mapped[str] = mapped_column(String(200), nullable=False)
    assigned_by: Mapped[str] = mapped_column(String(200), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class AssessmentCommentRow(Base):
    __tablename__ = "assessment_comments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    assessment_id: Mapped[str] = mapped_column(
        ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    assessment_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    author: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class ApprovalRow(Base):
    __tablename__ = "approval_votes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    assessment_id: Mapped[str] = mapped_column(
        ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    assessment_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    stage: Mapped[str] = mapped_column(String(20), nullable=False)
    decision: Mapped[str] = mapped_column(String(30), nullable=False)
    actor: Mapped[str] = mapped_column(String(200), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class InboxNotificationRow(Base):
    __tablename__ = "inbox_notifications"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    assessment_id: Mapped[str] = mapped_column(
        ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False
    )
    recipient: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    message: Mapped[str] = mapped_column(String(300), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OpportunityProfileRow(Base):
    __tablename__ = "opportunity_profiles"
    __table_args__ = (UniqueConstraint("assessment_id", name="uq_opportunity_profile_assessment"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    assessment_id: Mapped[str] = mapped_column(
        ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False
    )
    strategic_alignment: Mapped[int | None] = mapped_column(Integer)
    urgency: Mapped[int | None] = mapped_column(Integer)
    delivery_effort: Mapped[int | None] = mapped_column(Integer)
    updated_by: Mapped[str] = mapped_column(String(200), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class OpportunityEvidenceRow(Base):
    __tablename__ = "opportunity_evidence"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    assessment_id: Mapped[str] = mapped_column(
        ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    assessment_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    source_reference: Mapped[str] = mapped_column(String(300), nullable=False)
    created_by: Mapped[str] = mapped_column(String(200), nullable=False)
    verified_by: Mapped[str | None] = mapped_column(String(200))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class ValueScenarioRow(Base):
    __tablename__ = "value_scenarios"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    assessment_id: Mapped[str] = mapped_column(
        ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    assessment_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    inputs: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_by: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class PocPlanRow(Base):
    __tablename__ = "poc_plans"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    assessment_id: Mapped[str] = mapped_column(
        ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    assessment_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    inputs: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="planned")
    created_by: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class PocObservationRow(Base):
    __tablename__ = "poc_observations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    poc_id: Mapped[str] = mapped_column(
        ForeignKey("poc_plans.id", ondelete="CASCADE"), nullable=False, index=True
    )
    actual: Mapped[str] = mapped_column(String(50), nullable=False)
    source_reference: Mapped[str] = mapped_column(String(300), nullable=False)
    created_by: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
