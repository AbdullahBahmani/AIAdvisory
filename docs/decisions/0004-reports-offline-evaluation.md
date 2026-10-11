# ADR 0004: Read-only, revision-bound reports and offline synthetic regression suite

**Status:** Accepted for local-demo Phase 4, 2026-10-12.

## Context

Reports must show facts, assumptions, task-level controls, and final-human-decision limitations without laundering AI proposals into authoritative recommendations. We must also demonstrate AI/non-AI/hybrid contrasts while avoiding hosted CI runners and provider expenses.

## Decision

- Generate Markdown and self-contained HTML from **one typed assessment view model**, and refuse missing/stale analysis (HTTP 409).
- Escape all dynamic Markdown and HTML text; HTML uses a restrictive CSP; responses use `Cache-Control: no-store` and attachment filenames based on server-generated UUIDs.
- Keep optional provider outputs separate, labeled advisory/unverified, and marked stale if from a prior revision. Include their version/provenance, not their outputs as established facts.
- No new database columns or migrations for report content: reports are deterministic representations of existing saved records.
- Add a no-network `python -m app.evaluation` with versioned goldens and an isolated `python -m scripts.demo` for three end-to-end examples. New acceptance data should be measured and reviewed; curated examples are not accuracy claims.
- Add loopback-only Docker/Compose demo scaffolding; leave authentication/tenancy and enterprise deployment for a separate security gate.
- Defer PDF: HTML can be printed by the user but is not a validated rendering service.

## Consequences

- Every user input change requires a fresh deterministic analysis before exporting, avoiding out-of-date reports.
- HTML outputs must not execute user/model-provided scripts. Report downloads should never be shared publicly in this unauthenticated demo.
- The MVP still has no authenticated FDE identity, production authorization, provider evaluation evidence or operational hardening.
