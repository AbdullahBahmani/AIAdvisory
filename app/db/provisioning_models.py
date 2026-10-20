"""Storage for automated identity provisioning and approval policies.

These tables are deliberately separate from the assessment ledger:

* ``scim_users`` is the *SCIM-side attribute overlay* of a provisioned
  membership. The authoritative identity record stays in ``oidc_memberships``
  (that is the table the OIDC path resolves); the overlay only carries the
  directory attributes a SCIM client sends (``userName``, names, emails).
* ``organization_audit_events`` is the organization-scoped audit ledger. The
  assessment ledger (``audit_events``) requires an assessment, but a SCIM
  provisioning event or an approval-policy change is not about an assessment.
  Both ledgers are written through the single gate in ``app/services/audit.py``,
  so the "the actor is always the server-side principal" rule holds in one place.
* ``approval_policies`` stores the per-organization approval policy and its
  version. The version is what makes "this vote was cast under these rules"
  answerable.
* ``approval_vote_bindings`` records which policy version applied to a vote,
  which role the actor held, and whether a later policy change invalidated it.

The module lives outside ``app/db/models.py`` on purpose: it must not change the
existing tables, and the tables it adds are only reachable through the new
SCIM and policy code paths.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base, new_id, utcnow


class ScimUserRow(Base):
    """SCIM attributes of one provisioned membership.

    ``membership_id`` is the authoritative identity (an ``oidc_memberships``
    row). The overlay is optional: a membership provisioned by an operator
    script is visible through SCIM even when no overlay row exists.
    """

    __tablename__ = "scim_users"
    __table_args__ = (
        UniqueConstraint("membership_id", name="uq_scim_users_membership_id"),
        UniqueConstraint("organization_id", "user_name", name="uq_scim_users_org_user_name"),
        Index("ix_scim_users_organization_id_membership_id", "organization_id", "membership_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    membership_id: Mapped[str] = mapped_column(
        ForeignKey("oidc_memberships.id", ondelete="CASCADE"), nullable=False
    )
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: The IdP subject: the value the identity provider puts in the OIDC ``sub``
    #: claim. It is the SCIM ``externalId`` and the membership subject.
    external_id: Mapped[str] = mapped_column(String(200), nullable=False)
    #: The directory login name (SCIM ``userName``).
    user_name: Mapped[str] = mapped_column(String(200), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(200))
    given_name: Mapped[str | None] = mapped_column(String(200))
    family_name: Mapped[str | None] = mapped_column(String(200))
    emails: Mapped[list | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class OrganizationAuditEventRow(Base):
    """Organization-scoped audit event (SCIM provisioning, approval policies).

    Mirrors ``audit_events`` but without an assessment reference: provisioning an
    identity or changing an approval policy is an organization event, not an
    assessment event.
    """

    __tablename__ = "organization_audit_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(70), nullable=False)
    actor: Mapped[str] = mapped_column(String(200), default="local_demo")
    actor_organization_id: Mapped[str | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), index=True
    )
    actor_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ApprovalPolicyRow(Base):
    """The current approval policy of one organization."""

    __tablename__ = "approval_policies"
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True
    )
    #: Incremented on every accepted change; votes bind to the version that
    #: was in force when they were cast.
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    policy: Mapped[dict] = mapped_column(JSON, nullable=False)
    updated_by: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ApprovalVoteBindingRow(Base):
    """Which policy version applied to an approval vote.

    The binding is what makes "a policy change invalidates the approvals it
    affected" precise: a vote is invalidated when the rules that applied to it
    changed, and the binding records the role the actor held at cast time.
    """

    __tablename__ = "approval_vote_bindings"
    __table_args__ = (
        UniqueConstraint("vote_id", name="uq_approval_vote_bindings_vote_id"),
        Index(
            "ix_approval_vote_bindings_assessment_id_organization_id",
            "assessment_id",
            "organization_id",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    vote_id: Mapped[str] = mapped_column(
        ForeignKey("approval_votes.id", ondelete="CASCADE"), nullable=False
    )
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    assessment_id: Mapped[str] = mapped_column(
        ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    stage: Mapped[str] = mapped_column(String(20), nullable=False)
    #: The role the actor held when the vote was cast.
    actor_role: Mapped[str] = mapped_column(String(20), nullable=False)
    policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    invalidation_reason: Mapped[str | None] = mapped_column(String(200))


@event.listens_for(OrganizationAuditEventRow, "before_insert")
def _stamp_organization_audit_actor(
    _mapper, _connection, target: OrganizationAuditEventRow
) -> None:
    """Attribute the event to the server-side principal, never to request input.

    Same rule as the assessment ledger (``app/db/models.py``): the principal of
    the current session is the only accepted actor, and rows written outside a
    request stay marked unverified.
    """
    from app.services.audit import stamp_actor

    stamp_actor(target)
