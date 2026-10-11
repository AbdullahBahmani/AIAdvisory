# AI Opportunity Advisor

A human-led decision-support app for discovering business workflows, assessing whether AI is appropriate, comparing AI/non-AI/hybrid approaches, and documenting the Forward Deployed Engineer's (FDE's) reasoning.

> **AI assists. Rules enforce. Data informs. Humans decide.** The application cannot authorize a production rollout or execute business actions.

## Current status

**Phase 1: stateless deterministic analysis preview** is implemented. Tasks are evaluated independently with traceable policy IDs, advisory scoring and coverage, data/technical-readiness rubrics, risk classification, permission/approval checks, discovery questions and an evidence-sensitive labor-value model. Tests include three contrasts: AI-support triage, deterministic pricing, and human-approved refunds. No AI provider is called.

**Not yet implemented:** persistent assessments, credentials/login, tenancy, FDE sign-off, full workflow editing, production controls and reports. This is a **local demonstration only**, with synthetic/non-sensitive input. Track verified work and open gates in [main-planner.md](main-planner.md).

## Requirements and installation

Use Python 3.12+ (3.12 is preferred) with a virtual environment. From the repo root:

```bash
python3 -m venv .venv
source .venv/bin/activate                   # Windows: .venv\Scripts\Activate.ps1
python -m pip install -e '.[dev]'
```

Start the API in one terminal:

```bash
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Open [the health endpoint](http://127.0.0.1:8000/health) or [interactive API reference](http://127.0.0.1:8000/docs).

Start the optional UI in another terminal:

```bash
streamlit run ui/streamlit_app.py --server.address 127.0.0.1
```

Streamlit defaults to `http://127.0.0.1:8000`; override with `API_BASE_URL`. Nothing is stored on a server in Phase 1. The `.env.example` is documentation only, not automatically loaded. No AI keys are required.

## API endpoints

| Method | Route | Behavior |
| --- | --- | --- |
| GET | `/health` | Stateless API health (`rules-only`) |
| GET | `/api/v1/discovery/questions` | Typed question bank including conditional items |
| POST | `/api/v1/discovery/next-question` | Next unanswered/contradictory question and evidence gaps |
| POST | `/api/v1/analysis/preview` | Per-task factor scores, risk, policy hits and recommendations |

Example: POST a workflow preview (the API does **not** persist it):

```bash
curl -sS http://127.0.0.1:8000/api/v1/analysis/preview \
  -H 'Content-Type: application/json' \
  -d '{"title":"Pricing","steps":[{"id":"s1","name":"Calculate fee","order":0,"tasks":[{"id":"t1","title":"Apply formula","signals":{"deterministic_rules":true,"exact_math":true,"output_verifiable":true}}]}]}'
```

Expected recommendation category: `traditional_software`. The response includes rule IDs `DET-001` and `MATH-001`. For a human-approval example see `tests/fixtures/cases.py`.

## Local checks — no GitHub Actions runners

```bash
python -m pytest
python -m compileall -q app ui tests
# Optional if Ruff is installed:
ruff check .
ruff format --check .
```

**No hosted CI runner is used.** The former `.github/workflows/ci.yml` workflow is intentionally removed. There is no verified transitive dependency lock yet: resolve one in a network-enabled Python 3.12 environment and commit it before claiming fully reproducible installation. Unit/API smoke tests passing locally are not evidence of secure production readiness.

## Architecture and safety

```text
Local browser → Streamlit (thin API client) → FastAPI → deterministic rules/services
                                                    ↓
                                        explicit model schemas and policy results

Future: Phase 2 persistence & review | Phase 3 provider behind privacy/authorization gates
```

- The API holds **no user data** between requests and performs no actions on external systems.
- Missing inputs remain unknown; financial models are **illustrative only**, not observed ROI.
- Hard approval/privacy gates take precedence over numeric suitability scores.
- Advisory results never replace an FDE's final review.
- No public deployment or confidential data ingestion should occur until authentication, authorization, audit and provider governance are implemented.

Read [blueprint](docs/blueprint.md), [architecture](docs/architecture.md), [threat model](docs/security.md), and [technical ADR](docs/decisions/0001-modular-monolith.md).

## License

No open-source license is granted unless the repository owner adds one.
