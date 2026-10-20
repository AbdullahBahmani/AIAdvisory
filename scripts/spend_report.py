"""Local, offline, read-only provider-spend report over stored AI runs.

Cost in this application is *never* priced by the code. ``ai_runs.estimated_cost_usd``
and ``ai_runs.pricing_source`` are written once, at run time, from prices the
**operator** supplied through ``ADVISORY_AI_INPUT_USD_PER_MILLION``,
``ADVISORY_AI_OUTPUT_USD_PER_MILLION`` and ``ADVISORY_AI_PRICING_SOURCE``. A run
recorded without operator-supplied pricing therefore has an *unknown* cost.

This report never applies a price of its own:

* an unpriced run is reported as **unknown cost** and is never silently dropped
  from the denominator (the run count always includes it);
* a malformed stored cost is reported separately from both priced and unpriced;
* no missing price is ever back-filled, extrapolated or invented.

The output is a **local estimate** used for trend and anomaly spotting. It is
**not** a billing reconciliation and must not be presented as one.

Tenant scoping: the report is single-tenant by default. Pass ``--organization``
(name or UUID) to scope it to one organization, or pass ``--all-tenants`` to
explicitly ask for a cross-tenant view. Neither one is implied.

Examples:
  python -m scripts.spend_report --organization "Example Co"
  python -m scripts.spend_report --organization "Example Co" --group-by day --since 30d
  python -m scripts.spend_report --all-tenants --group-by model --format json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.models import AIRunRow, AssessmentRow, OrganizationRow
from app.db.session import SessionLocal

GROUP_BY_CHOICES = ("model", "organization", "feature", "day", "assessment")
UNASSIGNED_KEY = "(unassigned)"

PRICED = "priced"
UNPRICED = "unpriced"
MALFORMED = "malformed"

DISCLAIMER = "LOCAL ESTIMATE from operator-supplied prices - NOT a billing reconciliation"


def as_utc(value: datetime) -> datetime:
    """Normalise naive timestamps (SQLite) to UTC-aware ones."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


@dataclass(frozen=True)
class SpendRun:
    """One stored AI run, reduced to the fields the report needs."""

    run_id: str
    assessment_id: str
    organization_id: str | None
    organization_name: str | None
    feature: str
    provider: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: str | None
    pricing_source: str | None
    created_at: datetime


@dataclass
class Totals:
    """Aggregated counts for a group. Priced and unknown cost stay separate."""

    runs: int = 0
    priced_runs: int = 0
    unpriced_runs: int = 0
    malformed_cost_runs: int = 0
    priced_cost_usd: Decimal = field(default_factory=lambda: Decimal("0"))
    input_tokens: int = 0
    output_tokens: int = 0
    runs_with_input_tokens: int = 0
    runs_missing_input_tokens: int = 0
    runs_with_output_tokens: int = 0
    runs_missing_output_tokens: int = 0
    pricing_sources: set[str] = field(default_factory=set)

    def add(self, run: SpendRun) -> None:
        self.runs += 1
        state, amount = classify_cost(run.cost_usd)
        if state == PRICED:
            self.priced_runs += 1
            self.priced_cost_usd += amount
        elif state == UNPRICED:
            self.unpriced_runs += 1
        else:
            self.malformed_cost_runs += 1
        if run.input_tokens is None:
            self.runs_missing_input_tokens += 1
        else:
            self.runs_with_input_tokens += 1
            self.input_tokens += run.input_tokens
        if run.output_tokens is None:
            self.runs_missing_output_tokens += 1
        else:
            self.runs_with_output_tokens += 1
            self.output_tokens += run.output_tokens
        if run.pricing_source:
            self.pricing_sources.add(run.pricing_source)

    @property
    def unknown_cost_runs(self) -> int:
        """Runs whose cost could not be read from the operator-supplied price."""
        return self.unpriced_runs + self.malformed_cost_runs

    def as_dict(self) -> dict:
        return {
            "runs": self.runs,
            "priced_runs": self.priced_runs,
            "unpriced_runs": self.unpriced_runs,
            "malformed_cost_runs": self.malformed_cost_runs,
            "unknown_cost_runs": self.unknown_cost_runs,
            "priced_cost_usd": str(self.priced_cost_usd),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "runs_with_input_tokens": self.runs_with_input_tokens,
            "runs_missing_input_tokens": self.runs_missing_input_tokens,
            "runs_with_output_tokens": self.runs_with_output_tokens,
            "runs_missing_output_tokens": self.runs_missing_output_tokens,
            "pricing_sources": sorted(self.pricing_sources),
        }


