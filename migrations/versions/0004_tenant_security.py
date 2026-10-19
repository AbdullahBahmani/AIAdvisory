"""Provision organizations and API credentials; quarantine unscoped legacy data.

Revision ID: 0004_tenant_security
Revises: 0003_ai_cost
"""

import sqlalchemy as sa
from alembic import op

revision = "0004_tenant_security"
down_revision = "0003_ai_cost"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "organizations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "api_credentials",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "organization_id",
            sa.String(36),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("subject", sa.String(200), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_api_credentials_organization_id", "api_credentials", ["organization_id"])
    # Keep NULL on legacy assessments; never silently attach old records to a new tenant.
    with op.batch_alter_table("assessments") as batch:
        batch.add_column(sa.Column("organization_id", sa.String(36), nullable=True))
        batch.create_foreign_key(
            "fk_assessments_organization_id_organizations",
            "organizations",
            ["organization_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_index("ix_assessments_organization_id", ["organization_id"])
    with op.batch_alter_table("reviews") as batch:
        batch.add_column(
            sa.Column("identity_verified", sa.Boolean(), nullable=False, server_default=sa.false())
        )


def downgrade():
    with op.batch_alter_table("reviews") as batch:
        batch.drop_column("identity_verified")
    with op.batch_alter_table("assessments") as batch:
        batch.drop_index("ix_assessments_organization_id")
        batch.drop_column("organization_id")
    op.drop_index("ix_api_credentials_organization_id", table_name="api_credentials")
    op.drop_table("api_credentials")
    op.drop_table("organizations")
