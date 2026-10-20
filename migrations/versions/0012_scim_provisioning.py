"""SCIM provisioning and approval-policy storage.

Revision ID: 0012_scim_provisioning
Revises: 0011_audit_chain

Adds the storage the automated provisioning path needs:

* ``scim_users`` — the SCIM attribute overlay of a provisioned membership.
* ``organization_audit_events`` — the organization-scoped audit ledger that
  records provisioning and approval-policy events (the assessment ledger
  requires an assessment, and these events are not about one).
* ``approval_policies`` — the per-organization approval policy and its version.
* ``approval_vote_bindings`` — which policy version applied to an approval
  vote, and whether a later policy change invalidated it.

No existing table is changed: this migration only adds tables, so a
deployment that never enables SCIM keeps the same data model as before.
"""

import sqlalchemy as sa
from alembic import op

revision = "0012_scim_provisioning"
down_revision = "0011_audit_chain"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "scim_users",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("membership_id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("external_id", sa.String(length=200), nullable=False),
        sa.Column("user_name", sa.String(length=200), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=True),
        sa.Column("given_name", sa.String(length=200), nullable=True),
        sa.Column("family_name", sa.String(length=200), nullable=True),
        sa.Column("emails", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["membership_id"], ["oidc_memberships.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("membership_id", name="uq_scim_users_membership_id"),
        sa.UniqueConstraint("organization_id", "user_name", name="uq_scim_users_org_user_name"),
    )
    op.create_index("ix_scim_users_organization_id", "scim_users", ["organization_id"])
    op.create_index(
        "ix_scim_users_organization_id_membership_id",
        "scim_users",
        ["organization_id", "membership_id"],
    )

    op.create_table(
        "organization_audit_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("event_type", sa.String(length=70), nullable=False),
        sa.Column("actor", sa.String(length=200), nullable=False),
        sa.Column("actor_organization_id", sa.String(length=36), nullable=True),
        sa.Column("actor_verified", sa.Boolean(), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_organization_id"], ["organizations.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_organization_audit_events_organization_id",
        "organization_audit_events",
        ["organization_id"],
    )
    op.create_index(
        "ix_organization_audit_events_actor_organization_id",
        "organization_audit_events",
        ["actor_organization_id"],
    )

    op.create_table(
        "approval_policies",
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("policy", sa.JSON(), nullable=False),
        sa.Column("updated_by", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("organization_id"),
    )

    op.create_table(
        "approval_vote_bindings",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("vote_id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("assessment_id", sa.String(length=36), nullable=False),
        sa.Column("stage", sa.String(length=20), nullable=False),
        sa.Column("actor_role", sa.String(length=20), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invalidation_reason", sa.String(length=200), nullable=True),
        sa.ForeignKeyConstraint(["assessment_id"], ["assessments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["vote_id"], ["approval_votes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("vote_id", name="uq_approval_vote_bindings_vote_id"),
    )
    op.create_index(
        "ix_approval_vote_bindings_assessment_id",
        "approval_vote_bindings",
        ["assessment_id"],
    )
    op.create_index(
        "ix_approval_vote_bindings_organization_id",
        "approval_vote_bindings",
        ["organization_id"],
    )
    op.create_index(
        "ix_approval_vote_bindings_assessment_id_organization_id",
        "approval_vote_bindings",
        ["assessment_id", "organization_id"],
    )


def downgrade():
    op.drop_index(
        "ix_approval_vote_bindings_assessment_id_organization_id",
        table_name="approval_vote_bindings",
    )
    op.drop_index("ix_approval_vote_bindings_organization_id", table_name="approval_vote_bindings")
    op.drop_index("ix_approval_vote_bindings_assessment_id", table_name="approval_vote_bindings")
    op.drop_table("approval_vote_bindings")
    op.drop_table("approval_policies")
    op.drop_index(
        "ix_organization_audit_events_actor_organization_id",
        table_name="organization_audit_events",
    )
    op.drop_index(
        "ix_organization_audit_events_organization_id", table_name="organization_audit_events"
    )
    op.drop_table("organization_audit_events")
    op.drop_index("ix_scim_users_organization_id_membership_id", table_name="scim_users")
    op.drop_index("ix_scim_users_organization_id", table_name="scim_users")
    op.drop_table("scim_users")
