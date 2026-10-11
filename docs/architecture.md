# Architecture — Phase 1 decision engine

The application remains an intentionally small modular monolith (see [ADR-0001](decisions/0001-modular-monolith.md)). FastAPI is the only API boundary; Streamlit is an optional stateless HTTP client.

```text
Local Streamlit UI
      ↓ POST /api/v1/analysis/preview or /discovery/next-question
FastAPI validation (Pydantic, extra=forbid)
      ↓
Assessment orchestration (pure function)
      ├── suitability and readiness rubrics
      ├── deterministic policy hits (approval/privacy/action gates)
      ├── risk × impact/likelihood matrix
      ├── evidence-sensitive value calculation (Decimal)
      └── conservative recommendation with explicit alternatives & evidence gaps
      ↓
Per-task result + stable rules-v1 version marker (no writes, no execution)
```

## Local API contracts

- `GET /health` — health, version and `rules-only` mode.
- `GET /api/v1/discovery/questions` — question bank with conditional metadata.
- `POST /api/v1/discovery/next-question` — facts/assumptions/contradictions, outstanding questions and next question.
- `POST /api/v1/analysis/preview` — per-task scoring, policy hits, risks, recommendations, advisory warnings.

This **is not the Phase 2 persistence API**: there is no account, stored assessment, approval workflow, model inference or report generation yet. API calls with confidential data should not be made to public hosts. Missing values are preserved as `null` and score coverage is shown explicitly; `0` remains a true numeric zero. Outputs are reproducible only for the declared version and supplied inputs.

## Security boundary

No LLM calls, business tool execution, database writes, or final rollout approval. The health endpoint is unauthenticated by design; the whole API should be bound to localhost and used only with synthetic data. A future deployment must enforce auth, tenancy, provider data consent, limits and audit before receiving real assessments.

## Local quality verification

`python -m pytest` and `python -m compileall -q app ui tests`. No GitHub runner is required or configured. A lock file and hosted-runtime UI check are outstanding from Phase 0.
