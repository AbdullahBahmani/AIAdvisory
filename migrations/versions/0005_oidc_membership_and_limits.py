"""OIDC memberships and persistent per-principal rate limits.

Revision ID: 0005_oidc_limits
Revises: 0004_tenant_security
"""
from alembic import op
import sqlalchemy as sa

revision = "0005_oidc_limits"
down_revision = "0004_tenant_security"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("oidc_memberships",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("issuer", sa.String(400), nullable=False),
        sa.Column("subject", sa.String(200), nullable=False),
        sa.Column("organization_id", sa.String(36), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.UniqueConstraint("issuer", "subject", "organization_id", name="uq_oidc_member_identity_org"))
    op.create_index("ix_oidc_memberships_organization_id", "oidc_memberships", ["organization_id"])
    op.create_table("request_limits",
        sa.Column("identity_hash", sa.String(64), primary_key=True),
        sa.Column("minute_bucket", sa.Integer, primary_key=True),
        sa.Column("requests", sa.Integer, nullable=False))


def downgrade():
    op.drop_table("request_limits")
    op.drop_index("ix_oidc_memberships_organization_id", table_name="oidc_memberships")
    op.drop_table("oidc_memberships")
