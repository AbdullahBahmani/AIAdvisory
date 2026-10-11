# ADR-0002 — Relational persistence and local-only review (2026-10-11)

**Status:** Accepted for Phase 2 developer demo, NOT a security approval.

## Decision

Use SQLAlchemy 2 models, a frozen Alembic `0001` migration, and SQLite locally. Preserve a clean database abstraction so PostgreSQL can be introduced before real multi-user use. Keep API validation in Pydantic and business analysis in the existing pure deterministic engine. Save independent revision-stamped JSON analysis snapshots. Implement append-only audit events on changes and soft-delete assessments rather than destroying history.

Implement locally guarded review/finalization using a server-configured minimum-length secret compared in constant time. Without it, finalization/reopening/deleting are disabled. The token only unlocks a **developer-demo state transition** and cannot establish identity or permission to deploy AI. No final approval of live business operations is implemented.

## Rationale

This supplies resumable workflow discovery and human review quickly while preserving policy ordering, evidence traces, tests and control separation. A relational schema is more practical for future permission checks than an opaque JSON-only store. Stateless `/analysis/preview` remains backward compatible.

## Trade-offs and follow-up

- SQLite is unsuitable for this app's future multi-user concurrency needs; add PostgreSQL, migrations and locking before production.
- There is no RBAC/SSO or tenant isolation. Bind localhost, use synthetic data, and do not deploy publicly.
- No retention or audit immutability guarantee against DB administrators yet; add signed/exported audit, retention, deletion policy and backup restore tests before production.
- The local reviewer token is a shared secret, not authorization of a person. Replace with actual identity and organization-scoped reviewer roles before public access.
- Dependency locking, Ruff and actual Streamlit execution require a suitable network-enabled development environment.
