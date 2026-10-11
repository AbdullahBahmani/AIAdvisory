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
              └─ Pure deterministic assessment engine (rules-v1)
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
