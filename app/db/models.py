"""Relational persistence models. Analysis snapshots stay distinct from user input."""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def new_id() -> str:
    return str(uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class AssessmentRow(Base):
    __tablename__ = "assessments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    organization: Mapped[str | None] = mapped_column(String(200))
    owner: Mapped[str | None] = mapped_column(String(200))
    business_problem: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="draft")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    value_inputs: Mapped[dict | None] = mapped_column(JSON)
    minimum_net_value_per_period: Mapped[str | None] = mapped_column(String(50))
    analysis_snapshot: Mapped[dict | None] = mapped_column(JSON)
    analysis_revision: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    workflows: Mapped[list["WorkflowRow"]] = relationship(cascade="all, delete-orphan", back_populates="assessment", order_by="WorkflowRow.created_at")
    answers: Mapped[list["AnswerRow"]] = relationship(cascade="all, delete-orphan", back_populates="assessment", order_by="AnswerRow.created_at")
    reviews: Mapped[list["ReviewRow"]] = relationship(cascade="all, delete-orphan", back_populates="assessment", order_by="ReviewRow.created_at")
    audit_events: Mapped[list["AuditRow"]] = relationship(cascade="all, delete-orphan", back_populates="assessment", order_by="AuditRow.created_at")
    ai_runs: Mapped[list["AIRunRow"]] = relationship(cascade="all, delete-orphan", back_populates="assessment", order_by="AIRunRow.created_at")


class WorkflowRow(Base):
    __tablename__ = "workflows"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    assessment_id: Mapped[str] = mapped_column(ForeignKey("assessments.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    assessment: Mapped[AssessmentRow] = relationship(back_populates="workflows")
    steps: Mapped[list["StepRow"]] = relationship(cascade="all, delete-orphan", back_populates="workflow", order_by="StepRow.position")


class StepRow(Base):
    __tablename__ = "workflow_steps"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(300))
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    owner: Mapped[str | None] = mapped_column(String(200))
    system: Mapped[str | None] = mapped_column(String(200))
    input_description: Mapped[str | None] = mapped_column(Text)
    output_description: Mapped[str | None] = mapped_column(Text)
    workflow: Mapped[WorkflowRow] = relationship(back_populates="steps")
    tasks: Mapped[list["TaskRow"]] = relationship(cascade="all, delete-orphan", back_populates="step", order_by="TaskRow.created_at")


class TaskRow(Base):
    __tablename__ = "tasks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    step_id: Mapped[str] = mapped_column(ForeignKey("workflow_steps.id", ondelete="CASCADE"), index=True)
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
    assessment_id: Mapped[str] = mapped_column(ForeignKey("assessments.id", ondelete="CASCADE"), index=True)
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
    assessment_id: Mapped[str] = mapped_column(ForeignKey("assessments.id", ondelete="CASCADE"), index=True)
    analysis_revision: Mapped[int] = mapped_column(Integer)
    reviewer_id: Mapped[str] = mapped_column(String(200))
    selected_category: Mapped[str] = mapped_column(String(60))
    rationale: Mapped[str] = mapped_column(Text)
    override_reason: Mapped[str | None] = mapped_column(Text)
    is_final: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    assessment: Mapped[AssessmentRow] = relationship(back_populates="reviews")


class AuditRow(Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    assessment_id: Mapped[str] = mapped_column(ForeignKey("assessments.id", ondelete="CASCADE"), index=True)
    event_type: Mapped[str] = mapped_column(String(70))
    actor: Mapped[str] = mapped_column(String(200), default="local_demo")
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    assessment: Mapped[AssessmentRow] = relationship(back_populates="audit_events")


class AIRunRow(Base):
    __tablename__ = "ai_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    assessment_id: Mapped[str] = mapped_column(ForeignKey("assessments.id", ondelete="CASCADE"), index=True)
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
