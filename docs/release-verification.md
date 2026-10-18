# Independent release verification (not yet complete)

**Current release state: BLOCKED.** Source-code hardening does not certify safe production use. `ADVISORY_DEPLOYMENT_MODE=production` deliberately refuses to boot.

## Local developer checks (no GitHub runners)

```bash
python -m pip install -e '.[dev]'
python -m alembic upgrade head
python -m alembic check
python -m pytest -q
python -m compileall -q app ui scripts tests migrations
python -m app.evaluation
python -m scripts.demo --output demo_out
ruff check .
ruff format --check .
# Requires network access and Python 3.12:
uv lock
uv sync --extra dev
# Local-only synthetic testing:
docker compose up --build
```

## What was actually verified on 2026-10-13

- Local reconstructed API source (Phase 0/1/2/4/5 + new hardening, with Phase 3 module stubs): **73 tests passed**, including OIDC cryptographic signature/issuer/audience failures, ambiguous memberships, cross-tenant authorization, database-backed rate limiting (including HTTP), backup integrity and production startup refusal.
- SQLite Alembic migrations 0001–0005: created a new database successfully; `alembic check` showed no schema drift.
- Synthetic fixture evaluation: four expected policy outcomes passed; the three demonstration assessments exported Markdown and HTML reports (six files).
- No GitHub Actions runners were used or enabled.

## Not verified / external owner evidence required

- [ ] Run the **full current GitHub checkout**, including actual Phase 3 AI/provider tests, not reconstructed stubs.
- [ ] Resolve and commit Python 3.12 dependency lock; Ruff check and format verification.
- [ ] Connect a real, organization-managed OIDC IdP with MFA; test JWKS rotation, JWT revocation windows, memberships and browser flow.
- [ ] Exercise all authorization paths on live PostgreSQL; verify concurrent request rate limiting and serializable data behavior.
- [ ] Restore encrypted PostgreSQL backup to an isolated instance; document RPO/RTO, rotation and deletion policy.
- [ ] Establish DPA/DPIA, residency, external model policy, retention, erasure/legal hold and access review; validate DLP/redaction.
- [ ] Deploy TLS ingress with rate limiting before auth, secret store, external audit/alerting, vulnerability scanning and independent penetration test.
- [ ] Boot Docker/Streamlit in a browser and verify login/review/export as a real authorized test user.
- [ ] Assign accountable release approver and verify rollback/incident runbooks and evidence before disabling the production guard in a reviewed change.

The available environment had Python 3.13, lacked a Docker engine, had no network package cache for a 3.12 lock, and no connected IdP/PostgreSQL service. Do not record any of these gates as passing solely because this Markdown file exists.


## Phase 6 source hardening — NOT release approval (2026-10-18)

The Phase 6 code adds a database optimistic-concurrency column (migration 0006),
`If-Match` revision preconditions for authenticated pilot writes, HTTP 409 on
concurrent database updates, read-only `/ready` schema probe, narrow outbound
AI-context screening for common emails and credentials, and fail-closed pilot
configuration (OIDC-only, PostgreSQL, `sslmode=verify-full`).

The Streamlit API client now supplies assessment revisions on writes, but it is
**not** an interactive OIDC login, does not implement secure server-side sessions
and is not an enterprise SSO browser app. A regex is not a substitute for DLP.

Run on a trusted machine (no GitHub-hosted runners):

```bash
python -m pip install -e '.[dev,postgres]'
python -m alembic upgrade head
python -m alembic check
python -m pytest -q
python -m app.evaluation
ruff check .
ruff format --check .
python -m app.enterprise_release
```

Then collect timestamped evidence from real PostgreSQL concurrency/load/restore
tests, full SSO/MFA/browser tests, external TLS/WAF, vulnerability scans, a security
review, incident/rollback drills, DPA/DPIA, regional retention/provider governance
and a named independent approver. **These checks have NOT been executed or
certified just because source files were committed. The production startup guard
remains CLOSED.** Do not put personal, customer or regulated data into this
prototype until the appropriate review gates are actually satisfied.


## Phase 7 source verification requirements (2026-10-18)

Migration `0007_collaboration` and the workspace routes introduce additional
tenant-scoped data. Run the full merged codebase tests, check SQLite/PostgreSQL
schema drift, validate authorization on every new route including inbox reads,
reviewer assignment and approval finalization, and test concurrent approval
submissions in a live PostgreSQL cluster. These have not been independently
run or signed off merely by writing test files.

The use of the words "security review" on a business workflow stage does not
certify cybersecurity compliance. Security personnel still need verified IdP
roles, reviewed policy, independent testing and their own acceptance evidence.
The enterprise release gate remains CLOSED.


## Phase 8 source verification (2026-10-18)

Apply migration `0008_portfolio` and independently run
`pytest -q tests/test_phase8_portfolio.py`, then all merged tests and
`alembic check`. Check verified-evidence segregation, assessment revision
staleness, mixed-currency non-aggregation, financial Decimal correctness,
cross-tenant access and human PoC tracking. On live PostgreSQL test
concurrent evidence/profile/observation writes. No new security certification
is implied by these source changes: full identity, privacy, backup, pen test
and named release-owner gates remain OPEN.
