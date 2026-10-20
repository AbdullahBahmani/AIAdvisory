"""Credential and membership lifecycle: expiry, rotation, de-provisioning, sweep.

The tests below prove the policy that ``docs/credential-lifecycle.md`` states:

* an expired or revoked credential is refused on **every** authenticated route;
* the expiry leeway is strict, documented and no longer than it claims;
* a credential cannot be issued without an expiry unless the unlimited opt-in
  is explicit, and the listing labels such a credential;
* a rotation leaves exactly one usable credential, also when the issue step
  fails halfway;
* reducing a membership role revokes the credential that relied on it;
* deactivating a member clears their reviewer assignment, so no assessment is
  left unreviewable;
* the sweep reports by default and only acts with ``--execute``;
* the access review lists the live credentials, their expiry and the
  outstanding assignments;
* every lifecycle change is audited.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

import scripts.admin as admin_cli
from app.db.models import (
    ApiCredentialRow,
    AssessmentRow,
    OidcMembershipRow,
    OrganizationRow,
    ReviewerAssignmentRow,
)
from app.db.provisioning_models import OrganizationAuditEventRow
from app.db.session import get_db
from app.main import app
from app.security import (
    CREDENTIAL_EXPIRY_LEEWAY,
    credential_is_live,
    digest_token,
    make_token,
)
from app.services import credential_lifecycle as lifecycle
from app.services.credential_lifecycle import (
    LastAdminError,
    LifecycleError,
    access_review,
    change_membership_role,
    disable_membership,
    issue_credential,
    rotate_credential,
    sweep_lifecycle,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------------------
# fixtures and helpers
# --------------------------------------------------------------------------------------


@pytest.fixture
def engine(db_engine):
    """The database the lifecycle tests run against (SQLite or PostgreSQL)."""
    return db_engine


@pytest.fixture
def session(engine):
    with Session(engine) as db:
        yield db


@pytest.fixture
def api(engine, monkeypatch):
    """A test client bound to the isolated database, without the demo switch."""
    monkeypatch.delenv("ADVISORY_DEMO_MODE", raising=False)
    monkeypatch.delenv("ADVISORY_REVIEW_TOKEN", raising=False)
    monkeypatch.delenv("ADVISORY_OIDC_REQUIRED", raising=False)

    def override_db():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture
def cli(engine, monkeypatch):
    """Run the operator CLI against the isolated database."""

    def run(*argv: str) -> None:
        monkeypatch.setattr(admin_cli, "SessionLocal", sessionmaker(bind=engine))
        monkeypatch.setattr(sys, "argv", ["admin", *argv])
        admin_cli.main()

    return run


def make_organization(db: Session, name: str = "Acme") -> OrganizationRow:
    org = db.scalar(select(OrganizationRow).where(OrganizationRow.name == name))
    if org is None:
        org = OrganizationRow(name=name)
        db.add(org)
        db.flush()
    return org


def make_credential(
    db: Session,
    organization_id: str,
    subject: str,
    role: str = "reviewer",
    *,
    expires_in: timedelta | None = timedelta(days=30),
    unlimited: bool = False,
    revoked: bool = False,
) -> tuple[ApiCredentialRow, str]:
    """Provision a credential the way the operator CLI does."""
    token = make_token()
    row = ApiCredentialRow(
        organization_id=organization_id,
        subject=subject,
        role=role,
        token_hash=digest_token(token),
        expires_at=None if unlimited else datetime.now(UTC) + expires_in,
        unlimited=unlimited,
        revoked_at=datetime.now(UTC) if revoked else None,
    )
    db.add(row)
    db.flush()
    return row, token


def make_membership(
    db: Session, organization_id: str, subject: str, role: str, *, active: bool = True
) -> OidcMembershipRow:
    row = OidcMembershipRow(
        issuer="https://idp.example.test",
        subject=subject,
        organization_id=organization_id,
        role=role,
        active=active,
    )
    db.add(row)
    db.flush()
    return row


def make_assessment(db: Session, organization_id: str, title: str = "Assessment") -> AssessmentRow:
    row = AssessmentRow(title=title, organization_id=organization_id, status="in_review")
    db.add(row)
    db.flush()
    return row


def assign_reviewer(
    db: Session, assessment_id: str, reviewer_subject: str
) -> ReviewerAssignmentRow:
    row = ReviewerAssignmentRow(
        assessment_id=assessment_id, reviewer_subject=reviewer_subject, assigned_by="admin"
    )
    db.add(row)
    db.flush()
    return row


def audit_event_types(db: Session, organization_id: str | None = None) -> list[str]:
    """The event types recorded for an organization.

    Credential and membership lifecycle events are organization-scoped, not
    assessment-scoped, so they live in the organization audit ledger rather than
    in ``audit_events``. That table keeps the invariant that every row belongs to
    one assessment, which is what the per-assessment tamper-evident chain relies
    on, so a lifecycle event can never be mistaken for part of a decision
    history.
    """
    query = select(OrganizationAuditEventRow.event_type)
    if organization_id is not None:
        query = query.where(OrganizationAuditEventRow.organization_id == organization_id)
    return list(db.scalars(query.order_by(OrganizationAuditEventRow.created_at)).all())


def authenticated_routes() -> list[tuple[str, str]]:
    """Every authenticated route of the application, as (method, path).

    FastAPI keeps an included router behind an ``_IncludedRouter`` wrapper, so
    the router routes are collected explicitly.
    """
    routes: list[tuple[str, str]] = []

    def collect(entries) -> None:
        for route in entries:
            included = getattr(route, "original_router", None)
            if included is not None:
                collect(included.routes)
                continue
            path = getattr(route, "path", "")
            methods = getattr(route, "methods", None)
            if not methods or not (path.startswith("/api/v1") or path == "/metrics"):
                continue
            for method in sorted(methods - {"HEAD", "OPTIONS"}):
                routes.append((method, path))

    collect(app.routes)
    return routes


def concrete_url(path: str) -> str:
    """Replace the path parameters of a route with a harmless identifier."""
    url = path
    for parameter in ("assessment_id", "workflow_id", "step_id", "task_id", "notification_id"):
        url = url.replace("{" + parameter + "}", "00000000-0000-0000-0000-000000000000")
    return url


# --------------------------------------------------------------------------------------
# 1. expiry is enforced everywhere
# --------------------------------------------------------------------------------------


def test_expired_credential_is_rejected_on_every_authenticated_route(api, engine):
    with Session(engine) as db:
        org = make_organization(db)
        _expired, expired_token = make_credential(
            db, org.id, "alice", "admin", expires_in=timedelta(seconds=-1)
        )
        _valid, valid_token = make_credential(db, org.id, "bob", "admin")
        db.commit()

    expired_headers = {"Authorization": f"Bearer {expired_token}"}
    valid_headers = {"Authorization": f"Bearer {valid_token}"}
    routes = authenticated_routes()
    assert len(routes) >= 20, "the test must cover the whole authenticated surface"
    for method, path in routes:
        response = api.request(method, concrete_url(path), headers=expired_headers)
        assert response.status_code == 401, (method, path, response.text)

    # The same routes accept a live credential, so the 401 above is about the
    # expiry and not about the route.
    for method, path in routes[:6]:
        response = api.request(method, concrete_url(path), headers=valid_headers)
        assert response.status_code != 401, (method, path, response.text)


def test_use_of_an_expired_credential_is_audited(api, engine):
    with Session(engine) as db:
        org = make_organization(db)
        _row, token = make_credential(
            db, org.id, "alice", "admin", expires_in=timedelta(seconds=-1)
        )
        db.commit()
        organization_id = org.id

    response = api.get("/api/v1/assessments", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401

    with Session(engine) as db:
        events = list(
            db.scalars(
                select(OrganizationAuditEventRow).where(
                    OrganizationAuditEventRow.event_type == "credential.use_rejected",
                    OrganizationAuditEventRow.organization_id == organization_id,
                )
            ).all()
        )
        assert len(events) == 1
        assert events[0].details["reason"] == "expired"
        assert events[0].details["credential_id"]
        # An operator action is not a verified human identity.
        assert events[0].actor_verified is False


def test_revoked_credential_is_rejected_and_audited(api, engine):
    with Session(engine) as db:
        org = make_organization(db)
        row, token = make_credential(db, org.id, "alice", "admin")
        row.revoked_at = datetime.now(UTC)
        db.commit()
        organization_id = org.id
        credential_id = row.id

    response = api.get("/api/v1/assessments", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    with Session(engine) as db:
        events = list(
            db.scalars(
                select(OrganizationAuditEventRow).where(
                    OrganizationAuditEventRow.event_type == "credential.use_rejected",
                    OrganizationAuditEventRow.organization_id == organization_id,
                )
            ).all()
        )
        assert [event.details["reason"] for event in events] == ["revoked"]
        assert events[0].details["credential_id"] == credential_id


# --------------------------------------------------------------------------------------
# 2. the documented leeway
# --------------------------------------------------------------------------------------


def test_expiry_leeway_is_strict_and_no_longer_than_documented(api, engine):
    assert CREDENTIAL_EXPIRY_LEEWAY == timedelta(seconds=30)
    documented = (REPO_ROOT / "docs" / "credential-lifecycle.md").read_text(encoding="utf-8")
    assert "CREDENTIAL_EXPIRY_LEEWAY" in documented
    assert "30 seconds" in documented

    with Session(engine) as db:
        org = make_organization(db)
        # Inside the leeway: the credential is already refused.
        inside, inside_token = make_credential(
            db, org.id, "inside", "admin", expires_in=timedelta(seconds=10)
        )
        # Outside the leeway: still usable.
        outside, outside_token = make_credential(
            db, org.id, "outside", "admin", expires_in=timedelta(seconds=45)
        )
        # Long expired: no grace period at all.
        past, past_token = make_credential(
            db, org.id, "past", "admin", expires_in=timedelta(seconds=-1)
        )
        db.commit()
        identifiers = (inside.id, outside.id, past.id)

    for token in (inside_token, past_token):
        response = api.get("/api/v1/assessments", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 401
    response = api.get("/api/v1/assessments", headers={"Authorization": f"Bearer {outside_token}"})
    assert response.status_code == 200

    # The leeway is exactly what the constant says: a credential that expires
    # in more than the leeway still works, one that expires inside it does not.
    with Session(engine) as db:
        inside, outside, past = (db.get(ApiCredentialRow, identifier) for identifier in identifiers)
        now = datetime.now(UTC)
        assert lifecycle.credential_is_expired(inside, now) is True
        assert lifecycle.credential_is_expired(outside, now) is False
        assert lifecycle.credential_is_expired(past, now) is True
        assert credential_is_live(outside, now) is True
        assert credential_is_live(inside, now) is False


# --------------------------------------------------------------------------------------
# 3. an expiry is mandatory
# --------------------------------------------------------------------------------------


def test_credential_cannot_be_issued_without_an_expiry(session):
    org = make_organization(session)
    with pytest.raises(LifecycleError):
        issue_credential(
            session,
            organization_id=org.id,
            subject="alice",
            role="admin",
            days=None,
            unlimited=False,
        )
    row, _token = issue_credential(
        session, organization_id=org.id, subject="alice", role="admin", unlimited=True
    )
    assert row.expires_at is None
    assert row.unlimited is True


def test_cli_requires_an_explicit_unlimited_opt_in_and_labels_it(cli, capsys):
    cli("create-org", "--organization", "Acme")
    cli("issue", "--organization", "Acme", "--subject", "alice", "--role", "admin")
    output = capsys.readouterr().out
    assert "Token (save now" in output
    assert "UNLIMITED" not in output

    with pytest.raises(SystemExit):
        cli("issue", "--organization", "Acme", "--subject", "bob", "--role", "admin", "--unlimited")
    capsys.readouterr()

    cli(
        "issue",
        "--organization",
        "Acme",
        "--subject",
        "bob",
        "--role",
        "admin",
        "--unlimited",
        "--unlimited-reason",
        "break-glass key for the incident commander",
    )
    output = capsys.readouterr().out
    assert "UNLIMITED CREDENTIAL" in output
    assert "NEVER expires" in output

    cli("list", "--organization", "Acme")
    listed = capsys.readouterr().out
    assert "UNLIMITED" in listed
    assert "never expires" in listed


def test_unlimited_credential_is_labelled_in_the_access_review(engine, cli, capsys):
    cli("create-org", "--organization", "Acme")
    cli(
        "issue",
        "--organization",
        "Acme",
        "--subject",
        "alice",
        "--role",
        "admin",
        "--unlimited",
        "--unlimited-reason",
        "test",
    )
    capsys.readouterr()
    with Session(engine) as db:
        org = make_organization(db, "Acme")
        review = access_review(db, org.id)
        assert review["unlimited_credentials"]
        member = next(m for m in review["members"] if m["subject"] == "alice")
        assert member["credentials"][0]["unlimited"] is True
        assert "UNLIMITED" in member["credentials"][0]["label"]


# --------------------------------------------------------------------------------------
# 4. rotation
# --------------------------------------------------------------------------------------


def test_rotation_leaves_exactly_one_usable_credential(session, api, engine):
    org = make_organization(session)
    old, old_token = make_credential(session, org.id, "alice", "admin")
    session.commit()

    result = rotate_credential(session, old.id, days=30)
    session.commit()

    assert result.previous.id == old.id
    assert result.credential.id != old.id
    assert result.overlap_until is None

    with Session(engine) as db:
        live = [
            row
            for row in db.scalars(
                select(ApiCredentialRow).where(ApiCredentialRow.organization_id == org.id)
            ).all()
            if credential_is_live(row)
        ]
        assert [row.id for row in live] == [result.credential.id]
        retired = db.get(ApiCredentialRow, old.id)
        assert retired.revoked_at is not None
        assert retired.replaced_by_id == result.credential.id

    response = api.get("/api/v1/assessments", headers={"Authorization": f"Bearer {old_token}"})
    assert response.status_code == 401
    response = api.get("/api/v1/assessments", headers={"Authorization": f"Bearer {result.token}"})
    assert response.status_code == 200


def test_rotation_that_fails_midway_leaves_the_old_credential_alone(session, engine, monkeypatch):
    org = make_organization(session)
    old, _old_token = make_credential(session, org.id, "alice", "admin")
    session.commit()

    def boom() -> str:
        raise RuntimeError("token service unavailable")

    monkeypatch.setattr(lifecycle, "make_token", boom)
    with pytest.raises(RuntimeError):
        rotate_credential(session, old.id, days=30)
    session.rollback()

    with Session(engine) as db:
        rows = db.scalars(
            select(ApiCredentialRow).where(ApiCredentialRow.organization_id == org.id)
        ).all()
        assert len(rows) == 1
        assert rows[0].id == old.id
        assert rows[0].revoked_at is None
        assert credential_is_live(rows[0]) is True


def test_rotation_overlap_is_explicit_bounded_and_reported(cli, capsys):
    cli("create-org", "--organization", "Acme")
    cli("issue", "--organization", "Acme", "--subject", "alice", "--role", "admin")
    issued = capsys.readouterr().out
    credential_id = issued.split("Credential ID: ")[1].split("\n")[0]

    with pytest.raises(SystemExit):
        cli(
            "rotate",
            "--token-id",
            credential_id,
            "--overlap-seconds",
            str(lifecycle.MAX_ROTATION_OVERLAP_SECONDS + 1),
        )
    capsys.readouterr()

    cli("rotate", "--token-id", credential_id, "--overlap-seconds", "60")
    output = capsys.readouterr().out
    assert "Overlap" in output
    assert "expires by itself" in output

    cli("list", "--organization", "Acme")
    listed = capsys.readouterr().out
    assert "rotation overlap until" in listed


def test_rotation_is_audited(session, engine):
    org = make_organization(session)
    old, _token = make_credential(session, org.id, "alice", "admin")
    session.commit()
    rotate_credential(session, old.id, days=30)
    session.commit()
    with Session(engine) as db:
        events = audit_event_types(db, org.id)
        assert "credential.rotated" in events
        assert "credential.issued" in events


# --------------------------------------------------------------------------------------
# 5. de-provisioning on role change
# --------------------------------------------------------------------------------------


def test_reducing_a_membership_role_revokes_the_credential_that_relied_on_it(session, engine, api):
    org = make_organization(session)
    membership = make_membership(session, org.id, "alice", "reviewer")
    credential, token = make_credential(session, org.id, "alice", "reviewer")
    session.commit()

    change_membership_role(session, membership.id, "analyst", reason="left the review team")
    session.commit()

    with Session(engine) as db:
        row = db.get(ApiCredentialRow, credential.id)
        assert row.revoked_at is not None
        events = audit_event_types(db, org.id)
        assert "membership.role_changed" in events
        assert "credential.revoked" in events
        membership_row = db.get(OidcMembershipRow, membership.id)
        assert membership_row.role == "analyst"

    response = api.get("/api/v1/assessments", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_deactivating_a_member_clears_the_reviewer_assignment(session, engine):
    org = make_organization(session)
    membership = make_membership(session, org.id, "alice", "reviewer")
    assessment = make_assessment(session, org.id)
    assignment = assign_reviewer(session, assessment.id, "alice")
    session.commit()
    assignment_id = assignment.id

    disable_membership(session, membership.id, reason="left the company")
    session.commit()

    with Session(engine) as db:
        assert db.get(ReviewerAssignmentRow, assignment_id) is None
        events = audit_event_types(db, org.id)
        assert "membership.disabled" in events
        assert "reviewer.assignment_cleared" in events
        cleared = db.scalars(
            select(OrganizationAuditEventRow).where(
                OrganizationAuditEventRow.event_type == "reviewer.assignment_cleared"
            )
        ).all()
        # The assessment is recorded as a detail: a lifecycle event is
        # organization-scoped, not part of the assessment's own decision history.
        assert cleared[0].details["assessment_id"] == assessment.id


def test_the_unreviewable_assessment_can_be_assigned_to_somebody_else(session, engine, api):
    org = make_organization(session)
    membership = make_membership(session, org.id, "alice", "reviewer")
    make_membership(session, org.id, "bob", "reviewer")
    assessment = make_assessment(session, org.id)
    assign_reviewer(session, assessment.id, "alice")
    session.commit()
    organization_id = org.id
    assessment_id = assessment.id

    disable_membership(session, membership.id, reason="left the company")
    session.commit()

    with Session(engine) as db:
        # The assessment is not left waiting for somebody who cannot act.
        assert (
            db.scalar(
                select(ReviewerAssignmentRow).where(
                    ReviewerAssignmentRow.assessment_id == assessment_id
                )
            )
            is None
        )

    # A reviewer who can act can still be assigned.
    with Session(engine) as db:
        admin_row, admin_token = make_credential(db, organization_id, "operator-admin", "admin")
        db.commit()
    response = api.put(
        f"/api/v1/assessments/{assessment_id}/reviewer",
        json={"reviewer_subject": "bob"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["reviewer_subject"] == "bob"


# --------------------------------------------------------------------------------------
# 6. the last administrator is never locked out by accident
# --------------------------------------------------------------------------------------


def test_the_last_administrator_is_not_removed_without_an_explicit_override(session, engine):
    org = make_organization(session)
    membership = make_membership(session, org.id, "alice", "admin")
    credential, _token = make_credential(session, org.id, "alice", "admin")
    session.commit()

    with pytest.raises(LastAdminError):
        change_membership_role(session, membership.id, "analyst")
    session.rollback()

    with Session(engine) as db:
        assert db.get(ApiCredentialRow, credential.id).revoked_at is None
        assert db.get(OidcMembershipRow, membership.id).role == "admin"

    change_membership_role(session, membership.id, "analyst", override_last_admin=True)
    session.commit()
    with Session(engine) as db:
        assert db.get(ApiCredentialRow, credential.id).revoked_at is not None
        events = db.scalars(
            select(OrganizationAuditEventRow).where(
                OrganizationAuditEventRow.event_type == "membership.role_changed"
            )
        ).all()
        assert events[0].details["last_admin_override"] is True


def test_another_administrator_makes_the_role_change_allowed(session):
    org = make_organization(session)
    membership = make_membership(session, org.id, "alice", "admin")
    make_membership(session, org.id, "bob", "admin")
    session.commit()
    change_membership_role(session, membership.id, "analyst")
    assert session.get(OidcMembershipRow, membership.id).role == "analyst"


# --------------------------------------------------------------------------------------
# 7. the sweep
# --------------------------------------------------------------------------------------


def _drifted_state(session):
    """An organization with every kind of lifecycle drift."""
    org = make_organization(session, "Acme")
    make_membership(session, org.id, "alice", "analyst")
    excessive, _token = make_credential(session, org.id, "alice", "reviewer")
    expired, _token2 = make_credential(
        session, org.id, "bob", "admin", expires_in=timedelta(seconds=-5)
    )
    stale, _token3 = make_credential(session, org.id, "carol", "reviewer", revoked=True)
    assessment = make_assessment(session, org.id)
    assignment = assign_reviewer(session, assessment.id, "carol")
    session.commit()
    return org, excessive, expired, stale, assessment, assignment


def test_sweep_reports_by_default_and_only_acts_with_execute(engine, cli, capsys):
    with Session(engine) as db:
        org, excessive, expired, _stale, _assessment, assignment = _drifted_state(db)
        organization_id = org.id
        assignment_id = assignment.id
        excessive_id = excessive.id
        expired_id = expired.id

    cli("sweep-membership-lifecycle", "--organization", "Acme")
    output = capsys.readouterr().out
    assert "Dry run" in output
    assert "Nothing was changed" in output
    assert "expired_credential" in output
    assert "credential_role_exceeds_membership" in output
    assert "stale_reviewer_assignment" in output

    with Session(engine) as db:
        # Nothing changed ...
        assert db.get(ApiCredentialRow, excessive_id).revoked_at is None
        assert db.get(ApiCredentialRow, expired_id).revoked_at is None
        assert db.get(ReviewerAssignmentRow, assignment_id) is not None
        # ... and nothing was audited.
        assert audit_event_types(db, organization_id) == []

    cli("sweep-membership-lifecycle", "--organization", "Acme", "--execute")
    output = capsys.readouterr().out
    assert "Sweep executed" in output

    with Session(engine) as db:
        assert db.get(ApiCredentialRow, excessive_id).revoked_at is not None
        assert db.get(ApiCredentialRow, expired_id).revoked_at is not None
        assert db.get(ReviewerAssignmentRow, assignment_id) is None
        events = audit_event_types(db, organization_id)
        assert "credential.revoked" in events
        assert "reviewer.assignment_cleared" in events


def test_sweep_reports_memberships_without_a_credential(session):
    org = make_organization(session, "Acme")
    make_membership(session, org.id, "alice", "reviewer")
    session.commit()
    report = sweep_lifecycle(session, organization_id=org.id)
    assert [finding.kind for finding in report.findings] == ["member_without_credential"]
    assert report.findings[0].severity == "report"
    assert report.executed is False


# --------------------------------------------------------------------------------------
# 8. the access review
# --------------------------------------------------------------------------------------


def test_access_review_lists_credentials_expiry_and_assignments(engine, cli, capsys):
    cli("create-org", "--organization", "Acme")
    cli("issue", "--organization", "Acme", "--subject", "alice", "--role", "reviewer")
    issued = capsys.readouterr().out
    credential_id = issued.split("Credential ID: ")[1].split("\n")[0]

    with Session(engine) as db:
        org = make_organization(db, "Acme")
        assessment = make_assessment(db, org.id, "Quarterly pricing")
        assign_reviewer(db, assessment.id, "alice")
        db.commit()
        assessment_id = assessment.id

    cli("access-review", "--organization", "Acme")
    text = capsys.readouterr().out
    assert "ACCESS REVIEW - Acme" in text
    assert "alice" in text
    assert credential_id in text
    assert "Quarterly pricing" in text
    assert "OUTSTANDING REVIEWER ASSIGNMENTS" in text

    cli("access-review", "--organization", "Acme", "--format", "json")
    payload = json.loads(capsys.readouterr().out)
    assert payload["organization"]["name"] == "Acme"
    member = next(m for m in payload["members"] if m["subject"] == "alice")
    assert member["credentials"][0]["id"] == credential_id
    assert member["credentials"][0]["expires_at"] is not None
    assert member["credentials"][0]["state"] == "live"
    assert member["can_review"] is True
    assert member["assigned_assessments"][0]["assessment_id"] == assessment_id
    assert payload["outstanding_assignments"][0]["reviewer_can_act"] is True
    assert payload["live_credentials"] == 1


def test_access_review_is_exportable(engine, cli, capsys, tmp_path):
    cli("create-org", "--organization", "Acme")
    cli("issue", "--organization", "Acme", "--subject", "alice", "--role", "admin")
    capsys.readouterr()
    target = tmp_path / "review.csv"
    cli("access-review", "--organization", "Acme", "--format", "csv", "--output", str(target))
    capsys.readouterr()
    content = target.read_text(encoding="utf-8")
    assert "subject" in content.splitlines()[0]
    assert "alice" in content


# --------------------------------------------------------------------------------------
# 9. everything is audited
# --------------------------------------------------------------------------------------


def test_every_lifecycle_change_is_audited(session, engine):
    org = make_organization(session, "Acme")
    membership = make_membership(session, org.id, "alice", "reviewer")
    credential, _token = make_credential(session, org.id, "alice", "reviewer")
    assessment = make_assessment(session, org.id)
    assign_reviewer(session, assessment.id, "alice")
    session.commit()

    rotate_credential(session, credential.id, days=30)
    session.commit()
    change_membership_role(session, membership.id, "analyst")
    session.commit()
    disable_membership(session, membership.id)
    session.commit()

    with Session(engine) as db:
        events = audit_event_types(db, org.id)
        for expected in [
            "credential.issued",
            "credential.rotated",
            "membership.role_changed",
            "membership.disabled",
            "credential.revoked",
            "reviewer.assignment_cleared",
        ]:
            assert expected in events, (expected, events)
        # Operator actions are auditable but never a verified human identity.
        rows = db.scalars(select(OrganizationAuditEventRow)).all()
        assert all(row.actor_verified is False for row in rows)
        assert all(row.actor.startswith("operator") for row in rows)
        assert all(row.details["identity_unverified"] is True for row in rows)
