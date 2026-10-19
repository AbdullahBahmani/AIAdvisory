"""Optional, operator-sourced AI cost estimate.

Revision ID: 0003_ai_cost
Revises: 0002_ai_runs
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_ai_cost"
down_revision = "0002_ai_runs"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("ai_runs", sa.Column("estimated_cost_usd", sa.String(60)))
    op.add_column("ai_runs", sa.Column("pricing_source", sa.String(200)))


def downgrade():
    op.drop_column("ai_runs", "pricing_source")
    op.drop_column("ai_runs", "estimated_cost_usd")
