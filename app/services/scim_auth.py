"""Authentication and role limits for the SCIM provisioning endpoint.

SCIM *provisions identities*: whoever holds this credential can create, change
and deactivate organization memberships. That is a stronger capability than
anything the ordinary user API offers, so it uses its own secret:

* the credential is a dedicated bearer token from ``ADVISORY_SCIM_TOKEN`` — it
  is **not** a user API credential (``aia_…``), **not** an OIDC access token
  and **not** the synthetic demo identity. The local demo switch
  (``ADVISORY_DEMO_MODE``) never opens this endpoint;
* the token is compared in constant time (SHA-256 digests through
  :func:`secrets.compare_digest`), so neither the value nor its length can be
  recovered from the response time;
* the credential is scoped to exactly **one** organization
  (``ADVISORY_SCIM_ORGANIZATION``). The organization is never taken from the
  request body, so a SCIM request physically cannot address another tenant;
* the roles it may grant are allowlisted (``ADVISORY_SCIM_ROLES``). The
  ``admin`` role is excluded unless the operator opts in explicitly with
  ``ADVISORY_SCIM_ALLOW_ADMIN=1``.
"""

from __future__ import annotations

import hashlib
import os
import secrets
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import OrganizationRow
from app.security import Principal
from app.services.scim_errors import ScimError

#: Environment variable holding the dedicated SCIM bearer token.
SCIM_TOKEN_ENV = "ADVISORY_SCIM_TOKEN"
#: Environment variable naming the single organization this credential serves.
SCIM_ORGANIZATION_ENV = "ADVISORY_SCIM_ORGANIZATION"
#: Environment variable holding the issuer of the provisioned memberships.
SCIM_ISSUER_ENV = "ADVISORY_SCIM_ISSUER"
#: Set to ``1`` to let this credential grant the ``admin`` role.
SCIM_ALLOW_ADMIN_ENV = "ADVISORY_SCIM_ALLOW_ADMIN"
#: Comma separated list of roles this credential may grant.
SCIM_ROLES_ENV = "ADVISORY_SCIM_ROLES"
#: Role used when a provisioning request does not name one.
SCIM_DEFAULT_ROLE_ENV = "ADVISORY_SCIM_DEFAULT_ROLE"
#: Optional JSON object mapping a SCIM group name to a role.
SCIM_GROUP_ROLE_MAP_ENV = "ADVISORY_SCIM_GROUP_ROLE_MAP"

#: Minimum length of the dedicated SCIM token.
MIN_TOKEN_LENGTH = 32
#: Subject recorded for the provisioning service account in the audit trail.
SCIM_SUBJECT = "scim_provisioner"
#: Role label of the provisioning service account. It is deliberately *not* an
#: organization role: the service account may provision identities but it holds
#: no reviewer or administrator authority of its own.
SCIM_SERVICE_ROLE = "scim_provisioner"

#: Roles the platform knows about. The vocabulary is fixed by the platform
#: (``app/security.py``); an organization policy may narrow it but never
#: invent new roles.
KNOWN_ROLES = ("viewer", "analyst", "reviewer", "admin")
#: Roles a SCIM credential may grant when the operator did not say otherwise.
DEFAULT_GRANTABLE_ROLES = ("viewer", "analyst", "reviewer")


@dataclass(frozen=True)
class ScimConfig:
    """The resolved SCIM configuration of this deployment."""

    token: str
    organization: str
    issuer: str
    grantable_roles: frozenset[str]
    default_role: str
    allow_admin: bool
    group_role_map: dict[str, str]


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes"}


def load_config() -> ScimConfig | None:
    """Read the SCIM configuration, or ``None`` when SCIM is not configured.

    A deployment that has not set both the token and the organization has no
    SCIM endpoint at all; this is the safe default and it cannot be reached by
    accident.
    """
    token = os.getenv(SCIM_TOKEN_ENV, "").strip()
    organization = os.getenv(SCIM_ORGANIZATION_ENV, "").strip()
    if not token or not organization:
        return None
    if len(token) < MIN_TOKEN_LENGTH:
        raise ScimError(503, f"{SCIM_TOKEN_ENV} must be at least {MIN_TOKEN_LENGTH} characters")
    issuer = (
        os.getenv(SCIM_ISSUER_ENV, "").strip() or os.getenv("ADVISORY_OIDC_ISSUER", "")
    ).strip()
    if not issuer:
        raise ScimError(503, f"SCIM provisioning requires {SCIM_ISSUER_ENV}")
    allow_admin = _env_flag(SCIM_ALLOW_ADMIN_ENV)
    grantable = _grantable_roles(allow_admin)
    default_role = os.getenv(SCIM_DEFAULT_ROLE_ENV, "").strip() or "viewer"
    if default_role not in grantable:
        raise ScimError(503, f"{SCIM_DEFAULT_ROLE_ENV} is not a role this credential may grant")
    return ScimConfig(
        token=token,
        organization=organization,
        issuer=issuer.rstrip("/"),
        grantable_roles=frozenset(grantable),
        default_role=default_role,
        allow_admin=allow_admin,
        group_role_map=_group_role_map(grantable),
    )


