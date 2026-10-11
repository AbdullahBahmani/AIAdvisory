# ADR-0003: Opt-in structured AI; deterministic authority

**Status:** Accepted for local synthetic-data demo (2026-10-11).

## Context

Phase 2 can produce full rules-only assessments. Phase 3 should reduce
manual discovery/analysis effort without giving a model authority to make
business decisions or trigger business actions. No authentication, tenant
permissions or provider-data governance framework exists yet.

## Decision

- Add an optional fixed-host OpenAI Chat Completions JSON-mode provider via
  standard-library HTTPS (no untrusted remote URL or agent/tools).
- Disable it by default; require explicit per-request data attestations and
  task-level provider policy checks for LOCAL SYNTHETIC assessments only.
- Provide six distinct versioned advisory behaviors and strict Pydantic outputs.
- Reject unknown task/step IDs, malformed responses and model suggestions
  contradicting hard deterministic approval/automation boundaries.
- Persist successful AI proposals and provenance in a new Alembic `ai_runs`
  table. Proposals never update existing workflow, score, risk, review or
  decision records. Mark them stale when the source revision changes.
- Use mocked provider injections for offline tests. Run tests locally only;
  never configure hosted GitHub runners.

## Consequences

The project remains fully functional without keys or network. JSON mode
returns valid JSON but is not itself a guarantee of schema compliance;
Pydantic validation is authoritative. External provider models may change
and incur token charges. AI responses remain untrusted; adoption is a manual
FDE action. The local app is still **not approved for sensitive data or
public production use** pending tenant authentication, retention, DLP and
provider compliance review.
