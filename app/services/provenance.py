"""Decision provenance ledger: the inputs the system knew when a human finalized.

The ledger is a factual record of a decision's basis, never an endorsement. It
does not change a recommendation category, an approval outcome or the human
finalization gate, and it never calls a model or a provider.

Persistence uses the existing JSON-backed ``audit_events.details`` column so no
schema change (and no Alembic revision) is required. The ledger entry is written
as one append-only audit event of type ``provenance.recorded``; it therefore
survives a reopen, which is what makes later drift detectable.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    AIRunRow,
    AssessmentRow,
    AuditRow,
    OpportunityEvidenceRow,
    ReviewRow,
    ValueScenarioRow,
)
from app.domain.portfolio import (
    AiProposalVersion,
    ProvenanceSnapshot,
    ProvenanceStaleReason,
    ProvenanceView,
)
from app.security import Principal
from app.services.scoring import POLICY_VERSION, WEIGHTS

LEDGER_EVENT = "provenance.recorded"


def scoring_weight_version() -> str:
    """Fingerprint the scoring weights so a later weight change stays visible.

    The digest is derived, not a released version number: it states which weight
    table produced the analysis, nothing more.
    """
    payload = json.dumps(WEIGHTS, sort_keys=True, separators=(",", ":"))
    return f"{POLICY_VERSION}-weights-{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:12]}"


def analysis_digest(snapshot: dict | None) -> str:
    """Stable digest of a stored analysis snapshot, or an empty marker."""
    if not snapshot:
        return ""
    payload = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _decision_evidence(
    db: Session, assessment_id: str, revision: int
) -> list[OpportunityEvidenceRow]:
    return list(
        db.scalars(
            select(OpportunityEvidenceRow)
            .where(
                OpportunityEvidenceRow.assessment_id == assessment_id,
                OpportunityEvidenceRow.assessment_revision == revision,
            )
            .order_by(OpportunityEvidenceRow.created_at, OpportunityEvidenceRow.id)
        ).all()
    )


def _decision_scenarios(db: Session, assessment_id: str, revision: int) -> list[ValueScenarioRow]:
    return list(
        db.scalars(
            select(ValueScenarioRow)
            .where(
                ValueScenarioRow.assessment_id == assessment_id,
                ValueScenarioRow.assessment_revision == revision,
            )
            .order_by(ValueScenarioRow.created_at.desc(), ValueScenarioRow.id.desc())
        ).all()
    )


def _decision_ai_runs(db: Session, assessment_id: str, revision: int) -> list[AIRunRow]:
    return list(
        db.scalars(
            select(AIRunRow)
            .where(
                AIRunRow.assessment_id == assessment_id,
                AIRunRow.assessment_revision == revision,
            )
            .order_by(AIRunRow.created_at, AIRunRow.id)
        ).all()
    )


def capture_provenance(
    db: Session, row: AssessmentRow, review: ReviewRow, principal: Principal
) -> ProvenanceSnapshot:
    """Collect the inputs that were on the record when the decision was taken.

    The reviewer identity is the one the server authenticated; a client-supplied
    label is never promoted to a verified identity.
    """
    revision = review.analysis_revision
    evidence = _decision_evidence(db, row.id, revision)
    scenarios = _decision_scenarios(db, row.id, revision)
    runs = _decision_ai_runs(db, row.id, revision)
    proposals = [
        AiProposalVersion(
            feature=run.feature,
            provider=run.provider,
            model=run.model,
            prompt_version=run.prompt_version,
            schema_version=run.schema_version,
        )
        for run in runs
    ]
    snapshot = row.analysis_snapshot or {}
    return ProvenanceSnapshot(
        policy_version=str(snapshot.get("policy_version") or POLICY_VERSION),
        scoring_weight_version=scoring_weight_version(),
        analysis_revision=revision,
        assessment_revision=row.revision,
        analysis_digest=analysis_digest(snapshot),
        evidence_ids=[item.id for item in evidence],
        verified_evidence_ids=[item.id for item in evidence if item.verified_at is not None],
        value_scenario_id=scenarios[0].id if scenarios else None,
        value_scenario_ids=[item.id for item in scenarios],
        reviewer_subject=review.reviewer_id,
        reviewer_identity_verified=bool(review.identity_verified),
        decision_maker_subject=principal.subject,
        decision_maker_identity_verified=not principal.demo,
        ai_contributed=bool(proposals),
        ai_proposals=proposals,
        recorded_at=datetime.now(UTC),
    )


def record_provenance(db: Session, row: AssessmentRow, snapshot: ProvenanceSnapshot) -> None:
    """Append the ledger entry to the immutable audit trail.

    Routed through the shared audit gate so the ledger entry carries exactly the same
    identity evidence as every other audit event: a server-derived actor, an explicit
    ``identity_unverified`` flag and the identity source. Writing the row directly would
    silently omit those flags and make an unverified demo decision indistinguishable from
    a verified one in the audit history.
    """
    from app.services.audit import audit

    audit(db, row, LEDGER_EVENT, **snapshot.model_dump(mode="json"))


def latest_snapshot(db: Session, row: AssessmentRow) -> ProvenanceSnapshot | None:
    """The ledger entry of the most recent finalized decision, if any.

    Earlier entries stay in the audit history; they are superseded, not rewritten.
    """
    events = db.scalars(
        select(AuditRow)
        .where(AuditRow.assessment_id == row.id, AuditRow.event_type == LEDGER_EVENT)
        .order_by(AuditRow.created_at.desc(), AuditRow.id.desc())
    ).all()
    for event in events:
        try:
            return ProvenanceSnapshot.model_validate(event.details)
        except ValueError:
            # A malformed ledger entry is never trusted; it is skipped, not guessed.
            continue
    return None


def _stale_reasons(
    db: Session, row: AssessmentRow, snapshot: ProvenanceSnapshot
) -> list[ProvenanceStaleReason]:
    """Compare the recorded basis with the current basis of the assessment.

    The comparison mirrors the Phase 8 ``stale`` convention: a record belongs to
    the revision it was created against, and it is stale once that revision is
    no longer the current one.
    """
    reasons: list[ProvenanceStaleReason] = []
    if row.analysis_revision != snapshot.analysis_revision:
        reasons.append("analysis_revision_changed")
    current = row.analysis_snapshot
    if current and analysis_digest(current) != snapshot.analysis_digest:
        reasons.append("analysis_changed")
    if snapshot.policy_version != POLICY_VERSION:
        reasons.append("policy_version_changed")

    recorded_evidence = set(snapshot.evidence_ids)
    evidence = _decision_evidence(db, row.id, row.revision)
    current_evidence = {item.id for item in evidence}
    stored: dict[str, OpportunityEvidenceRow] = {}
    if recorded_evidence:
        stored = {
            item.id: item
            for item in db.scalars(
                select(OpportunityEvidenceRow).where(
                    OpportunityEvidenceRow.assessment_id == row.id,
                    OpportunityEvidenceRow.id.in_(recorded_evidence),
                )
            ).all()
        }
    if any(
        identifier not in stored or stored[identifier].assessment_revision != row.revision
        for identifier in recorded_evidence
    ):
        reasons.append("evidence_changed")
    if current_evidence - recorded_evidence:
        reasons.append("evidence_added")

    if snapshot.value_scenario_ids:
        stored_scenarios = {
            item.id: item
            for item in db.scalars(
                select(ValueScenarioRow).where(
                    ValueScenarioRow.assessment_id == row.id,
                    ValueScenarioRow.id.in_(snapshot.value_scenario_ids),
                )
            ).all()
        }
        if any(
            identifier not in stored_scenarios
            or stored_scenarios[identifier].assessment_revision != row.revision
            for identifier in snapshot.value_scenario_ids
        ):
            reasons.append("value_scenario_changed")
    current_scenarios = {item.id for item in _decision_scenarios(db, row.id, row.revision)}
    if current_scenarios - set(snapshot.value_scenario_ids):
        reasons.append("value_scenario_added")
    return reasons


def provenance_view(db: Session, row: AssessmentRow) -> ProvenanceView | None:
    """The ledger entry for the current final decision, with its drift state."""
    snapshot = latest_snapshot(db, row)
    if snapshot is None:
        return None
    reasons = _stale_reasons(db, row, snapshot)
    return ProvenanceView(
        assessment_id=row.id,
        **snapshot.model_dump(),
        stale=bool(reasons),
        stale_reasons=reasons,
    )
