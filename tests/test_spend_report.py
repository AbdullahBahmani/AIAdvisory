"""Tests for the local provider-spend report.

The point of these tests is the *honesty* of the numbers: a run recorded without
operator-supplied pricing must appear as unknown cost, must stay in the run
count, and must never be turned into an invented dollar figure.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import AIRunRow, AssessmentRow, Base, OrganizationRow
from scripts.spend_report import (
    MALFORMED,
    PRICED,
    UNPRICED,
    SpendReport,
    SpendRun,
    aggregate,
    build_report,
    classify_cost,
    main,
    parse_since,
    render_text,
)

BASE_TIME = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def make_run(**overrides) -> SpendRun:
    base = {
        "run_id": "run-1",
        "assessment_id": "assessment-1",
        "organization_id": "org-1",
        "organization_name": "Example Co",
        "feature": "discovery",
        "provider": "openai",
        "model": "gpt-4.1-mini",
        "input_tokens": 100,
        "output_tokens": 20,
        "cost_usd": "0.000200",
        "pricing_source": "operator-reviewed-rate-sheet",
        "created_at": BASE_TIME,
    }
    base.update(overrides)
    return SpendRun(**base)


# --------------------------------------------------------------------------
# Cost classification
# --------------------------------------------------------------------------


def test_missing_cost_is_unpriced_not_zero():
    assert classify_cost(None) == (UNPRICED, None)
    assert classify_cost("   ") == (UNPRICED, None)


def test_stored_cost_is_read_exactly_as_written():
    state, amount = classify_cost("0.000120")
    assert state == PRICED
    assert amount == Decimal("0.000120")


def test_malformed_cost_is_reported_separately():
    assert classify_cost("not-a-number") == (MALFORMED, None)
    assert classify_cost("NaN") == (MALFORMED, None)
    assert classify_cost("Infinity") == (MALFORMED, None)


# --------------------------------------------------------------------------
# Aggregation: the priced / unpriced split
# --------------------------------------------------------------------------


def test_unpriced_runs_stay_in_the_run_count_and_out_of_the_cost_total():
    runs = [
        make_run(run_id="a", cost_usd="0.001000", pricing_source="rate-sheet"),
        make_run(run_id="b", cost_usd="0.002000", pricing_source="rate-sheet"),
        make_run(run_id="c", cost_usd=None, pricing_source=None),
    ]
    groups, total = aggregate(runs, "model")
    assert total.runs == 3
    assert total.priced_runs == 2
    assert total.unpriced_runs == 1
    assert total.malformed_cost_runs == 0
    # The unpriced run is not silently dropped from the denominator ...
    assert total.runs == total.priced_runs + total.unpriced_runs + total.malformed_cost_runs
    # ... and it is not given an invented price either.
    assert total.priced_cost_usd == Decimal("0.003000")
    assert len(groups) == 1


def test_malformed_cost_is_never_added_to_the_total():
    runs = [
        make_run(run_id="a", cost_usd="0.001000"),
        make_run(run_id="b", cost_usd="oops"),
    ]
    _, total = aggregate(runs, "model")
    assert total.runs == 2
    assert total.priced_runs == 1
    assert total.malformed_cost_runs == 1
    assert total.unknown_cost_runs == 1
    assert total.priced_cost_usd == Decimal("0.001000")


def test_totals_are_exactly_the_sum_of_the_stored_prices():
    runs = [
        make_run(run_id="a", cost_usd="0.000100"),
        make_run(run_id="b", cost_usd="0.000250"),
        make_run(run_id="c", cost_usd="1.500000"),
        make_run(run_id="d", cost_usd=None),
    ]
    _, total = aggregate(runs, "model")
    expected = sum(Decimal(r.cost_usd) for r in runs if r.cost_usd is not None)
    assert total.priced_cost_usd == expected


def test_token_totals_and_missing_token_counts():
    runs = [
        make_run(run_id="a", input_tokens=100, output_tokens=20),
        make_run(run_id="b", input_tokens=50, output_tokens=None),
        make_run(run_id="c", input_tokens=None, output_tokens=None),
    ]
    _, total = aggregate(runs, "model")
    assert total.input_tokens == 150
    assert total.output_tokens == 20
    assert total.runs_with_input_tokens == 2
    assert total.runs_missing_input_tokens == 1
    assert total.runs_with_output_tokens == 1
    assert total.runs_missing_output_tokens == 2


# --------------------------------------------------------------------------
# Grouping
# --------------------------------------------------------------------------


def test_group_by_model_separates_models():
    runs = [
        make_run(run_id="a", model="gpt-4.1-mini", cost_usd="0.001000"),
        make_run(run_id="b", model="gpt-4.1", cost_usd="0.002000"),
        make_run(run_id="c", model="gpt-4.1-mini", cost_usd="0.003000"),
    ]
    groups, total = aggregate(runs, "model")
    # Ordered by priced cost descending: gpt-4.1-mini has 0.004000, gpt-4.1 has 0.002000.
    assert [key for key, _ in groups] == ["openai/gpt-4.1-mini", "openai/gpt-4.1"]
    assert total.runs == 3


def test_group_by_day_buckets_by_utc_day():
    runs = [
        make_run(run_id="a", created_at=datetime(2026, 10, 1, 1, 0, tzinfo=UTC)),
        make_run(run_id="b", created_at=datetime(2026, 10, 1, 23, 0, tzinfo=UTC)),
        make_run(run_id="c", created_at=datetime(2026, 10, 2, 5, 0, tzinfo=UTC)),
    ]
    groups, _ = aggregate(runs, "day")
    assert [key for key, _ in groups] == ["2026-10-01", "2026-10-02"]
    assert groups[0][1].runs == 2


def test_group_by_organization_marks_unassigned_rows():
    runs = [
        make_run(run_id="a", organization_id="org-1", organization_name="Example Co"),
        make_run(run_id="b", organization_id=None, organization_name="Legacy name"),
    ]
    groups, _ = aggregate(runs, "organization")
    assert sorted(key for key, _ in groups) == ["(unassigned)", "Example Co"]


# --------------------------------------------------------------------------
# Window parsing
# --------------------------------------------------------------------------


def test_relative_window_and_iso_window():
    now = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
    assert parse_since("30d", now) == now - timedelta(days=30)
    assert parse_since("4w", now) == now - timedelta(weeks=4)
    assert parse_since("12h", now) == now - timedelta(hours=12)
    assert parse_since("2026-01-01", now) == datetime(2026, 1, 1, tzinfo=UTC)
    assert parse_since("2026-01-01T00:00:00+00:00", now) == datetime(2026, 1, 1, tzinfo=UTC)


# --------------------------------------------------------------------------
# Database-backed behaviour
# --------------------------------------------------------------------------


@pytest.fixture
def session(tmp_path: Path):
    url = f"sqlite:///{tmp_path / 'spend.sqlite3'}"
    engine = create_engine(url, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _fk(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db
    engine.dispose()


def _add_assessment(db, *, org_id, org_name, assessment_id):
    db.add(
        AssessmentRow(
            id=assessment_id,
            organization_id=org_id,
            organization=org_name,
            title=f"Assessment {assessment_id}",
        )
    )
    db.flush()


def _add_run(db, *, run_id, assessment_id, created_at, cost=None, source=None, tokens=(10, 5)):
    db.add(
        AIRunRow(
            id=run_id,
            assessment_id=assessment_id,
            assessment_revision=1,
            feature="discovery",
            proposal={},
            provider="openai",
            model="gpt-4.1-mini",
            prompt_version="3.0",
            schema_version="3.0",
            elapsed_ms=10,
            input_tokens=tokens[0],
            output_tokens=tokens[1],
            estimated_cost_usd=cost,
            pricing_source=source,
            created_at=created_at,
        )
    )
    db.flush()


def _seed(session):
    org = OrganizationRow(id="org-1", name="Example Co")
    other = OrganizationRow(id="org-2", name="Other Co")
    session.add_all([org, other])
    session.flush()
    _add_assessment(session, org_id="org-1", org_name="Example Co", assessment_id="a-1")
    _add_assessment(session, org_id="org-2", org_name="Other Co", assessment_id="a-2")
    _add_assessment(session, org_id=None, org_name=None, assessment_id="a-legacy")
    _add_run(
        session,
        run_id="r-1",
        assessment_id="a-1",
        created_at=BASE_TIME,
        cost="0.000120",
        source="operator-reviewed-rate-sheet",
    )
    _add_run(
        session,
        run_id="r-2",
        assessment_id="a-1",
        created_at=BASE_TIME + timedelta(hours=1),
        cost=None,
        source=None,
    )
    _add_run(
        session,
        run_id="r-3",
        assessment_id="a-2",
        created_at=BASE_TIME + timedelta(hours=2),
        cost="0.000240",
        source="operator-reviewed-rate-sheet",
    )
    _add_run(
        session,
        run_id="r-legacy",
        assessment_id="a-legacy",
        created_at=BASE_TIME + timedelta(hours=3),
        cost="0.000360",
        source="operator-reviewed-rate-sheet",
    )
    session.commit()


def test_single_tenant_scope_excludes_other_tenants(session):
    _seed(session)
    org = session.get(OrganizationRow, "org-1")
    report = build_report(
        session,
        since=BASE_TIME - timedelta(days=1),
        until=BASE_TIME + timedelta(days=1),
        group_by="model",
        organization=org,
    )
    assert report.total.runs == 2
    assert report.total.priced_runs == 1
    assert report.total.unpriced_runs == 1
    assert report.total.priced_cost_usd == Decimal("0.000120")
    # The legacy (unassigned) run cannot be attributed to this tenant.
    assert report.unassigned_runs_excluded == 1


def test_all_tenants_view_includes_unassigned_rows(session):
    _seed(session)
    report = build_report(
        session,
        since=BASE_TIME - timedelta(days=1),
        until=BASE_TIME + timedelta(days=1),
        group_by="organization",
        organization=None,
    )
    assert report.total.runs == 4
    assert report.total.priced_runs == 3
    assert report.total.unpriced_runs == 1
    assert report.total.priced_cost_usd == Decimal("0.000720")
    keys = {key for key, _ in report.groups}
    assert keys == {"Example Co", "Other Co", "(unassigned)"}


def test_window_excludes_runs_outside_the_period(session):
    _seed(session)
    report = build_report(
        session,
        since=BASE_TIME + timedelta(hours=2),
        until=BASE_TIME + timedelta(days=1),
        group_by="model",
        organization=None,
    )
    assert report.total.runs == 2


def test_report_is_labelled_as_a_local_estimate(session):
    _seed(session)
    report = build_report(
        session,
        since=BASE_TIME - timedelta(days=1),
        until=BASE_TIME + timedelta(days=1),
        group_by="model",
        organization=None,
    )
    text = render_text(report)
    assert "NOT a billing reconciliation" in text
    assert "UNKNOWN cost" in text
    assert "lower bound" in text
    assert report.as_dict()["disclaimer"].startswith("LOCAL ESTIMATE")


def test_a_group_without_any_priced_run_does_not_show_a_zero_cost():
    runs = [make_run(run_id="a", cost_usd=None, pricing_source=None)]
    groups, total = aggregate(runs, "model")
    report = SpendReport(
        group_by="model",
        since=BASE_TIME,
        until=BASE_TIME + timedelta(days=1),
        scope="test",
        groups=groups,
        total=total,
    )
    text = render_text(report)
    # A missing price must never be rendered as a zero dollar amount.
    assert "0.000000" not in text
    assert "'-' means no run in that group carried a stored price" in text


# --------------------------------------------------------------------------
# Command-line guards
# --------------------------------------------------------------------------


def test_cli_requires_an_explicit_scope(monkeypatch, capsys):
    with pytest.raises(SystemExit) as excinfo:
        main([])
    assert excinfo.value.code == 2
    assert "--all-tenants" in capsys.readouterr().err


def test_cli_rejects_scope_and_all_tenants_together(monkeypatch, capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["--organization", "Example Co", "--all-tenants"])
    assert excinfo.value.code == 2
    assert "--all-tenants" in capsys.readouterr().err


@pytest.fixture
def cli_session(monkeypatch, tmp_path: Path):
    """Point the command at an isolated database."""
    engine = create_engine(f"sqlite:///{tmp_path / 'cli.sqlite3'}")
    Base.metadata.create_all(engine)
    monkeypatch.setattr("scripts.spend_report.SessionLocal", sessionmaker(bind=engine))
    with Session(engine) as db:
        yield db
    engine.dispose()


def test_cli_reports_an_unknown_organization(cli_session, capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["--organization", "Nope"])
    assert excinfo.value.code == 2
    assert "Organization not found" in capsys.readouterr().err


def test_cli_prints_the_local_estimate(cli_session, capsys):
    _seed(cli_session)
    assert main(["--all-tenants", "--since", "2026-09-01", "--until", "2026-11-01"]) == 0
    out = capsys.readouterr().out
    assert "NOT a billing reconciliation" in out
    assert "ALL tenants" in out
    assert "UNKNOWN cost" in out
    assert "0.000720" in out


def test_cli_json_output_keeps_the_split(cli_session, capsys):
    _seed(cli_session)
    assert (
        main(
            [
                "--organization",
                "Example Co",
                "--since",
                "2026-09-01",
                "--until",
                "2026-11-01",
                "--format",
                "json",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["total"]["runs"] == 2
    assert payload["total"]["priced_runs"] == 1
    assert payload["total"]["unpriced_runs"] == 1
    assert payload["total"]["unknown_cost_runs"] == 1
    assert payload["total"]["priced_cost_usd"] == "0.000120"
    assert payload["unassigned_runs_excluded"] == 1
