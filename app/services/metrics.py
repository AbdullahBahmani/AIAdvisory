"""Ingestion of measurements taken by a production system.

An observed metric is a *measurement*.  It records what a source system
reported, and it exists so that a modelled assumption can be checked against
something real.  Three rules govern it:

* **It never decides anything.**  Nothing here feeds the rules engine, the
  scoring or the recommendation precedence.  A measurement arriving from a
  machine must not be able to weaken the product's rule that *rules enforce
  and humans decide*.
* **It is never rewritten.**  The value is stored exactly as the source
  system reported it, bound to the assessment revision that was current when
  it arrived.
* **It is never counted twice.**  A repeated delivery of the same
  measurement is a no-op, so a retried push cannot inflate the evidence.

A scenario *cites* a measurement.  The citation is checked: the measurement
must exist, it must belong to the same assessment, and when it names a
quantity the scenario also states, the two must agree.  A mismatch is
reported — never silently resolved.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AssessmentRow, ObservedMetricRow, utcnow
from app.domain.portfolio import (
    SCENARIO_INPUT_FIELDS,
    ObservedMetricIn,
    ValueScenarioIn,
)

#: The number of measurements one request may carry.
MAX_BATCH = 200


@dataclass
class IngestResult:
    """What an ingestion request actually did."""

    stored: list[ObservedMetricRow] = field(default_factory=list)
    already_recorded: int = 0


def ingest(
    db: Session, row: AssessmentRow, metrics: list[ObservedMetricIn], actor: str
) -> IngestResult:
    """Store the measurements that are not recorded yet.

    A measurement that is already recorded is counted, not stored again: the
    unique key on (assessment, source system, metric key, observed moment)
    makes a repeated delivery idempotent.

    The moment is normalised to UTC before it is stored.  A measurement
    without a timezone is refused by the contract, and a measurement with one
    is stored in a single reference frame, so two deliveries of the same
    instant in two timezones are recognised as the same measurement.
    """
    result = IngestResult()
    for metric in metrics:
        observed_at = metric.observed_at.astimezone(UTC)
        existing = db.scalar(
            select(ObservedMetricRow).where(
                ObservedMetricRow.assessment_id == row.id,
                ObservedMetricRow.source_system == metric.source_system,
                ObservedMetricRow.metric_key == metric.metric_key,
                ObservedMetricRow.observed_at == observed_at,
            )
        )
        if existing is not None:
            result.already_recorded += 1
            continue
        item = ObservedMetricRow(
            assessment_id=row.id,
            assessment_revision=row.revision,
            source_system=metric.source_system,
            metric_key=metric.metric_key,
            value=str(metric.value),
            unit=metric.unit,
            observed_at=observed_at,
            period=metric.period,
            source_reference=metric.source_reference,
            sample_size=metric.sample_size,
            created_by=actor,
            created_at=utcnow(),
        )
        db.add(item)
        result.stored.append(item)
    if result.stored:
        db.flush()
    return result


def load_metrics(db: Session, assessment_id: str, revision: int) -> list[ObservedMetricRow]:
    """Every measurement of an assessment, newest first."""
    rows = db.scalars(
        select(ObservedMetricRow)
        .where(ObservedMetricRow.assessment_id == assessment_id)
        .order_by(ObservedMetricRow.observed_at.desc(), ObservedMetricRow.id.desc())
    ).all()
    return list(rows)


def resolve_citations(
    db: Session, assessment_id: str, metric_ids: list[str]
) -> dict[str, ObservedMetricRow]:
    """The measurements a scenario cites.

    A citation to a measurement that does not exist, or that belongs to
    another assessment, is a mistake the caller must fix: the ids are
    returned so the API can refuse rather than record a dangling claim.
    """
    if not metric_ids:
        return {}
    rows = db.scalars(
        select(ObservedMetricRow).where(
            ObservedMetricRow.id.in_(metric_ids),
            ObservedMetricRow.assessment_id == assessment_id,
        )
    ).all()
    return {row.id: row for row in rows}


def citation_warnings(
    inputs: ValueScenarioIn, metrics: dict[str, ObservedMetricRow], revision: int
) -> list[str]:
    """The ways a citation disagrees with the scenario that cites it.

    A citation is a claim.  When the claim and the scenario disagree, the
    disagreement is reported: the reader must be able to see that the
    scenario says something the measurement does not support.
    """
    warnings: list[str] = []
    for metric_id in inputs.observed_metric_ids:
        metric = metrics.get(metric_id)
        if metric is None:
            continue
        if metric.assessment_revision != revision:
            warnings.append(
                f"The cited measurement {metric.metric_key} was recorded for an earlier "
                f"revision and is stale."
            )
        if metric.metric_key not in SCENARIO_INPUT_FIELDS:
            continue
        stated = getattr(inputs, metric.metric_key, None)
        if stated is None:
            continue
        measured = Decimal(metric.value)
        if measured != Decimal(stated):
            warnings.append(
                f"The cited measurement {metric.metric_key} is {measured} {metric.unit}, "
                f"but the scenario states {stated}."
            )
    return warnings
