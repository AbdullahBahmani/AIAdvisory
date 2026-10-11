# ADR-0001: Python modular monolith with Streamlit and FastAPI

- **Date:** 2026-10-11
- **Status:** Accepted for prototype (revisit for production)

## Context

The repository is empty except for planning documentation. The blueprint calls for a fast, reviewable FDE demonstration with deterministic recommendations and optional guarded AI. Complexity and safety are more important than deployment scale at this stage.

## Decision

- Python 3.12+ project, FastAPI REST backend with Pydantic types, Streamlit UI.
- Keep all durable business logic in backend services independent of UI and LLM provider.
- Use SQLite with Alembic migrations for local MVP (Phase 2); move to PostgreSQL before multi-user rollout.
- AI is optional and never the source of hard policy, numeric truth, or final approval.
- Use a modular monolith and plain application services; defer agents, microservices and workflow orchestration systems until their need is demonstrated.

## Alternatives

- Next.js/React: better enterprise UX, but more early frontend overhead than Streamlit.
- Pure Streamlit app: faster to start, but entangles UI, data, and security boundaries.
- Microservices/agents first: adds deployment and audit complexity before proving the decision engine.

## Consequences

- Local demos and CI are easy; testability is high when the deterministic core stays pure.
- Streamlit's UX is limited; reassess if advanced team collaboration becomes a near-term requirement.
- The frontend and backend are separate processes, so local startup requires both.
- Phase 0 has no auth, database, scoring, model integration or production hardening.
