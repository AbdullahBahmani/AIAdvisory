# Security and privacy — initial threat model

**Status: design note, not completed controls.** Phase 0 must only be used locally with non-sensitive demo data. Nothing in the current scaffold authorizes internet-exposed or regulated-data use.

## Assets and trust boundaries

- Current assets: source code, configuration, API response and local UI process.
- Planned sensitive assets: organization assessments, stakeholder answers, evidence attachments, system data, model outputs, risk findings and FDE decisions.
- Boundaries: browser → Streamlit; Streamlit → FastAPI; future API → database; future API → external AI provider.

## Initial threats and required future mitigations

| Threat | Mitigation / phase |
| --- | --- |
| Publicly reachable unauthenticated assessment API | Bind to localhost in development; require authenticated access, role checks and server-side tenant scopes before real data (Phase 5) |
| Secrets or personal data accidentally committed | `.gitignore` excludes `.env`, `.streamlit/secrets.toml`, local databases; use environment-based secret management, scanning and redacted logs (Phase 5) |
| Untrusted stakeholder input / prompt injection | Validate lengths/schemas, treat text as data, constrain tool use, parse structured AI output and never obey retrieved instructions (Phase 3) |
| Model leaks information to external provider | Default to no AI; review provider retention/region/permission policy before enabling requests (Phase 3/5) |
| Insecure direct data access from UI | UI calls API only; authentication and row-level authorization live in backend (Phase 2/5) |
| Unapproved consequential actions | No execution endpoints in MVP; human approval matrix and non-overridable safety gates (Phase 1 onward) |
| Cross-organization data access | Mandatory org scopes, authorization tests and isolation before multi-user launch (Phase 5) |
| Tampered assessments / unverifiable analysis | Versioned rule inputs, immutable audit records and human decision trails (Phase 2/5) |
| Dependency compromise | Dependency pinning/lock, vulnerability scanning, least-privilege CI and periodic upgrades (Phase 0/5) |

## Operational defaults

- Keep all local servers bound to 127.0.0.1 where possible; Streamlit can be configured with `--server.address 127.0.0.1`.
- Never commit production credentials or upload client data to a model during Phase 0.
- Review full blueprint security sections 38–41 before changing the deployment classification.
- The health endpoint is intentionally public; it returns no sensitive content.

## Blockers to production use

Authentication/authorization, data segregation, secret handling, audit trails, retention/deletion, privacy impact review, threat modeling for AI tools, API rate limits, TLS, monitoring, backup/recovery and deployment hardening have **not** been implemented.

## Phase 2 delta — local persistence and human review boundary

- **No authentication or tenancy yet.** The REST API can read stored assessments without login. Keep it **strictly localhost-only** with dummy/non-sensitive data; do not forward the API/UI through public services or tunneling.
- SQLite now stores names, evidence, assessments and review notes locally; the database file is ignored by Git. No backup, purge schedule, encryption-at-rest or deletion compliance guarantees exist yet.
- A 16+-character `ADVISORY_REVIEW_TOKEN` enables only developer-demo finalization/reopen/soft delete via `X-Reviewer-Token`. Missing/wrong token returns 403. **This is not a production identity, authority, or legal sign-off.** Never hardcode or commit the token.
- Analysis uses a pure rules engine. Input edits invalidate the persisted analysis, review notes retain their original revision, and hard risk/privacy controls remain independent from reviewer comments.
- Deletion is soft: an audit record is retained. No API mutates audit events directly. Audit tamper-resistance and formal retention are future hardening tasks.
- For any external deployment: add authenticated users, tenant-scoped queries, RBAC on EVERY endpoint and export, CSRF/session controls, concurrency protection, encrypted database/backups, rate/payload limits, privacy/retention policy and a threat-model review.


## Phase 3 delta — external provider opt-in (demo only)

- `ADVISORY_AI_ENABLED` defaults off. A separate local
  `ADVISORY_AI_ACCESS_TOKEN` (16+ characters) is required for every AI POST;
  the local demo caps requests at 30 proposals per assessment. Neither is
  real authentication, a global rate limiter, or a financial guarantee. With no provider key, deterministic
  discovery/scoring/analysis stays functional. No provider calls occur in offline tests.
- Every AI POST requires **both** affirmative `send_to_provider` and
  `approved_non_sensitive_data`. This is a user attestation, **not identity, DLP,
  legal consent, or data-residency compliance**. Keep real client data out.
- Server additionally blocks false `provider_data_permitted` / `permission_to_use_data`
  or a `sensitive_data` flag without explicit provider permission. Unknown
  sensitivity is still a deployment risk; no automated classifier exists.
- Provider host is fixed to HTTPS `api.openai.com`, no arbitrary user-controlled
  endpoint; keys remain server-side. Only the selected data subset is sent,
  and AI has no tools to call or actions to execute. Stored proposals include
  validated output and provenance only, not prompts or keys. Local DB still
  lacks encryption/tenant controls, so no real personal/confidential data.
- Structured JSON is validated with Pydantic extra-field rejection, length
  bounds and task-reference checks. Model output is untrusted, and any
  architecture suggestion overriding a deterministic or approval gate is rejected.
  This is defense-in-depth, **not a proof against all prompt injection**.
