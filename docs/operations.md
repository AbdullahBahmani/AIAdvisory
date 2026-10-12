# Phase 5 operational controls — pilot ONLY

**Release gate: NOT CLEARED.** An API token credential is not enterprise SSO/MFA, an issuer's claimed subject is not external proof of identity, and localhost demo mode has no protection. No sensitive data or external production access without audited IdP/OIDC integration, organizational governance, independent security review, and an actual PostgreSQL/backup/restore drill.

## API access and tenant boundaries

- Default state is **deny all `/api/v1` requests** without an opaque Bearer token. `/health` is intentionally public. Leave `ADVISORY_DEMO_MODE` **unset** in authenticated environments. Compose intentionally enables it only for local synthetic demonstrations and binds both host ports to `127.0.0.1`.
- After `alembic upgrade head`, use the trusted *local operator CLI* to provision a credential. No issuance endpoint is exposed over HTTP:

  ```bash
  python -m scripts.admin issue --organization "Example" --subject "fde@example" --role admin --days 7
  python -m scripts.admin issue --organization "Example" --subject "analyst@example" --role analyst --days 7
  python -m scripts.admin revoke --token-id "credential-uuid"
  ```

- Save the generated `aia_...` token securely on first display. The database stores only SHA-256 of randomly generated 288-bit tokens; expiry and revocation are enforced on each request. Use `Authorization: Bearer aia_...`. Never put the token in a URL, git file, report or log. The Streamlit sidebar holds it per session, but **does not provide enterprise session management**.
- Role hierarchy: viewer (read), analyst (edit/run analysis), reviewer (review/finalize demo decision), admin (organization management/soft delete). Authenticated reviews require the reviewer's credential-bound subject, not a typed impersonated name; finalization is limited to that reviewer or admin. No role can override hard-rule controls or authorize real-world execution.
- Every persisted assessment is assigned a server-derived `organization_id`. Legacy assessments remain **NULL/quarantined** and are invisible to authenticated tenants until an explicit, audited migration is designed. Nested workflow/step/task accesses, AI proposal history and report exports must resolve to the same tenant. No client-provided tenant ID is trusted.
- No token lifecycle self-service, OIDC verification, MFA, independent admin approval or policy engine yet; these remain production blockers. Do not grant admin tokens to browser users in real deployments.

## Database and backups

- Local test uses SQLite; `SQLAlchemy` uses `DATABASE_URL` for PostgreSQL when the optional `.[postgres]` driver is installed. **Live PostgreSQL migration, load, isolation, failure, backup and restore tests have NOT been run.** Use TLS, least-privilege service account, non-default secrets and a managed database when piloting.
- Stop the app before a filesystem SQLite copy, or use SQLite's online `.backup()` API to a protected file; encrypt backups at rest and test restore to a separate database. For PostgreSQL, take encrypted managed snapshots/`pg_dump` and drill `pg_restore` in isolation; do not log DB URL credentials.
- Establish written retention, deletion, soft-delete, backup expiry, export access, DSAR process and legal hold policies *before* collecting real data. A soft-deleted record is not erased; it remains in the database and audit log. Do not call this GDPR-complete.
- Use a single migration owner, preserve reversible backups, and review Alembic before release. Schema `0004_tenant_security` does NOT attach legacy data to a new tenant.

## Monitoring and incident handling

- All HTTP replies set a request ID, no-store, nosniff, no-referrer, deny-framing. Local log lines record **only route template, method, status, request ID and elapsed milliseconds**; they exclude query strings, request bodies, response bodies and tokens.
- Monitor 401/403, 5xx, DB latency, model-call failure/budget, unusual edits, export volume, review overrides, backups and space usage through a privately configured observability stack. No external logs connector is configured yet.
- Operate behind a TLS termination proxy with request/body size limits, distributed rate limiting, security scanning and outbound provider allowlisting. There is **no implemented distributed rate limiter**; this is a production blocker. Keep API/UI loopback-bound for local demo.
- Incident: stop API/public ingress, revoke compromised credential IDs via CLI, rotate any leaked AI/DB keys, preserve audit events, inspect request IDs and relevant tenant scopes, and restore from verified backups if needed.

## Pre-deployment gates still open

- [ ] Independent identity/SSO, MFA, verified tenant provisioning and user lifecycle
- [ ] Automated negative authorization tests for every endpoint, including AI acceptance and reports, in the *full merged repository*
- [ ] PostgreSQL deployment, concurrency, migrations, backup and restore drill
- [ ] Data processing agreements, residency/retention consent, redaction/DLP and deletion runbook
- [ ] TLS ingress, distributed request limiting, CSRF/session review for browser deployment
- [ ] Secret lifecycle and key rotation, dependency/audit scanner, penetration test
- [ ] Full merged Phase 3+4+5 test suite, browser and container runtime smoke, Ruff/lint, dependency lock
- [ ] Approved rollback and incident response owners
