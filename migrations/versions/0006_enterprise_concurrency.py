"""Add optimistic locking to assessments.

Revision ID: 0006_concurrency
Revises: 0005_oidc_limits
"""

import sqlalchemy as sa
from alembic import op

revision = "0006_concurrency"
down_revision = "0005_oidc_limits"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "assessments",
        sa.Column("write_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
    )


def downgrade():
    op.drop_column("assessments", "write_version")
