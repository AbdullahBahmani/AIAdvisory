# ADR-0009: Verified, organization-scoped actor attribution in local/demo mode

**Status:** Accepted for local/demo and access-controlled pilot source code.
**Date:** 2026-10-20

## Context

Phase 5 made `/api/v1` fail closed and bound every request to a server-provisioned
credential or a pre-provisioned OIDC membership (ADR-0005, ADR-0006). The
`ADVISORY_DEMO_MODE=1` switch, however, still answered **every** caller with one
global principal:

```
Principal("local_demo", None, "admin", demo=True)
```

Because that principal has no organization and is flagged `demo`, three separate
controls were bypassed in the local/demo path:

1. Tenant scoping. `list_assessments`, `get_or_404` and the workspace summaries
   only filtered by organization `if not principal.demo`, so any demo caller saw
   every assessment of every organization in the database.
2. Reviewer attribution. The audit actor was `payload.reviewer_id`, free text
   supplied in the request body. The server never verified it; it only recorded
   `identity_unverified=True` next to an unverifiable name.
3. Separation of duties. The Phase 7 approval chain (technical, security,
   business) needs three *different* actors. One shared demo identity could not
   express that, so the chain could either be skipped or collapse onto one
   unverified actor.

The planner item P2-07 asked for "authenticated organization-scoped FDE roles"
before multi-user release. The production path already had them; the residual gap
was the local/demo path.

## Decision

**A presented credential is authenticated identically in demo mode and in the
production path.** `authenticate_request` no longer short-circuits to a global
demo principal. It parses the `Authorization` header first:

- a bearer credential matching the existing `TOKEN_RE` is looked up in
  `api_credentials` exactly as before (SHA-256 hash lookup, revocation check,
  expiry check, organization and role check) and becomes a **verified** principal
  with a server-resolved `subject`, `organization_id` and `role`;
- a JWT is validated against the pinned OIDC issuer/audience/JWKS and mapped to a
  pre-provisioned membership, also verified;
- anything else is rejected with 401 — a bad, revoked, expired or malformed
  credential fails closed even in demo mode.

Only the **absence** of any credential falls back to the synthetic `local_demo`
identity. That identity has no organization, is never verified, and every record
it produces stays marked `identity_unverified=True` / `actor_verified=False`.

Consequences:

- **Organization scoping applies to local principals.** An assessment created by a
  named local principal belongs to that principal's organization and is invisible
  (404) to principals of another organization. Records created by the synthetic
  demo identity have no organization and stay quarantined from verified
  principals, exactly as legacy unscoped rows are in production.
- **The audit actor is server-derived.** `app/api/assessments.py::audit` always
  writes the authenticated principal as the actor, and a `before_insert` mapper
  hook on `AuditRow` re-stamps the actor, its organization and the verification
  flag from the session principal, so no code path can write a forged actor.
  A client-supplied reviewer label is *rejected* for a verified principal (403)
  and, for the synthetic demo identity, is kept only as an explicitly unverified
  `claimed_actor` in the event details — never as the actor.
- **`identity_verified` reflects real verification.** It is now derived from the
  principal, not from the demo flag: a credential-backed or membership-backed
  review is marked verified, the synthetic demo review is not. The review row
  also stores the reviewer's organization (`reviews.reviewer_organization_id`),
  and audit rows store `actor_organization_id` and `actor_verified`.
- **`ADVISORY_REVIEW_TOKEN` remains only as the synthetic fallback.** It still
  gates finalization, reopen and soft delete for the unverified demo identity
  (403 otherwise), it can never produce a verified identity, and every record it
  produces is marked unverified. A verified local principal instead passes the
  reviewer gate with the `reviewer` role of its own credential, which is a
  strictly stronger proof than a shared secret.
- **The approval chain cannot collapse.** The synthetic demo identity may not
  cast approval votes at all (403), so it can never satisfy — or fake — the
  three-stage chain. Verified local principals cast votes under their own
  subject, and the existing chain rule of three distinct actors applies. With
  `ADVISORY_REQUIRE_APPROVAL_CHAIN=1` the local path enforces the same gate as
  the pilot path.
