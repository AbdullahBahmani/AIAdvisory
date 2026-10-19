"""Add append-only validated AI proposal records; never authoritative assessment state.

Revision ID: 0002_ai_runs
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op

revision = "0002_ai_runs"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "assessment_id",
            sa.String(36),
            sa.ForeignKey("assessments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("assessment_revision", sa.Integer, nullable=False),
        sa.Column("feature", sa.String(30), nullable=False),
        sa.Column("proposal", sa.JSON, nullable=False),
        sa.Column("provider", sa.String(60), nullable=False),
        sa.Column("model", sa.String(150), nullable=False),
        sa.Column("prompt_version", sa.String(30), nullable=False),
        sa.Column("schema_version", sa.String(30), nullable=False),
        sa.Column("elapsed_ms", sa.Integer, nullable=False),
        sa.Column("input_tokens", sa.Integer),
        sa.Column("output_tokens", sa.Integer),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ai_runs_assessment_id", "ai_runs", ["assessment_id"])


def downgrade() -> None:
    op.drop_index("ix_ai_runs_assessment_id", table_name="ai_runs")
    op.drop_table("ai_runs")
