# Independent release verification (not yet complete)

**Current release state: BLOCKED.** Source-code hardening does not certify safe production use. `ADVISORY_DEPLOYMENT_MODE=production` deliberately refuses to boot.

## Run every local gate in one command

The repository has no hosted CI by owner direction, so the local checks live in one entry point:

```bash
python -m scripts.verify_all          # every local check, one verdict
python -m scripts.verify_all --list   # list the checks
python -m scripts.verify_all --only lint
```

It runs, in order: byte-compilation, `ruff check`, `ruff format --check`, the full test suite,
the Alembic migration chain against a **fresh** database plus `alembic check` for drift, the
31-case policy evaluation, the Phase 8 portfolio regressions, and the three synthetic demo
journeys with their report exports. Exit code is non-zero if any check fails.

This is a **local** gate. It does not run a live AI provider, a real IdP, production PostgreSQL,
a penetration test or any external approval. A green result means "the local gate is green",
never "this is approved for production".

## Distributed rate limiting and load check (local, synthetic)

The rate limiter stores its counter in the application database with an atomic upsert, so it
*should* be shared by every API replica pointing at the same database. "Should" is not
evidence, so this is now checked directly.

```bash
# disposable PostgreSQL (or omit --database-url for a throwaway SQLite file)
python -m scripts.pg_drill up
python -m scripts.load_check \
  --database-url "postgresql+psycopg://aiadvisory:local_drill_only_pw@127.0.0.1:15432/aiadvisory" \
  --instances 4 --capacity 30 --requests 120
python -m scripts.pg_drill down
```

The harness starts N **real** API processes against one shared database, provisions a throwaway
credential, and fires concurrent authenticated requests across them. The assertion is that the
per-principal limit is enforced **globally**: the number of admitted requests must not exceed
the configured capacity. A per-process limiter would admit up to `capacity × instances`, and
that is precisely the failure mode the check exists to catch.

Measured results:

| Instances | Backend | Capacity | Requests | Admitted (2xx) | Rate limited (429) | p50 | p95 | Throughput |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2 | SQLite | 20 | 60 | **20** | 40 | 184 ms | 1227 ms | 44.8 rps |
| 3 | PostgreSQL 16 | 25 | 90 | **25** | 65 | 92 ms | 225 ms | 378.7 rps |
| 4 | PostgreSQL 16 | 30 | 120 | **30** | 90 | 116 ms | 232 ms | 462.7 rps |

In every case the admitted count equalled the capacity exactly and never exceeded it, so the
limit is enforced across replicas, not per process. No unexpected status codes and no
connection errors occurred. The verdict logic is covered offline by
`tests/test_load_check.py`, including the case that detects a per-process limiter.

**What this is not.** This is a local, synthetic check with a throwaway database, throwaway
credentials and a handful of processes on one host. It is **not** a capacity or load test at
production volume, and it says nothing about ingress/WAF limits against unauthenticated
floods, TLS termination overhead, or behaviour under partial database failure. The
per-principal limiter is an application-level control; an ingress limit in front of it is
still required by P5-05.

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
# Dependency vulnerability scan of the committed lock file (requires network;
# runs entirely locally, no hosted runner, no new project dependency):
uv audit --locked
# Local provider-spend estimate (read-only; see docs/spend.md):
python -m scripts.spend_report --organization "Example Co"
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
- [ ] Deploy TLS ingress with rate limiting before auth, secret store, external audit/alerting, vulnerability scanning and independent penetration test. (*A local dependency scan of the committed lock file now exists — see "P5-04 / P5-05 local checks" below. The hosted/CI scan, image scanning, SBOM and independent penetration test remain OPEN.*)
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


## P4-05 container deployment verification (2026-10-19)

This section records **only what was actually executed** on 2026-10-19 in the
`feat/p4-05-docker` worktree. It is a local, synthetic, loopback-only
deployment check. It is **not** release approval, not a security review and
not a production-readiness statement. The enterprise release gate remains
CLOSED and `ADVISORY_DEPLOYMENT_MODE=production` still refuses to boot
(verified below).

