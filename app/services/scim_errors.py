"""SCIM 2.0 error envelope.

A SCIM client must be able to tell *what* went wrong from the response, so the
API answers with the SCIM error schema instead of the platform's generic
``{"detail": ...}`` body:

    {"schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
     "status": "400", "detail": "...", "scimType": "invalidValue"}

The exception carries the HTTP status, so the SCIM routes never have to guess
one; see ``app/api/scim.py`` for the handler that renders it.
"""

from __future__ import annotations

SCIM_CONTENT_TYPE = "application/scim+json"
SCIM_ERROR_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:Error"


class ScimError(Exception):
    """A SCIM failure with its HTTP status and optional ``scimType``."""

    def __init__(self, status: int, detail: str, scim_type: str | None = None) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.scim_type = scim_type

    def payload(self) -> dict:
        body: dict = {
            "schemas": [SCIM_ERROR_SCHEMA],
            "status": str(self.status),
            "detail": self.detail,
        }
        if self.scim_type:
            body["scimType"] = self.scim_type
        return body


def not_found(detail: str = "Resource not found") -> ScimError:
    return ScimError(404, detail)


def invalid_value(detail: str) -> ScimError:
    return ScimError(400, detail, "invalidValue")


def invalid_filter(detail: str) -> ScimError:
    return ScimError(400, detail, "invalidFilter")


def invalid_path(detail: str) -> ScimError:
    return ScimError(400, detail, "invalidPath")


def invalid_syntax(detail: str) -> ScimError:
    return ScimError(400, detail, "invalidSyntax")


def uniqueness(detail: str) -> ScimError:
    return ScimError(409, detail, "uniqueness")


def mutability(detail: str) -> ScimError:
    return ScimError(400, detail, "mutability")
