"""Portfolio prioritization evidence, scenarios and PoC results.

Revision ID: 0008_portfolio
Revises: 0007_collaboration
"""

import sqlalchemy as sa
from alembic import op

revision = "0008_portfolio"
down_revision = "0007_collaboration"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "opportunity_profiles",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "assessment_id",
            sa.String(36),
            sa.ForeignKey("assessments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("strategic_alignment", sa.Integer(), nullable=True),
        sa.Column("urgency", sa.Integer(), nullable=True),
        sa.Column("delivery_effort", sa.Integer(), nullable=True),
        sa.Column("updated_by", sa.String(200), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("assessment_id", name="uq_opportunity_profile_assessment"),
    )
    op.create_table(
        "opportunity_evidence",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "assessment_id",
            sa.String(36),
            sa.ForeignKey("assessments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("assessment_revision", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("source_reference", sa.String(300), nullable=False),
        sa.Column("created_by", sa.String(200), nullable=False),
        sa.Column("verified_by", sa.String(200), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_opportunity_evidence_assessment_id", "opportunity_evidence", ["assessment_id"]
    )
    op.create_table(
        "value_scenarios",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "assessment_id",
            sa.String(36),
            sa.ForeignKey("assessments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("assessment_revision", sa.Integer(), nullable=False),
        sa.Column("inputs", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_value_scenarios_assessment_id", "value_scenarios", ["assessment_id"])
    op.create_table(
        "poc_plans",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "assessment_id",
            sa.String(36),
            sa.ForeignKey("assessments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("assessment_revision", sa.Integer(), nullable=False),
        sa.Column("inputs", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("created_by", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_poc_plans_assessment_id", "poc_plans", ["assessment_id"])
    op.create_table(
        "poc_observations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "poc_id",
            sa.String(36),
            sa.ForeignKey("poc_plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("actual", sa.String(50), nullable=False),
        sa.Column("source_reference", sa.String(300), nullable=False),
        sa.Column("created_by", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_poc_observations_poc_id", "poc_observations", ["poc_id"])


def downgrade():
    op.drop_index("ix_poc_observations_poc_id", table_name="poc_observations")
    op.drop_table("poc_observations")
    op.drop_index("ix_poc_plans_assessment_id", table_name="poc_plans")
    op.drop_table("poc_plans")
    op.drop_index("ix_value_scenarios_assessment_id", table_name="value_scenarios")
    op.drop_table("value_scenarios")
    op.drop_index("ix_opportunity_evidence_assessment_id", table_name="opportunity_evidence")
    op.drop_table("opportunity_evidence")
    op.drop_table("opportunity_profiles")
