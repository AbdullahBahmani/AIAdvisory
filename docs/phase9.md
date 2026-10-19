# Phase 9 — decision attribution, calibration and value maturity

**Status:** proposed (2026-10-19). Not started. No task in this phase may be marked complete
without code, tests, and recorded evidence.

## Why this phase exists

Phases 0–8 delivered a complete, rule-enforced advisory journey with an evidence registry,
exact value scenarios and a versioned portfolio ranking heuristic. The remaining weakness is
not missing features — it is **attribution and calibration**:

- A final FDE decision in **local demo mode** is bound to a *shared* reviewer secret, not to a
  named, organization-scoped human. The reviewer's own name (`reviewer_id`) is self-asserted
  free text in the request body, so the audit trail records an actor the server never verified.
- The evaluation suite is **4 synthetic golden cases**. `docs/evaluation.md` explicitly names
  the uncovered cases: contradictions, absent API permissions, high consequence without a
  reviewer, low value, and fully human-only activities. There is no confusion matrix and no
  false-positive AI-recommendation rate, which `main-planner.md` §12 names as the mitigation
  for the "scores imply false certainty" risk.
- Value scenarios are exact `Decimal` projections for a single currency and a single period.
  There is **no sensitivity analysis** over the assumptions, and therefore no honest answer to
  "how wrong would we have to be about volume, minutes or hourly cost before this stops being
  worth building?"

Phase 9 closes those three gaps. It deliberately does **not** open new product surface.

## Non-negotiables (unchanged from Phases 1–8)

- **AI assists. Rules enforce. Data informs. Humans decide.**
- Deterministic policy gates still take precedence over any score, model output or ranking.
- No claim of measured ROI, field accuracy or model quality may be made from synthetic fixtures.
  Synthetic results may only be described as *regression* evidence.
- The production startup guard (`ADVISORY_DEPLOYMENT_MODE=production`) stays closed. This phase
  does not change that and no source commit can satisfy the Phase 6/7 external gates.
- Unknown stays unknown. A missing baseline is never silently replaced by zero or an estimate.

## Scope

### P9-01 — Verified reviewer attribution for local and pilot modes

The demo/pilot path currently authenticates with one shared secret
(`ADVISORY_REVIEW_TOKEN`), so every demo reviewer is the same principal (`local_demo`) and the
`reviewer_id` in a review payload is unverified self-asserted text.

Deliver a **named, locally provisioned principal** concept that works without an external IdP:

- A local membership record binding `subject` → `organization_id` → `role`
  (`analyst` / `reviewer` / `admin`), mirroring the existing `OidcMembershipRow` shape.
- Local mode then resolves a distinct principal per reviewer, so:
  - the actor written to `audit_events` is server-verified, not client-supplied;
  - `identity_verified` reflects real verification rather than a mode flag;
  - `require_assigned_reviewer` binds to a specific human in pilot mode too;
  - the three-stage approval chain (Phase 7) can require **distinct** actors.
- The shared token may remain only as an explicitly-labelled synthetic-demo fallback, and every
  record it produces must continue to be marked `identity_unverified`.

Acceptance: a test proves two different named local reviewers produce different, correct audit
actors, that a reviewer cannot record a review under another reviewer's name, and that
cross-organization access is rejected.

### P9-02 — Decision provenance ledger

Every final decision should answer, without guesswork: *which* policy version, *which* analysis
revision, *which* evidence records and *which* value scenario produced it.

- Persist a provenance snapshot on finalization: policy version, scoring-weight version,
  analysis revision, contributing evidence ids, contributing scenario id, prompt/schema versions
  if AI was used, and the reviewer identity.
- Expose it in the exported report and in a `GET .../provenance` endpoint.
- Detect drift: if evidence or the scenario changes after finalization, the provenance record
  must show the decision was taken against a now-stale basis.

Acceptance: a report export reproduces the exact provenance, and a stale-basis decision is
visibly labelled rather than presented as current.

### P9-03 — Evaluation suite expansion and outcome metrics

Replace the 4-case suite with a **curated, versioned corpus** covering every branch the policy
engine actually has, and report metrics instead of a single pass/fail.