Environment: macOS (Darwin) on arm64, Docker Engine 29.8.2, Docker Compose
v5.6.0, Python 3.12.15 (`uv` 0.12.1), repository at commit `e2557f1`
plus the Dockerfile/Compose changes described in this section.

### Commands that actually worked

```bash
cd <worktree>/p4-05-docker
uv sync --extra dev --extra postgres
uv run --no-sync python -m pytest -q            # 125 passed
docker compose build
docker compose up -d
docker compose ps
docker compose logs api
curl -sS -o /dev/null -w "%{http_code}\n" http://127.0.0.1:<host-port>/health
curl -sS -o /dev/null -w "%{http_code}\n" http://127.0.0.1:<host-port>/ready
curl -sS -o /dev/null -w "%{http_code}\n" http://127.0.0.1:<host-port>/openapi.json
uv run --no-sync python -m scripts.docker_smoke --base-url http://127.0.0.1:<host-port>
docker compose down -v
```

`<host-port>` was `18000` for the API and `18501` for the UI in this run
(see the host-port deviation below); with the committed Compose file and
free host ports it is `8000` and `8501`.

`docker compose build`, `up -d`, `ps`, `logs`, `down -v` all worked with the
stock Docker CLI; no alternative invocation, wrapper or legacy
`docker-compose` binary was needed.

**Host-port deviation (recorded, not hidden).** At the time of this run
another local process on the same machine already held `127.0.0.1:8000` and
`127.0.0.1:8501`, so the committed mapping could not be bound. The identical
containers were therefore published on other loopback host ports using a
*local, uncommitted* Compose override, and every HTTP check below was made
against those containers:

```bash
# temporary, NOT committed: ports: !override ["127.0.0.1:18000:8000"] / ["127.0.0.1:18501:8501"]
docker compose -f compose.yaml -f /tmp/ports.override.yaml up -d
```

The committed `compose.yaml` still binds `127.0.0.1:8000:8000` and
`127.0.0.1:8501:8501` (confirmed by `docker compose config`, which prints
`host_ip: 127.0.0.1`, `target: 8000` and `host_ip: 127.0.0.1`,
`target: 8501`, and by `docker inspect` of the published bindings).
Nothing else was changed between the two port assignments: same image, same
environment, same volumes, same healthchecks, same commands. **The canonical
host ports themselves were therefore never exercised in this run** and are
reported as configuration-verified only.

### Build

```
$ docker compose build
 Image aiadvisory-local:0.8.0 Built
```

The build was **failing before this change**. Real defects found and fixed:

1. **Build was broken.** `.dockerignore` excluded `scripts/` while the
   Dockerfile did `COPY scripts/ ./scripts/`, so the build aborted with
   `failed to calculate checksum ... "/scripts": not found`. The exclusion
   was removed (the operator CLIs `scripts/admin.py` and
   `scripts/maintenance.py` are useful in the image; the offline demo
   script `scripts/demo.py` is excluded because it needs the dev extra).
2. **Unlocked dependency resolution.** The image installed the project with
   `pip install '.[postgres]'`, i.e. floating versions unrelated to the
   committed `uv.lock` that the test suite runs against. The image now
   installs the locked set with `uv sync --locked --no-dev --extra postgres
   --no-install-project`, so the build fails loudly if `pyproject.toml` and
   `uv.lock` ever disagree.
3. **Ran as root.** A dedicated unprivileged user was added
   (`appuser`, uid/gid 10001); the container now runs as that user and the
   SQLite volume directory is owned by it.
4. **No image healthcheck.** A `HEALTHCHECK` on `/ready` was added to the
   Dockerfile, and Compose overrides it per service so the Streamlit role
   probes its own port instead of the API port.
