# Technical architecture — Phase 0

**Decision status:** Adopted for local prototype; re-evaluate before production.

The repository is a modular Python monolith. FastAPI hosts the backend boundary and response schemas; Streamlit is a thin web UI that talks only to the API. The runtime is stateless today and has **no persistence or AI calls**. Phase 1 adds pure deterministic services, Phase 2 adds a persistence layer and API contracts, and Phase 3 adds optional, validated AI suggestions. See [ADR-0001](decisions/0001-modular-monolith.md).

```text
Local browser
    |
Streamlit (ui/streamlit_app.py)
    |  GET http://127.0.0.1:8000/health
FastAPI (app/main.py)
    |
  [future deterministic analysis services]
    |
  [future SQLAlchemy repository -> SQLite local / PostgreSQL production]
    |
  [future optional AI provider, only from API and after privacy checks]
```

## Contracts available today

- `GET /health`: JSON with `status`, `service`, `version`, and `mode` (`rules-only`). No secrets or assessment data.
- `GET /openapi.json` and `/docs`: generated FastAPI API description.

## Operational boundaries

- `AI_ENABLED=false` is documented for future phases, not connected to an AI runtime; AI is unavailable in Phase 0 regardless of this value.
- Streamlit's API target defaults to `http://127.0.0.1:8000`. The API and UI are local-development services, not a hardened deployment.
- The initial health endpoint has no database/readiness probe. Later health checks should distinguish liveness from downstream dependencies.
- Versioned scores, evidence, rule audit, FDE sign-off and replay are reserved for later milestones, **not existing features**.

## Release evolution

1. Create service/domain modules and offline rule tests (Phase 1).
2. Add transaction handling, migration and API request validation (Phase 2).
3. Add provider interface, structured output validation and prompt-injection controls (Phase 3).
4. Add reproducible reports, evaluation and container demos (Phase 4).
5. Secure identity, tenant isolation, backup and monitoring before real data (Phase 5).
