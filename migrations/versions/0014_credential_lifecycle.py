"""Credential and membership lifecycle: explicit expiry, rotation and audit scope.

The credential lifecycle (ADR-0014 follow-up, planner item P5-01) needs three
things the credential table could not express before:

* a credential that never expires must be an **explicit** operator opt-in, so
  ``expires_at`` becomes nullable and is paired with an ``unlimited`` flag. A
  row with ``expires_at IS NULL`` and ``unlimited = false`` is refused by the
  authentication path: only a deliberately created standing key may live
  forever.
* a rotated-away credential may be kept alive for an explicitly bounded overlap
  window. ``overlap_until`` records that window and ``replaced_by_id`` records
  the rotation lineage for the access review.
* audit events that are about an organization rather than about one assessment
  (credential issuance, rotation, revocation, de-provisioning) need a tenant
  scope. Organization-scoped audit events live in the ``organization_audit_events``
  table added by revision 0012, so ``audit_events`` keeps its invariant that every
  row belongs to an assessment.

Revision ID: 0014_credential_lifecycle
Revises: 0013_notification_delivery
"""

import sqlalchemy as sa
from alembic import op

revision = "0014_credential_lifecycle"
down_revision = "0013_notification_delivery"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("api_credentials") as batch:
        # A credential may now state "no expiry", but only together with the
        # explicit `unlimited` opt-in added below.
        batch.alter_column(
            "expires_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=True,
        )
        batch.add_column(
            sa.Column("unlimited", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.add_column(sa.Column("overlap_until", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("replaced_by_id", sa.String(36), nullable=True))

    # Organization-scoped audit events live in their own table (organization_audit_events,
    # added by 0012), so audit_events keeps its invariant that every row belongs to an
    # assessment. That invariant is what the tamper-evident per-assessment chain relies
    # on, and keeping it means a credential lifecycle event can never be mistaken for
    # part of an assessment's decision history.


def downgrade():
    # The old schema cannot express an organization-scoped audit event (it
    # requires an assessment) and it cannot express a credential without an
    # expiry. Both are removed explicitly instead of letting the constraint
    # change fail halfway through the downgrade.
    # An unlimited credential becomes a far-future expiry: the old schema has
    # no "never expires", so the key keeps working until it is revoked.
    op.execute(
        "UPDATE api_credentials SET expires_at = '9999-12-31 23:59:59' WHERE expires_at IS NULL"
    )
    with op.batch_alter_table("api_credentials") as batch:
        batch.drop_column("replaced_by_id")
        batch.drop_column("overlap_until")
        batch.drop_column("unlimited")
        # A credential without an expiry cannot be represented any more.
        batch.alter_column(
            "expires_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=False,
        )