5. **Duplicate application copy.** The project was installed into
   site-packages *and* copied to `/app`. Only `/app` is used now
   (`PYTHONPATH=/app`, `--no-install-project`).
6. **Compose declared a separate build per service**, so the two roles could
   drift onto two different image tags built from the same Dockerfile, and
   `ADVISORY_DEPLOYMENT_MODE` was not pinned. Both services now share one
   declared image (`image: aiadvisory-local:0.8.0`, via a YAML anchor) and
   `ADVISORY_DEPLOYMENT_MODE: "local"` is explicit, together with
   `cap_drop: [ALL]`, `read_only: true` and a `/tmp` tmpfs. (Compose still
   walks the build context once per service — the second pass is a cache hit
   and both services run the same tag — so this is a deduplication of the
   declared image, not of the build invocations.)
7. **No way to use the local reviewer flow without a baked secret.**
   `ADVISORY_REVIEW_TOKEN` is now passed through from the operator's shell
   (empty by default). No token, key or password is stored in the image or
   in `compose.yaml`.

### Startup, migrations and health (fresh, empty volume)

```
$ docker compose up -d
 Container p4-05-docker-api-1 Started
 Container p4-05-docker-api-1 Healthy
 Container p4-05-docker-ui-1 Started

$ docker compose ps
p4-05-docker-api-1   aiadvisory-local:0.8.0   Up 25 seconds (healthy)   127.0.0.1:18000->8000/tcp
p4-05-docker-ui-1    aiadvisory-local:0.8.0   Up 20 seconds (healthy)   127.0.0.1:18501->8501/tcp
```

Alembic ran at container start against the fresh named volume (all eight
revisions, from an empty database):

```
api-1  | INFO  [alembic.runtime.migration] Running upgrade  -> 0001, Initial frozen relational schema for local assessment persistence.
api-1  | INFO  [alembic.runtime.migration] Running upgrade 0001 -> 0002_ai_runs, ...
api-1  | INFO  [alembic.runtime.migration] Running upgrade 0002_ai_runs -> 0003_ai_cost, ...
api-1  | INFO  [alembic.runtime.migration] Running upgrade 0003_ai_cost -> 0004_tenant_security, ...
api-1  | INFO  [alembic.runtime.migration] Running upgrade 0004_tenant_security -> 0005_oidc_limits, ...
api-1  | INFO  [alembic.runtime.migration] Running upgrade 0005_oidc_limits -> 0006_concurrency, ...
api-1  | INFO  [alembic.runtime.migration] Running upgrade 0006_concurrency -> 0007_collaboration, ...
api-1  | INFO  [alembic.runtime.migration] Running upgrade 0007_collaboration -> 0008_portfolio, ...
```

`alembic current` inside the container reports `0008_portfolio (head)`.

Measured HTTP results (all against the running containers):

| Check | Result |
| --- | --- |
| `GET /health` | **200** `{"status":"ok","service":"ai-opportunity-advisor-api","version":"0.8.0","mode":"rules-only"}` |
| `GET /ready` | **200** `{"status":"ready"}` |
| `GET /openapi.json` | **200** (OpenAPI 3.1.0, `AI Opportunity Advisor API` v0.8.0) |
| `GET /api/v1/assessments` (demo mode) | **200** `[]` on a fresh database |
| `GET /` (Streamlit) | **200** |
| `GET /_stcore/health` (Streamlit) | **200** `ok` |
| Compose healthcheck (`api`, `/ready`) | container reported **healthy** |
| Compose healthcheck (`ui`, `/_stcore/health`) | container reported **healthy** |

### AI provider OFF, no key present

`docker compose exec api env` inside the running container showed exactly:

```
ADVISORY_AI_ENABLED=0
ADVISORY_DEMO_MODE=1
ADVISORY_DEPLOYMENT_MODE=local
ADVISORY_REVIEW_TOKEN=<redacted>
DATABASE_URL=sqlite:////data/aiadvisory.sqlite3
```

