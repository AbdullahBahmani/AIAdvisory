# AI Opportunity Advisor

A human-led decision-support app to discover business workflows, evaluate whether AI is appropriate, explain safer non-AI/hybrid alternatives, and document a Forward Deployed Engineer's (FDE) decision.

> **AI assists. Rules enforce. Data informs. Humans decide.** This project must not autonomously approve production AI rollouts.

## Implementation status

**Phase 0 — foundation only.** FastAPI exposes a public health endpoint, Streamlit displays an API connectivity smoke page, and offline tests and GitHub CI are configured. No assessment workflow, scoring engine, database, AI integration, authentication, or production-ready security controls exist yet. **Use only with non-sensitive demo data.** See [main-planner.md](main-planner.md) for the implementation checklist.

## Requirements

- Python 3.12 or newer (3.12 used in CI)
- pip and a virtual environment

## Run locally

From the repository root, create a virtual environment and install the project:

**macOS/Linux**

```bash
python3.12 -m venv .venv  # or python3 when it is version 3.12+
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

**Windows PowerShell**

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

Run these in separate terminals with the virtual environment activated:

**API**

```bash
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Check http://127.0.0.1:8000/health and http://127.0.0.1:8000/docs.

**UI**

```bash
streamlit run ui/streamlit_app.py --server.address 127.0.0.1
```

Open the local address shown by Streamlit (usually http://127.0.0.1:8501).

The UI assumes the API is running at http://127.0.0.1:8000. Override with `API_BASE_URL` if needed, e.g. `API_BASE_URL=http://127.0.0.1:9000 streamlit run ui/streamlit_app.py`. An optional `.env.example` documents configuration; the app does **not** automatically load `.env` in Phase 0.

## Quality checks

```bash
ruff check .
ruff format --check .
python -m pytest
```

GitHub Actions runs these offline smoke tests on pushes to `main` and pull requests. CI installs dependency ranges from `pyproject.toml`; **a fully resolved dependency lock is still pending** (see the Phase 0 checklist for this known gap). Do not interpret a clean unit test run as a production-security approval.

## Architecture

```text
Browser → Streamlit UI → FastAPI REST API → [Phase 1: deterministic services]
                                       → [Phase 2: persistence]
                                       → [Phase 3: optional guarded AI]
```

The UI is an API client and must never connect directly to a database or AI provider. The deterministic engine must work without AI credentials. Refer to [architecture](docs/architecture.md), [security](docs/security.md), and [ADR-0001](docs/decisions/0001-modular-monolith.md).

## Blueprint and roadmap

- [Original product blueprint](docs/blueprint.md)
- [Milestones, tasks, exit criteria](main-planner.md)

## Safety and deployment

The system is advisory; human reviewers retain final decisions. Future requirements include permission checks, privacy controls, a full audit log, and explicit approvals for consequential actions. **Do not deploy this scaffold publicly or use it for sensitive assessments.**

## License

No open-source license has been selected; all rights remain with the repository owner until one is explicitly added.