def _grantable_roles(allow_admin: bool) -> tuple[str, ...]:
    raw = os.getenv(SCIM_ROLES_ENV, "").strip()
    if not raw:
        roles = list(DEFAULT_GRANTABLE_ROLES)
    else:
        roles = [item.strip() for item in raw.split(",") if item.strip()]
    unknown = [role for role in roles if role not in KNOWN_ROLES]
    if unknown:
        raise ScimError(503, f"{SCIM_ROLES_ENV} contains unknown roles: {', '.join(unknown)}")
    if allow_admin:
        # The explicit opt-in is the only way to reach the admin role.
        if "admin" not in roles:
            roles.append("admin")
    else:
        # An operator that listed admin but did not opt in does not get admin.
        roles = [role for role in roles if role != "admin"]
    if not roles:
        raise ScimError(503, f"{SCIM_ROLES_ENV} does not allow any role")
    return tuple(roles)


def _group_role_map(grantable: tuple[str, ...]) -> dict[str, str]:
    """Parse the optional group-to-role map.

    The map is empty by default: a SCIM group never grants a role unless an
    operator configured it explicitly, and even then the mapped role has to be
    inside the grantable allowlist.
    """
    raw = os.getenv(SCIM_GROUP_ROLE_MAP_ENV, "").strip()
    if not raw:
        return {}
    import json

    try:
        parsed = json.loads(raw)
    except ValueError:
        raise ScimError(503, f"{SCIM_GROUP_ROLE_MAP_ENV} is not valid JSON") from None
    if not isinstance(parsed, dict):
        raise ScimError(503, f"{SCIM_GROUP_ROLE_MAP_ENV} must be a JSON object")
    mapping: dict[str, str] = {}
    for group, role in parsed.items():
        if not isinstance(group, str) or not isinstance(role, str):
            raise ScimError(503, f"{SCIM_GROUP_ROLE_MAP_ENV} must map group names to roles")
        if role not in KNOWN_ROLES:
            raise ScimError(503, f"{SCIM_GROUP_ROLE_MAP_ENV} maps to unknown role {role!r}")
        if role not in grantable:
            raise ScimError(
                503, f"{SCIM_GROUP_ROLE_MAP_ENV} maps to a role this credential may not grant"
            )
        mapping[group] = role
    return mapping


def token_matches(presented: str, expected: str) -> bool:
    """Compare the presented token with the configured one in constant time.

    Both values are hashed first so the comparison always runs over the same
    number of bytes: the response time then carries no information about the
    token or its length, and no early-exit comparison is possible.
    """
    return secrets.compare_digest(
        hashlib.sha256(presented.encode("utf-8")).digest(),
        hashlib.sha256(expected.encode("utf-8")).digest(),
    )


@dataclass(frozen=True)
class ScimContext:
    """The authenticated SCIM caller and the scope it may act in."""

    organization: OrganizationRow
    config: ScimConfig

    @property
    def organization_id(self) -> str:
        return self.organization.id

    @property
    def issuer(self) -> str:
        return self.config.issuer


def resolve_organization(db: Session, name_or_id: str) -> OrganizationRow:
    """Resolve the single organization this SCIM credential is scoped to."""
    organization = db.scalar(select(OrganizationRow).where(OrganizationRow.name == name_or_id))
    if organization is None:
        organization = db.scalar(select(OrganizationRow).where(OrganizationRow.id == name_or_id))
    if organization is None:
        raise ScimError(503, "SCIM organization scope does not exist")
    return organization


def authenticate_scim(request, db: Session) -> ScimContext:
    """Authenticate a SCIM request and pin it to one organization.

    The synthetic demo identity is never accepted here: a demo request without
    a credential is rejected exactly like a production request without one.
    """
    config = load_config()
    if config is None:
        raise ScimError(503, "SCIM provisioning is not configured for this deployment")
    authorization = request.headers.get("authorization", "")
    if not authorization.startswith("Bearer "):
        raise ScimError(401, "SCIM bearer credential required")
    presented = authorization[len("Bearer ") :].strip()
    if not presented or not token_matches(presented, config.token):
        raise ScimError(401, "Invalid SCIM credential")
    organization = resolve_organization(db, config.organization)
    # The audit gate reads the actor from the session principal. The SCIM
    # service account is a server-side identity: it is verified (it presented a
    # strong secret) but it holds no organization role of its own.
    db.info["principal"] = Principal(
        subject=SCIM_SUBJECT,
        organization_id=organization.id,
        role=SCIM_SERVICE_ROLE,
        demo=False,
        source="scim_bearer",
    )
    request.state.principal = db.info["principal"]
    return ScimContext(organization=organization, config=config)


def scim_principal(context: ScimContext) -> Principal:
    """The principal recorded in the audit trail for SCIM events."""
    return Principal(
        subject=SCIM_SUBJECT,
        organization_id=context.organization_id,
        role=SCIM_SERVICE_ROLE,
        demo=False,
        source="scim_bearer",
    )