Coverage to add (from `docs/evaluation.md` plus the rule catalogue in `main-planner.md` §5.1):
contradictory answers; absent API permission; high-consequence action with no reviewer;
value below the evidenced threshold; fully human-only activity; missing-baseline value;
privacy-blocked material; exact-arithmetic tasks; zero-error-tolerance tasks; irreversible
actions; agent-vs-workflow cases; and stale/insufficient-evidence cases.

The runner must additionally report:
- per-category accuracy and a confusion matrix over the six recommendation categories;
- the **false-positive AI-recommendation rate** (cases that must not be AI-recommended but are);
- the **unknown-data handling rate** (cases where ROI/feasibility must stay unavailable);
- the hard-rule firing rate per rule id, so a rule that silently stops firing is visible;
- advisory-only compliance (every case advisory, no approval side effects).

Acceptance: `python -m app.evaluation` exits non-zero on any regression, prints the metrics, and
the dataset carries a `dataset_version` plus a per-case provenance note. The runner must
continue to state plainly that these are regression expectations and **not** field accuracy.

### P9-04 — Value sensitivity and scenario comparison

A single point estimate hides the assumption risk the product exists to surface.

- Given a `ValueScenarioIn`, compute a sensitivity envelope over the declared assumptions
  (cases per period, baseline minutes, target minutes, hourly cost) with an explicit,
  documented method — exact `Decimal` arithmetic, no floating point, no silent rounding.
- Report the breakeven point at which net savings turn negative, and the assumption range for
  which the scenario remains above the minimum net value threshold.
- Support comparing multiple scenarios of the **same currency and period** side by side.
- Do **not** convert between currencies without an explicitly sourced rate and an explicit
  rate date. Mixed-currency comparison must be refused, not approximated.

Acceptance: tests prove exactness at the boundary, that an unchanged assumption set reproduces
byte-identical output, and that mixed-currency comparison is refused with a clear error.

### P9-05 — Ranking calibration harness

`portfolio-v1` is a versioned heuristic. It must be *measured*, not trusted.

- A calibration runner that takes a labelled set of portfolios and reports how well the
  priority ordering matches an independently supplied expected ordering (e.g. rank
  correlation), plus the cases the heuristic gets wrong.
- Explicitly guard the Phase 8 rule that policy-blocked and discovery-only proposals must not
  be ranked, and that unranked cases stay visible as unranked.
- Ship `portfolio-v2` only if calibration shows a materially better ordering, and record the
  comparison as an ADR. Never change ranking weights silently.

Acceptance: the runner reports a calibration summary; a version bump requires an ADR and a
regression comparison against `portfolio-v1`.

## Out of scope for Phase 9

- Enterprise SSO/IdP integration, SCIM, and MFA (external dependency — Phase 7 P7-09).
- Metrics ingestion from production systems and evidence file attachments (P8-10) — these need
  a storage, DLP and residency decision that has not been made; they remain blocked on it.
- Cross-currency conversion (needs a sourced rate policy).
- Any production release authorisation (Phase 6 P6-09 / Phase 5 P5-07).
- Model-quality evaluation — that requires consented real data and expert review (P8-09).

## Dependencies and sequencing

| Task | Depends on | Blocks |
| --- | --- | --- |
| P9-01 | Phase 7 approval chain (done) | P9-02 (provenance needs a verified actor) |
| P9-02 | P9-01 | Report fidelity claims |
| P9-03 | none (read-only on the engine) | Nothing; can start immediately |
| P9-04 | Phase 8 value scenarios (done) | P9-05 calibration |
| P9-05 | P8-09 labelled portfolios | Any ranking change |

P9-03 and P9-04 can proceed in parallel with P9-01. P9-05 remains blocked on collecting
labelled field data (P8-09) and must not be closed with synthetic data presented as calibration.

## Relationship to the planner

This phase is additive to `main-planner.md`. The planner's Phase 8 item **P8-10** anticipates
"enterprise-safe evidence attachments, metrics ingestion, comprehensive sensitivity/NPV,
approved compliance controls and cross-currency logic only if explicitly required and correctly
sourced". Phase 9 covers the **sensitivity** part of P8-10 and the attribution/calibration
gaps; attachments, metrics ingestion and cross-currency stay with P8-10 because they require
decisions not yet taken.

Phase 9 does **not** close any Phase 5, 6 or 7 gate. Those remain external, and the production
guard remains closed.
