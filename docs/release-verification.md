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


## P5-03 local PostgreSQL drill — NOT production validation (2026-10-19)

**Planner item P5-03 stays OPEN.** What follows is a *local, disposable* drill
against a throwaway `postgres:16-alpine` container. It is **not** a production
PostgreSQL validation, **not** an off-site encrypted backup, **not** a capacity
or load test at production volume and **not** a tested RPO/RTO.
`ADVISORY_DEPLOYMENT_MODE=production` still refuses to boot.

### How it was run (reproducible)

```bash
uv sync --extra dev --extra postgres
python -m scripts.pg_drill all     # up + migrate + fresh + backup-restore + down
# or, against an already running instance:
ADVISORY_PG_DRILL_URL=postgresql+psycopg://... python -m pytest -q tests/test_postgres_drill.py
```

Environment: Docker 29.8.2, `postgres:16-alpine`, Python 3.12.15, SQLAlchemy
2.1.5, Alembic 1.13, psycopg 3. Containers `aiadvisory-pg-drill` (port 15432)
and `aiadvisory-pg-drill-restore` (port 15433), both **removed** at the end of
the session; the dump file was deleted. The drill password is a throwaway
local-only value read from `PG_DRILL_PASSWORD` (documented default
`local_drill_only_pw`); it is not stored in the repository.

New file `tests/test_postgres_drill.py` (16 tests) is skipped unless a
PostgreSQL URL is configured (`ADVISORY_PG_DRILL_URL`, or `DATABASE_URL`).
It uses its own `aiadvisory_pg_drill` schema inside the target database and
drops it afterwards, so it never mutates the application schema.

### 1. Alembic migration chain on PostgreSQL — CLEAN

| Step | Result |
| --- | --- |
| `alembic upgrade head` on an empty PostgreSQL 16 database | All 8 revisions applied, no errors |
| `alembic check` | `No new upgrade operations detected.` (exit 0) |
| `CREATE DATABASE <fresh>` → `upgrade head` → `check` | Clean on a brand-new database |
| `alembic downgrade base` | Succeeded (all application tables dropped, only `alembic_version` left) |
| `upgrade head` again after the downgrade | Clean, all 21 tables present |

Observed output (fresh database):

```
INFO  [alembic.runtime.migration] Running upgrade  -> 0001, ...
INFO  [alembic.runtime.migration] Running upgrade 0007_collaboration -> 0008_portfolio, ...
INFO  [alembic.runtime.plugins] setting up autogenerate plugin alembic.autogenerate.constraints
No new upgrade operations detected.
```

No SQLite-specific statement was found in `migrations/versions/*.py`. The one
migration worth calling out is `0004_tenant_security`, which uses
`batch_alter_table` to add `assessments.organization_id` plus a foreign key and
to drop it again on downgrade. On PostgreSQL the batch runs as plain
`ALTER TABLE assessments ADD COLUMN ...; ALTER TABLE assessments ADD CONSTRAINT
fk_assessments_organization_id_organizations ...;` and the downgrade emits
`ALTER TABLE assessments DROP COLUMN organization_id;`. PostgreSQL drops the
dependent foreign key with the column, so the downgrade completes (verified
explicitly, including a manual `ALTER TABLE ... DROP COLUMN`).

### 2. Test suite against PostgreSQL

| Run | Result |
| --- | --- |
| `pytest -q` (default, SQLite) | **125 passed**, 16 skipped (the new drill module) |
| `DATABASE_URL=postgresql+psycopg://… pytest -q` | **141 passed, 0 failed** (125 existing + 16 drill) |

**No test failed on PostgreSQL, but the honest reading is that this proves very
little about the existing suite.** A throwaway SQLAlchemy event probe counted
the statements executed on the global `app.db.session.engine` during the whole
125-test run with `DATABASE_URL` pointing at PostgreSQL:

```
total statements on global engine: 0
tests that executed >=1 global-engine statement: 0
```