- **Reviewer assignment binds locally.** The Phase 7 assignment rule ("only the
  assigned reviewer may review or finalize") applies to verified local
  principals; an organization admin can assign a reviewer in the local workflow
  as well.
- **Operator provisioning.** `python -m scripts.admin issue-local --organization
  ... --subject ... --role ...` provisions a named local principal: it creates the
  organization if needed, generates a high-entropy `aia_` credential, prints it
  exactly once and stores only its SHA-256 hash. Revocation uses the existing
  `revoke --token-id` command.

`require_local_reviewer` in `app/api/assessments.py` was dead code (defined, never
called, with a second, divergent copy of the reviewer gate). It was removed; the
single reviewer gate is `app.security.require_reviewer_or_local_demo`.

## Threat model

| Threat | Control |
| --- | --- |
| Local/demo caller impersonates another reviewer | Verified principals may only submit their own subject; a mismatch is 403 and the audit actor is the credential subject, never request text. |
| Cross-tenant read/write in the local path | The credential's organization is server-resolved and every nested read/write is filtered by it; other organizations get 404. |
| Shared local token used to fake a named identity | The shared token can only act as the synthetic `local_demo` actor; all of its records are flagged unverified and it cannot cast approval votes. |
| Forged audit actor | The audit actor, its organization and the verification flag are stamped from the session principal by a mapper hook, so a client payload cannot become an actor. |
| Approval chain collapsed onto one actor | The synthetic identity cannot vote; verified principals are recorded by subject and the chain requires three distinct subjects. |
| Credential theft / reuse | Existing controls apply unchanged: hashed at rest, revocable, expiring, role- and organization-bound, and rate limited per principal. A revoked, expired or malformed credential is 401 in demo mode as well. |
| Stale schema silently dropping provenance columns | `/ready` verifies that the required columns exist, not only that a version string matches. |

## Alternatives considered

- Keep the global demo principal and only mark records unverified: rejected; it
  leaves tenant scoping and reviewer attribution unaddressed.
- Trust the `reviewer_id` body field and record it as the actor: rejected; it is
  self-asserted free text and was the root cause of the gap.
- Require the shared `ADVISORY_REVIEW_TOKEN` *in addition* to a local credential:
  rejected; the shared secret adds no separation of duties, and the credential
  is the stronger proof. The shared token is kept for the unverified fallback.
- Introduce a separate "local principal" table or a bespoke token format:
  rejected; `api_credentials` already models subject, organization, role,
  revocation and expiry, and the existing `TOKEN_RE`/`digest_token` helpers are
  reused unchanged.
- Mint local credentials as an additional column on `api_credentials`
  (e.g. `local_only`): rejected; in a local deployment the flag would have no
  effect, and in pilot mode `ADVISORY_OIDC_REQUIRED=1` already rejects API
  credentials, so it would add schema without adding a control.

## What remains unverified

- A stored credential proves **possession of a secret**, not a human identity.
  There is no MFA, no IdP round trip, no device or session binding and no
  personhood proof. `identity_verified=True` means "the server resolved this
  subject from a stored credential or membership", not "this person is who they
  claim to be".
- The synthetic `local_demo` identity and the shared `ADVISORY_REVIEW_TOKEN`
  remain usable for unauthenticated local demonstration. Every record they
  produce is flagged unverified; they are not a path to a verified identity.
- Local principals are provisioned by an operator CLI. There is no self-service
  registration, no automated de-provisioning on role change and no per-person
  credential rotation policy.
- The audit trail records server-derived attribution, but it is still an
  application-level, append-only-by-convention table: no cryptographic
  tamper-evidence, no write-once storage and no independent retention or
  legal-hold guarantee.
- The organization scope of a local principal is trusted from the credential
  row; there is no external directory of record for local organizations.
- Production mode still refuses to boot, and the release gates in
  `docs/operations.md` (SSO/MFA, DLP/privacy review, backup/restore, monitoring)
  remain open. See `docs/security.md` and ADR-0005/ADR-0006/ADR-0007.
