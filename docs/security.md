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
