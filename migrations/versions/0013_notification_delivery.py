"""External notification delivery: destinations and queued deliveries.

Revision ID: 0013_notification_delivery
Revises: 0010_index_and_constraint_hardening

Two tables are added:

* ``notification_destinations`` - the operator-configured destinations of one
  organization (a signed webhook URL or a path in the on-disk outbox). The
  unique constraint keeps one destination per organization, channel and target.
* ``notification_deliveries`` - the queued outbound messages. Every row is
  bound to one organization and to the notification it was generated from, and
  it carries the exact payload that was signed, so a retry is byte-identical.

The monotonic ``seq`` column is the ordering key of the dispatcher: it keeps
the messages of one destination in the order they were generated, so an
approval request is never overtaken by the decision that follows it.

No secret is stored here: the webhook signing secret comes from the
environment and is never written to the database.

The revision id is provisional: it is chained onto ``0010`` for this branch
and is expected to be re-pointed into the migration chain when the branch is
integrated. The tables and their names do not depend on the position in the
chain.
"""

import sqlalchemy as sa
from alembic import op

revision = "0013_notification_delivery"
down_revision = "0010_index_and_constraint_hardening"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "notification_destinations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("target", sa.String(length=500), nullable=False),
        sa.Column("events", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "channel", "target", name="uq_notification_destination_target"
        ),
    )
    op.create_index(
        "ix_notification_destinations_organization_id_enabled",
        "notification_destinations",
        ["organization_id", "enabled"],
    )

    op.create_table(
        "notification_deliveries",
        sa.Column("seq", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("notification_id", sa.String(length=36), nullable=False),
        sa.Column("destination_id", sa.String(length=36), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.String(length=40), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["notification_id"], ["inbox_notifications.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("seq"),
        sa.UniqueConstraint("id", name="uq_notification_delivery_id"),
    )
    op.create_index(
        "ix_notification_deliveries_destination_id_seq",
        "notification_deliveries",
        ["destination_id", "seq"],
    )
    op.create_index(
        "ix_notification_deliveries_notification_id",
        "notification_deliveries",
        ["notification_id"],
    )
    op.create_index(
        "ix_notification_deliveries_organization_id_status",
        "notification_deliveries",
        ["organization_id", "status"],
    )
    op.create_index(
        "ix_notification_deliveries_status_next_attempt_at",
        "notification_deliveries",
        ["status", "next_attempt_at"],
    )


def downgrade():
    op.drop_index(
        "ix_notification_deliveries_status_next_attempt_at",
        table_name="notification_deliveries",
    )
    op.drop_index(
        "ix_notification_deliveries_organization_id_status",
        table_name="notification_deliveries",
    )
    op.drop_index(
        "ix_notification_deliveries_notification_id", table_name="notification_deliveries"
    )
    op.drop_index(
        "ix_notification_deliveries_destination_id_seq", table_name="notification_deliveries"
    )
    op.drop_table("notification_deliveries")
    op.drop_index(
        "ix_notification_destinations_organization_id_enabled",
        table_name="notification_destinations",
    )
    op.drop_table("notification_destinations")
