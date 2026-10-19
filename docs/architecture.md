# Architecture — Phase 2 persistent local MVP

Phase 2 extends the rules-only modular monolith (ADR-0001) with a relational persistence layer. Streamlit remains a thin HTTP client; no UI access to SQLAlchemy or internal scoring functions.

```text
Local browser (synthetic data only)
  └─ Streamlit wizard: intake / discovery / workflow / analysis / FDE review
        └─ HTTP calls to FastAPI /api/v1/
              ├─ Pydantic input validation + response schemas
              ├─ SQLAlchemy transactional persistence (Alembic schema)
              │    ├─ assessments, workflows, workflow_steps, tasks
              │    ├─ answers with source/kind/confidence
              │    ├─ reviews and audit_events
              │    └─ revision-stamped immutable JSON analysis snapshots
              └─ Pure deterministic assessment engine (rules-v2)
                   ├─ suitability scores and factor coverage
                   ├─ data/technical-readiness rubrics
                   ├─ risks and hard policy gates
                   └─ recommendation and traditional-software alternatives
```

## Input revision and state machine

- `draft` → `discovering` on first input change → `analyzed` → `in_review` → `finalized`.
- Any input edit on a nonfinalized assessment increments the revision, removes the current analysis snapshot, and returns to `discovering`. Reanalysis is required before new review.
- A review records the analysis revision, proposed category, rationale, optional override, and a **self-declared** (not authenticated) reviewer label. Earlier reviews remain available when inputs change.
- Finalization requires an explicitly configured local token and an existing review for the current revision. The review is marked `is_final`, the assessment becomes read-only, and the audit trail records the event. There is no production authorization capability.
- Explicit guarded reopen increments the revision and invalidates the old snapshot; older reviews and event history remain.
- Soft deletion marks a record `deleted` and hides it from public list/get paths while retaining audit rows for later retention-policy design.

## Database and migrations

- SQLite at `sqlite:///./aiadvisory.sqlite3` by default, PostgreSQL later. Database URL is configured by `DATABASE_URL`.
- A frozen initial Alembic schema lives at `migrations/versions/0001_initial.py`; run `alembic upgrade head` before starting the API. `alembic check` verifies the ORM and schema agree.
- Store ordered step/task rows; serialize Pydantic task signals and risk inputs as JSON. Store typed nullable value inputs and the entire analysis result as a separate JSON snapshot, labeled by revision and policy version.
- API mutations happen inside a single SQLAlchemy transaction and add an audit row; audit history persists when the assessment is soft-deleted. No public event-edit API exists.
- This local MVP is **not concurrency safe for multi-user writes**: revision bumps are not compare-and-swap updates. Plan optimistic locking and tenant-scoped authorization before real deployment.

## API boundaries and compatibility

- Phase 1 stateless endpoints (`/api/v1/discovery/next-question` and `/api/v1/analysis/preview`) remain for fixtures and integration clients.
- Phase 2 routes expose assessment/workflow CRUD, conditional answers, saved analysis, review and local token-gated finalization. `GET /openapi.json` documents request/response validation.
- Streamlit never calls deterministic functions directly and never uses DB credentials.
- No provider SDKs, no ingestion, no business system writes, no external tooling, and no authenticated/multi-tenant signoff.

## Operational validation

Run `python -m pytest -q`, `python -m compileall -q app ui tests migrations`, `python -m alembic upgrade head`, `python -m alembic check` locally. Do not add GitHub hosted runners.


## Phase 3: bounded optional AI proposals

`app/api/advisor.py` exposes six opt-in features (`discovery`, `workflow`,
`risk`, `architecture`, `evaluation`, `challenge`) backed by
`app/ai/provider.py`, `app/ai/service.py`, versioned prompts, and strict
Pydantic response schemas. The standard `POST /analyze` route and the rules
engine remain deterministic and have no AI dependency.

Input policy checks run before model invocation: explicit server enablement
plus API key, explicit per-request provider/data approval, and restrictions for
tasks flagged as sensitive or not provider-permitted. Approved local demo data
is passed only as bounded structured context. AI receives **no tools**. Returned
JSON is length-limited, validated strictly, and task/step IDs are checked
against the current revision. Architecture suggestions contradicting existing
mandatory human-approval or deterministic-only outcomes are rejected.

Successful proposals persist in `ai_runs` with output schema/prompt version,
provider/model, latency and token usage (not raw model requests). Input edits
mark past runs **stale** without changing scores or final decisions. Proposed
tasks/risks are never auto-applied; FDEs must review and manually enter
accepted suggestions through Phase 2 forms. The new tab is deliberately
not an agent or autonomous workflow executor.


The optional `POST /assessments/{id}/ai-runs/{run_id}/accept-tasks` operation
requires the local review token, explicit selection and a rationale. It
imports only task title/description into their known steps, with decision type
unknown and no asserted policy signals; all analysis is invalidated.
Proposed question texts are normalized for exact duplicate rejection against
answered catalog questions and current-revision proposals; semantic similarity
is NOT guaranteed. Optional cost estimates require external verified pricing.


## Phase 5: scoped API-token pilot boundary

A FastAPI authorization dependency resolves an opaque credential in a shared request SQLAlchemy session, attaches principal/role/organization context, checks method permissions, and checks server-owned assessment ancestry for reads/writes of workflow, step, task, review, AI proposal and report resources. Credential subjects are set by a trusted local CLI; client-supplied organization text does not grant tenant access. Legacy assessments with NULL tenant IDs remain quarantined. This API-token boundary is for controlled pilots only, not a production federated login or independently verified human identity. See `docs/operations.md`.
