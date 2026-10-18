"""Revision-bound collaboration, assignments and in-app notification inbox.

Revision ID: 0007_collaboration
Revises: 0006_concurrency
"""
from alembic import op
import sqlalchemy as sa

revision = "0007_collaboration"
down_revision = "0006_concurrency"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "reviewer_assignments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("assessment_id", sa.String(36), sa.ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("reviewer_subject", sa.String(200), nullable=False),
        sa.Column("assigned_by", sa.String(200), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("assessment_id", name="uq_reviewer_assignment_assessment"),
    )
    op.create_table(
        "assessment_comments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("assessment_id", sa.String(36), sa.ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("assessment_revision", sa.Integer(), nullable=False),
        sa.Column("author", sa.String(200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_assessment_comments_assessment_id", "assessment_comments", ["assessment_id"])
    op.create_table(
        "approval_votes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("assessment_id", sa.String(36), sa.ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("assessment_revision", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(20), nullable=False),
        sa.Column("decision", sa.String(30), nullable=False),
        sa.Column("actor", sa.String(200), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_approval_votes_assessment_id", "approval_votes", ["assessment_id"])
    op.create_table(
        "inbox_notifications",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("organization_id", sa.String(36), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("assessment_id", sa.String(36), sa.ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("recipient", sa.String(200), nullable=False),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("message", sa.String(300), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_inbox_notifications_organization_id", "inbox_notifications", ["organization_id"])
    op.create_index("ix_inbox_notifications_recipient", "inbox_notifications", ["recipient"])


def downgrade():
    op.drop_index("ix_inbox_notifications_recipient", table_name="inbox_notifications")
    op.drop_index("ix_inbox_notifications_organization_id", table_name="inbox_notifications")
    op.drop_table("inbox_notifications")
    op.drop_index("ix_approval_votes_assessment_id", table_name="approval_votes")
    op.drop_table("approval_votes")
    op.drop_index("ix_assessment_comments_assessment_id", table_name="assessment_comments")
    op.drop_table("assessment_comments")
    op.drop_table("reviewer_assignments")