`ADVISORY_AI_API_KEY` and `ADVISORY_AI_ACCESS_TOKEN` are **not set** (grep
count 0) and are not present in the image. The AI routes refuse to run:

```
POST /api/v1/assessments/{id}/ai/discovery -> 503
{"detail":"Optional AI disabled; rules-only mode remains available"}
```

### End-to-end synthetic flow through the container

`python -m scripts.docker_smoke` (new helper, stdlib only) drove the
running container over HTTP: create assessment -> workflow -> step -> task
-> rules-only analysis -> Markdown report -> HTML report. Every check
passed:

```
[PASS] GET /health: HTTP 200
[PASS] GET /ready: HTTP 200
[PASS] GET /openapi.json: HTTP 200
[PASS] POST /api/v1/assessments: HTTP 201
[PASS] POST assessments/{id}/workflows: HTTP 201
[PASS] POST workflows/{id}/steps: HTTP 201
[PASS] POST workflow-steps/{id}/tasks: HTTP 201
[PASS] POST assessments/{id}/analyze: HTTP 200
[PASS] GET report (markdown): HTTP 200
[PASS] GET report (html): HTTP 200
[PASS] POST ai/discovery is refused (AI disabled): HTTP 503
SMOKE RESULT: PASS
```

The analysis returned the deterministic rules-only verdict
(`recommendation.category = "traditional_software"`, `policy_version
"rules-v1"`), the FDE review was recorded (HTTP 201), and the exported
Markdown report contains the assessment summary, scores and the
"local demo only" marker.

**Demo-mode switch behaves as designed.** Starting the same image with
`ADVISORY_DEMO_MODE=0` and `ADVISORY_DEPLOYMENT_MODE=local`:

```
GET /health               -> 200  {"status":"ok",...}
GET /ready                -> 200  {"status":"ready"}
GET /api/v1/assessments   -> 401  {"detail":"Bearer credential required"}
GET /api/v1/assessments   -> 401  {"detail":"Invalid or revoked API credential"}   (bogus Bearer)
```

With an operator-supplied `ADVISORY_REVIEW_TOKEN` (exported in the shell,
never committed), the local reviewer flow completed
(`POST .../fde-review` -> 201, `POST .../final-decision` -> 200, assessment
status `finalized`); the same call with a wrong token returned
**403** `{"detail":"Local demo reviewer token required"}`.

### Production startup guard still closed

```
$ docker compose run --rm --no-deps -e ADVISORY_DEPLOYMENT_MODE=production -e ADVISORY_DEMO_MODE= api
RuntimeError: Production deployment blocked pending independent release review
exit status 1
```

(With the Compose demo switch still on, the earlier, stricter check fires
first: `RuntimeError: Demo authentication bypass forbidden outside local
mode` — also exit status 1. Both paths refuse to boot. No gate was
relaxed, and no production, pilot, TLS, PostgreSQL or OIDC behaviour was
altered.)

### Clean rebuild from an empty volume

```
$ docker compose down -v
 Volume p4-05-docker_app-data Removed
$ docker compose up -d
 Container p4-05-docker-api-1 Healthy
```

After `down -v` the named volume was gone (`docker volume ls` showed no
`p4-05-docker_app-data`), and the next `up -d` applied all eight revisions
again from scratch (`grep -c "Running upgrade"` = 8) and left an empty
database (`GET /api/v1/assessments` -> `200 []`). Data written before
`down` (without `-v`) survived a container recreation, i.e. the SQLite file
really lives in the named volume.

### Container posture observed

```
/p4-05-docker-api-1 User=appuser ReadonlyRootfs=true CapDrop=[ALL] SecurityOpt=[no-new-privileges:true]
/p4-05-docker-ui-1  User=appuser ReadonlyRootfs=true CapDrop=[ALL] SecurityOpt=[no-new-privileges:true]
```