Every pre-existing test creates its own `sqlite:///…` file database and
overrides the `get_db` dependency, so **none of them executes a single
statement against PostgreSQL**. The suite is SQLite-bound by construction:
there is no backend-parametrized fixture, so "pointing `DATABASE_URL` at
PostgreSQL" does not exercise the PostgreSQL code path. This is a real gap,
not a passing grade. The new `tests/test_postgres_drill.py` is the first
automated coverage of the PostgreSQL path, and it is opt-in.

Classification of results:

- 125 existing tests: pass on SQLite and (vacuously) with a PostgreSQL
  `DATABASE_URL`. Not failures, but not PostgreSQL evidence either.
- 16 new drill tests: pass on a real PostgreSQL 16 instance. They are the
  actual PostgreSQL evidence in this section.

### 3. Concurrency (optimistic version, row locks, If-Match)

Verified on PostgreSQL, not SQLite:

- **Optimistic write version**: two sessions load the same assessment, both
  modify it and both commit. Exactly one commit succeeded, the other raised
  `StaleDataError`; the stored value is the winner's (no lost update).
- **HTTP 409**: with a competing write injected between the request's read and
  its `UPDATE`, the losing `PATCH /api/v1/assessments/{id}` returned **409**
  (`Assessment changed concurrently; reload and retry`) and the stored title
  and revision were unchanged. No 500 was produced.
- **Two parallel writers**: exactly one `PATCH` returned 200, the other 409 or
  412; the row revision advanced by exactly one.
- **If-Match preconditions**: no `If-Match` → **428**; stale `If-Match` → **412**;
  current `If-Match` → 200 with `revision + 1`.
- **`SELECT … FOR UPDATE`**: `app/api/workspace.py` and `app/api/portfolio.py`
  call `db.refresh(row, with_for_update=True)`. A second writer was verifiably
  **blocked** while the first transaction held the row lock and only proceeded
  after the first committed. This is a genuine PostgreSQL row lock; SQLite
  ignores `FOR UPDATE` entirely, so the SQLite suite cannot show this.

### 4. Rate limiter atomicity

`app/throttling.py` uses `INSERT … ON CONFLICT DO UPDATE` on
`request_limits(identity_hash, minute_bucket)`.

- 24 parallel writers, capacity 10: the counter reached exactly 24 (no lost
  increments), each minute bucket admitted at most 10, and every request over
  the limit raised HTTP 429.
- 20 parallel HTTP requests through the real API, capacity 5: the persisted
  counter equals the number of requests, HTTP 200 and HTTP 429 counts match
  the per-bucket capacity exactly.

The upsert is atomic on PostgreSQL across concurrent connections.

### 5. Index and constraint review against the live catalogue

All 19 declared `ix_*` indexes and all 5 unique-constraint indexes exist in the
PostgreSQL catalogue with the expected shape (45 indexes in total, including
the 21 primary keys), and the expected foreign keys exist with the expected
`ON DELETE` behaviour (CASCADE on child tables, RESTRICT on
`assessments.organization_id`). The tenant listing query is served by
`ix_assessments_organization_id` (verified with
`EXPLAIN (ANALYZE, BUFFERS)` on 20 050 rows: `Index Scan using
ix_assessments_organization_id`, no sequential scan).

Findings (all real, none fixed here because `app/db/*` and `migrations/*` are
owned elsewhere):

1. **`inbox_notifications.assessment_id` has no index** — it is the only
   foreign key in the schema without a supporting index (PostgreSQL does not
   index referencing columns automatically). A parent assessment delete must
   therefore scan the notification table.
2. **No composite index for the tenant listing order.** `GET /api/v1/assessments`
   (`app/api/assessments.py:180`) and `GET /api/v1/portfolio/opportunities`
   (`app/api/portfolio.py:92`) run
   `where status <> 'deleted' and organization_id = :org order by created_at desc limit 50`.
   The plan is `Index Scan using ix_assessments_organization_id` → `Sort (top-N
   heapsort)`: every request sorts the tenant's whole assessment set in memory.
   A composite `(organization_id, created_at DESC)` index removes the sort.
