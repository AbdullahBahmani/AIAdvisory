"""Server-derived actor attribution for reviews and audit events.

Adds the verified organization and verification flag of the acting principal so
that a reviewer decision and its audit trail carry server-side provenance
instead of a client supplied label.

Revision ID: 0009_local_attribution
Revises: 0008_portfolio
"""
from alembic import op
import sqlalchemy as sa

revision = "0009_local_attribution"
down_revision = "0008_portfolio"
branch_labels = None
depends_on = None


def upgrade():
    # Reviews keep the organization of the verified reviewer; NULL means the
    # decision was recorded by the synthetic, never verified local demo identity.
    with op.batch_alter_table("reviews") as batch:
        batch.add_column(sa.Column("reviewer_organization_id", sa.String(36), nullable=True))
        batch.create_foreign_key(
            "fk_reviews_reviewer_organization_id_organizations",
            "organizations", ["reviewer_organization_id"], ["id"], ondelete="RESTRICT",
        )
        batch.create_index("ix_reviews_reviewer_organization_id", ["reviewer_organization_id"])
    # Audit events keep the organization of the actor and whether the server
    # verified that actor. Existing rows stay unverified by default.
    with op.batch_alter_table("audit_events") as batch:
        batch.add_column(sa.Column("actor_organization_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("actor_verified", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.create_foreign_key(
            "fk_audit_events_actor_organization_id_organizations",
            "organizations", ["actor_organization_id"], ["id"], ondelete="RESTRICT",
        )
        batch.create_index("ix_audit_events_actor_organization_id", ["actor_organization_id"])


def downgrade():
    with op.batch_alter_table("audit_events") as batch:
        batch.drop_index("ix_audit_events_actor_organization_id")
        batch.drop_constraint("fk_audit_events_actor_organization_id_organizations", type_="foreignkey")
        batch.drop_column("actor_verified")
        batch.drop_column("actor_organization_id")
    with op.batch_alter_table("reviews") as batch:
        batch.drop_index("ix_reviews_reviewer_organization_id")
        batch.drop_constraint("fk_reviews_reviewer_organization_id_organizations", type_="foreignkey")
        batch.drop_column("reviewer_organization_id")