def classify_cost(raw: str | None) -> tuple[str, Decimal | None]:
    """Classify a stored cost string without ever producing an estimate."""
    if raw is None:
        return UNPRICED, None
    candidate = raw.strip()
    if not candidate:
        return UNPRICED, None
    try:
        amount = Decimal(candidate)
    except (InvalidOperation, ValueError, ArithmeticError):
        return MALFORMED, None
    if not amount.is_finite():
        return MALFORMED, None
    return PRICED, amount


def group_key(run: SpendRun, group_by: str) -> str:
    if group_by == "model":
        return f"{run.provider}/{run.model}"
    if group_by == "organization":
        if run.organization_id is None:
            return UNASSIGNED_KEY
        return run.organization_name or f"organization:{run.organization_id}"
    if group_by == "feature":
        return run.feature
    if group_by == "day":
        return as_utc(run.created_at).date().isoformat()
    if group_by == "assessment":
        return run.assessment_id
    raise ValueError(f"Unsupported grouping: {group_by!r}")


@dataclass
class SpendReport:
    """A complete, self-describing spend estimate for one scope and window."""

    group_by: str
    since: datetime
    until: datetime
    scope: str
    dialect: str = "unknown"
    groups: list[tuple[str, Totals]] = field(default_factory=list)
    total: Totals = field(default_factory=Totals)
    unassigned_runs_excluded: int = 0

    def as_dict(self) -> dict:
        return {
            "disclaimer": DISCLAIMER,
            "group_by": self.group_by,
            "window": {
                "since_utc": as_utc(self.since).isoformat(),
                "until_utc": as_utc(self.until).isoformat(),
            },
            "scope": self.scope,
            "database_dialect": self.dialect,
            "unassigned_runs_excluded": self.unassigned_runs_excluded,
            "groups": [{"key": key, **totals.as_dict()} for key, totals in self.groups],
            "total": self.total.as_dict(),
        }


def aggregate(runs: list[SpendRun], group_by: str = "model") -> tuple[list, Totals]:
    """Aggregate stored runs into groups, keeping priced and unknown cost apart."""
    buckets: dict[str, Totals] = {}
    total = Totals()
    for run in runs:
        total.add(run)
        key = group_key(run, group_by)
        buckets.setdefault(key, Totals()).add(run)
    if group_by == "day":
        ordered = sorted(buckets.items(), key=lambda item: item[0])
    else:
        ordered = sorted(
            buckets.items(),
            key=lambda item: (-item[1].priced_cost_usd, -item[1].runs, item[0]),
        )
    return ordered, total


def _rows_to_runs(rows) -> list[SpendRun]:
    return [
        SpendRun(
            run_id=row.run_id,
            assessment_id=row.assessment_id,
            organization_id=row.organization_id,
            organization_name=row.organization,
            feature=row.feature,
            provider=row.provider,
            model=row.model,
            input_tokens=row.input_tokens,
            output_tokens=row.output_tokens,
            cost_usd=row.estimated_cost_usd,
            pricing_source=row.pricing_source,
            created_at=as_utc(row.created_at),
        )
        for row in rows
    ]


def fetch_runs(
    session: Session,
    since: datetime,
    until: datetime,
    organization_id: str | None = None,
) -> list[SpendRun]:
    """Read AI runs in the window, optionally restricted to one tenant."""
    statement = (
        select(
            AIRunRow.id.label("run_id"),
            AIRunRow.assessment_id,
            AIRunRow.feature,
            AIRunRow.provider,
            AIRunRow.model,
            AIRunRow.input_tokens,
            AIRunRow.output_tokens,
            AIRunRow.estimated_cost_usd,
            AIRunRow.pricing_source,
            AIRunRow.created_at,
            AssessmentRow.organization_id,
            AssessmentRow.organization,
        )
        .join(AssessmentRow, AIRunRow.assessment_id == AssessmentRow.id)
        .where(AIRunRow.created_at >= since, AIRunRow.created_at < until)
        .order_by(AIRunRow.created_at)
    )
    if organization_id is not None:
        statement = statement.where(AssessmentRow.organization_id == organization_id)
    return _rows_to_runs(session.execute(statement).all())


def count_unassigned_runs(session: Session, since: datetime, until: datetime) -> int:
    """Count window runs whose assessment has no tenant assignment (legacy rows)."""
    statement = (
        select(func.count())
        .select_from(AIRunRow)
        .join(AssessmentRow, AIRunRow.assessment_id == AssessmentRow.id)
        .where(
            AIRunRow.created_at >= since,
            AIRunRow.created_at < until,
            AssessmentRow.organization_id.is_(None),
        )
    )
    return int(session.execute(statement).scalar_one())


