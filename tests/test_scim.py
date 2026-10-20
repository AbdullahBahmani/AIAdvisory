"""SCIM 2.0 provisioning: authentication, mapping, isolation and auditing.

The endpoint provisions identities, so the tests focus on the controls that
make it safe to expose: a dedicated credential, one organization, an allowlist
of roles, deactivation instead of deletion and an audit trail for every
mutation.
"""

from __future__ import annotations

import ast
import inspect

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import OidcMembershipRow, OrganizationRow
from app.db.provisioning_models import OrganizationAuditEventRow, ScimUserRow
from app.db.session import get_db
from app.main import app
from app.services import scim_auth

SCIM_TOKEN = "scim-token-for-tests-0123456789-abcdefghijklmnop"
OTHER_TOKEN = "scim-token-for-tests-0123456789-abcdefghijklmnoq"
SCIM_ORG = "Directory Org"
SCIM_ISSUER = "https://idp.example.com"
SCIM_HEADERS = {"Authorization": f"Bearer {SCIM_TOKEN}"}


@pytest.fixture
def scim_client(monkeypatch, db_engine):
    """A client bound to an isolated database with SCIM configured."""
    monkeypatch.delenv("ADVISORY_DEMO_MODE", raising=False)
    monkeypatch.delenv("ADVISORY_SCIM_ALLOW_ADMIN", raising=False)
    monkeypatch.delenv("ADVISORY_SCIM_ROLES", raising=False)
    monkeypatch.delenv("ADVISORY_SCIM_GROUP_ROLE_MAP", raising=False)
    monkeypatch.setenv("ADVISORY_SCIM_TOKEN", SCIM_TOKEN)
    monkeypatch.setenv("ADVISORY_SCIM_ORGANIZATION", SCIM_ORG)
    monkeypatch.setenv("ADVISORY_SCIM_ISSUER", SCIM_ISSUER)
    engine = db_engine

    def override_db():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client:
        with Session(engine) as db:
            db.add(OrganizationRow(name=SCIM_ORG))
            db.add(OrganizationRow(name="Other Org"))
            db.commit()
        yield client, engine
    app.dependency_overrides.clear()


def _user_payload(**overrides) -> dict:
    payload = {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "externalId": "sub-alice",
        "userName": "alice@example.com",
        "active": True,
    }
    payload.update(overrides)
    return payload


def _create(client, **overrides) -> dict:
    payload = _user_payload(**overrides)
    if payload.get("userName", "keep") is None:
        payload.pop("userName")
    response = client.post("/scim/v2/Users", json=payload, headers=SCIM_HEADERS)
    assert response.status_code == 201, response.text
    return response.json()


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


def test_scim_rejects_unauthenticated_requests(scim_client):
    client, _ = scim_client
    response = client.get("/scim/v2/Users")
    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/scim+json")
    body = response.json()
    assert body["schemas"] == ["urn:ietf:params:scim:api:messages:2.0:Error"]
    assert body["status"] == "401"
    assert response.headers.get("WWW-Authenticate") == "Bearer"


def test_scim_rejects_a_wrong_credential(scim_client):
    client, _ = scim_client
    response = client.get("/scim/v2/Users", headers={"Authorization": f"Bearer {OTHER_TOKEN}"})
    assert response.status_code == 401
    assert response.json()["schemas"] == ["urn:ietf:params:scim:api:messages:2.0:Error"]


def test_scim_accepts_the_dedicated_credential(scim_client):
    client, _ = scim_client
    response = client.get("/scim/v2/Users", headers=SCIM_HEADERS)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/scim+json")
    assert response.json()["schemas"] == ["urn:ietf:params:scim:api:messages:2.0:ListResponse"]


