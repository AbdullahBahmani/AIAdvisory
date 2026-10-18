"""Tenant-scoped collaboration, review assignment and in-app activity inbox.

Human approvals apply to one analysis revision; they never authorize business
execution or independent deployment. No user-submitted actor/tenant is trusted.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.assessments import audit, get_or_404
from app.concurrency import assert_current_revision
from app.db.models import (
    ApiCredentialRow, ApprovalRow, AssessmentCommentRow, AssessmentRow,
    InboxNotificationRow, OidcMembershipRow, ReviewerAssignmentRow,
)
from app.db.session import get_db
from app.domain.collaboration import (
    ApprovalCreate, ApprovalSummary, ApprovalView, AssignmentCreate, AssignmentView,
    CommentCreate, CommentView, MemberView, NotificationView, WorkspaceSummary,
)
from app.security import current_principal, require_role, ROLE_LEVEL
from app.services.approval_chain import STAGES, current_chain
from app.db.models import utcnow

router = APIRouter(prefix="/api/v1", tags=["workspace"])


def _active_member(db: Session, subject: str, org: str) -> bool:
    conditions = [
        OidcMembershipRow.organization_id == org,
        OidcMembershipRow.subject == subject,
        OidcMembershipRow.active.is_(True),
    ]
    if os.getenv("ADVISORY_OIDC_REQUIRED") == "1":
        conditions.append(OidcMembershipRow.issuer == os.getenv("ADVISORY_OIDC_ISSUER", ""))
    memberships = db.scalars(select(OidcMembershipRow).where(*conditions)).all()
    if any(ROLE_LEVEL.get(m.role, 0) >= ROLE_LEVEL["reviewer"] for m in memberships):
        return True
    if os.getenv("ADVISORY_OIDC_REQUIRED") == "1":
        return False
    credentials = db.scalars(select(ApiCredentialRow).where(
        ApiCredentialRow.organization_id == org,
        ApiCredentialRow.subject == subject,
        ApiCredentialRow.revoked_at.is_(None),
    )).all()
    now = datetime.now(timezone.utc)
    return any(
        ROLE_LEVEL.get(c.role, 0) >= ROLE_LEVEL["reviewer"]
        and (c.expires_at.replace(tzinfo=timezone.utc) if c.expires_at.tzinfo is None else c.expires_at) > now
        for c in credentials
    )


def _assignment(db: Session, assessment_id: str):
    return db.scalar(select(ReviewerAssignmentRow).where(
        ReviewerAssignmentRow.assessment_id == assessment_id
    ))


def _notice(db: Session, row: AssessmentRow, recipient: str, event: str, message: str):
    if row.organization_id is not None:
        db.add(InboxNotificationRow(
            organization_id=row.organization_id,
            assessment_id=row.id,
            recipient=recipient, event_type=event, message=message,
        ))


@router.get("/workspace/members", response_model=list[MemberView])
def list_members(db: Session = Depends(get_db)):
    require_role(db, "admin")
    p = current_principal(db)
    if p.demo:
        return []
    members = {}
    member_conditions = [
        OidcMembershipRow.organization_id == p.organization_id,
        OidcMembershipRow.active.is_(True),
    ]
    if os.getenv("ADVISORY_OIDC_REQUIRED") == "1":
        member_conditions.append(OidcMembershipRow.issuer == os.getenv("ADVISORY_OIDC_ISSUER", ""))
    for item in db.scalars(select(OidcMembershipRow).where(*member_conditions)):
        members[("oidc", item.subject)] = MemberView(subject=item.subject, role=item.role, provider="oidc")
    if os.getenv("ADVISORY_OIDC_REQUIRED") != "1":
        now = datetime.now(timezone.utc)
        for item in db.scalars(select(ApiCredentialRow).where(
            ApiCredentialRow.organization_id == p.organization_id,
            ApiCredentialRow.revoked_at.is_(None),
        )):
            expiration = item.expires_at.replace(tzinfo=timezone.utc) if item.expires_at.tzinfo is None else item.expires_at
            if expiration > now:
                members[("token", item.subject)] = MemberView(subject=item.subject, role=item.role, provider="token")
    return sorted(members.values(), key=lambda m: (m.subject, m.provider))


@router.get("/assessments/{assessment_id}/reviewer", response_model=AssignmentView | None)
def get_reviewer(assessment_id: str, db: Session = Depends(get_db)):
    get_or_404(db, AssessmentRow, assessment_id)
    item = _assignment(db, assessment_id)
    return AssignmentView.model_validate(item, from_attributes=True) if item else None


@router.put("/assessments/{assessment_id}/reviewer", response_model=AssignmentView)
def assign_reviewer(assessment_id: str, payload: AssignmentCreate, db: Session = Depends(get_db)):
    require_role(db, "admin")
    p = current_principal(db)
    if p.demo or not p.organization_id:
        raise HTTPException(status_code=403, detail="Assignments require an authenticated organization administrator")
    row = get_or_404(db, AssessmentRow, assessment_id)
    db.refresh(row, with_for_update=True)
    assert_current_revision(row)
    if row.status == "finalized":
        raise HTTPException(status_code=409, detail="Reopen finalized assessment before reassignment")
    if not _active_member(db, payload.reviewer_subject, p.organization_id):
        raise HTTPException(status_code=422, detail="Reviewer must be an active reviewer/admin in this organization")
    item = _assignment(db, row.id)
    if item is None:
        item = ReviewerAssignmentRow(assessment_id=row.id, reviewer_subject=payload.reviewer_subject, assigned_by=p.subject)
        db.add(item)
    else:
        item.reviewer_subject = payload.reviewer_subject
        item.assigned_by = p.subject
        item.updated_at = utcnow()
    row.updated_at = utcnow()
    _notice(db, row, payload.reviewer_subject, "reviewer.assigned", "An assessment was assigned for your review")
    audit(db, row, "reviewer.assigned", reviewer_subject=payload.reviewer_subject)
    db.commit()
    return AssignmentView.model_validate(item, from_attributes=True)


@router.get("/assessments/{assessment_id}/comments", response_model=list[CommentView])
def list_comments(assessment_id: str, db: Session = Depends(get_db)):
    get_or_404(db, AssessmentRow, assessment_id)
    rows = db.scalars(select(AssessmentCommentRow).where(
        AssessmentCommentRow.assessment_id == assessment_id
    ).order_by(AssessmentCommentRow.created_at, AssessmentCommentRow.id).limit(500)).all()
    return [CommentView.model_validate(x, from_attributes=True) for x in rows]


@router.post("/assessments/{assessment_id}/comments", response_model=CommentView, status_code=201)
def add_comment(assessment_id: str, payload: CommentCreate, db: Session = Depends(get_db)):
    p = current_principal(db)
    row = get_or_404(db, AssessmentRow, assessment_id)
    db.refresh(row, with_for_update=True)
    assert_current_revision(row)
    item = AssessmentCommentRow(assessment_id=row.id, assessment_revision=row.revision,
                                author=p.subject, body=payload.body.strip())
    db.add(item)
    assigned = _assignment(db, row.id)
    if assigned and assigned.reviewer_subject != p.subject:
        _notice(db, row, assigned.reviewer_subject, "comment.added", "A comment was added to an assigned assessment")
    audit(db, row, "assessment.comment_added", comment_id=item.id, revision=row.revision)
    db.commit()
    return CommentView.model_validate(item, from_attributes=True)


@router.get("/assessments/{assessment_id}/approvals", response_model=ApprovalSummary)
def get_approvals(assessment_id: str, db: Session = Depends(get_db)):
    row = get_or_404(db, AssessmentRow, assessment_id)
    states, _ = current_chain(db, row)
    history = db.scalars(select(ApprovalRow).where(
        ApprovalRow.assessment_id == row.id,
        ApprovalRow.assessment_revision == row.revision,
    ).order_by(ApprovalRow.created_at, ApprovalRow.id)).all()
    return ApprovalSummary(
        revision=row.revision, stages={x: states.get(x, "pending") for x in STAGES},
        complete=all(states.get(x) == "approved" for x in STAGES),
        history=[ApprovalView.model_validate(x, from_attributes=True) for x in history],
    )


@router.post("/assessments/{assessment_id}/approvals", response_model=ApprovalView, status_code=201)
def cast_approval(assessment_id: str, payload: ApprovalCreate, db: Session = Depends(get_db)):
    p = current_principal(db)
    if p.demo:
        raise HTTPException(status_code=403, detail="Enterprise approval requires an authenticated reviewer")
    require_role(db, "admin" if payload.stage == "business" else "reviewer")
    row = get_or_404(db, AssessmentRow, assessment_id)
    db.refresh(row, with_for_update=True)
    assert_current_revision(row)
    if row.status in {"deleted", "finalized"}:
        raise HTTPException(status_code=409, detail="Assessment is not open for approvals")
    if row.analysis_revision != row.revision or row.analysis_snapshot is None:
        raise HTTPException(status_code=409, detail="Analyze this assessment revision before approval")
    states, actors = current_chain(db, row)
    position = STAGES.index(payload.stage)
    if position and states.get(STAGES[position-1]) != "approved":
        raise HTTPException(status_code=409, detail="Previous approval stage is not approved")
    if p.subject in (actors.get(s) for s in STAGES[:position]):
        raise HTTPException(status_code=409, detail="Each approval stage requires a different reviewer")
    vote = ApprovalRow(assessment_id=row.id, assessment_revision=row.revision,
                       stage=payload.stage, decision=payload.decision,
                       actor=p.subject, rationale=payload.rationale.strip())
    db.add(vote)
    row.updated_at = utcnow()  # row-version bump serializes parallel votes
    assigned = _assignment(db, row.id)
    if assigned and assigned.reviewer_subject != p.subject:
        _notice(db, row, assigned.reviewer_subject, "approval.recorded", "A review stage has changed")
    audit(db, row, "assessment.approval_recorded", stage=payload.stage,
          decision=payload.decision, revision=row.revision, vote_id=vote.id)
    db.commit()
    return ApprovalView.model_validate(vote, from_attributes=True)


@router.get("/workspace/summary", response_model=WorkspaceSummary)
def workspace_summary(db: Session = Depends(get_db)):
    p = current_principal(db)
    scope = [AssessmentRow.status != "deleted"]
    if not p.demo:
        scope.append(AssessmentRow.organization_id == p.organization_id)
    statuses = db.execute(
        select(AssessmentRow.status, func.count(AssessmentRow.id))
        .where(*scope).group_by(AssessmentRow.status)
    ).all()
    by_status = {s: count for s, count in statuses}
    assigned = 0
    unread = 0
    if not p.demo:
        assigned = db.scalar(select(func.count(ReviewerAssignmentRow.id)).join(
            AssessmentRow, ReviewerAssignmentRow.assessment_id == AssessmentRow.id,
        ).where(
            AssessmentRow.organization_id == p.organization_id,
            AssessmentRow.status.not_in(("deleted", "finalized")),
            ReviewerAssignmentRow.reviewer_subject == p.subject,
        )) or 0
        unread = db.scalar(select(func.count(InboxNotificationRow.id)).where(
            InboxNotificationRow.organization_id == p.organization_id,
            InboxNotificationRow.recipient == p.subject,
            InboxNotificationRow.read_at.is_(None),
        )) or 0
    return WorkspaceSummary(total=sum(by_status.values()), by_status=by_status,
        pending_review=by_status.get("in_review", 0),
        assigned_to_me=assigned, unread_notifications=unread)


@router.get("/workspace/inbox")
def inbox(db: Session = Depends(get_db)):
    p = current_principal(db)
    if p.demo:
        return {"assigned_assessments": [], "notifications": []}
    assignments = db.execute(select(AssessmentRow).join(
        ReviewerAssignmentRow, ReviewerAssignmentRow.assessment_id == AssessmentRow.id,
    ).where(
        AssessmentRow.organization_id == p.organization_id,
        AssessmentRow.status.not_in(("deleted", "finalized")),
        ReviewerAssignmentRow.reviewer_subject == p.subject,
    ).order_by(AssessmentRow.updated_at.desc()).limit(100)).scalars().all()
    notices = db.scalars(select(InboxNotificationRow).where(
        InboxNotificationRow.organization_id == p.organization_id,
        InboxNotificationRow.recipient == p.subject,
    ).order_by(InboxNotificationRow.created_at.desc()).limit(100)).all()
    return {
        "assigned_assessments": [{"id": x.id, "title": x.title,
                                  "revision": x.revision, "status": x.status} for x in assignments],
        "notifications": [NotificationView.model_validate(x, from_attributes=True).model_dump(mode="json")
                          for x in notices],
    }


@router.post("/workspace/notifications/{notification_id}/read", response_model=NotificationView)
def read_notification(notification_id: str, db: Session = Depends(get_db)):
    p = current_principal(db)
    if p.demo:
        raise HTTPException(status_code=404, detail="Notification not found")
    item = db.scalar(select(InboxNotificationRow).where(
        InboxNotificationRow.id == notification_id,
        InboxNotificationRow.organization_id == p.organization_id,
        InboxNotificationRow.recipient == p.subject,
    ))
    if item is None:
        raise HTTPException(status_code=404, detail="Notification not found")
    if item.read_at is None:
        item.read_at = utcnow()
        db.commit()
    return NotificationView.model_validate(item, from_attributes=True)
