"""Observed metrics: measurements ingested from a production system.

Planner item P8-10 listed *metrics ingestion from production systems* as
undelivered.  The gap was that every number in the product was either an
assumption the author typed or a PoC outcome somebody measured by hand.  There
was no way to bring in a measurement that a production system had already
taken.

This revision adds the table that holds such a measurement:

* ``observed_metrics`` stores one measurement exactly as the source system
  reported it: the source system, the key, the value, the unit, the moment it
  was observed, the period it covers and where the report came from.
* The measurement is **revision-bound** and **immutable**.  It is bound to the
  assessment revision that was current when it was ingested, and it is never
  rewritten afterwards, so a scenario that cites it can always be checked
  against the value that was actually reported.
* A unique key on (assessment, source system, metric key, observed moment)
  makes a repeated delivery a no-op rather than a second record.  A retried
  push must not be able to inflate the evidence.
* Nothing in this table feeds the rules engine, the scoring or the
  recommendation precedence.  A measurement is evidence about a quantity, not a
  conclusion, and the product's rule that *rules enforce and humans decide*
  must not be weakened by a number arriving from a machine.

Revision ID: 0015_observed_metrics
Revises: 0014_credential_lifecycle
"""

import sqlalchemy as sa
from alembic import op

revision = "0015_observed_metrics"
down_revision = "0014_credential_lifecycle"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "observed_metrics",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("assessment_id", sa.String(36), nullable=False),
        sa.Column("assessment_revision", sa.Integer(), nullable=False),
        sa.Column("source_system", sa.String(120), nullable=False),
        sa.Column("metric_key", sa.String(120), nullable=False),
        # The value is stored as a string so the exact decimal survives the
        # round trip; a float would lose digits the author entered.
        sa.Column("value", sa.String(50), nullable=False),
        sa.Column("unit", sa.String(60), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period", sa.String(24), nullable=True),
        sa.Column("source_reference", sa.String(300), nullable=False),
        sa.Column("sample_size", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["assessment_id"],
            ["assessments.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "assessment_id",
            "source_system",
            "metric_key",
            "observed_at",
            name="uq_observed_metric_measurement",
        ),
    )
    op.create_index(
        "ix_observed_metrics_assessment_id",
        "observed_metrics",
        ["assessment_id"],
    )
    op.create_index(
        "ix_observed_metrics_assessment_observed",
        "observed_metrics",
        ["assessment_id", "metric_key", "observed_at"],
    )


def downgrade():
    op.drop_index("ix_observed_metrics_assessment_observed", table_name="observed_metrics")
    op.drop_index("ix_observed_metrics_assessment_id", table_name="observed_metrics")
    op.drop_table("observed_metrics")