`docker compose exec api id` -> `uid=10001(appuser) gid=10001(appuser)`.
Both host ports are bound to `127.0.0.1` only. The image is one shared
image (`aiadvisory-local:0.8.0`): the virtualenv is ~523 MB uncompressed
and `docker save` produces a ~193 MB archive, dominated by Streamlit's
`pyarrow`, `pandas` and `numpy`. A separate API-only image would be
smaller, but it was not built here, so that smaller size is not claimed
as verified.

### FAILED / still open (honest list)

- **Canonical host port binding — now VERIFIED (2026-10-20).** The earlier run could not bind
  `127.0.0.1:8000` / `127.0.0.1:8501` because another local process held them, so those
  mappings were configuration-verified only. Re-run with the committed `compose.yaml` unchanged
  and no port override:
  - `docker compose build` → success; `docker compose up -d` → `aiadvisory-api-1` and
    `aiadvisory-ui-1` both report **healthy**, published as `127.0.0.1:8000->8000/tcp` and
    `127.0.0.1:8501->8501/tcp`.
  - `GET /health` → 200, `GET /ready` → 200, `GET /openapi.json` → 200, Streamlit `/` → 200,
    Streamlit `/_stcore/health` → 200 — all over the **canonical** ports.
  - `python -m scripts.docker_smoke --base-url http://127.0.0.1:8000` → **PASS** across all
    eleven assertions, including the analysis response carrying `policy_version: rules-v2`,
    so the image runs the current policy and not a stale build.
  - Container posture re-confirmed inside the running container: `uid=10001(appuser)`,
    `CapEff: 0000000000000000` (all capabilities dropped), `NoNewPrivs: 1`, and **zero**
    `ADVISORY_AI_API_KEY` / `ADVISORY_AI_ACCESS_TOKEN` variables present; the AI route answers
    **503** with the rules-only mode still available.
  - `docker compose down -v` afterwards removed the container, the named volume and the
    network; no container or volume was left behind.

- The canonical host ports `127.0.0.1:8000` / `127.0.0.1:8501` could not be
  bound during the run because another local process held them; the HTTP
  checks were made on `127.0.0.1:18000` / `127.0.0.1:18501` with the same
  containers (see above). *(Superseded on 2026-10-20: the canonical binding
  was verified separately with the committed `compose.yaml` and no port
  override — see the entry at the top of the Docker section.)*
- **`alembic check` drift — FOUND AND FIXED (2026-10-19, after this container
  run).** During this run `alembic check` FAILED inside the container with
  `Detected type change from FLOAT() to Double() on 'answers.confidence'`
  (`FAILED: New upgrade operations detected`, exit 255). It was **not** caused
  by the container: the identical result reproduced with the local virtualenv
  outside Docker. The cause was that migration `0001` created
  `answers.confidence` as `FLOAT` while SQLAlchemy 2.1 infers `DOUBLE` for a
  bare Python `float`, so the ORM models no longer described the schema the
  migrations build — and nothing enforced the gate. It has since been fixed by
  declaring the column type explicitly in `app/db/models.py` (no schema change
  and no new migration; `FLOAT` and `DOUBLE` are equivalent in SQLite and
  PostgreSQL), and `tests/test_alembic_drift.py` now rebuilds the schema from
  the migration chain and fails if the models and migrations ever drift apart
  again. `alembic check` now reports no drift.
- The canonical host ports `127.0.0.1:8000` / `127.0.0.1:8501` could not be
  bound during the run because another local process held them; the HTTP
  checks were made on `127.0.0.1:18000` / `127.0.0.1:18501` with the same
  containers (see above). *(Superseded on 2026-10-20: the canonical binding
  was verified separately with the committed `compose.yaml` and no port
  override — see the entry at the top of the Docker section.)*
