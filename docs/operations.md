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


## OIDC operation and release checks (2026-10-13)

1. Provision an HTTPS IdP with RS256 signing, short-lived access tokens, MFA and proper issuer/audience configuration. Set `ADVISORY_OIDC_REQUIRED=1`, `ADVISORY_DEPLOYMENT_MODE=pilot`, and `ADVISORY_OIDC_ISSUER`, `ADVISORY_OIDC_AUDIENCE`, `ADVISORY_OIDC_JWKS_URL` on the API host. Configure TLS/WAF before any external users. The JWKS URL must be HTTPS and on the pinned issuer's hostname.
2. On a trusted shell run `python -m alembic upgrade head`; `python -m scripts.admin create-org --organization 'Example'`; then `python -m scripts.admin grant-oidc --organization 'Example' --issuer 'https://login.example.com' --subject 'subject-from-IdP' --role reviewer`. Use `disable-oidc --membership-id ...` to deactivate. No tenant roles come from claims or user-submitted organization names.
3. The authenticated limiter defaults to 120 requests/minute per org/subject/role. Set `ADVISORY_REQUESTS_PER_MINUTE` for piloting, monitor 429 and response `Retry-After`, schedule `python -m scripts.admin prune-ratelimits`. Deploy a separately enforced edge/IP limit before internet exposure.
4. SQLite: `python -m scripts.maintenance backup-sqlite --output /protected/backup.sqlite3`; verify permissions and run a restore rehearsal on a separate instance. On PostgreSQL use encrypted `pg_dump` and `pg_restore`, managed backups and least-privilege roles. Live PostgreSQL was **not available for verification** in this implementation session.
5. Deletion: `python -m scripts.maintenance purge-soft-deleted --organization-id <uuid> --before 2026-09-01T00:00:00+00:00` only previews. Add `--execute` after confirming tenant, legal hold, separate tested backup and retention approval. The hard purge also destroys per-assessment audit events; update legal records as required.
6. A real production launch requires independent security and operational sign-off; `ADVISORY_DEPLOYMENT_MODE=production` intentionally refuses boot. Never use local/demo mode to bypass a blocked gate. See `docs/release-verification.md`.


## Phase 6 pilot operator requirements (2026-10-18)

A `pilot` API no longer boots with demo auth, SQLite, opaque local API-token
access, or a PostgreSQL URL lacking `sslmode=verify-full`. Configure a real,
properly authorized OIDC IdP with pinned issuer/audience/JWKS, PostgreSQL over
certificate-verified TLS and a trusted edge ingress. These checks do not imply
that the identity-provider UI, MFA, browser CSRF/session lifecycle, data governance
or external edge controls have been independently validated.

After `alembic upgrade head`, `GET /ready` must report `{"status":"ready"}`
only if DB connectivity and migration `0006_concurrency` are present. `/health`
remains a liveness probe. The local Docker Compose healthcheck uses `/ready`
after it applies migrations, but Compose deliberately enables insecure
synthetic-only local demo authentication and must not be promoted to production.

Use the current `revision` or `ETag` from `GET /api/v1/assessments/{id}`
as `If-Match: "<revision>"` on pilot assessment changes. Retry only after
reloading on HTTP 409, 412 or 428. The server's independent database row
version rejects concurrent changes to the same assessment.

If upstream ingress cannot enforce TLS, WAF, IP/body-size limits, credential
rotation and alerting, **do not** expose this pilot to internet clients.
`ADVISORY_DEPLOYMENT_MODE=production` still refuses startup until a named
release owner signs off independent security/operations/privacy evidence.


## Phase 7 collaboration operations

After `alembic upgrade head`, the database schema includes reviewer
assignments, revision-bound comments, append-only human approval votes and
recipient-scoped in-app notifications. `/ready` now expects migration
`0007_collaboration`. Do not use old DB schemas with v0.7.0.

Admin assignment requires an active reviewer/admin in the same tenant and,
when OIDC-only is enabled, the configured issuer. No self-service membership
granting is offered. The in-app inbox is not an external email/push delivery
guarantee. Backup and erasure policies must explicitly cover the new tables;
hard deletion of an assessment cascades to these records subject to DB FK
enforcement and approved retention procedures.

A protected pilot FDE final decision requires three current-revision human
approvals; operational rollback/runbook and verified SSO/MFA still remain
required before production. No source code change replaces external review.


## Phase 8 data lifecycle / privacy

