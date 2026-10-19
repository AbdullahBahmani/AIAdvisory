# ADR-0009: A strong AI candidate requires an observed language-oriented signal

**Date:** 2026-10-20
**Status:** Accepted
**Policy version:** `rules-v2` (supersedes `rules-v1`)
**Affects:** `app/services/recommendation.py`, `app/services/scoring.py`

## Context

The evaluation corpus introduced by the Phase 4 work (P4-03) surfaced a genuine engine
discrepancy in the `authoritative-lookup-only` case: a task consisting of nothing but a
system-of-record lookup was recommended as a `strong_ai_candidate`.

The reason is structural. `app/services/scoring.py` computes AI suitability by starting from a
**base of 50** and adding the weights of *observed* signals. For a plain integration with a
reliable API and verifiable output, the observed positives are `output_verifiable` (+10),
`data_available` (+10) and `api_available` (+5), giving **75** with no language signal at all.
The `strong_ai_candidate` branch in `app/services/recommendation.py` was gated on:

```python
s.data_available is True and score.value is not None and score.value >= 65 and (
    s.output_verifiable is True or s.human_review_available is True
)
```

That condition never inspected a language-oriented signal, yet the rationale it emitted claimed
*"Observed language-oriented signals and a review path support a bounded PoC."* The recommendation
was therefore derived **solely from one numeric score** — precisely what `main-planner.md` §5.2
forbids: *"Never derive solely from one numeric score."*

Why this matters beyond tidiness: `strong_ai_candidate` is the category that says *build an AI
project*. A lookup task reaching it inflates the AI opportunity pipeline with work that ordinary
integration code should do — the exact false positive the product exists to prevent, and the same
class of error §12 lists under "scores imply false certainty".

## Decision

The `strong_ai_candidate` branch must additionally require **at least one observed
language-oriented signal**:

- `unstructured_content`
- `classification_or_extraction`
- `semantic_search`
- `natural_language_generation`

These are the same signals the `hybrid` branch already used, and they correspond to the
blueprint's supported AI patterns (classification, extraction, RAG with citations). A task with no
interpretation work has nothing for an LLM to do, so it cannot be a strong AI candidate.

The change is **strictly more conservative**: it can only move a verdict *away* from "build an AI
project" and towards `more_discovery`, never the reverse. No case in the corpus moved in the
permissive direction.

## What was deliberately not changed

- **The scoring weights and the base-50 model are untouched.** The suitability score is a
  published, explainable artefact reported with per-factor contributions, and the report shows it
  to the reviewer. Changing the weights would silently change every suitability figure the
  product has ever displayed. The defect was in the *decision*, not the *measurement*.
- **The `more_discovery` fallback is unchanged**, and `more_discovery` remains the correct answer
  for a lookup task: the right follow-up is to establish whether an interpretation step exists at
  all, not to assert that one does.

## Consequences

- `POLICY_VERSION` moves from `rules-v1` to `rules-v2`. Analysis snapshots record the policy
  version that produced them, so reports remain reproducible and a verdict change is visible
  rather than silent.
- The false-positive AI-recommendation rate over the 20 non-AI corpus cases falls from **1 of 20
  (0.05)** to **0 of 20 (0.0)**. All six categories now score 100% on the corpus.
- The `authoritative-lookup-only` case's `known_discrepancy` exemption was **removed** once the
  defect was fixed, so it is strictly enforced again: a regression fails the run instead of being
  absorbed as a known divergence. The resolution is recorded in the case's `history_note`.
- `tests/test_evaluation_metrics.py` gained a direct regression test that a high suitability score
  with no language signal must not produce `strong_ai_candidate`.

## Known limits of this decision

- The gate uses the same coarse boolean signals as the rest of the engine. It cannot tell a
  genuinely useful extraction task from a trivial one; it only ensures that *some* interpretation
  work is present.
- Requiring a language signal means a task whose AI value comes from something other than
  language (for example a forecasting or optimisation use) is not classified as a strong AI
  candidate by this engine. That is a deliberate conservative bias: the engine under-claims
  rather than over-claims, and the FDE can still override with a recorded rationale.
- The corpus that detected this is synthetic. A 0.0 false-positive rate over hand-written cases is
  a **regression guard**, not evidence of real-world accuracy.

## References

- `main-planner.md` §5.2 (scoring, and the "never derive solely from one numeric score" rule) and
  §5.3 (the supported pattern catalogue).
- `main-planner.md` §12 (risk table: "scores imply false certainty").
- `docs/evaluation.md` — the resolved-discrepancy section and the false-positive metric.
- `evals/golden_cases.json` — the `authoritative-lookup-only` case and its `history_note`.