def resolve_organization(session: Session, reference: str) -> OrganizationRow | None:
    """Look up one organization by UUID or by name."""
    row = session.scalar(select(OrganizationRow).where(OrganizationRow.id == reference))
    if row is None:
        row = session.scalar(select(OrganizationRow).where(OrganizationRow.name == reference))
    return row


def parse_timestamp(value: str) -> datetime:
    """Parse an ISO date/datetime; a missing timezone is read as UTC."""
    candidate = value.strip()
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    parsed = datetime.fromisoformat(candidate)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def parse_since(value: str, now: datetime) -> datetime:
    """Accept a relative window (``30d``, ``12h``, ``4w``, ``3m``) or an ISO stamp."""
    match = re.fullmatch(r"(\d+)\s*([hdwm])", value.strip().lower())
    if not match:
        return parse_timestamp(value)
    amount = int(match.group(1))
    unit = match.group(2)
    if unit == "h":
        delta = timedelta(hours=amount)
    elif unit == "d":
        delta = timedelta(days=amount)
    elif unit == "w":
        delta = timedelta(weeks=amount)
    else:
        delta = timedelta(days=30 * amount)
    return now - delta


def _format_cost(totals: Totals) -> str:
    """Show '-' when a group has no priced run, so zero is never implied."""
    if totals.priced_runs == 0:
        return "-"
    return f"{totals.priced_cost_usd:.6f}"


def render_text(report: SpendReport) -> str:
    """Render the report as plain text for an operator terminal."""
    total = report.total
    lines: list[str] = []
    lines.append("=" * 78)
    lines.append(DISCLAIMER)
    lines.append("=" * 78)
    lines.append(
        f"Window (UTC)   : {as_utc(report.since).isoformat()}  ->  "
        f"{as_utc(report.until).isoformat()}"
    )
    lines.append(f"Scope          : {report.scope}")
    lines.append(f"Grouping       : {report.group_by}")
    lines.append(f"Database       : {report.dialect} (read-only)")
    lines.append(f"Runs in scope  : {total.runs}")
    if report.unassigned_runs_excluded:
        lines.append(
            f"Excluded       : {report.unassigned_runs_excluded} run(s) with no tenant "
            "assignment (visible only in --all-tenants)"
        )
    lines.append("")

    header = (
        f"{'GROUP':<34}{'RUNS':>6}{'PRICED':>8}{'UNPRICED':>10}"
        f"{'MALFORMED':>11}{'COST USD':>14}{'IN TOK':>10}{'OUT TOK':>10}"
    )
    lines.append(header)
    lines.append("-" * len(header))
    for key, totals in report.groups:
        lines.append(
            f"{key[:34]:<34}{totals.runs:>6}{totals.priced_runs:>8}"
            f"{totals.unpriced_runs:>10}{totals.malformed_cost_runs:>11}"
            f"{_format_cost(totals):>14}"
            f"{totals.input_tokens:>10}{totals.output_tokens:>10}"
        )
    lines.append("-" * len(header))
    lines.append(
        f"{'TOTAL':<34}{total.runs:>6}{total.priced_runs:>8}{total.unpriced_runs:>10}"
        f"{total.malformed_cost_runs:>11}{_format_cost(total):>14}"
        f"{total.input_tokens:>10}{total.output_tokens:>10}"
    )
    lines.append(
        "COST USD is the sum of the operator-supplied prices actually stored on the priced "
        "runs only."
    )
    lines.append(
        "'-' means no run in that group carried a stored price, so the cost is unknown, not zero."
    )
    lines.append("")

    if total.runs == 0:
        lines.append("No AI runs were recorded in this window and scope.")
        lines.append("")
        return "\n".join(lines)

    priced_share = (total.priced_runs / total.runs * 100) if total.runs else 0.0
    lines.append(
        f"Cost coverage  : {total.priced_runs} of {total.runs} runs priced "
        f"({priced_share:.1f}%); {total.unpriced_runs} unpriced "
        f"(no operator price recorded), {total.malformed_cost_runs} malformed."
    )
    if total.unknown_cost_runs:
        lines.append(
            f"                 {total.unknown_cost_runs} run(s) have UNKNOWN cost. They are "
            "counted in every"
        )
        lines.append(
            "                 run total above but excluded from the USD figure, so the USD "
            "figure is a"
        )
        lines.append(
            "                 lower bound for this window - not the full spend. This tool "
            "never estimates"
        )
        lines.append(
            "                 a missing price; supply ADVISORY_AI_* pricing on the API host to "
            "record costs."
        )
    else:
        lines.append(
            "                 Every run in scope carries an operator-supplied price; no cost is "
            "unknown."
        )
    lines.append(
        f"Token usage    : {total.input_tokens} input tokens "
        f"({total.runs_with_input_tokens} run(s) reported, "
        f"{total.runs_missing_input_tokens} without),"
    )
    lines.append(
        f"                 {total.output_tokens} output tokens "
        f"({total.runs_with_output_tokens} run(s) reported, "
        f"{total.runs_missing_output_tokens} without)."
    )
    if total.pricing_sources:
        lines.append(f"Price sources  : {', '.join(sorted(total.pricing_sources))}")
    lines.append("")
    lines.append(
        "Reminder: these numbers come from prices the operator supplied at run time. They are "
        "not provider"
    )
    lines.append("invoices, not a reconciliation and not a basis for external billing claims.")
    lines.append("")
    return "\n".join(lines)