3. **`GET /api/v1/workspace/inbox`** filters `organization_id` **and**
   `recipient` and orders by `created_at desc`, but only two single-column
   indexes exist (`ix_inbox_notifications_organization_id`,
   `ix_inbox_notifications_recipient`). Observed plan: `Index Scan using
   ix_inbox_notifications_recipient` + `Filter: organization_id = …` + `Sort`.
   A composite `(organization_id, recipient, created_at DESC)` index is the fix.
4. **Membership lookups on the request path** (`app/api/workspace.py:40` and
   `:45`) filter `organization_id` + `subject` (+ `active`), but only
   `organization_id` is indexed on `oidc_memberships` and `api_credentials`;
   the remaining predicates are heap filters. The OIDC login lookup
   (`app/identity.py:57`, `issuer` + `subject`) *is* covered by the leading
   columns of `uq_oidc_member_identity_org`.
5. **`ix_answers_key` is unused.** No query in `app/` filters answers by
   `key` (answers are always read per assessment through the relationship).
   It only adds write cost on every answer insert.
6. `app/api/assessments.py:288` (step order uniqueness) is enforced in Python
   over the loaded relationship, not by a database constraint; two concurrent
   step creations with the same `order` can therefore both succeed.

### 6. Backup / restore drill (off-site equivalent)

`pg_dump -Fc` from the source container, restored with `pg_restore` into a
**second, independent container** (the local stand-in for a separate host),
then verified.

| Measurement | Value |
| --- | --- |
| Seeded data (written through the real API) | 200 assessments, 200 workflows, 400 steps, 400 tasks, 200 answers, 1 400 audit events, 1 organization, 1 credential |
| `pg_dump -Fc` | 185 268 bytes in **0.10 s** |
| `pg_restore` into the second container | **0.15 s** (0.24 s in a repeated run) |
| Total dump + restore | **0.25 s** (0.34 s in a repeated run) |
| Row counts on 12 key tables, source vs restored | **identical** |
| API read from the restored instance | `GET /api/v1/assessments` → 200, same rows; `GET /api/v1/assessments/{id}` → 200, same title (`Drill assessment 199`) |

**These timings are NOT an RPO/RTO measurement.** The dataset is ~185 KB of
synthetic data on a local laptop, the dump is **not encrypted**, the "off-site"
target is a second container on the same host, and no continuous archiving,
point-in-time recovery, WAL shipping or failover was exercised. A meaningful
RPO/RTO requires the managed instance, encrypted off-site backups, a documented
retention policy and an independent restore rehearsal with realistic volume.

### 7. Additional local observation

The real ASGI server was started against PostgreSQL (`uvicorn app.main:app`):
`/health` → 200, `/ready` → `{"status":"ready"}`, `POST /api/v1/assessments` →
201, `GET /api/v1/assessments` → 50 rows.

### 8. What is still unverified for production

- [ ] The **managed** PostgreSQL cluster with TLS (`sslmode=verify-full`),
      least-privilege service account, connection pooling, backups and
      point-in-time recovery. The drill used a throwaway local container with
      no TLS and a superuser.
- [ ] **Encrypted, off-site** backups with a written retention, rotation,
      deletion and legal-hold policy, restored and verified by someone other
      than the author of the dump.
- [ ] **Capacity and load** at production volume: concurrent tenants, long
      transactions, `VACUUM`/bloat behaviour, autovacuum settings, connection
      exhaustion, pgbouncer, replica lag and failover.
- [ ] **Index tuning at volume**: the findings in section 5 were derived from
      a small dataset; the optimal indexes must be re-derived with
      `pg_stat_statements` and realistic row counts.
- [ ] A **backend-parametrized test suite**. The 125 existing tests still run
      only against SQLite; the PostgreSQL coverage is the 16 opt-in drill
      tests and is not part of the default `pytest -q` run.
- [ ] Failure-recovery drills: crash/kill during a write, disk-full, network
      partition between replicas, restore into a running service, and
      verification that audit history survives.
- [ ] Independent penetration test, DPA/DPIA, residency/retention, external
      audit and alerting, and a named release approver.

Do not record P5-03 as complete because this section exists.