- **Not verified**: interactive browser use of the Streamlit UI (screenshots
  and login/review/export as a real user are still open in P4-07);
  PostgreSQL in the container; image vulnerability scanning/SBOM/signing;
  multi-architecture builds; resource limits and load testing; TLS ingress;
  a backup/restore drill of the container volume (`python -m
  scripts.maintenance backup-sqlite --output /data/backup.sqlite3` was
  confirmed to run inside the container and produced a file, but no restore
  rehearsal was performed); crash-restart behaviour.
- The test suite result for this worktree is `125 passed`; `ruff check .`
  still reports the repository's pre-existing findings (607 errors, mostly
  `E501`/`I001` in files this task did not touch) — the new helper script
  is `ruff check` clean. The repository-wide lint/format gate therefore
  remains **OPEN**.
- No container, network, volume or image from this run was left behind:
  `docker compose down -v` was executed at the end and
  `docker ps -a`, `docker volume ls`, `docker network ls` and
  `docker images aiadvisory-local` all returned nothing afterwards.

This section does not change any other gate in this file. The production
startup guard remains CLOSED and the enterprise release gate remains
BLOCKED.
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

## P5-04 / P5-05 local checks — provider-spend report and dependency scan (2026-10-20)

**These are the two halves that were buildable without hosted runners, a
networked CI, or an external review.** They are local, offline (except for the
vulnerability advisory lookup) and read-only. **Neither one closes a planner
item.** The enterprise release gate remains BLOCKED, `ADVISORY_DEPLOYMENT_MODE=production`
still refuses to boot, and the alerting/dashboard, pen-test and CI-scanning
halves of P5-04/P5-05 remain OPEN.

Environment: macOS (Darwin) on arm64, Python 3.12.15, `uv` 0.12.1,
SQLAlchemy 2.1.5, repository at commit `bc1ea5a` plus the changes in this
section.

### 1. Local provider-spend report (P5-04, provider-spend half)

New file `scripts/spend_report.py` (no dependency changes, no application code
touched) and new test file `tests/test_spend_report.py` (21 tests).

The command aggregates the cost and token usage already stored in `ai_runs`:

```bash
DATABASE_URL=sqlite:///./aiadvisory.sqlite3 \
  uv run --no-sync python -m scripts.spend_report --organization "Example Co"
uv run --no-sync python -m scripts.spend_report --all-tenants --group-by day
uv run --no-sync python -m scripts.spend_report --organization "Example Co" --format json
```

**Tenant scoping: single tenant by default.** The command refuses to run
without an explicit scope — `--organization NAME_OR_UUID` (one tenant) or
`--all-tenants` (the cross-tenant view). Both together, or neither, is an
error (exit 2).

**Unknown cost is reported, never invented.** `ai_runs.estimated_cost_usd` is
only written when the operator supplied `ADVISORY_AI_INPUT_USD_PER_MILLION`,
`ADVISORY_AI_OUTPUT_USD_PER_MILLION` and `ADVISORY_AI_PRICING_SOURCE`. The
report classifies every run as priced / unpriced (no price recorded) / malformed
(stored value is not a finite decimal), keeps `PRICED + UNPRICED + MALFORMED = RUNS`,
never applies a price of its own, and shows `-` (not `0.000000`) where a group
has no priced run. The USD column is the exact sum of the stored prices of the
priced runs and is labelled a lower bound when any run has unknown cost.

Actual output on a local synthetic database (3 runs for one tenant: 2 priced,
1 unpriced; plus 1 unattributed legacy run):

