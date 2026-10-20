"""Index and constraint hardening for the hot tenant-scoped read paths.

Revision ID: 0010_index_and_constraint_hardening
Revises: 0008_portfolio
"""

from alembic import op

revision = "0010_index_and_constraint_hardening"
down_revision = "0008_portfolio"
branch_labels = None
depends_on = None

STEP_ORDER_CONSTRAINT = "uq_workflow_steps_workflow_position"

#: Alembic stores the revision id in ``alembic_version.version_num``, which it
#: creates as VARCHAR(32). This revision id is 36 characters long, so the
#: column is widened before Alembic writes the new revision into it.
VERSION_COLUMN_WIDTH = 64


def _resize_version_column(width: int) -> None:
    """Widen (or narrow) ``alembic_version.version_num`` to ``width``."""
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(f"ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR({width})")
    elif dialect == "mysql":
        op.execute(f"ALTER TABLE alembic_version MODIFY version_num VARCHAR({width})")
    # SQLite does not enforce VARCHAR lengths, so the column needs no change.


def upgrade():
    _resize_version_column(VERSION_COLUMN_WIDTH)

    # Finding 1: `inbox_notifications.assessment_id` was the only foreign key
    # without a supporting index, so deleting a parent assessment scanned the
    # whole notification table.
    op.create_index(
        "ix_inbox_notifications_assessment_id", "inbox_notifications", ["assessment_id"]
    )

    # Finding 2: the tenant-scoped assessment listing (and the portfolio
    # listing that reuses it) filtered on organization_id and ordered by
    # created_at. The composite index serves the filter and the order, which
    # removes the top-N heapsort.
    op.create_index(
        "ix_assessments_organization_id_created_at",
        "assessments",
        ["organization_id", "created_at"],
    )

    # Finding 3: the in-app inbox filtered on organization_id + recipient and
    # ordered by created_at; two single-column indexes could not serve it.
    op.create_index(
        "ix_inbox_notifications_organization_id_recipient_created_at",
        "inbox_notifications",
        ["organization_id", "recipient", "created_at"],
    )

    # Finding 4: membership lookups on the request path filter
    # organization_id + subject. Only organization_id was indexed.
    op.create_index(
        "ix_oidc_memberships_organization_id_subject",
        "oidc_memberships",
        ["organization_id", "subject"],
    )
    op.create_index(
        "ix_api_credentials_organization_id_subject",
        "api_credentials",
        ["organization_id", "subject"],
    )

    # Finding 5: `ix_answers_key` is not used by any application query. Answers
    # are only ever read through the assessment relationship, which is served
    # by ix_answers_assessment_id. The index only cost write throughput.
    op.drop_index("ix_answers_key", table_name="answers")

    # Finding 6: step order was only unique in Python. The database now
    # rejects a duplicate (workflow_id, position) as well, so two concurrent
    # writers cannot both claim the same order. SQLite cannot ALTER TABLE ADD
    # CONSTRAINT, so the statement is written through batch mode there.
    with op.batch_alter_table("workflow_steps") as batch:
        batch.create_unique_constraint(STEP_ORDER_CONSTRAINT, ["workflow_id", "position"])


def downgrade():
    with op.batch_alter_table("workflow_steps") as batch:
        batch.drop_constraint(STEP_ORDER_CONSTRAINT, type_="unique")
    op.create_index("ix_answers_key", "answers", ["key"])
    op.drop_index("ix_api_credentials_organization_id_subject", table_name="api_credentials")
    op.drop_index("ix_oidc_memberships_organization_id_subject", table_name="oidc_memberships")
    op.drop_index(
        "ix_inbox_notifications_organization_id_recipient_created_at",
        table_name="inbox_notifications",
    )
    op.drop_index("ix_inbox_notifications_assessment_id", table_name="inbox_notifications")
    op.drop_index("ix_assessments_organization_id_created_at", table_name="assessments")
    # The widened `alembic_version.version_num` column is kept: Alembic writes
    # the *previous* revision id into it only after this migration body ran, so
    # narrowing it here would fail on the row that still holds this revision.
