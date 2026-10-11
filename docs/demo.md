# Phase 4 demo and smoke-test runbook

**Local synthetic data only. No hosted runner, external model, payments, or business-system execution.**

## One-command offline decision and export demo

```bash
python -m scripts.demo --output demo_out
```

This creates an **isolated ephemeral SQLite database** via FastAPI TestClient and drives three saved workflows through API create → add steps and tasks → deterministic analyze → provisional FDE review → Markdown/HTML report export. It does **not** modify your configured production/dev database and does not finalize any decision or authorize rollout. Reports appear in `demo_out/` (Git-ignored). Expected outputs:

- `ai-support.{md,html}` → `strong_ai_candidate`
- `exact-pricing.{md,html}` → `traditional_software`
- `hybrid-refund.{md,html}` → `ai_human_approval` and approval controls

A fourth separate evaluation (`privacy-blocked`) asserts the external-provider permission gate.

## Local API + browser UI

```bash
python -m pip install -e '.[dev]'
alembic upgrade head
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

In another shell:

```bash
streamlit run ui/streamlit_app.py --server.address=127.0.0.1
```

Open `http://127.0.0.1:8501`. Create a synthetic assessment, add workflow/step/task, analyze, then use **7 · Reports** to download Markdown and HTML. Edit a task to check that exports are unavailable until analysis is refreshed. Record a review and confirm the report labels the reviewer identity unverified. AI features are optional and disabled in rules-only mode.

## Docker Compose (local loopback binding)

```bash
docker compose up --build
```

Open `http://127.0.0.1:8501`; API health is `http://127.0.0.1:8000/health`. The API container migrates the SQLite database at startup; the UI waits for its health check. Compose binds both published ports to `127.0.0.1` only, disables AI provider access by default, and stores the demo SQLite database in a named Docker volume. Data remains after container removal until the volume is explicitly deleted.

> **Not production-ready:** HTTP endpoints are unauthenticated, shared local reviewer tokens are not identity verification, and tenant isolation is not implemented. The container definitions have not been hardened for public multi-tenant use. Do not expose ports externally or upload real customer/regulated data.

## Manual smoke checklist

1. `alembic upgrade head` completes on a fresh database; `alembic check` reports no unapplied model migrations.
2. `/health` responds with HTTP 200 and `/openapi.json` includes `/api/v1/assessments/{assessment_id}/report`.
3. Offline pytest passes; `python -m app.evaluation` reports 4/4 curated cases.
4. `python -m scripts.demo` produces all six files; exact pricing prefers software and refund requires approval.
5. HTML escapes user-provided text rather than executing it; Markdown escapes injected formatting.
6. Reports return HTTP 409 for un-analyzed or edited assessments, and 404 for missing/deleted records.
7. Streamlit downloads both formats without calling database or AI providers directly.
8. No external model or GitHub Actions runner was used.