```
==============================================================================
LOCAL ESTIMATE from operator-supplied prices - NOT a billing reconciliation
==============================================================================
Window (UTC)   : 2026-09-01T00:00:00+00:00  ->  2026-11-01T00:00:00+00:00
Scope          : organization 'Example Co' (id org-1) - single tenant
Grouping       : model
Database       : sqlite (read-only)
Runs in scope  : 3
Excluded       : 1 run(s) with no tenant assignment (visible only in --all-tenants)

GROUP                               RUNS  PRICED  UNPRICED  MALFORMED      COST USD    IN TOK   OUT TOK
-------------------------------------------------------------------------------------------------------
openai/gpt-4.1-mini                    3       2         1          0      0.007600      2640       540
-------------------------------------------------------------------------------------------------------
TOTAL                                  3       2         1          0      0.007600      2640       540
COST USD is the sum of the operator-supplied prices actually stored on the priced runs only.
'-' means no run in that group carried a stored price, so the cost is unknown, not zero.

Cost coverage  : 2 of 3 runs priced (66.7%); 1 unpriced (no operator price recorded), 0 malformed.
                 1 run(s) have UNKNOWN cost. They are counted in every
                 run total above but excluded from the USD figure, so the USD figure is a
                 lower bound for this window - not the full spend. This tool never estimates
                 a missing price; supply ADVISORY_AI_* pricing on the API host to record costs.
Token usage    : 2640 input tokens (3 run(s) reported, 0 without),
                 540 output tokens (3 run(s) reported, 0 without).
Price sources  : operator-reviewed-rate-sheet-2026-10

Reminder: these numbers come from prices the operator supplied at run time. They are not provider
invoices, not a reconciliation and not a basis for external billing claims.
```

Cross-tenant view of the same database (6 runs, 2 tenants + 1 legacy
assessment with no tenant assignment, including one deliberately corrupted
cost string):

```
Runs in scope  : 6

GROUP                               RUNS  PRICED  UNPRICED  MALFORMED      COST USD    IN TOK   OUT TOK
-------------------------------------------------------------------------------------------------------
Other Co                               2       1         1          0      0.011000      3600       880
Example Co                             3       2         1          0      0.007600      2640       540
(unassigned)                           1       0         0          1             -       400        60
-------------------------------------------------------------------------------------------------------
TOTAL                                  6       3         2          1      0.018600      6640      1480
```

The seed used to reproduce this is in `docs/spend.md`. It is synthetic data
with synthetic price strings; no price in this repository is a real provider
price and no run in it came from a real provider.

**What this is not:** no alerting, no thresholds, no dashboard, no scheduled
job, no reconciliation against provider invoices, and no currency handling
beyond the single USD estimate column. The provider-spend half of P5-04 is
*partially* addressed by a local command only.

### 2. Dependency vulnerability scan (P5-05, dependency-scanner half)

No GitHub-hosted runners exist for this repository, so the scan is a **local
command**. `uv` 0.12.1 ships `uv audit`, which queries the OSV advisory
service for the packages in the committed lock file. It adds **no project
dependency**: `pyproject.toml` and `uv.lock` are unchanged by this work.

Primary command (scans the committed `uv.lock`; `--locked` asserts that the
lock file is current, so the audited set is exactly the locked set and not a
freshly resolved one):

```bash
uv audit --locked
```

Actual result:

```
$ uv audit --locked
warning: `uv audit` is experimental and may change without warning. Pass `--preview-features audit-command` to disable this warning.
Resolved 60 packages in 3ms
Found no known vulnerabilities and no adverse project statuses in 59 packages
$ echo $?
0
```

Machine-readable form of the same run:

```
$ uv audit --locked --output-format json
{
  "schema": {
    "version": "preview"
  },
  "summary": {
    "audited_packages": 59,
    "vulnerabilities": 0,
    "adverse_statuses": 0
  },
  "vulnerabilities": [],
  "adverse_statuses": []
}
```

`uv.lock` contains 60 packages; 59 are audited because the project itself
(`ai-opportunity-advisor`) is not a dependency of anything.

**Independent second scanner (hash-checked), via `uvx` so the project's
dependency surface is unchanged.** The locked set is exported with its hashes
and audited with `pip-audit`:

```bash
uv export --frozen --all-extras --no-emit-project --format requirements-txt -o /tmp/req-locked.txt
uvx pip-audit -r /tmp/req-locked.txt --require-hashes -f columns          # PyPI service, hash validity enforced
uvx pip-audit -r /tmp/req-locked.txt --require-hashes -s osv -f columns    # OSV service
```

