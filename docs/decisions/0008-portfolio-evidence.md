# ADR-0008: Evidence-first portfolio and PoC intelligence

**Status:** Accepted for local and controlled pilot source use; not released.
**Date:** 2026-10-18

## Decision

Build deterministic portfolio calculations in the existing FastAPI modular
monolith. Store typed inputs, actor/source provenance and assessment revision;
never let LLM suggestions become verified facts. Keep appraisal separate from
the rules engine and human decision, and keep business action execution absent.

An opportunity is unranked if planning signals, current rule analysis or
independently verified evidence are missing. A versioned priority heuristic
exposes its weights and cannot override privacy/security/human-approval gates.

ROI scenario records are immutable snapshots; missing values are invalid for
the numerical simulator rather than being replaced with zero. Mixed time
periods/currencies cannot be aggregated. PoC measurements are append-only
observations with explicit KPI direction, threshold and source reference.

Tracking state is *bookkeeping*, not authorization to run a business system.
Authenticated reviewer checks and, in pilot, a completed Phase 7 human
approval chain precede the tracking transition. No AI provider calls or
external operational actions are triggered.

## Trade-offs and follow-ups

Evidence verification remains an accountable human attestation, not automated
fact-checking or cryptographic data authenticity. Only text references are
stored; production-safe attachments and DLP remain future work. The simple
payback formula is not NPV/IRR or audited ROI. Portfolio weights are starting
hypotheses pending field calibration. This iteration preserves Streamlit,
requires no hosted GitHub runner and does not relax production startup gating.