def test_scim_is_not_reachable_with_the_demo_bypass(scim_client, monkeypatch):
    """The local demo switch must not open the provisioning endpoint."""
    client, _ = scim_client
    monkeypatch.setenv("ADVISORY_DEMO_MODE", "1")
    assert client.get("/scim/v2/Users").status_code == 401
    # A user API credential is not a SCIM credential either.
    assert (
        client.get(
            "/scim/v2/Users", headers={"Authorization": "Bearer aia_" + "x" * 40}
        ).status_code
        == 401
    )
    # The dedicated credential still works in the local demo mode.
    assert client.get("/scim/v2/Users", headers=SCIM_HEADERS).status_code == 200


def test_scim_is_disabled_without_configuration(monkeypatch, db_engine):
    monkeypatch.delenv("ADVISORY_DEMO_MODE", raising=False)
    monkeypatch.delenv("ADVISORY_SCIM_TOKEN", raising=False)
    monkeypatch.delenv("ADVISORY_SCIM_ORGANIZATION", raising=False)
    engine = db_engine

    def override_db():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client:
        response = client.get("/scim/v2/Users", headers=SCIM_HEADERS)
        assert response.status_code == 503
        assert response.json()["schemas"] == ["urn:ietf:params:scim:api:messages:2.0:Error"]
    app.dependency_overrides.clear()


def test_scim_token_comparison_is_constant_time():
    """The credential comparison must not have an early exit."""
    source = inspect.getsource(scim_auth.token_matches)
    assert "compare_digest" in source
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            for operator in node.ops:
                assert not isinstance(operator, (ast.Eq, ast.NotEq)), (
                    "the SCIM token comparison must not use == or !="
                )


# ---------------------------------------------------------------------------
# User lifecycle
# ---------------------------------------------------------------------------


def test_scim_create_read_and_list(scim_client):
    client, engine = scim_client
    created = _create(client, **{"urn:aiadvisory:params:scim:User:1.0:role": "reviewer"})
    assert created["schemas"] == ["urn:ietf:params:scim:schemas:core:2.0:User"]
    assert created["externalId"] == "sub-alice"
    assert created["userName"] == "alice@example.com"
    assert created["active"] is True
    assert created["groups"] == [
        {"value": "role-reviewer", "display": "reviewer", "type": "direct"}
    ]
    assert created["meta"]["resourceType"] == "User"

    fetched = client.get(f"/scim/v2/Users/{created['id']}", headers=SCIM_HEADERS)
    assert fetched.status_code == 200
    assert fetched.json()["id"] == created["id"]

    with Session(engine) as db:
        membership = db.scalar(
            select(OidcMembershipRow).where(OidcMembershipRow.id == created["id"])
        )
        assert membership is not None
        assert membership.subject == "sub-alice"
        assert membership.issuer == SCIM_ISSUER
        assert membership.organization_id is not None
        assert membership.role == "reviewer"
        assert membership.active is True

    listing = client.get("/scim/v2/Users", headers=SCIM_HEADERS)
    assert listing.status_code == 200
    body = listing.json()
    assert body["totalResults"] == 1
    assert body["startIndex"] == 1
    assert body["itemsPerPage"] == 1
    assert [x["id"] for x in body["Resources"]] == [created["id"]]


def test_scim_filters_and_paginates(scim_client):
    client, _ = scim_client
    first = _create(client, externalId="sub-1", userName="one@example.com")
    second = _create(client, externalId="sub-2", userName="two@example.com")
    third = _create(client, externalId="sub-3", userName="three@example.com")

    by_user_name = client.get(
        "/scim/v2/Users", params={"filter": 'userName eq "two@example.com"'}, headers=SCIM_HEADERS
    )
    assert by_user_name.status_code == 200
    assert [x["id"] for x in by_user_name.json()["Resources"]] == [second["id"]]

    by_external_id = client.get(
        "/scim/v2/Users", params={"filter": 'externalId eq "sub-3"'}, headers=SCIM_HEADERS
    )
    assert [x["id"] for x in by_external_id.json()["Resources"]] == [third["id"]]

    by_id = client.get(
        "/scim/v2/Users", params={"filter": f'id eq "{first["id"]}"'}, headers=SCIM_HEADERS
    )
    assert [x["id"] for x in by_id.json()["Resources"]] == [first["id"]]

    page = client.get(
        "/scim/v2/Users", params={"startIndex": 2, "count": 1}, headers=SCIM_HEADERS
    ).json()
    assert page["totalResults"] == 3
    assert page["startIndex"] == 2
    assert page["itemsPerPage"] == 1
    assert len(page["Resources"]) == 1

    unsupported = client.get(
        "/scim/v2/Users", params={"filter": 'displayName co "x"'}, headers=SCIM_HEADERS
    )
    assert unsupported.status_code == 400
    assert unsupported.json()["scimType"] == "invalidFilter"


