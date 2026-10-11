# AI Opportunity Advisor

A **human-led, rules-first** decision-support app for discovering workflows, evaluating AI vs. conventional automation, and recording an FDE's recommendation.

> **AI assists. Rules enforce. Data informs. Humans decide.** This project never authorizes production rollout or executes customer/business actions.

## Project status (Phase 2)

**Implemented:** persistent local assessments, guided discovery answers and provenance, editable workflow steps and atomic tasks, per-task deterministic scores/risks/recommendations, revision-aware stale analysis invalidation, stored FDE review notes, guarded **local-demo-only** finalization and reopen, and an append-only event trail with soft deletion. The FastAPI `/api/v1/analysis/preview` stateless endpoint remains available. No LLM is required.

**Not implemented:** user authentication/SSO, organizational authorization and tenant separation, external AI provider, data retention and backup policy, formal report export, secure production signoff, multi-user concurrency controls, and cloud deployment. The locally supplied reviewer name is **not authenticated identity**. Never enter real customer records, personal data, or confidential business information into this demo. **Do not expose the service on a public network.**

The [implementation planner](main-planner.md) tracks completed checks and remaining gates.

## Run locally — no GitHub runners

Use Python **3.12+**. The repository intentionally contains no GitHub Actions workflow or hosted-runner dependency.

```bash
python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
python -m pip install -e '.[dev]'

# Create/update SQLite schema before serving requests (required)
python -m alembic upgrade head

# Terminal 1
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

# Terminal 2
streamlit run ui/streamlit_app.py --server.address 127.0.0.1
```

API: http://127.0.0.1:8000/docs · UI: http://127.0.0.1:8501 · Health: http://127.0.0.1:8000/health

`DATABASE_URL` defaults to `sqlite:///./aiadvisory.sqlite3` (ignored by Git); use an absolute SQLite path if running commands from different directories. The `.env.example` file documents environment variables but is **not automatically loaded**. The Streamlit client uses `API_BASE_URL` (defaults to `http://127.0.0.1:8000`). Use only one developer at a time; the demo doesn't have concurrency/tenant controls.

### Optional local reviewer lock

By default the `final-decision`, `reopen`, and soft-delete operations return **403**. To enable these local-demo actions, set a private environment value **on the API server**:

```bash
export ADVISORY_REVIEW_TOKEN='replace-with-a-random-secret-at-least-16-characters'
```

Enter that same secret in the review tab's password field when finalizing or reopening. The server checks the `X-Reviewer-Token` header. The token is **not a login or production authorization model**; a name typed in the review form remains unverified. Finalization never switches off mandatory privacy/approval policy controls. Do not commit this secret.

## Guided workflow

1. Create or resume an assessment and edit problem, owner, and optional numerical baselines.
2. Answer the next discovery question with an evidence kind, source, and confidence; missing information and contradictions remain visible.
3. Create one or more workflows, add ordered steps and at least one atomic task per step; save JSON suitability signals, business actions, and optional risks.
4. Analyze saved workflows. Results use the same deterministic `rules-v1` engine as the Phase 1 preview, including explicit policy hits and missing evidence warnings.
5. Record a review with rationale and an override reason whenever departing from proposed categories. If the local reviewer token is configured, finalize the **demo assessment**. Finalized assessments are read-only until explicitly reopened.

Any change to intake, answers, workflows, steps, or tasks increments `revision` and invalidates stored analysis. Re-run analysis before recording or finalizing a new review. Earlier reviews and audit history are retained and labeled by revision. Soft-deleted assessments are hidden without destroying audit records.

## API endpoints

| Method | Route | Description |
| --- | --- | --- |
| GET | `/health` | Health/mode, no AI provider |
| GET | `/api/v1/discovery/questions` | Conditional question bank |
| POST | `/api/v1/discovery/next-question` | Stateless discovery helper |
| POST | `/api/v1/analysis/preview` | Stateless deterministic scoring (Phase 1 compatibility) |
| POST / GET | `/api/v1/assessments` | Create / list assessments |
| GET / PATCH / DELETE | `/api/v1/assessments/{id}` | Read / edit / soft-delete (delete requires token) |
| POST | `/api/v1/assessments/{id}/answers` | Record sourced, typed answers |
| GET | `/api/v1/assessments/{id}/next-question` | Next applicable unanswered/contradictory question |
| GET | `/api/v1/assessments/{id}/missing-information` | Facts and evidence gaps |
| POST | `/api/v1/assessments/{id}/workflows` | Add a workflow |
| GET / DELETE | `/api/v1/workflows/{id}` | View / remove workflow |
| POST | `/api/v1/workflows/{id}/steps` | Add ordered step |
| PATCH / DELETE | `/api/v1/workflow-steps/{id}` | Update / remove step |
| POST | `/api/v1/workflow-steps/{id}/tasks` | Add task |
| PATCH / DELETE | `/api/v1/tasks/{id}` | Edit / remove task |
| POST | `/api/v1/assessments/{id}/analyze` | Persist analysis snapshot and policy version |
| GET | `/api/v1/assessments/{id}/scores` | Read fresh scores (409 if stale) |
| GET | `/api/v1/assessments/{id}/risks` | Read risks and controls |
| GET | `/api/v1/assessments/{id}/recommendations` | Read recommendations |
| POST | `/api/v1/assessments/{id}/fde-review` | Record provisional local review |
| POST | `/api/v1/assessments/{id}/final-decision` | Guarded **local-demo-only** finalization |
| POST | `/api/v1/assessments/{id}/reopen` | Guarded reopen |
| GET | `/api/v1/assessments/{id}/audit` | Read local audit events |

See OpenAPI for exact request schemas and validation errors. API requests never call a live AI model or execute financial, customer, or operational actions.

## Local verification

```bash
python -m pytest -q
python -m compileall -q app ui tests migrations
python -m alembic check    # after `upgrade head` on your chosen DB
# Optional if installed locally:
ruff check .
ruff format --check .
```

The test suite uses isolated SQLite database files and offline FastAPI TestClient calls. It covers creation, reload, updates, discovery, task scoring, three golden AI/non-AI/hybrid fixtures, stale-result handling, finalization gates, and soft deletion. The migration was applied and `alembic check` reported no model/schema drift in the implementation environment.

**Open verification gates:** Python 3.12 lock file not yet committed; Ruff and a live Streamlit browser smoke test not verified in the available environment (Streamlit is not installed there). Python 3.13 local unit/API tests and migration checks passed. Local-only test runs replace hosted CI; **do not enable GitHub Actions runners**.

## Design and security

- [Complete project blueprint](docs/blueprint.md)
- [Architecture and state transitions](docs/architecture.md)
- [Security and deployment boundaries](docs/security.md)
- [ADR-0001: Modular monolith](docs/decisions/0001-modular-monolith.md)
- [ADR-0002: Persistent local MVP](docs/decisions/0002-phase2-persistence.md)
- [Main implementation planner](main-planner.md)

## License

No open-source license is granted unless the repository owner adds one.
