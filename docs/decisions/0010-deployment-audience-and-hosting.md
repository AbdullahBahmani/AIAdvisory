# ADR-0010: Deployment audience and hosting posture

**Date:** 2026-10-20
**Status:** Accepted
**Answers:** `main-planner.md` §13 open decision "local-only vs authorized pilot audience" and
"hosting environment".

## Context

The blueprint described a "local or access-controlled portfolio MVP" and explicitly warned
against exposing sensitive real business data or a public multi-user deployment before
authentication, tenant isolation, retention and provider governance controls existed.

Those controls now exist in code (OIDC validation, pre-provisioned tenant memberships, RBAC,
tenant-scoped queries, audit events, SQL rate limits, retention and backup commands). What does
not exist is the *evidence* that any of it works in a real deployment: no real IdP, no MFA, no
external security review, no privacy sign-off, no named release approver.

The question this ADR answers is therefore not "is the code ready" but "who is allowed to run it,
and where".

## Decision

**The repository's supported deployment posture is local/synthetic only.** Concretely:

1. **Local development and synthetic demos are supported and verified.** `uv sync` + `alembic
   upgrade head` + `uvicorn` + `streamlit`, or `docker compose up --build`. The Compose file
   binds every port to `127.0.0.1`, hard-disables the AI provider, and sets the explicit
   synthetic-data demo switch.
2. **A pilot with real organizational data is NOT authorised by this repository.** The
   `ADVISORY_DEPLOYMENT_MODE=pilot` path exists and is fail-closed (OIDC-only, PostgreSQL with
   `sslmode=verify-full`, pinned issuer/audience/JWKS), but running it requires an
   organization-controlled IdP and the approvals listed below. It is a mechanism, not an
   authorisation.
3. **`ADVISORY_DEPLOYMENT_MODE=production` continues to refuse to boot.** This is deliberate and
   must not be worked around by relabelling a real deployment as `local` or `pilot`.
4. **The host environment is not decided by this repository.** It is an operator decision that
   depends on the organization's IdP, data-residency obligations and existing hosting
   standards. The repository constrains the host requirements instead: loopback-only or
   TLS-terminated ingress, managed secrets, PostgreSQL with `verify-full`, non-root containers,
   and no hosted CI runners.

## Why not authorise a pilot now

A pilot with real customer data would be the first time real personal or commercial data
reaches the system. The safety argument of the whole project — *"AI assists. Rules enforce. Data
informs. Humans decide."* — depends on the reviewer identity being trustworthy, and reviewer
identity is precisely what is still unverified: there is no real IdP session, no MFA, and no
independent access-control verification. Authorising a pilot before that would put real data
behind a reviewer identity the system cannot yet prove.

## Conditions to revise this decision

An authorized pilot becomes possible when **all** of the following hold, and each must be
recorded as evidence, not as a source commit:

- A real, organization-managed OIDC IdP with MFA, tested for JWKS rotation, revocation windows
  and ambiguous-membership handling.
- Independent access-control verification of every data and export route.
- A deployed PostgreSQL backup/restore drill with a measured RPO/RTO.
- Approved DPA/DPIA, residency, retention, erasure and legal-hold procedures.
- Managed TLS ingress with rate limiting, a secret store, external monitoring and alerting.
- Vulnerability scanning and an independent penetration test.
- A named accountable release approver, distinct from the implementer.

Until then, real, personal or regulated data does not enter this system.

## Consequences

- The repository can be cloned, run and demonstrated by anyone, with no approval needed and no
  risk to anyone's data.
- Every release-gate item in `main-planner.md` §10 stays open and visible.
- The synthetic demo mode remains the only supported path for demonstrating the full journey,
  and it is explicitly labelled as synthetic wherever it appears.

## References

- `main-planner.md` §2 (release boundaries), §10 Phase 5–8 gates, §13 (open decisions).
- `docs/security.md`, `docs/operations.md`, `docs/release-verification.md`.
- ADR-0005 (credential-bound tenancy), ADR-0006 (OIDC, rate limit and release guard).
