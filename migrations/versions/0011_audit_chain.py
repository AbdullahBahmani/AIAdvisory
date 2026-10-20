"""Add the tamper-evident audit chain columns.

Revision ID: 0011_audit_chain
Revises: 0010_index_and_constraint_hardening

Adds ``sequence`` and ``chain_hash`` to ``audit_events``. The sequence is the row's position
in its assessment's audit chain; the chain hash is computed over the row's canonical content
chained onto the previous row's hash. Together they make an in-place alteration detectable.

The companion out-of-database high-water mark (a local file, not a schema object) is what
makes a *rollback* detectable, because a restore rolls back the database but not that file.
"""

import sqlalchemy as sa
from alembic import op

revision = "0011_audit_chain"
down_revision = "0010_index_and_constraint_hardening"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("audit_events") as batch:
        batch.add_column(
            sa.Column("sequence", sa.Integer(), nullable=False, server_default=sa.text("0"))
        )
        batch.add_column(
            sa.Column("chain_hash", sa.String(length=64), nullable=False, server_default="")
        )
        batch.create_unique_constraint(
            "uq_audit_events_assessment_sequence", ["assessment_id", "sequence"]
        )
    # Existing rows have no chain history to reference, so they start at sequence 0 with an
    # empty hash. They are not part of a verifiable chain and the application treats a chain
    # that does not start at 1 as reporting a gap rather than pretending it is intact.
    op.execute("UPDATE audit_events SET sequence = 0, chain_hash = ''")


def downgrade():
    with op.batch_alter_table("audit_events") as batch:
        batch.drop_constraint("uq_audit_events_assessment_sequence", type_="unique")
        batch.drop_column("chain_hash")
        batch.drop_column("sequence")