Schema `0008_portfolio` adds opportunity profiles, evidence references,
immutable value-scenario assumption snapshots and PoC measurements. Include
these in encryption, per-tenant restore, data subject deletion, evidence
retention, legal-hold and export-access reviews before production. Assessment
hard deletion has cascading database foreign keys; verify live PostgreSQL
behaviour and backup retention rather than assuming cascading erasure
satisfies GDPR. The public `/ready` check now expects migration
`0008_portfolio`. No new external provider/agent or business execution
permissions are introduced.


## Local Docker/Compose deployment (verified 2026-10-19)

This is the **local synthetic demo** deployment only. It is not a pilot and
not a production deployment: Compose publishes both host ports on
`127.0.0.1` only, keeps the AI provider switched off, and enables the
explicit local demo authentication switch. The production startup guard is
untouched — `ADVISORY_DEPLOYMENT_MODE=production` still refuses to boot
(verified in `docs/release-verification.md`).

```bash
docker compose build          # single shared image aiadvisory-local:0.8.0
docker compose up -d          # api on 127.0.0.1:8000, ui on 127.0.0.1:8501
docker compose ps             # wait for both services to report (healthy)
curl -sS http://127.0.0.1:8000/health
curl -sS http://127.0.0.1:8000/ready
curl -sS http://127.0.0.1:8000/openapi.json >/dev/null
uv run --no-sync python -m scripts.docker_smoke --base-url http://127.0.0.1:8000
docker compose down -v        # stop and delete the SQLite volume
```

Operator notes:

- **Migrations run at container start.** The API command is
  `alembic upgrade head && exec uvicorn app.main:app ...`, so a fresh volume
  is migrated from revision `0001` to `0008_portfolio` before the API
  serves traffic. `/ready` only returns `200` when the database is at
  `0008_portfolio`; the Compose healthcheck uses `/ready`, so a container
  that failed to migrate never becomes healthy and the UI never starts.
- **The SQLite database lives in the named volume** `app-data` mounted at
  `/data` (`DATABASE_URL=sqlite:////data/aiadvisory.sqlite3`). `docker
  compose down` keeps it; `docker compose down -v` deletes it. Back it up
  with `docker compose exec api python -m scripts.maintenance backup-sqlite
  --output /data/backup.sqlite3` before discarding a volume.
- **No secret is stored in the image or in `compose.yaml`.** If you want to
  use the local reviewer/finalize flow, export your own token before
  `up`; it is passed through to the API container and never committed:
  `export ADVISORY_REVIEW_TOKEN="$(openssl rand -hex 16)"` (at least 16
  characters). Without it the reviewer endpoints return HTTP 403.
- **AI stays off.** `ADVISORY_AI_ENABLED=0` is hard-coded in `compose.yaml`
  and no `ADVISORY_AI_API_KEY` is set or forwarded; the AI routes answer
  HTTP 503 "Optional AI disabled". Turning AI on for a local experiment
  means setting both variables in your own shell, and it still requires the
  explicit per-request provider/data approval in the UI.
- **The image is built from the committed `uv.lock`** (`uv sync --locked`),
  so the build fails if `pyproject.toml` and `uv.lock` disagree. The
  container runs as the unprivileged user `appuser` (uid 10001) with a
  read-only root file system, all capabilities dropped and
  `no-new-privileges`.
- **If the host ports are already in use** (another local service on
  `127.0.0.1:8000` or `127.0.0.1:8501`), do not edit `compose.yaml`.
  Use a temporary override file that keeps the loopback binding:
  `docker compose -f compose.yaml -f /tmp/ports.override.yaml up -d` with
  `ports: !override ["127.0.0.1:18000:8000"]` (and `18501:8501` for the
  UI). Never publish these demo ports on `0.0.0.0`.
- **The Compose stack is not a pilot.** For anything beyond a local
  synthetic demonstration use the pilot runbook above: real OIDC IdP,
  PostgreSQL over verified TLS, `ADVISORY_DEPLOYMENT_MODE=pilot` and an
  audited ingress. The Compose demo mode authenticates every request as a
  local admin and must never be exposed to a network.
- `scripts/docker_smoke.py` is a stdlib-only HTTP smoke test for a running
  container. It creates a synthetic assessment, runs the rules-only
  analysis, exports both report formats and asserts that the AI route is
  refused; it exits non-zero on the first failure and needs no key.

