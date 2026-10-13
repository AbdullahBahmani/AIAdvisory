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
