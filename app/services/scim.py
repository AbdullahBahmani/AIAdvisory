"""SCIM 2.0 user and group provisioning.

The OIDC path requires an operator to pre-provision an ``oidc_memberships`` row
before a person can sign in. SCIM is how an enterprise directory does that
automatically, so this module owns the mapping between the two worlds:

============  ==========================================================
SCIM          Stored identity
============  ==========================================================
``externalId``  the IdP subject — ``oidc_memberships.subject``, the value
                the identity provider puts in the OIDC ``sub`` claim
``userName``    the directory login; it resolves to the same membership
                subject and is stored as the directory attribute
``groups``      a *projection* of the organization and the role; a group
                never grants a role unless an operator configured an
                explicit group-to-role map
``active``      ``oidc_memberships.active`` — deactivation, not deletion
============  ==========================================================

Two rules dominate the implementation:

* **Deactivation, not deletion.** A membership carries review history and
  audit references; deleting it would orphan that history. A SCIM ``DELETE``
  therefore sets ``active = false`` and keeps the record.
* **No self-escalation.** The role is only ever taken from an allowlist, and
  ``admin`` is only grantable when the deployment explicitly opted in.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db.models import OidcMembershipRow
from app.db.provisioning_models import ScimUserRow
from app.services.scim_auth import KNOWN_ROLES, ScimContext
from app.services.scim_errors import (
    ScimError,
    invalid_filter,
    invalid_path,
    invalid_syntax,
    invalid_value,
    mutability,
    not_found,
    uniqueness,
)

SCIM_USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
SCIM_GROUP_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:Group"
SCIM_LIST_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
SCIM_PATCH_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
#: Extension namespace used for the role attribute.
SCIM_ROLE_EXTENSION = "urn:aiadvisory:params:scim:User:1.0"
#: Fully qualified name of the role attribute.
SCIM_ROLE_ATTRIBUTE = f"{SCIM_ROLE_EXTENSION}:role"

#: Default page size and the hard upper bound for ``count``.
DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 500

#: Attributes the SCIM filter may address. Anything else is rejected instead
#: of silently ignored, so a directory never believes it filtered something.
SUPPORTED_FILTER_ATTRIBUTES = ("userName", "externalId", "id", "active")

_FILTER_TERM = re.compile(
    r"^\s*(?P<attribute>[A-Za-z][A-Za-z0-9:._-]*)\s+eq\s+"
    r'(?P<value>"(?:[^"\\]|\\.)*"|true|false|[^\s()]+)\s*$',
    re.IGNORECASE,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _scim_list(resources: list[dict], total: int, start_index: int) -> dict:
    return {
        "schemas": [SCIM_LIST_SCHEMA],
        "totalResults": total,
        "startIndex": start_index,
        "itemsPerPage": len(resources),
        "Resources": resources,
    }


def _text(value: Any, field: str, *, max_length: int = 200, required: bool = True) -> str | None:
    if value is None:
        if required:
            raise invalid_value(f"{field} is required")
        return None
    if not isinstance(value, str):
        raise invalid_value(f"{field} must be a string")
    cleaned = value.strip()
    if not cleaned:
        if required:
            raise invalid_value(f"{field} must not be blank")
        return None
    if len(cleaned) > max_length:
        raise invalid_value(f"{field} must be at most {max_length} characters")
    return cleaned


def _emails(value: Any) -> list[dict] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise invalid_value("emails must be an array")
    result: list[dict] = []
    for item in value[:20]:
        if isinstance(item, str):
            result.append({"value": item, "primary": True})
            continue
        if not isinstance(item, dict):
            raise invalid_value("emails must contain strings or objects")
        address = _text(item.get("value"), "emails.value", max_length=320)
        entry = {"value": address, "primary": bool(item.get("primary", False))}
        if item.get("type"):
            entry["type"] = str(item["type"])[:40]
        result.append(entry)
    return result


def _name(value: Any) -> dict | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise invalid_value("name must be an object")
    name: dict[str, str] = {}
    for key in ("givenName", "familyName", "formatted"):
        part = _text(value.get(key), f"name.{key}", required=False)
        if part:
            name[key] = part
    return name or None


# ---------------------------------------------------------------------------
# Filtering and pagination
# ---------------------------------------------------------------------------


def parse_filter(expression: str | None) -> list[tuple[str, Any]]:
    """Parse the supported subset of the SCIM filter grammar.

    Supported: ``attribute eq "value"`` for ``userName``, ``externalId``,
    ``id`` and ``active``, optionally combined with ``and``. Every other filter
    is rejected with ``invalidFilter`` instead of being ignored.
    """
    if expression is None or not expression.strip():
        return []
    terms: list[tuple[str, Any]] = []
    for part in re.split(r"\s+and\s+", expression, flags=re.IGNORECASE):
        match = _FILTER_TERM.match(part)
        if match is None:
            raise invalid_filter("Unsupported SCIM filter expression")
        attribute = match.group("attribute")
        # SCIM attribute names are case insensitive.
        canonical = next(
            (x for x in SUPPORTED_FILTER_ATTRIBUTES if x.lower() == attribute.lower()), None
        )
        if canonical is None:
            raise invalid_filter(f"Unsupported filter attribute: {attribute}")
        raw = match.group("value")
        if raw.startswith('"') and raw.endswith('"') and len(raw) >= 2:
            value: Any = raw[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        elif raw.lower() == "true":
            value = True
        elif raw.lower() == "false":
            value = False
        else:
            value = raw
        terms.append((canonical, value))
    return terms


def _pagination(start_index: int | None, count: int | None) -> tuple[int, int]:
    start = 1 if start_index is None else start_index
    size = DEFAULT_PAGE_SIZE if count is None else count
    if start < 1:
        raise invalid_value("startIndex must be greater than or equal to 1")
    if size < 0:
        raise invalid_value("count must not be negative")
    if size > MAX_PAGE_SIZE:
        raise invalid_value(f"count must not be greater than {MAX_PAGE_SIZE}")
    return start, size


# ---------------------------------------------------------------------------
# Membership helpers
# ---------------------------------------------------------------------------


def _memberships_query(organization_id: str):
    return (
        select(OidcMembershipRow, ScimUserRow)
        .outerjoin(
            ScimUserRow,
            ScimUserRow.membership_id == OidcMembershipRow.id,
        )
        .where(OidcMembershipRow.organization_id == organization_id)
    )


def _overlay_for(db: Session, membership_id: str) -> ScimUserRow | None:
    return db.scalar(select(ScimUserRow).where(ScimUserRow.membership_id == membership_id))


def _find_membership(db: Session, organization_id: str, user_id: str) -> OidcMembershipRow:
    membership = db.scalar(
        select(OidcMembershipRow).where(
            OidcMembershipRow.id == user_id,
            OidcMembershipRow.organization_id == organization_id,
        )
    )
    if membership is None:
        # A user of another organization is indistinguishable from a user that
        # does not exist: cross-organization access is a 404, never a hint.
        raise not_found("User not found")
    return membership


def _find_by_subject(db: Session, organization_id: str, issuer: str, subject: str):
    return db.scalar(
        select(OidcMembershipRow).where(
            OidcMembershipRow.organization_id == organization_id,
            OidcMembershipRow.issuer == issuer,
            OidcMembershipRow.subject == subject,
        )
    )


def _user_name_of(membership: OidcMembershipRow, overlay: ScimUserRow | None) -> str:
    if overlay is not None and overlay.user_name:
        return overlay.user_name
    return membership.subject


def _user_name_taken(
    db: Session, organization_id: str, user_name: str, exclude_membership_id: str | None = None
) -> bool:
    """True when another user of the organization already uses this login."""
    overlay = db.scalar(
        select(ScimUserRow).where(
            ScimUserRow.organization_id == organization_id,
            ScimUserRow.user_name == user_name,
        )
    )
    if overlay is not None and overlay.membership_id != exclude_membership_id:
        return True
    subject = db.scalar(
        select(OidcMembershipRow).where(
            OidcMembershipRow.organization_id == organization_id,
            OidcMembershipRow.subject == user_name,
        )
    )
    return subject is not None and subject.id != exclude_membership_id


def _group_reference(role: str) -> dict:
    return {"value": f"role-{role}", "display": role, "type": "direct"}


def user_resource(membership: OidcMembershipRow, overlay: ScimUserRow | None) -> dict:
    """Render one membership as a SCIM user resource."""
    name = None
    if overlay is not None:
        parts = {
            "givenName": overlay.given_name,
            "familyName": overlay.family_name,
            "formatted": overlay.display_name,
        }
        parts = {key: value for key, value in parts.items() if value}
        name = parts or None
    resource: dict[str, Any] = {
        "schemas": [SCIM_USER_SCHEMA],
        "id": membership.id,
        "externalId": membership.subject,
        "userName": _user_name_of(membership, overlay),
        "active": bool(membership.active),
        "groups": [_group_reference(membership.role)],
        "meta": {
            "resourceType": "User",
            "created": _iso(overlay.created_at if overlay is not None else None),
            "lastModified": _iso(overlay.updated_at if overlay is not None else None),
            "location": f"/scim/v2/Users/{membership.id}",
        },
    }
    if name:
        resource["name"] = name
    if overlay is not None and overlay.emails:
        resource["emails"] = overlay.emails
    return resource


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Role resolution
# ---------------------------------------------------------------------------


def _requested_role(payload: dict) -> str | None:
    value = payload.get(SCIM_ROLE_ATTRIBUTE)
    if value is None:
        value = payload.get("role")
    if value is None:
        return None
    if not isinstance(value, str):
        raise invalid_value("role must be a string")
    return value.strip().lower()


def _requested_groups(payload: dict) -> list[str]:
    raw = payload.get("groups")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise invalid_value("groups must be an array")
    names: list[str] = []
    for item in raw[:50]:
        if isinstance(item, str):
            names.append(item)
        elif isinstance(item, dict):
            candidate = item.get("display") or item.get("value")
            if isinstance(candidate, str):
                names.append(candidate)
        else:
            raise invalid_value("groups must contain strings or objects")
    return names


def resolve_role(
    context: ScimContext, payload: dict, fallback: str | None = None
) -> tuple[str, str]:
    """Determine the role of a provisioned user.

    The role can only ever come from the allowlist. Precedence:

    1. an explicit role attribute (namespaced extension or plain ``role``);
    2. a SCIM group, but only through an operator-configured group-to-role
       map (``ADVISORY_SCIM_GROUP_ROLE_MAP``); without that map a group never
       grants anything;
    3. the fallback role — the configured default role on create, the current
       role on update, so an update that does not mention a role never demotes
       somebody.
    """
    config = context.config
    explicit = _requested_role(payload)
    if explicit is not None:
        if explicit not in KNOWN_ROLES:
            raise invalid_value(f"Unknown role: {explicit}")
        if explicit not in config.grantable_roles:
            # The credential is not allowed to grant this role. ``admin`` is
            # rejected here unless the deployment opted in explicitly.
            raise invalid_value(f"This SCIM credential may not grant the role {explicit!r}")
        return explicit, "attribute"
    for group in _requested_groups(payload):
        mapped = config.group_role_map.get(group)
        if mapped is not None:
            if mapped not in config.grantable_roles:
                raise invalid_value(f"This SCIM credential may not grant the role {mapped!r}")
            return mapped, "group"
    if fallback is not None:
        return fallback, "unchanged"
    return config.default_role, "default"


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def list_users(
    db: Session,
    context: ScimContext,
    *,
    filter_expression: str | None = None,
    start_index: int | None = None,
    count: int | None = None,
) -> dict:
    """List the users of the SCIM organization, with filtering and paging."""
    start, size = _pagination(start_index, count)
    terms = parse_filter(filter_expression)
    stmt = _memberships_query(context.organization_id)
    for attribute, value in terms:
        if attribute == "userName":
            stmt = stmt.where(
                or_(
                    OidcMembershipRow.subject == value,
                    ScimUserRow.user_name == value,
                )
            )
        elif attribute == "externalId":
            stmt = stmt.where(OidcMembershipRow.subject == value)
        elif attribute == "id":
            stmt = stmt.where(OidcMembershipRow.id == value)
        elif attribute == "active":
            if not isinstance(value, bool):
                raise invalid_filter("active must be compared with true or false")
            stmt = stmt.where(OidcMembershipRow.active.is_(value))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    if size == 0:
        return _scim_list([], total, start)
    rows = db.execute(
        stmt.order_by(OidcMembershipRow.subject, OidcMembershipRow.id).offset(start - 1).limit(size)
    ).all()
    resources = [user_resource(membership, overlay) for membership, overlay in rows]
    return _scim_list(resources, total, start)


def get_user(db: Session, context: ScimContext, user_id: str) -> dict:
    """Read one user of the SCIM organization."""
    membership = _find_membership(db, context.organization_id, user_id)
    return user_resource(membership, _overlay_for(db, membership.id))


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


def _require_user_schema(payload: dict) -> None:
    schemas = payload.get("schemas")
    if schemas is None:
        raise invalid_value("schemas is required and must include the SCIM core User schema")
    if not isinstance(schemas, list) or SCIM_USER_SCHEMA not in schemas:
        raise invalid_value(f"schemas must include {SCIM_USER_SCHEMA}")


def _overlay_values(payload: dict) -> dict:
    """The directory attributes stored in the SCIM overlay."""
    values: dict[str, Any] = {}
    user_name = _text(payload.get("userName"), "userName", required=False)
    if user_name is not None:
        values["user_name"] = user_name
    name = _name(payload.get("name"))
    if name:
        values["given_name"] = name.get("givenName")
        values["family_name"] = name.get("familyName")
        values["display_name"] = name.get("formatted")
    emails = _emails(payload.get("emails"))
    if emails is not None:
        values["emails"] = emails
    return values


def create_user(db: Session, context: ScimContext, payload: dict) -> tuple[dict, str, str]:
    """Provision a new membership from a SCIM create request."""
    if not isinstance(payload, dict):
        raise invalid_syntax("The request body must be a JSON object")
    _require_user_schema(payload)
    external_id = _text(payload.get("externalId"), "externalId", required=False)
    user_name = _text(payload.get("userName"), "userName", required=False)
    if external_id is None and user_name is None:
        raise invalid_value("externalId or userName is required")
    subject = external_id or user_name
    role, source = resolve_role(context, payload)
    active = payload.get("active", True)
    if not isinstance(active, bool):
        raise invalid_value("active must be a boolean")
    existing = _find_by_subject(db, context.organization_id, context.issuer, subject)
    if existing is not None:
        raise uniqueness("A user with this externalId already exists in this organization")
    if user_name is not None:
        if _user_name_taken(db, context.organization_id, user_name):
            raise uniqueness("A user with this userName already exists in this organization")
    membership = OidcMembershipRow(
        issuer=context.issuer,
        subject=subject,
        organization_id=context.organization_id,
        role=role,
        active=active,
    )
    db.add(membership)
    db.flush()
    values = _overlay_values(payload)
    overlay = ScimUserRow(
        membership_id=membership.id,
        organization_id=context.organization_id,
        external_id=subject,
        user_name=user_name or subject,
        display_name=values.get("display_name"),
        given_name=values.get("given_name"),
        family_name=values.get("family_name"),
        emails=values.get("emails"),
    )
    db.add(overlay)
    db.flush()
    return user_resource(membership, overlay), role, source


def _apply_overlay(db: Session, membership: OidcMembershipRow, values: dict) -> ScimUserRow:
    overlay = _overlay_for(db, membership.id)
    if overlay is None:
        overlay = ScimUserRow(
            membership_id=membership.id,
            organization_id=membership.organization_id,
            external_id=membership.subject,
            user_name=membership.subject,
        )
        db.add(overlay)
    for key, value in values.items():
        setattr(overlay, key, value)
    overlay.updated_at = _now()
    db.flush()
    return overlay


def replace_user(
    db: Session, context: ScimContext, user_id: str, payload: dict
) -> tuple[dict, str, str]:
    """Apply a SCIM ``PUT`` (replace) request to a user."""
    if not isinstance(payload, dict):
        raise invalid_syntax("The request body must be a JSON object")
    _require_user_schema(payload)
    membership = _find_membership(db, context.organization_id, user_id)
    external_id = _text(payload.get("externalId"), "externalId", required=False)
    if external_id is not None and external_id != membership.subject:
        raise mutability("externalId is immutable")
    role, source = resolve_role(context, payload, fallback=membership.role)
    active = payload.get("active", membership.active)
    if not isinstance(active, bool):
        raise invalid_value("active must be a boolean")
    values = _overlay_values(payload)
    user_name = values.get("user_name")
    if user_name is not None and _user_name_taken(
        db, context.organization_id, user_name, exclude_membership_id=membership.id
    ):
        raise uniqueness("A user with this userName already exists in this organization")
    membership.role = role
    membership.active = active
    overlay = _apply_overlay(db, membership, values)
    return user_resource(membership, overlay), role, source


#: Attributes a PATCH may address.
_PATCHABLE = {
    "active",
    "userName",
    "displayName",
    "role",
    SCIM_ROLE_ATTRIBUTE,
    "name.givenName",
    "name.familyName",
    "name.formatted",
    "emails",
}


def _patch_operations(payload: dict) -> list[dict]:
    """Normalize a SCIM PATCH body into a flat list of operations.

    Both the SCIM ``Operations`` array and a path-less attribute map are
    accepted; a path-less operation is expanded into one operation per
    attribute so the rest of the code only ever sees ``path``/``value`` pairs.
    """
    operations = payload.get("Operations")
    if operations is None:
        operations = payload.get("operations")
    if operations is None:
        raise invalid_syntax("A SCIM PATCH request must contain an Operations array")
    if not isinstance(operations, list) or not operations:
        raise invalid_syntax("A SCIM PATCH request must contain an Operations array")
    result: list[dict] = []
    for operation in operations:
        if not isinstance(operation, dict):
            raise invalid_syntax("Each SCIM operation must be an object")
        op = str(operation.get("op", "replace")).lower()
        if op not in {"add", "replace", "remove"}:
            raise invalid_syntax(f"Unsupported SCIM operation: {op!r}")
        path = operation.get("path")
        value = operation.get("value")
        if path is None:
            if not isinstance(value, dict):
                raise invalid_path("A path-less SCIM operation must carry an attribute map")
            for key, item in value.items():
                result.append({"op": "replace", "path": key, "value": item})
            continue
        result.append({"op": op, "path": path, "value": value})
    if not result:
        raise invalid_syntax("A SCIM PATCH request must contain at least one operation")
    return result


def patch_user(
    db: Session, context: ScimContext, user_id: str, payload: dict
) -> tuple[dict, str | None, str]:
    """Apply a SCIM ``PATCH`` request to a user."""
    if not isinstance(payload, dict):
        raise invalid_syntax("The request body must be a JSON object")
    membership = _find_membership(db, context.organization_id, user_id)
    operations = _patch_operations(payload)
    values: dict[str, Any] = {}
    role: str | None = None
    source = "attribute"
    active = membership.active
    for operation in operations:
        op = operation["op"]
        path = operation["path"]
        value = operation["value"]
        if not isinstance(path, str):
            raise invalid_path("SCIM operation path must be a string")
        normalized = path.strip()
        if normalized.lower().startswith("urn:aiadvisory:params:scim:user:1.0:"):
            normalized = "role"
        if normalized.lower() == "externalid":
            raise mutability("externalId is immutable")
        if normalized not in _PATCHABLE:
            raise invalid_path(f"Unsupported SCIM patch path: {path}")
        if op == "remove":
            if normalized == "active":
                active = False
                continue
            raise invalid_path(f"Removing {normalized} is not supported")
        if normalized == "active":
            if not isinstance(value, bool):
                raise invalid_value("active must be a boolean")
            active = value
        elif normalized == "userName":
            values["user_name"] = _text(value, "userName")
        elif normalized == "displayName":
            values["display_name"] = _text(value, "displayName", required=False)
        elif normalized == "role":
            candidate = str(value).strip().lower() if value is not None else ""
            if candidate not in KNOWN_ROLES:
                raise invalid_value(f"Unknown role: {candidate}")
            if candidate not in context.config.grantable_roles:
                raise invalid_value(f"This SCIM credential may not grant the role {candidate!r}")
            role = candidate
        elif normalized == "name.givenName":
            values["given_name"] = _text(value, "name.givenName", required=False)
        elif normalized == "name.familyName":
            values["family_name"] = _text(value, "name.familyName", required=False)
        elif normalized == "name.formatted":
            values["display_name"] = _text(value, "name.formatted", required=False)
        elif normalized == "emails":
            values["emails"] = _emails(value)
    user_name = values.get("user_name")
    if user_name is not None and _user_name_taken(
        db, context.organization_id, user_name, exclude_membership_id=membership.id
    ):
        raise uniqueness("A user with this userName already exists in this organization")
    if role is not None:
        membership.role = role
    membership.active = active
    overlay = _apply_overlay(db, membership, values)
    return user_resource(membership, overlay), role, source


def deactivate_user(db: Session, context: ScimContext, user_id: str) -> OidcMembershipRow:
    """Deactivate a user.

    A SCIM ``DELETE`` deactivates the membership instead of deleting the row:
    the membership is referenced by review history and audit events, and
    deleting it would orphan that history.
    """
    membership = _find_membership(db, context.organization_id, user_id)
    membership.active = False
    db.flush()
    return membership


# ---------------------------------------------------------------------------
# Groups
# ---------------------------------------------------------------------------


def _group_members(db: Session, context: ScimContext, role: str | None) -> list[dict]:
    stmt = _memberships_query(context.organization_id)
    if role is not None:
        stmt = stmt.where(OidcMembershipRow.role == role)
    rows = db.execute(stmt.order_by(OidcMembershipRow.subject, OidcMembershipRow.id)).all()
    return [
        {
            "value": membership.id,
            "display": _user_name_of(membership, overlay),
            "$ref": f"/scim/v2/Users/{membership.id}",
        }
        for membership, overlay in rows
    ]


def list_groups(db: Session, context: ScimContext) -> dict:
    """List the groups of the SCIM organization."""
    roles = db.scalars(
        select(OidcMembershipRow.role)
        .where(OidcMembershipRow.organization_id == context.organization_id)
        .distinct()
        .order_by(OidcMembershipRow.role)
    ).all()
    resources = [
        {
            "schemas": [SCIM_GROUP_SCHEMA],
            "id": "organization",
            "displayName": context.organization.name,
            "members": _group_members(db, context, None),
            "meta": {"resourceType": "Group", "location": "/scim/v2/Groups/organization"},
        }
    ]
    for role in roles:
        resources.append(
            {
                "schemas": [SCIM_GROUP_SCHEMA],
                "id": f"role-{role}",
                "displayName": role,
                "members": _group_members(db, context, role),
                "meta": {
                    "resourceType": "Group",
                    "location": f"/scim/v2/Groups/role-{role}",
                },
            }
        )
    return _scim_list(resources, len(resources), 1)


def get_group(db: Session, context: ScimContext, group_id: str) -> dict:
    """Read one group of the SCIM organization."""
    if group_id == "organization":
        return {
            "schemas": [SCIM_GROUP_SCHEMA],
            "id": "organization",
            "displayName": context.organization.name,
            "members": _group_members(db, context, None),
            "meta": {"resourceType": "Group", "location": "/scim/v2/Groups/organization"},
        }
    if not group_id.startswith("role-"):
        raise not_found("Group not found")
    role = group_id[len("role-") :]
    members = _group_members(db, context, role)
    if not members:
        raise not_found("Group not found")
    return {
        "schemas": [SCIM_GROUP_SCHEMA],
        "id": group_id,
        "displayName": role,
        "members": members,
        "meta": {"resourceType": "Group", "location": f"/scim/v2/Groups/{group_id}"},
    }


__all__ = [
    "SCIM_GROUP_SCHEMA",
    "SCIM_LIST_SCHEMA",
    "SCIM_USER_SCHEMA",
    "ScimError",
    "create_user",
    "deactivate_user",
    "get_group",
    "get_user",
    "list_groups",
    "list_users",
    "parse_filter",
    "patch_user",
    "replace_user",
    "resolve_role",
    "user_resource",
]
