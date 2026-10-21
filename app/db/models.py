"""Relational persistence models. Analysis snapshots stay distinct from user input."""

from datetime import UTC, datetime
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    object_session,
    relationship,
)

#: Name of the database-level step-order uniqueness constraint.
STEP_ORDER_CONSTRAINT = "uq_workflow_steps_workflow_position"

#: Message the API returns when a step order is already used in its workflow.
#: It is intentionally identical to the friendly pre-check in
#: ``app/api/assessments.py`` so both paths answer with the same 409.
STEP_ORDER_CONFLICT_DETAIL = "Step order already used in this workflow"


def new_id() -> str:
    return str(uuid4())


def utcnow() -> datetime:
    return datetime.now(UTC)


def _is_step_order_violation(error: BaseException) -> bool:
    """True when ``error`` is the step-order uniqueness violation.

    PostgreSQL names the constraint in the driver error, SQLite reports the
    offending columns instead.
    """
    text = str(error)
    if STEP_ORDER_CONSTRAINT in text:
        return True
    return "workflow_steps.workflow_id" in text and "workflow_steps.position" in text


@event.listens_for(Dialect, "handle_error")
def _translate_step_order_violation(context) -> None:
    """Answer a lost step-order race with 409 instead of a raw IntegrityError.

    The API validates the step order before writing, so this only fires when a
    concurrent writer wins the race and the database rejects the duplicate. The
    client gets the same friendly 4xx message as the pre-check; no constraint
    name, SQL statement or parameter value is leaked to the caller.
    """
    original = context.original_exception
    sql_error = context.sqlalchemy_exception
    if not isinstance(original, IntegrityError) and not isinstance(sql_error, IntegrityError):
        return None
    # ``original`` is the driver error; the SQLAlchemy wrapper keeps it in ``orig``.
    driver_error = getattr(original, "orig", None) or original
    if not _is_step_order_violation(driver_error):
        return None
    return HTTPException(status_code=409, detail=STEP_ORDER_CONFLICT_DETAIL)


class Base(DeclarativeBase):
    pass


class AssessmentRow(Base):
    __tablename__ = "assessments"
    # Tenant-scoped listing order (`GET /api/v1/assessments`,
    # `GET /api/v1/portfolio/opportunities`): filter organization_id, order by
    # created_at. The composite index removes the top-N heapsort.
    __table_args__ = (
        Index(
            "ix_assessments_organization_id_created_at",
            "organization_id",
            "created_at",
        ),
    )

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
    # Step order is unique per workflow. The application validates this before
    # writing; the database enforces it so concurrent writers cannot both win.
    __table_args__ = (UniqueConstraint("workflow_id", "position", name=STEP_ORDER_CONSTRAINT),)
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
    key: Mapped[str] = mapped_column(String(120))
    value: Mapped[dict] = mapped_column(JSON)
    kind: Mapped[str] = mapped_column(String(24))
    source: Mapped[str | None] = mapped_column(String(200))
    # Declared explicitly: SQLAlchemy 2.1 infers DOUBLE for a bare Python float,
    # while migration 0001 created FLOAT. Leaving it inferred made `alembic check`
    # report schema drift on a clean database after an ORM upgrade.
    confidence: Mapped[float | None] = mapped_column(Float())
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
    reviewer_organization_id: Mapped[str | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), index=True
    )
    selected_category: Mapped[str] = mapped_column(String(60))
    rationale: Mapped[str] = mapped_column(Text)
    override_reason: Mapped[str | None] = mapped_column(Text)
    is_final: Mapped[bool] = mapped_column(default=False)
    identity_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    assessment: Mapped[AssessmentRow] = relationship(back_populates="reviews")