def test_scim_update_and_patch(scim_client):
    client, engine = scim_client
    created = _create(client)
    replaced = client.put(
        f"/scim/v2/Users/{created['id']}",
        json={
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
            "externalId": "sub-alice",
            "userName": "alice@example.com",
            "active": False,
            "name": {"givenName": "Alice", "familyName": "Anderson"},
            "urn:aiadvisory:params:scim:User:1.0:role": "analyst",
        },
        headers=SCIM_HEADERS,
    )
    assert replaced.status_code == 200, replaced.text
    assert replaced.json()["active"] is False
    assert replaced.json()["groups"][0]["display"] == "analyst"
    assert replaced.json()["name"]["givenName"] == "Alice"

    patched = client.patch(
        f"/scim/v2/Users/{created['id']}",
        json={
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [
                {"op": "replace", "path": "active", "value": True},
                {"op": "replace", "path": "role", "value": "reviewer"},
            ],
        },
        headers=SCIM_HEADERS,
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["active"] is True
    assert patched.json()["groups"][0]["display"] == "reviewer"

    immutable = client.patch(
        f"/scim/v2/Users/{created['id']}",
        json={
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [{"op": "replace", "path": "externalId", "value": "sub-other"}],
        },
        headers=SCIM_HEADERS,
    )
    assert immutable.status_code == 400
    assert immutable.json()["scimType"] == "mutability"


def test_scim_delete_deactivates_and_keeps_the_history(scim_client):
    client, engine = scim_client
    created = _create(client)
    deleted = client.delete(f"/scim/v2/Users/{created['id']}", headers=SCIM_HEADERS)
    assert deleted.status_code == 204

    with Session(engine) as db:
        membership = db.get(OidcMembershipRow, created["id"])
        assert membership is not None, "the membership row must survive a SCIM delete"
        assert membership.active is False
        events = db.scalars(
            select(OrganizationAuditEventRow)
            .where(OrganizationAuditEventRow.organization_id == membership.organization_id)
            .order_by(OrganizationAuditEventRow.created_at, OrganizationAuditEventRow.id)
        ).all()
    types = [event.event_type for event in events]
    assert "scim.user_created" in types
    assert "scim.user_deactivated" in types
    deactivated = next(event for event in events if event.event_type == "scim.user_deactivated")
    assert deactivated.details["deactivation_only"] is True
    assert deactivated.details["subject"] == "sub-alice"

    # The deactivated user is still readable, and it is marked inactive.
    fetched = client.get(f"/scim/v2/Users/{created['id']}", headers=SCIM_HEADERS)
    assert fetched.status_code == 200
    assert fetched.json()["active"] is False


def test_scim_mutations_are_audited_through_the_shared_gate(scim_client):
    client, engine = scim_client
    created = _create(client)
    client.patch(
        f"/scim/v2/Users/{created['id']}",
        json={
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [{"op": "replace", "path": "active", "value": False}],
        },
        headers=SCIM_HEADERS,
    )
    with Session(engine) as db:
        events = db.scalars(
            select(OrganizationAuditEventRow).order_by(OrganizationAuditEventRow.id)
        ).all()
    assert events, "every SCIM mutation must write an audit event"
    for event in events:
        # The actor is the server-side SCIM service account, never request input.
        assert event.actor == scim_auth.SCIM_SUBJECT
        assert event.actor_verified is True
        assert event.actor_organization_id is not None
        assert event.details["identity_unverified"] is False
        assert event.details["identity_source"] == "scim_bearer"
    assert {event.event_type for event in events} >= {
        "scim.user_created",
        "scim.user_updated",
    }


# ---------------------------------------------------------------------------
# Roles and escalation
# ---------------------------------------------------------------------------


def test_scim_rejects_a_role_outside_the_allowlist(scim_client):
    client, _ = scim_client
    response = client.post(
        "/scim/v2/Users",
        json=_user_payload(**{"urn:aiadvisory:params:scim:User:1.0:role": "superuser"}),
        headers=SCIM_HEADERS,
    )
    assert response.status_code == 400
    body = response.json()
    assert body["scimType"] == "invalidValue"
    assert body["schemas"] == ["urn:ietf:params:scim:api:messages:2.0:Error"]


def test_scim_cannot_grant_admin_without_an_explicit_opt_in(scim_client):
    client, _ = scim_client
    response = client.post(
        "/scim/v2/Users",
        json=_user_payload(**{"urn:aiadvisory:params:scim:User:1.0:role": "admin"}),
        headers=SCIM_HEADERS,
    )
    assert response.status_code == 400
    assert "admin" in response.json()["detail"]


def test_scim_grants_admin_only_with_the_explicit_opt_in(scim_client, monkeypatch):
    client, _ = scim_client
    monkeypatch.setenv("ADVISORY_SCIM_ALLOW_ADMIN", "1")
    response = client.post(
        "/scim/v2/Users",
        json=_user_payload(**{"urn:aiadvisory:params:scim:User:1.0:role": "admin"}),
        headers=SCIM_HEADERS,
    )
    assert response.status_code == 201, response.text
    assert response.json()["groups"][0]["display"] == "admin"


def test_scim_cannot_escalate_a_user_through_the_group_projection(scim_client, monkeypatch):
    """Without an operator-configured group map, a group grants nothing."""
    client, _ = scim_client
    created = _create(client, **{"urn:aiadvisory:params:scim:User:1.0:role": "viewer"})
    response = client.put(
        f"/scim/v2/Users/{created['id']}",
        json={
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
            "externalId": "sub-alice",
            "userName": "alice@example.com",
            "groups": [{"value": "role-admin", "display": "admin"}],
        },
        headers=SCIM_HEADERS,
    )
    assert response.status_code == 200
    assert response.json()["groups"][0]["display"] == "viewer"

    monkeypatch.setenv("ADVISORY_SCIM_GROUP_ROLE_MAP", '{"Security Team": "reviewer"}')
    mapped = client.put(
        f"/scim/v2/Users/{created['id']}",
        json={
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
            "externalId": "sub-alice",
            "userName": "alice@example.com",
            "groups": [{"value": "security-team", "display": "Security Team"}],
        },
        headers=SCIM_HEADERS,
    )
    assert mapped.status_code == 200
    assert mapped.json()["groups"][0]["display"] == "reviewer"


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------


def test_scim_cannot_reach_another_organization(scim_client):
    client, engine = scim_client
    with Session(engine) as db:
        other = db.scalar(select(OrganizationRow).where(OrganizationRow.name == "Other Org"))
        foreign = OidcMembershipRow(
            issuer=SCIM_ISSUER,
            subject="sub-outsider",
            organization_id=other.id,
            role="admin",
            active=True,
        )
        db.add(foreign)
        db.commit()
        foreign_id = foreign.id
        own = db.scalar(select(OrganizationRow).where(OrganizationRow.name == SCIM_ORG))

    # A user of another organization is not readable, not writable and not
    # listed: the SCIM credential is pinned to one organization.
    assert client.get(f"/scim/v2/Users/{foreign_id}", headers=SCIM_HEADERS).status_code == 404
    assert (
        client.put(
            f"/scim/v2/Users/{foreign_id}",
            json={"schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"], "userName": "x"},
            headers=SCIM_HEADERS,
        ).status_code
        == 404
    )
    assert client.delete(f"/scim/v2/Users/{foreign_id}", headers=SCIM_HEADERS).status_code == 404

    listing = client.get("/scim/v2/Users", headers=SCIM_HEADERS).json()
    assert listing["totalResults"] == 0
    assert listing["Resources"] == []

    # The organization is never taken from the request body either.
    created = _create(client)
    with Session(engine) as db:
        membership = db.get(OidcMembershipRow, created["id"])
        assert membership.organization_id == own.id


