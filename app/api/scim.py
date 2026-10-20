"""SCIM 2.0 provisioning API.

The routes speak SCIM: ``application/scim+json`` in and out, the SCIM list
envelope for collections and the SCIM error schema for failures.

Authentication is the dedicated SCIM bearer token (see
``app/services/scim_auth.py``), *not* the ordinary user credential and *not*
the synthetic demo identity. The credential is pinned to one organization, so
every query in this module is scoped to that organization by construction: a
SCIM request cannot read or write another tenant's memberships.

Every mutation is written to the organization audit ledger through the shared
gate in ``app/services/audit.py``.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services import scim as scim_service
from app.services.audit import audit_organization
from app.services.scim_auth import ScimContext, authenticate_scim
from app.services.scim_errors import SCIM_CONTENT_TYPE, ScimError, invalid_syntax


class ScimResponse(JSONResponse):
    """A JSON response that speaks SCIM."""

    media_type = SCIM_CONTENT_TYPE


router = APIRouter(
    prefix="/scim/v2",
    tags=["scim"],
    default_response_class=ScimResponse,
    responses={
        400: {"description": "SCIM error"},
        401: {"description": "SCIM credential required"},
        404: {"description": "Not found"},
        409: {"description": "Conflict"},
    },
)


@contextmanager
def _schema_guard():
    """Answer 503 when the database does not have the provisioning tables."""
    try:
        yield
    except SQLAlchemyError:
        raise ScimError(503, "Database migration not current") from None


async def scim_json_body(request: Request) -> dict[str, Any]:
    """Parse the request body as a SCIM object."""
    raw = await request.body()
    if not raw:
        raise invalid_syntax("A request body is required")
    try:
        data = json.loads(raw)
    except ValueError:
        raise invalid_syntax("The request body must be valid JSON") from None
    if not isinstance(data, dict):
        raise invalid_syntax("The request body must be a JSON object")
    return data


def _scim_context(request: Request, db: Session = Depends(get_db)) -> ScimContext:
    """Authenticate the SCIM caller and pin it to its organization."""
    context = authenticate_scim(request, db)
    enforce_scim_rate_limit(db, context)
    return context


def enforce_scim_rate_limit(db: Session, context: ScimContext) -> None:
    """Apply the platform rate limit to the provisioning service account."""
    from app.throttling import enforce_limit

    enforce_limit(
        db,
        org=context.organization_id,
        principal="scim_provisioner",
        role="scim_provisioner",
    )


@router.get("/Users")
def list_users(
    request: Request,
    db: Session = Depends(get_db),
    context: ScimContext = Depends(_scim_context),
) -> dict:
    """List the users of the SCIM organization."""
    with _schema_guard():
        return scim_service.list_users(
            db,
            context,
            filter_expression=request.query_params.get("filter"),
            start_index=_int_param(request.query_params.get("startIndex")),
            count=_int_param(request.query_params.get("count")),
        )


@router.post("/Users", status_code=201)
def create_user(
    response: Response,
    payload: dict[str, Any] = Depends(scim_json_body),
    db: Session = Depends(get_db),
    context: ScimContext = Depends(_scim_context),
) -> dict:
    """Provision a new user (an active membership in the SCIM organization)."""
    with _schema_guard():
        resource, role, source = scim_service.create_user(db, context, payload)
        audit_organization(
            db,
            context.organization_id,
            "scim.user_created",
            subject=resource["externalId"],
            user_name=resource["userName"],
            role=role,
            role_source=source,
            active=resource["active"],
            scim_user_id=resource["id"],
        )
        db.commit()
    response.headers["Location"] = f"/scim/v2/Users/{resource['id']}"
    return resource


@router.get("/Users/{user_id}")
def get_user(
    user_id: str,
    db: Session = Depends(get_db),
    context: ScimContext = Depends(_scim_context),
) -> dict:
    """Read one user of the SCIM organization."""
    with _schema_guard():
        return scim_service.get_user(db, context, user_id)


@router.put("/Users/{user_id}")
def replace_user(
    user_id: str,
    payload: dict[str, Any] = Depends(scim_json_body),
    db: Session = Depends(get_db),
    context: ScimContext = Depends(_scim_context),
) -> dict:
    """Replace a user (role, activation and directory attributes)."""
    with _schema_guard():
        previous = scim_service.get_user(db, context, user_id)
        resource, role, source = scim_service.replace_user(db, context, user_id, payload)
        audit_organization(
            db,
            context.organization_id,
            "scim.user_updated",
            subject=resource["externalId"],
            user_name=resource["userName"],
            role=role,
            role_source=source,
            active=resource["active"],
            previous_role=previous.get("groups", [{}])[0].get("display"),
            previous_active=previous.get("active"),
            scim_user_id=resource["id"],
        )
        db.commit()
    return resource


@router.patch("/Users/{user_id}")
def patch_user(
    user_id: str,
    payload: dict[str, Any] = Depends(scim_json_body),
    db: Session = Depends(get_db),
    context: ScimContext = Depends(_scim_context),
) -> dict:
    """Patch a user (a subset of the attributes)."""
    with _schema_guard():
        previous = scim_service.get_user(db, context, user_id)
        resource, role, source = scim_service.patch_user(db, context, user_id, payload)
        audit_organization(
            db,
            context.organization_id,
            "scim.user_updated",
            subject=resource["externalId"],
            user_name=resource["userName"],
            role=role,
            role_source=source,
            active=resource["active"],
            previous_role=previous.get("groups", [{}])[0].get("display"),
            previous_active=previous.get("active"),
            scim_user_id=resource["id"],
        )
        db.commit()
    return resource


@router.delete("/Users/{user_id}", status_code=204)
def delete_user(
    user_id: str,
    db: Session = Depends(get_db),
    context: ScimContext = Depends(_scim_context),
) -> Response:
    """Deactivate a user.

    A SCIM ``DELETE`` never deletes the membership: the membership is
    referenced by review history and audit events, so it is deactivated
    (``active = false``) and kept. The audit event survives the deactivation.
    """
    with _schema_guard():
        resource = scim_service.get_user(db, context, user_id)
        membership = scim_service.deactivate_user(db, context, user_id)
        audit_organization(
            db,
            context.organization_id,
            "scim.user_deactivated",
            subject=membership.subject,
            user_name=resource["userName"],
            role=membership.role,
            active=False,
            previous_active=resource.get("active"),
            scim_user_id=membership.id,
            deactivation_only=True,
        )
        db.commit()
    return Response(status_code=204)


@router.get("/Groups")
def list_groups(
    db: Session = Depends(get_db),
    context: ScimContext = Depends(_scim_context),
) -> dict:
    """List the groups of the SCIM organization."""
    with _schema_guard():
        return scim_service.list_groups(db, context)


@router.get("/Groups/{group_id}")
def get_group(
    group_id: str,
    db: Session = Depends(get_db),
    context: ScimContext = Depends(_scim_context),
) -> dict:
    """Read one group of the SCIM organization."""
    with _schema_guard():
        return scim_service.get_group(db, context, group_id)


def _int_param(raw: str | None) -> int | None:
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except ValueError:
        raise ScimError(400, "Pagination parameters must be integers", "invalidValue") from None
