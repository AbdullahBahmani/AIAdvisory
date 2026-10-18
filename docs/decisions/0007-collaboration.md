# ADR-0007: Human-led enterprise collaboration inside the modular monolith

**Status:** Accepted for synthetic/local and access-controlled pilot source code.
**Date:** 2026-10-18

## Context

Phase 6 added stronger tenant authentication, row-version concurrency checks and
a closed production gate. Phase 7 must provide an organization workspace, review
assignment, discussion and auditable multi-person approvals without implying that
a SaaS production deployment or automated rollout is authorized.

## Decision

Retain FastAPI, SQLAlchemy and Streamlit instead of adding a second frontend
stack before the authenticated browser and infrastructure are independently
tested. All collaboration writes happen via the authenticated API.

- Reviewer assignments are organization scoped and restricted to active reviewer
  or administrator subjects; a submitted subject cannot create a membership.
- Comments are append-only, attributed from server-verified principal identity
  and pinned to the current assessment revision. They may be added to finalized
  assessments without changing their deterministic analysis.
- Approval votes are append-only and revision-bound. The three stages are
  technical, security and business; each requires a different authenticated
  actor. The business stage requires administrator role. A renewed upstream vote
  invalidates its downstream approvals; an input edit invalidates all current
  revision votes by changing the assessment revision.
- Protected pilot final signoff requires the complete three-stage chain and
  the pre-existing FDE review. In local authenticated tests, setting
  ADVISORY_REQUIRE_APPROVAL_CHAIN=1 enables the same gate; the default
  synthetic demo remains compatible.
- Assignment/activity messages use an in-app, user-scoped inbox rather than
  sending email or introducing uncontrolled external notification services.
- All writes preserve the Phase 6 optimistic-locking and server-side tenant
  authorization boundaries. No chat/comment/approval may relax AI safety rules
  or authorize operational transactions.

## Limitations

Pilot reviewers currently share the existing reviewer/admin role matrix; a
separate security-officer role and enterprise separation-of-duties policy,
identity-provider managed provisioning, multilingual/accessibility audits,
retention for collaboration records, data residency, real SSO browser sessions,
compliance review and reliable external notifications require additional work.

The new screens are an evolution of Streamlit, not a production-ready React UI.
Production mode remains deliberately disabled.