def test_scim_error_bodies_use_the_scim_schema(scim_client):
    client, _ = scim_client
    missing = client.get("/scim/v2/Users/does-not-exist", headers=SCIM_HEADERS)
    assert missing.status_code == 404
    body = missing.json()
    assert body["schemas"] == ["urn:ietf:params:scim:api:messages:2.0:Error"]
    assert body["status"] == "404"
    assert "detail" in body


def test_scim_rejects_a_duplicate_user(scim_client):
    client, _ = scim_client
    _create(client)
    duplicate = client.post("/scim/v2/Users", json=_user_payload(), headers=SCIM_HEADERS)
    assert duplicate.status_code == 409
    assert duplicate.json()["scimType"] == "uniqueness"


def test_scim_groups_describe_the_organization_and_the_roles(scim_client):
    client, _ = scim_client
    created = _create(client, **{"urn:aiadvisory:params:scim:User:1.0:role": "reviewer"})
    groups = client.get("/scim/v2/Groups", headers=SCIM_HEADERS)
    assert groups.status_code == 200
    body = groups.json()
    assert body["schemas"] == ["urn:ietf:params:scim:api:messages:2.0:ListResponse"]
    ids = [group["id"] for group in body["Resources"]]
    assert "organization" in ids
    assert "role-reviewer" in ids
    organization = next(g for g in body["Resources"] if g["id"] == "organization")
    assert [m["value"] for m in organization["members"]] == [created["id"]]

    role_group = client.get("/scim/v2/Groups/role-reviewer", headers=SCIM_HEADERS)
    assert role_group.status_code == 200
    assert role_group.json()["displayName"] == "reviewer"
    assert client.get("/scim/v2/Groups/role-nobody", headers=SCIM_HEADERS).status_code == 404