class AuditRow(Base):
    """One append-only audit event.

    Every row belongs to exactly one assessment. Organization-scoped events -
    credential and membership lifecycle changes, SCIM provisioning, approval
    policy changes - live in their own ``organization_audit_events`` table, so
    that this table keeps the invariant the tamper-evident per-assessment chain
    relies on: a row here is always part of one assessment's decision history.
    """

    __tablename__ = "audit_events"
    __table_args__ = (
        UniqueConstraint("assessment_id", "sequence", name="uq_audit_events_assessment_sequence"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    assessment_id: Mapped[str] = mapped_column(
        ForeignKey("assessments.id", ondelete="CASCADE"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(70))
    actor: Mapped[str] = mapped_column(String(200), default="local_demo")
    actor_organization_id: Mapped[str | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), index=True
    )
    actor_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    # Per-assessment position in the tamper-evident audit chain, and the hash over
    # this row's canonical content chained onto the previous row's hash. Together
    # they make an in-place alteration detectable; the out-of-database high-water
    # mark makes a rollback (a restore) detectable. See app/services/audit_chain.py.
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    chain_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    assessment: Mapped["AssessmentRow | None"] = relationship(back_populates="audit_events")


class OrganizationRow(Base):
    __tablename__ = "organizations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class ApiCredentialRow(Base):
    """One stored API credential.

    The lifecycle fields carry the policy of ``docs/credential-lifecycle.md``:

    * ``expires_at`` is the expiry. ``NULL`` means *never expires* and is only
      accepted when ``unlimited`` is True, so a standing key is always an
      explicit operator opt-in and never a default.
    * ``revoked_at`` is set the moment the credential stops working.
    * ``overlap_until`` marks a credential that was rotated away but is still
      accepted for an explicitly bounded overlap window. It expires by itself.
    * ``replaced_by_id`` records the rotation lineage for the access review.
    """

    __tablename__ = "api_credentials"
    # Membership lookup on the request path: organization_id + subject.
    __table_args__ = (
        Index("ix_api_credentials_organization_id_subject", "organization_id", "subject"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    unlimited: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    overlap_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replaced_by_id: Mapped[str | None] = mapped_column(String(36))
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
        # Membership lookup on the request path: organization_id + subject.
        Index("ix_oidc_memberships_organization_id_subject", "organization_id", "subject"),
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
    # Inbox listing: organization_id + recipient, ordered by created_at.
    __table_args__ = (
        Index(
            "ix_inbox_notifications_organization_id_recipient_created_at",
            "organization_id",
            "recipient",
            "created_at",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    assessment_id: Mapped[str] = mapped_column(
        ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False, index=True
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


class ObservedMetricRow(Base):
    """One measurement ingested from a production system.

    The measurement is stored exactly as the source system reported it.  It
    is never rewritten, never aggregated and never substituted for a modelled
    assumption: a scenario cites it, and the citation is checked separately.
    """

    __tablename__ = "observed_metrics"
    __table_args__ = (
        # A source system may push the same measurement twice.  The unique
        # key makes the second push a no-op instead of a second record, so a
        # retried delivery cannot inflate the evidence.
        UniqueConstraint(
            "assessment_id",
            "source_system",
            "metric_key",
            "observed_at",
            name="uq_observed_metric_measurement",
        ),
        Index(
            "ix_observed_metrics_assessment_observed",
            "assessment_id",
            "metric_key",
            "observed_at",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    assessment_id: Mapped[str] = mapped_column(
        ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    assessment_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    source_system: Mapped[str] = mapped_column(String(120), nullable=False)
    metric_key: Mapped[str] = mapped_column(String(120), nullable=False)
    value: Mapped[str] = mapped_column(String(50), nullable=False)
    unit: Mapped[str] = mapped_column(String(60), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period: Mapped[str | None] = mapped_column(String(24))
    source_reference: Mapped[str] = mapped_column(String(300), nullable=False)
    sample_size: Mapped[int | None] = mapped_column(Integer)
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


@event.listens_for(AuditRow, "before_insert")
def _stamp_audit_actor(_mapper, _connection, target: AuditRow) -> None:
    """Attribute audit events to the server-side principal, never to request input.

    The authenticated principal of the current session is the only accepted actor.
    Rows written outside a request (operator scripts) keep their explicit actor
    and stay marked unverified: an operator command is not a human identity
    proof, so it must never look like a verified principal.
    """
    session = object_session(target)
    principal = session.info.get("principal") if session is not None else None
    if principal is None:
        target.actor_verified = False
        return
    target.actor = principal.subject
    target.actor_organization_id = principal.organization_id
    target.actor_verified = bool(principal.verified)