def build_report(
    session: Session,
    *,
    since: datetime,
    until: datetime,
    group_by: str,
    organization: OrganizationRow | None,
) -> SpendReport:
    """Read the window and aggregate it into a report."""
    if organization is not None:
        scope = f"organization {organization.name!r} (id {organization.id}) - single tenant"
        runs = fetch_runs(session, since, until, organization_id=organization.id)
        excluded = count_unassigned_runs(session, since, until)
    else:
        scope = "ALL tenants - cross-tenant view requested with --all-tenants"
        runs = fetch_runs(session, since, until, organization_id=None)
        excluded = 0
    groups, total = aggregate(runs, group_by)
    return SpendReport(
        group_by=group_by,
        since=since,
        until=until,
        scope=scope,
        dialect=session.get_bind().dialect.name,
        groups=groups,
        total=total,
        unassigned_runs_excluded=excluded,
    )


def _read_only_session(warnings: list[str]) -> Session:
    """Open a session that cannot write, where the database supports it."""
    session = SessionLocal()
    try:
        dialect = session.get_bind().dialect.name
    except SQLAlchemyError:  # pragma: no cover - defensive
        return session
    if dialect == "postgresql":
        try:
            session.execute(text("SET TRANSACTION READ ONLY"))
        except SQLAlchemyError as exc:
            session.rollback()
            warnings.append(
                f"Could not mark the PostgreSQL transaction read-only ({exc.__class__.__name__}); "
                "the report only issues SELECT statements."
            )
    return session


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.spend_report",
        description=(
            "Local, offline, read-only provider-spend estimate built from stored AI runs. "
            "Costs come from operator-supplied prices only; unknown costs stay unknown."
        ),
    )
    parser.add_argument(
        "--organization",
        help="Scope the report to one organization (name or UUID). Single-tenant by default.",
    )
    parser.add_argument(
        "--all-tenants",
        action="store_true",
        help="Explicitly request a cross-tenant view. Required to read across organizations.",
    )
    parser.add_argument(
        "--since",
        default="30d",
        help="Start of the window: relative ('30d', '12h', '4w') or ISO timestamp. Default 30d.",
    )
    parser.add_argument(
        "--until",
        default=None,
        help="End of the window: ISO timestamp. Default: now.",
    )
    parser.add_argument(
        "--group-by",
        choices=list(GROUP_BY_CHOICES),
        default="model",
        help="Grouping dimension. Default 'model' (spend follows the priced model).",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format. Default 'text'.",
    )
    args = parser.parse_args(argv)

    if args.organization and args.all_tenants:
        parser.error(
            "Choose either --organization (single tenant) or --all-tenants (cross-tenant view)"
        )
    if not args.organization and not args.all_tenants:
        parser.error(
            "Choose --organization NAME_OR_ID for a single-tenant view, or --all-tenants "
            "for an explicit cross-tenant view"
        )

    now = datetime.now(UTC)
    try:
        since = parse_since(args.since, now)
        until = parse_timestamp(args.until) if args.until else now
    except ValueError as exc:
        parser.error(f"Could not parse the requested window: {exc}")
    if until <= since:
        parser.error("The end of the window must be after its start")

    warnings: list[str] = []
    session = _read_only_session(warnings)
    try:
        organization = None
        if args.organization:
            organization = resolve_organization(session, args.organization)
            if organization is None:
                parser.error(f"Organization not found: {args.organization!r}")
        report = build_report(
            session,
            since=since,
            until=until,
            group_by=args.group_by,
            organization=organization,
        )
    finally:
        session.rollback()
        session.close()

    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)
    if args.format == "json":
        print(json.dumps(report.as_dict(), indent=2, sort_keys=True))
    else:
        print(render_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