def test_scim_requires_the_core_user_schema(scim_client):
    client, _ = scim_client
    response = client.post(
        "/scim/v2/Users",
        json={"externalId": "sub-alice", "userName": "alice@example.com"},
        headers=SCIM_HEADERS,
    )
    assert response.status_code == 400
    assert response.json()["scimType"] == "invalidValue"


def test_scim_accepts_a_user_without_a_directory_login(scim_client):
    """The IdP subject alone is enough to provision an identity."""
    client, _ = scim_client
    created = _create(client, userName=None)
    assert created["externalId"] == "sub-alice"
    assert created["userName"] == "sub-alice"


def test_scim_rejects_an_unsupported_patch_path(scim_client):
    client, _ = scim_client
    created = _create(client)
    response = client.patch(
        f"/scim/v2/Users/{created['id']}",
        json={
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [{"op": "replace", "path": "nickName", "value": "Al"}],
        },
        headers=SCIM_HEADERS,
    )
    assert response.status_code == 400
    assert response.json()["scimType"] == "invalidPath"


def test_scim_overlay_records_the_directory_attributes(scim_client):
    client, engine = scim_client
    created = _create(
        client,
        name={"givenName": "Alice", "familyName": "Anderson", "formatted": "Alice Anderson"},
        emails=[{"value": "alice@example.com", "primary": True}],
    )
    with Session(engine) as db:
        overlay = db.scalar(select(ScimUserRow).where(ScimUserRow.membership_id == created["id"]))
        assert overlay is not None
        assert overlay.user_name == "alice@example.com"
        assert overlay.external_id == "sub-alice"
        assert overlay.given_name == "Alice"
        assert overlay.emails == [{"value": "alice@example.com", "primary": True}]
