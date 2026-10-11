# Offline evaluation, reproducibility, and limitations

**As of 2026-10-12:** This dataset is an intentionally small synthetic regression suite, **not** evidence of field accuracy, performance, safety, ROI, or model reliability.

## Run without a hosted runner or AI provider

```bash
python -m pytest -q
python -m app.evaluation
python -m scripts.demo --output demo_out
```

`python -m app.evaluation` reads `evals/golden_cases.json` and returns nonzero if a golden invariant fails. It evaluates each task with the real deterministic policy engine and reports category correctness, required controls, named rules, and whether unavailable ROI stays unavailable. Every case is advisory-only.

| Fixture | Scenario | Expected category | Key invariant |
| --- | --- | --- | --- |
| ai-support | Unstructured support ticket routing | `strong_ai_candidate` | AI may help if outputs can be verified |
| exact-pricing | Known rate/tax formula | `traditional_software` | Numeric truth remains deterministic |
| hybrid-refund | Consequential refund action | `ai_human_approval` | Required human approval and risk controls |
| privacy-blocked | Sensitive customer material without provider permission | `more_discovery` | `PRIV-001` prevents external provider processing |

### Scope of this evaluation

These checks are **golden policy expectations**, not a representative sample and not empirical model accuracy. The result `4/4` means only that the curated fixtures match expected deterministic categories on this software version. There are no real customer datasets, live model calls, latency measurements, user interviews, business outcomes, or rollout authorization. New cases should cover contradictions, absent API permissions, high consequence without reviewer, low value, and fully human-only activities.

### Future model evaluation gate

A pilot should add a consented, diverse and documented test set with ground truth, expert review, acceptance thresholds, and systematic measurement of output correctness, extraction quality, precision/recall, latency, cost, provenance, data exposure, FDE override rates, and regression deltas across model/prompt changes. Do **not** invent these measurements or infer them from the four fixtures. Separate model output quality from deterministic policy compliance.

## Report snapshot reproducibility

`GET /api/v1/assessments/{id}/report?format=markdown|html` exports the **current deterministic analysis revision**. If inputs change, the stored analysis is invalidated and the endpoint returns HTTP 409 until rescore. Both formats use the same internal section builder, include labels for assumptions and unknowns, identify unverified/stale AI proposals, and reproduce from unchanged saved assessment contents. No wall-clock report-generation timestamp is injected.

The report API is unauthenticated in the **local demo**. Do not deploy it publicly or put sensitive data in the database. PDF export is explicitly deferred; HTML can be printed locally to PDF by the operator, but doing so is not a validated production PDF pipeline.
