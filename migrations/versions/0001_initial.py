"""Initial frozen relational schema for local assessment persistence.

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "assessments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("organization", sa.String(200)),
        sa.Column("owner", sa.String(200)),
        sa.Column("business_problem", sa.Text()),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("value_inputs", sa.JSON()),
        sa.Column("minimum_net_value_per_period", sa.String(50)),
        sa.Column("analysis_snapshot", sa.JSON()),
        sa.Column("analysis_revision", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "workflows",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "assessment_id",
            sa.String(36),
            sa.ForeignKey("assessments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_workflows_assessment_id", "workflows", ["assessment_id"])
    op.create_table(
        "workflow_steps",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "workflow_id",
            sa.String(36),
            sa.ForeignKey("workflows.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("owner", sa.String(200)),
        sa.Column("system", sa.String(200)),
        sa.Column("input_description", sa.Text()),
        sa.Column("output_description", sa.Text()),
    )
    op.create_index("ix_workflow_steps_workflow_id", "workflow_steps", ["workflow_id"])
    op.create_table(
        "tasks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "step_id",
            sa.String(36),
            sa.ForeignKey("workflow_steps.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("decision_type", sa.String(24), nullable=False),
        sa.Column("signals", sa.JSON(), nullable=False),
        sa.Column("actions", sa.JSON(), nullable=False),
        sa.Column("risks", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_tasks_step_id", "tasks", ["step_id"])
    op.create_table(
        "answers",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "assessment_id",
            sa.String(36),
            sa.ForeignKey("assessments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("key", sa.String(120), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("source", sa.String(200)),
        sa.Column("confidence", sa.Float()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_answers_assessment_id", "answers", ["assessment_id"])
    op.create_index("ix_answers_key", "answers", ["key"])
    op.create_table(
        "reviews",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "assessment_id",
            sa.String(36),
            sa.ForeignKey("assessments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("analysis_revision", sa.Integer(), nullable=False),
        sa.Column("reviewer_id", sa.String(200), nullable=False),
        sa.Column("selected_category", sa.String(60), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("override_reason", sa.Text()),
        sa.Column("is_final", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_reviews_assessment_id", "reviews", ["assessment_id"])
    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "assessment_id",
            sa.String(36),
            sa.ForeignKey("assessments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(70), nullable=False),
        sa.Column("actor", sa.String(200), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_audit_events_assessment_id", "audit_events", ["assessment_id"])


def downgrade():
    op.drop_table("audit_events")
    op.drop_table("reviews")
    op.drop_table("answers")
    op.drop_table("tasks")
    op.drop_table("workflow_steps")
    op.drop_table("workflows")
    op.drop_table("assessments")