- External requests use bounded timeouts and a single transient retry. HTTP
  errors expose sanitized messages. Provider pricing is **not** independently
  computed here; token totals are recorded, not cost claims. Usage is therefore
  a proxy, and budget/rate-limits and model-provider policy review remain needed.
- AI-run GET/POST remain **unauthenticated local-demo endpoints**. Never bind
  to a public interface, publish via tunnel, or use real customer data.


- Optional token-cost estimates use operator-entered per-million token prices
  with a source identifier. No unverified price is prefilled, and unknown
  monetary cost remains unknown. These are estimates, not billing records.
- Explicit reviewer-token acceptance can import only selected, current-revision
  workflow task drafts; it invalidates deterministic scores and defaults all
  imported signals/actions/risks to unknown/empty. Reviewer label remains
  unverified and is not a production authorization.


## Phase 5 pilot authorization and release gate

Every `/api/v1` route is now denied without a server-provisioned credential, except when `ADVISORY_DEMO_MODE=1` is **explicitly** enabled for a loopback synthetic demo. Random opaque tokens are hashed, revocable, expiring and organization/role-bound. Server-side tenant checks cover assessments, nested workflows, AI history, reviews and reports; old records without an organization ID remain inaccessible in authenticated mode. Reviewer identity labels are credential-bound, not independently verified human identities. Response/log telemetry omits bodies, URLs, credentials and sensitive query strings. **Do not claim this is enterprise identity, SSO/MFA or DLP/privacy compliance.** Production rollout stays blocked pending the gates in `docs/operations.md` and ADR-0005.


## Additional optional OIDC gate and persistent throttling (2026-10-13)

OIDC access tokens are validated with pinned HTTPS issuer, audience, JWKS and algorithm RS256, mandatory exp/iat/iss/aud/sub claims and bounded JWK cache/timeouts. Authorization is **not** derived from JWT role, group or organization claims: an operator-approved `oidc_memberships` row maps an external subject to an organization and role. Multi-org users must explicitly select an existing membership. `ADVISORY_OIDC_REQUIRED=1` rejects older local API credentials. There is no hosted IdP, MFA, browser OAuth session or consent system supplied by this repository.

After authentication, API requests use SQL atomic per-subject/organization minute buckets; use a separate ingress limiter for unauthenticated traffic and protection against distributed attacks. SQLite is for local use; PostgreSQL is required for cross-replica storage. Request counts are prunable by operator command. The SQLite backup and retention CLI require explicit output paths, tenants and cutoffs; retention deletes related audit entries, so obtain policy/legal-hold approval first. `ADVISORY_DEPLOYMENT_MODE=production` refuses startup while external release gates remain unverified.

## Local/demo actor attribution (2026-10-20, ADR-0014)

`ADVISORY_DEMO_MODE=1` no longer answers every caller with one global identity. A
presented credential is authenticated **exactly like the production path**: the
`Authorization: Bearer aia_…` value is resolved from `api_credentials` (hashed
lookup, revocation, expiry, organization and role checks) into a server-verified
`subject`, `organization_id` and `role`. A JWT is validated against the pinned OIDC
issuer/audience/JWKS and mapped to a pre-provisioned membership. A bad, revoked,
expired or malformed credential is **401 even in demo mode** — the demo switch is
never a fallback after a failed credential.

Only the **absence** of any credential produces the synthetic `local_demo`
identity. It has no organization, is never verified, and every record it produces
stays marked `identity_unverified=True` / `actor_verified=False`.

- **Tenant scope.** A named local principal sees only its own organization.
  Assessments created by the synthetic demo identity have no organization and
  stay quarantined from verified principals, like legacy unscoped rows.
- **Server-derived audit actor.** The audit actor, the actor's organization and
  the verification flag are stamped from the authenticated principal, including
  by a mapper hook, so request text can never become an actor. A submitted
  reviewer label is rejected (403) for a verified principal and, for the
  synthetic demo, kept only as an explicitly unverified `claimed_actor`.
- **Role and separation of duties.** A local principal needs the `reviewer` role
  to review or finalize; the synthetic demo identity keeps the shared
  `ADVISORY_REVIEW_TOKEN` (403 otherwise) and can never cast approval votes, so
  the three-stage chain cannot collapse onto one unverified actor. With
  `ADVISORY_REQUIRE_APPROVAL_CHAIN=1` the local path enforces the same
  three-distinct-actor gate as the pilot path. Reviewer assignment binds too.
- **Provisioning.** `python -m scripts.admin issue-local --organization … --subject
  … --role …` provisions a named local principal, prints the credential once and
  stores only its SHA-256 hash; `revoke --token-id …` invalidates it.

**Still not identity proof.** A credential proves possession of a secret, not a
human identity: no MFA, no IdP round trip, no session or device binding. The
synthetic demo identity and the shared reviewer token remain synthetic and
permanently unverified. Schema `0009_local_attribution` adds
`reviews.reviewer_organization_id`, `audit_events.actor_organization_id` and
`audit_events.actor_verified`; apply it with `python -m alembic upgrade head`
before starting the API.

