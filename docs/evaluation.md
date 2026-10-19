# Offline evaluation, reproducibility, and limitations

**As of 2026-10-19:** This dataset is a synthetic golden-expectation regression suite, **not**
evidence of field accuracy, model quality, safety, ROI, or reliability. The corpus was expanded
from 4 to 31 hand-authored cases, and the runner now reports outcome metrics. A larger corpus
means **more regression coverage, not more truth**. Read [Interpretation limits](#interpretation-limits)
before quoting any number from this page.

## Run without a hosted runner or AI provider

```bash
python -m pytest -q
python -m app.evaluation
python -m app.evaluation --json      # full machine-readable metrics
python -m scripts.demo --output demo_out
```

`python -m app.evaluation` reads `evals/golden_cases.json`, evaluates every task with the real
deterministic policy engine, prints the outcome metrics, and **exits non-zero on any unexpected
regression**. The end-to-end demo (create → analyze → review → export) is documented separately in
[docs/demo.md](demo.md); it drives the `ai-support`, `exact-pricing` and `hybrid-refund` scenarios
that also appear in this corpus, so the demo and the regression suite can be read side by side.

The runner is also a library:

```python
from app.evaluation import evaluate_corpus, evaluate_dataset

result = evaluate_corpus()  # every case in evals/golden_cases.json
legacy = evaluate_dataset()  # scope="core": the four original contrastive fixtures
```

`scope="core"` exists only to keep the historical four-fixture contract that the existing
reporting regression test (`tests/test_reporting.py`) asserts. The CLI and `evaluate_corpus()`
evaluate the **full** corpus; nothing is hidden by default in the CLI output.

## The corpus

`evals/golden_cases.json` (dataset version `5.0`) holds 31 synthetic cases. Every case is
invented for this repository: no customer data, no production logs, no external benchmark, no
model outputs. Every case carries a `provenance` note, the `invariant` it is meant to protect,
its expected category, the rule ids it expects to fire, and whether ROI must stay unavailable.

| Case | Scenario | Expected category | Rules expected | Key invariant |
| --- | --- | --- | --- | --- |
| `ai-support` | Knowledge-base support triage | `strong_ai_candidate` | — | Verifiable triage stays a bounded AI PoC |
| `exact-pricing` | Exact pricing calculation | `traditional_software` | — | Exact arithmetic stays deterministic |
| `hybrid-refund` | Refund email workflow | `ai_human_approval` | ACT-002 | Irreversible consequential action keeps a human approval gate |
| `privacy-blocked` | Sensitive data without provider permission | `more_discovery` | PRIV-001 | PRIV-001 blocks external provider processing |
| `employment-screening` | Shortlist screening for hiring panel | `human_led` | HUM-001 | HUM-001: the final hiring decision stays with a human |
| `medical-triage-draft` | Clinical intake note drafting | `human_led` | HUM-001 | HUM-001: the clinical decision stays with a human |
| `legal-commitment-review` | Contract renewal options summary | `human_led` | HUM-001 | HUM-001: the binding commitment stays with counsel |
| `zero-error-payout` | Bonus payout eligibility calculation | `human_led` | SAFE-001 | SAFE-001: no automation without verification or a reviewer |
| `high-consequence-no-reviewer` | Escalate disputed account closure | `human_led` | — | High consequence without any reviewer is human-led |
| `critical-risk-no-reviewer` | Fraud alert disposition | `human_led` | — | A critical risk score without a reviewer is human-led |
| `value-below-threshold` | Low-value document tagging pilot | `human_led` | VALUE-001 | VALUE-001: below-threshold evidenced value is human-led |
| `safe-rule-with-reviewer` | Statutory filing answer drafting | `ai_human_approval` | SAFE-001 | SAFE-001 with a reviewer is approval-gated AI |
| `approval-required-no-approver` | Cancel an order on a disputed invoice | `more_discovery` | ACT-002 | ACT-002 without a confirmed approver needs discovery |
| `approval-required-with-approver` | Update a customer address after a call | `ai_human_approval` | ACT-002 | ACT-002 with an approver is approval-gated AI |
| `irreversible-data-deletion` | Delete customer data on a retention request | `ai_human_approval` | ACT-001, ACT-002 | ACT-001/ACT-002: irreversible action needs approval |
| `contract-redline-draft` | Draft a contract redline proposal | `ai_human_approval` | — | High-consequence drafting stays approval-gated |
| `data-permission-missing` | Score inbound job applications | `more_discovery` | PRIV-002 | PRIV-002: no data permission blocks processing |
| `sensitive-data-provider-unknown` | Summarise a confidential case note | `more_discovery` | PRIV-001 | PRIV-001: unconfirmed provider permission blocks |
| `reviewer-availability-unknown` | Approve a large credit exception | `more_discovery` | EVID-001 | EVID-001: unknown reviewer availability blocks |
| `insufficient-evidence-unknown-inputs` | Unspecified internal workflow step | `more_discovery` | EVID-002 | EVID-002: unknown inputs need discovery |
| `no-reliable-data` | Classify legacy scanned invoices | `more_discovery` | EVID-003 | EVID-003: unreliable data needs discovery |
| `contradictory-data-claims` | Triage records with conflicting data statements | `more_discovery` | EVID-003 | Contradictory data claims need discovery first |
| `hybrid-authoritative-lookup` | Draft a policy answer with system-of-record facts | `hybrid` | DATA-001 | Language plus authoritative facts is hybrid |
| `hybrid-contract-review` | Compare a signed contract against the standard template | `hybrid` | DATA-001 | Clause extraction with retrieval is hybrid |
| `authoritative-lookup-only` | Look up the current account tier | `more_discovery` | DATA-001 | A pure lookup is not an AI candidate — was a known discrepancy, **fixed in `rules-v2`**, see below |
| `strong-ai-semantic-search` | Answer agent questions from the knowledge base | `strong_ai_candidate` | — | Semantic retrieval with verification is a PoC |
| `strong-ai-email-triage` | Route inbound supplier email | `strong_ai_candidate` | — | High-volume triage with verification is a PoC |
| `missing-baseline-roi` | Inbound request triage | `strong_ai_candidate` | — | Bounded AI case, but ROI must stay unavailable |
| `ai-lookalike-no-benefit` | Ad-hoc tagging with no measurable outcome | `more_discovery` | — | AI-shaped signals with no verifiable benefit |
| `deterministic-schedule` | Build the weekly shift roster | `traditional_software` | DET-001 | DET-001: roster rules are deterministic software |
| `payroll-calculation` | Calculate monthly payroll amounts | `traditional_software` | DET-001, MATH-001 | DET-001/MATH-001: payroll arithmetic is deterministic |

### Category coverage

| Category | Cases | Meaning in the policy |
| --- | --- | --- |
| `traditional_software` | 3 | The work is expressible as rules and typed code; no LLM is needed. |
| `strong_ai_candidate` | 4 | A bounded, verifiable AI PoC is proposed (with an explicit no-ROI variant). |
| `hybrid` | 2 | Language interpretation plus deterministic retrieval or policy gates. |
| `ai_human_approval` | 5 | AI may draft, but every suggestion needs separate human authorisation. |
| `human_led` | 7 | The decision itself stays with an accountable human. |
| `more_discovery` | 10 | Evidence, permission, value, a reviewer, or a benefit is missing. |

Every policy branch in `app/services/recommendation.py` and every rule id in
`app/services/rule_engine.py` is exercised at least once. The corpus is deliberately
**contrastive and adversarial**: it contains eight hand-picked "false-positive guard" cases
(`employment-screening`, `medical-triage-draft`, `legal-commitment-review`, `zero-error-payout`,
`high-consequence-no-reviewer`, `critical-risk-no-reviewer`, `value-below-threshold`,
`ai-lookalike-no-benefit`) that look strongly AI-shaped on the signals and must still not be
recommended as an AI project.

## Metrics reported by the runner

`python -m app.evaluation` (or `evaluate_corpus()`) returns, in addition to pass/fail per case:

| Metric | What it measures | Current value on the shipped corpus |
| --- | --- | --- |
| `per_category_accuracy` | Correctly classified cases per expected category | 3/3, 4/4, 2/2, 5/5, 7/7, 9/10 |
| `confusion_matrix` | Expected × actual counts over all six categories | one off-diagonal entry (see below) |
| `false_positive_ai_recommendation` | Cases that must **not** be an AI category but were classified as one | 1 of 20 non-AI cases (rate 0.05) |
| `false_positive_ai_recommendation.unexpected_false_positives` | Offenders that are *not* documented known discrepancies | 0 (any new offender fails the run) |
| `unknown_data_handling` | Cases where ROI/feasibility must stay unavailable and did | 31 of 31 (rate 1.0); ROI 30/30, feasibility 23/23 |
| `rule_firing_rates` | How often each rule id fired across the corpus | all 13 rule ids fire; a rule that stops firing shows 0.0 |
| `expected_rule_checks` | Whether the rule ids a case expects actually fired | 22 of 22 satisfied |
| `advisory_only` | Advisory flag, absence of approval side effects, purity of the analysis path | 31 of 31 compliant; analysis path pure |

Additional per-case facts are exported in the JSON: the rule ids that fired, the ROI state
(`absent` / `insufficient_evidence` / `illustrative_only`), whether the value baseline was
evidenced, and which checks passed.

### The false-positive metric is a regression guard, not a safety assurance

The false-positive rate is computed over 31 **hand-written** cases that were built to be
adversarial. It is a tripwire for the policy engine drifting (a case that used to land in
`human_led` now landing in `strong_ai_candidate` fails the run). It is **not** a probability of
harm, **not** a precision estimate, and **not** evidence that the system is safe for any real
workload. Real precision requires a consented, representative, human-labelled test set — see
[Future model evaluation gate](#future-model-evaluation-gate).

### Unknown-data handling

`unknown_data_handling` is the "never fabricate ROI" property: a case whose scenario lacks a
complete observed baseline (`cases_per_period`, `baseline_minutes_per_case`,
`target_minutes_per_case`, `loaded_hourly_cost`) must keep `potential_net_savings` unavailable, and
a dimension score whose inputs are all unknown must stay `None` rather than becoming a number.
`missing-baseline-roi` is the positive control: the automation is a legitimate AI candidate, yet
the ROI stays unavailable because the baseline was never observed. `value-below-threshold` is the
other direction: with a complete baseline the ROI *is* computed, and the case still ends
`human_led` because the evidenced value is under the threshold.

## Resolved discrepancy — `strong_ai_candidate` now requires a language signal

`authoritative-lookup-only` expects `more_discovery`; before policy `rules-v2` the engine
returned `strong_ai_candidate`.

* **Scenario:** a pure system-of-record lookup with no interpretation work at all.
* **Why the expectation is `more_discovery`:** the `strong_ai_candidate` branch's own rationale
  says "observed language-oriented signals", but the branch was actually reached by an aggregate
  suitability score ≥ 65 alone. Data, API availability and verifiability alone score 75 (the score
  starts from a base of 50 and adds observed signals), so a task with no language signal at all
  was classified as a strong AI candidate. For a task with no interpretation work, the honest
  category is `more_discovery` (or a deterministic integration), not an AI project. This is
  exactly the failure mode `main-planner.md` §5.2 forbids: a recommendation must never be
  derived **solely** from one numeric score.
* **Fix (policy `rules-v2`):** the `strong_ai_candidate` branch now additionally requires at
  least one observed language-oriented signal — `unstructured_content`,
  `classification_or_extraction`, `semantic_search` or `natural_language_generation` — on top of
  the score threshold and the review path. The `hybrid` branch already required such a signal.
  The change is strictly more conservative: it can only move a verdict *away* from "build an AI
  project", never towards it.
* **Effect on the corpus:** the case now passes on its own expectation, the exemption was removed
  from `evals/golden_cases.json` (the resolution is recorded in the case's `history_note`), and
  the false-positive rate over the 20 non-AI cases fell from **1 of 20 (0.05) to 0 of 20 (0.0)**.
  Every category now scores 100% on the corpus. Because the exemption is gone, the case is
  strictly enforced again: a regression would fail the run rather than be absorbed as a known
  discrepancy.
* **What was deliberately NOT changed:** the scoring weights and the base-50 scoring model are
  untouched, because the score itself is a published, explainable artefact with per-factor
  contributions and changing it would alter every reported suitability figure. The fix is in the
  *decision*, not the *measurement*.

## Scope of this evaluation

These checks are **golden policy expectations**, not a representative sample and not empirical
model accuracy. A result of `31/31` means only that the curated
fixtures still match the expected deterministic categories on this software version. There are no
real customer datasets, live model calls, latency measurements, user interviews, business
outcomes, or rollout authorization. The cases cover the gaps that were previously named —
contradictions, absent API/data permissions, high consequence without a reviewer, low value, and
fully human-only activities — but covering a branch in a synthetic fixture is not the same as
validating that branch against reality.

## Future model evaluation gate

A pilot should add a consented, diverse and documented test set with ground truth, expert review,
acceptance thresholds, and systematic measurement of output correctness, extraction quality,
precision/recall, latency, cost, provenance, data exposure, FDE override rates, and regression
deltas across model/prompt changes. Do **not** invent these measurements or infer them from this
corpus. Separate model output quality from deterministic policy compliance.

## Interpretation limits

Read this before quoting any number above.

* **These are synthetic regression expectations.** Every case was written by hand to describe a
  policy outcome. The corpus was built by the same people who wrote the policy, so it encodes
  *intent*, not *observed behaviour*.
* **Category accuracy here is not field accuracy.** `human_led: 7/7` means "the seven
  hand-written human-led expectations still hold". It is not a statement about how often the
  product would be right on a real workflow.
* **The false-positive rate is not a safety metric in the risk sense.** It is computed on a
  deliberately adversarial, hand-picked corpus with no sampling frame, so it has no population
  interpretation. A rate of 0.05 over 20 non-AI cases says nothing about the real probability of
  proposing an inappropriate AI project. Treat it as a guard rail that fires when the policy
  engine changes.
* **No model quality is measured.** The engine is deterministic and rules-only. Nothing here
  evaluates output correctness, extraction quality, hallucination rate, latency or cost of any
  model.
* **No ROI is claimed.** Every number the engine can produce is an illustrative labour model over
  an observed baseline. `missing-baseline-roi` exists precisely to prove the engine refuses to
  state a value it has not observed.
* **Advisory-only compliance is a structural property, not a workflow guarantee.** It shows the
  analysis path has no database or provider reachability and no approval fields. It does not show
  that a human will actually review anything.
* **A bigger corpus is not a validated dataset.** 31 cases give better regression coverage of the
  policy branches than 4 did. They do not create evidence, and they must not be used to claim
  that the product is ready for real decisions, customers or rollout.

## Report snapshot reproducibility

`GET /api/v1/assessments/{id}/report?format=markdown|html` exports the **current deterministic
analysis revision**. If inputs change, the stored analysis is invalidated and the endpoint returns
HTTP 409 until rescore. Both formats use the same internal section builder, include labels for
assumptions and unknowns, identify unverified/stale AI proposals, and reproduce from unchanged
saved assessment contents. No wall-clock report-generation timestamp is injected.

The report API is unauthenticated in the **local demo**. Do not deploy it publicly or put sensitive
data in the database. PDF export is explicitly deferred; HTML can be printed locally to PDF by the
operator, but that is not a validated production PDF pipeline.