Actual results:

```
$ uvx pip-audit -r /tmp/req-locked.txt --require-hashes -f columns
No known vulnerabilities found
$ echo $?
0

$ uvx pip-audit -r /tmp/req-locked.txt --require-hashes -s osv -f columns
WARNING:pip_audit._cli:The --require-hashes flag with --service osv only enforces hash presence NOT hash validity. Use --service pypi to enforce hash validity.
No known vulnerabilities found
$ echo $?
0
```

The exported file contains 59 pinned packages with their SHA-256 hashes from
`uv.lock`.

**No advisory was found in the locked dependency set, so no version was
changed.** `pyproject.toml` and `uv.lock` are byte-identical to the base
commit. Nothing was suppressed and nothing was bumped to hide a finding.

**Proof that the scan is not vacuous.** A control run against a deliberately
known-vulnerable pin (`pip==24.0`, not a project dependency) was used to
confirm that the tooling does report advisories and exits non-zero:

```
$ printf 'pip==24.0\n' > /tmp/self-test-vuln.txt
$ uvx pip-audit -r /tmp/self-test-vuln.txt -s osv -f columns
Found 12 known vulnerabilities in 1 package
Name Version ID              Fix Versions
---- ------- --------------- ------------
pip  24.0    PYSEC-2026-1795 25.3
pip  24.0    PYSEC-2026-2875 26.1
pip  24.0    PYSEC-2026-1796 26.0
pip  24.0    PYSEC-2026-2876 26.1
pip  24.0    PYSEC-2026-3721 26.2.0
pip  24.0    PYSEC-2026-196  26.1.2
pip  24.0    PYSEC-2026-1795 25.3
pip  24.0    PYSEC-2026-1796 26.0
pip  24.0    PYSEC-2026-196  26.1.2
pip  24.0    PYSEC-2026-2875 26.1
pip  24.0    PYSEC-2026-2876 26.1
pip  24.0    PYSEC-2026-3721 26.2
$ echo $?
1
```

The duplicate rows are the same advisories returned through two OSV aliases;
they are reproduced here exactly as the tool printed them.

**Caveats of this scan (state them, do not hide them):**

- The result is only as good as the OSV/PyPI advisory data **at the moment of
  the run**. It is a point-in-time check, not a subscription.
- `uv audit` is an **experimental** `uv` command; the warning is printed on
  every run.
- Only the **Python** dependency set is covered. Container images, the base
  OS, the Streamlit front end assets and the provider SDKs are **not** scanned.
- No SBOM is produced, nothing is signed, and no image scan was performed.
- The scan is a **local command**: nothing runs it automatically, it is not
  scheduled, and a regression introduced later would not be caught until an
  operator runs it again. There is no hosted runner and no CI job for it.
- `pip-audit` runs in an ephemeral `uvx` environment; it does not appear in
  `pyproject.toml` or `uv.lock`.

### 3. Test suite

```
$ uv run --no-sync python -m pytest -q
169 passed, 16 skipped, 1 warning in 7.70s
```

(148 pre-existing tests still pass, plus 21 new spend-report tests. The 16
skips are the opt-in PostgreSQL drill, which needs `ADVISORY_PG_DRILL_URL`.)

`ruff check` and `ruff format --check` are clean on the two new files. The
repository-wide lint gate remains OPEN for pre-existing findings in files this
work did not touch.

### 4. Still open after this work

- [ ] Alerting, thresholds, dashboards and a scheduled provider-spend job.
- [ ] A hosted CI job that runs the dependency scan on every change, plus
      container image scanning, SBOM generation and signature verification.
- [ ] Independent penetration test.
- [ ] Every other gate in this file. **Do not record P5-04 or P5-05 as
      complete because this section exists.**
