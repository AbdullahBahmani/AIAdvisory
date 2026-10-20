# Ranking calibration harness (Phase 9, P9-05)

**Status:** harness shipped and self-tested. **No field calibration has been performed.**
`portfolio-v1` is unchanged. No `portfolio-v2` exists.

## What this is

`portfolio-v1` is a *versioned heuristic* that the product trusts but never
*measures*. P9-05 adds the missing instrument: a harness that takes a labelled
set of portfolios — each with the profile dimensions the heuristic consumes,
plus an independently supplied expected ordering — and reports:

- how well the produced priority ordering matches the expected ordering
  (Kendall's tau-b, with ties handled explicitly);
- **which** portfolios the heuristic orders incorrectly, by name and by pair;
- how many expected-ranked items the heuristic leaves **unranked**, split into
  "a policy gate forbade it" and "the heuristic abstained because an input is
  unknown";
- the count of inputs where the heuristic abstains from ranking;
- whether the Phase 8 invariant still holds.

The harness is a **measurement instrument**. It does not create truth, and it
cannot make the heuristic better. It makes the heuristic *falsifiable*.

## Run it

```bash
python -m app.portfolio_calibration
python -m app.portfolio_calibration --labelled-set path/to/real_set.json
python -m app.portfolio_calibration --json
python -m app.portfolio_calibration --compare     # every registered ranking version
```

No network, no provider, no database. It runs offline against a JSON file.

### Exit codes

| Code | Meaning |
| --- | --- |
| `0` | The run completed and the Phase 8 ranking invariant holds. |
| `1` | **The ranking invariant was violated.** A policy-blocked or discovery-only opportunity was ranked. This is a failure, not a scoring nuance. |
| `2` | **The supplied labelled set is unusable** (missing, malformed, empty, or containing no expected ordering). The run failed loudly; it was *not* treated as "nothing to calibrate". |

## The honesty boundary — read this before quoting any number

**The labelled data required to make this meaningful does not exist yet.**

Real calibration needs consented real assessments and blinded expert review.
That is planner item **P8-09**, and it is still open. The corpus bundled with
this repository (`evals/portfolio_calibration_cases.json`) is **synthetic**:
every expected ordering in it was hand-authored in this repository to exercise
the harness.

So:

- A run over the bundled corpus is a **harness self-test**. It proves the
  harness computes what it claims to compute. It proves **nothing** about the
  heuristic.
- The runner prints a prominent banner on every synthetic run, and the report
  carries `"synthetic": true` plus `"label_source"`, so a synthetic number
  cannot be silently presented as field calibration.
- The `tau_b` value over the bundled corpus is **not** an accuracy figure, not
  expert agreement, not ROI, not safety, and not business impact. It is a
  regression expectation over a hand-written fixture.
- A labelled set with `"label_source": "field"` is still only *agreement with
  the supplied ordering*. It does not establish ROI, safety or impact.

The same discipline as `docs/evaluation.md`: synthetic results may only be
described as **regression** evidence.

## Correlation method: Kendall's tau-b

The headline number is **Kendall's tau-b**, implemented directly in
`app/services/calibration.py` over pairs, with no new dependency.

### Why tau-b and not Spearman's rho

1. **Ties are common on the produced side.** `portfolio-v1` returns
   `int(40*a/5 + 35*u/5 + 25*(6-e)/5)`, an integer score. Distinct portfolios
   with different profiles routinely land on the same score. tau-b has an
   *exact* tie correction for ties in either sequence; Spearman's rho needs
   average ranks as a pre-step, and getting that tie handling wrong silently
   inflates the score.
2. **Ties are plausible on the expected side too.** A reviewer who judges two
   opportunities equally urgent says so — that is a legitimate label, and it
   must be preserved as a tie rather than broken arbitrarily.
3. **tau-b is defined over *pairs*,** which maps directly onto the requirement
   to name the portfolios the heuristic gets wrong. A discordant pair *is*
   "these two are ordered the wrong way round", so the mis-ranking report and
   the correlation are computed from the same pass and cannot disagree.
4. **It is exactly reproducible by hand.** The report prints `C`, `D`, `n0`,
   `n1` and `n2`, so any reader can recompute the number from the printed
   counts with a pocket calculator. That is the property this repo needs: an
   audit trail a reviewer can check without running code.

Spearman's rho **is** computed as a secondary diagnostic and is printed
alongside, but it is never the headline number.

### The formula, and how ties are handled

```
tau_b = (C - D) / sqrt((n0 - n1) * (n0 - n2))
```

| Term | Meaning |
| --- | --- |
| `n0` | Total unordered pairs of comparable cases, `n(n-1)/2`. |
| `C` | Concordant pairs: both orderings agree on the direction. |
| `D` | Discordant pairs: the orderings disagree on the direction. |
| `n1` | Pairs tied in the **expected** ordering (including pairs tied in both). |
| `n2` | Pairs tied in the **produced** ordering (including pairs tied in both). |

A pair tied in **both** sequences counts towards both `n1` and `n2` and
contributes to neither `C` nor `D` — it carries no directional information.
A pair tied in **only one** sequence is likewise excluded from `C`/`D` and is
reported separately as a *tie disagreement* rather than being silently
dropped.

Ties are therefore **never** broken arbitrarily and **never** silently
discarded. The report prints:

- the tie groups on each side (`expected ordering ties`, `produced ordering ties`);
- the pair counts `C`, `D`, `n1`, `n2`, `n0` so the value can be recomputed by hand;
- a *tie disagreements* section naming every one-sided tie.

### Which cases enter the correlation

Only cases that are **comparable**: the heuristic ranked them *and* the
reviewer ranked them. Cases the heuristic left unranked are counted and
reported separately (they cannot contribute an ordering), and cases the
reviewer did not rank are likewise excluded. Both directions are reported, so
a case that silently falls out of the comparison cannot hide.

## The Phase 8 policy guard

`portfolio-v1` deliberately does **not** rank policy-blocked or discovery-only
opportunities. The harness asserts this invariant and reports it explicitly.

- Every labelled case with `"policy_gated": true` is checked. If the heuristic
  produces a score for any of them, the run **fails** with exit code `1` and
  the report lists the offending case ids under `policy_guard.invariant_violations`.
  This is a hard failure, not a scoring nuance: a deterministic policy gate
  refusing to rank is a safety property, and the moment a scoring change
  starts ranking gated work, the product is quietly advising on opportunities
  the policy engine says a human must decide.
- Gated cases must also stay **visible as unranked**. The report lists them
  under `policy_guard.policy_blocked_unranked`, and they are also counted in
  `expected_ranked_left_unranked_by_policy` when the reviewer expected them
  to be ranked. "Unranked" is a visible state, never a dropped row.

The guard is enforced in the harness itself, so it fires regardless of whether
the API, the UI or the scorer changed.

### Abstentions versus policy refusals

The harness counts two different kinds of "no ranking", and never conflates
them:

- a **policy refusal** is a deliberate, deterministic "this must not be ranked
  at all" — the Phase 8 policy gate fired. It is a policy decision.
- an **abstention** is a genuine "I cannot tell" — no verified evidence, a
  stale analysis, or an unknown dimension. Unknown stays unknown; nothing is
  defaulted to zero or estimated.

The classification is per case and non-overlapping: if the deterministic policy
gate is among the reasons a case is unranked, it is counted as a policy
refusal; otherwise it is an abstention. The two counts always sum to the
total number of unranked cases. The full per-reason breakdown is printed
underneath, so a case that is *both* gated and short of evidence is visible in
the reason counts even though it is classified once.

## The report

`python -m app.portfolio_calibration` prints:

| Section | What it contains |
| --- | --- |
| Banner | The honesty warning. Prominent, on every run. |
| Header | Ranking version, dataset version, label source, description, provenance note. |
| Counts | Cases, ranked, unranked, abstentions vs policy refusals, expected-ranked-but-left-unranked (split by cause), ranked-but-not-expected-ranked. |
| Policy guard | Policy-blocked cases, how many were ranked (must be 0), how many stayed unranked, whether the invariant holds, and any violations. |
| Rank correlation | tau-b value, `n`, `C`, `D`, `n1`, `n2`, `n0`, and the secondary rho. |
| Tie groups | Every tie group on each side. |
| Mis-ranked portfolios | Each one named, with its title, expected rank, heuristic rank, score, and the specific cases it conflicts with. |
| Tie disagreements | One-sided ties, named. |

`--json` emits the same content machine-readably under the same keys.

The **ranking version string** (`portfolio-v1`) is part of every report, so a
saved report always says which version produced the ordering.

## The input format for a real labelled set

A labelled set is a single JSON document. This is the format a pilot must
supply; it is validated strictly, and anything malformed fails loudly.

```json
{
  "dataset_version": "portfolio-calibration-pilot-2026-11",
  "label_source": "field",
  "description": "Blinded expert ordering of 18 pilot opportunities.",
  "provenance_note": "Consented pilot accounts; three reviewers ordered independently; conflicts adjudicated by a fourth. Reviewers were blinded to the heuristic's output.",
  "cases": [
    {
      "id": "pilot-op-014",
      "title": "Claims triage automation",
      "strategic_alignment": 4,
      "urgency": 3,
      "delivery_effort": 2,
      "verified_sources": 2,
      "analysis_fresh": true,
      "policy_gated": false,
      "expected_rank": 4,
      "gate_kind": null,
      "note": "Ordered 4th by both reviewers independently."
    }
  ]
}
```

### Document fields

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `dataset_version` | non-empty string | yes | Version of the labelled set. Must be bumped when the labels change. |
| `label_source` | `"synthetic"` \| `"field"` | yes | `"synthetic"` triggers the self-test banner. `"field"` triggers the externally-labelled banner. |
| `description` | non-empty string | yes | What the set is. |
| `provenance_note` | string | no (but expected for `"field"`) | How the labels were produced: consent basis, reviewer count, blinding, adjudication, date. |
| `cases` | non-empty list | yes | The labelled portfolios. An **empty** list is rejected. |

### Case fields

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `id` | non-empty string, unique | yes | Stable identifier for the portfolio. |
| `title` | non-empty string | no (defaults to `id`) | Human-readable name. |
| `strategic_alignment` | integer `1..5` or `null` | yes (may be `null`) | The reviewer's alignment score. `null` means unknown — the heuristic then abstains. |
| `urgency` | integer `1..5` or `null` | yes (may be `null`) | Same, for urgency. |
| `delivery_effort` | integer `1..5` or `null` | yes (may be `null`) | Same, for delivery effort. |
| `verified_sources` | integer `>= 0` | yes | Count of independently verified evidence records at the current revision. |
| `analysis_fresh` | boolean | yes | Whether the rules-engine analysis is for the current revision. |
| `policy_gated` | boolean | yes | Whether a deterministic policy gate blocks this opportunity. Must be `true` for any blocked or discovery-only case. |
| `expected_rank` | integer `>= 1` or `null` | yes (may be `null`) | The reviewer's position, `1` = highest priority. **Ties are allowed**: two cases with the same `expected_rank` are preserved as a tie, not broken. `null` means the reviewer did not place the case. |
| `gate_kind` | string or `null` | no | Free-text kind of the gate, e.g. `"policy_blocked"` or `"discovery_only"`. Reporting metadata only; both kinds must stay unranked. Requires `policy_gated: true`. |
| `note` | string | no | Per-case provenance for the label. |

### Rules the validator enforces

A labelled set that breaks any of these is **rejected** with a specific error
and the run exits `2`:

- the document is not a JSON object, or is missing a required top-level field;
- `label_source` is neither `"synthetic"` nor `"field"`;
- `dataset_version` or `description` is empty;
- `cases` is missing, is not a list, or is **empty**;
- a case has no `id`, or a duplicate `id`;
- a dimension is outside `1..5`;
- `verified_sources` is negative;
- `analysis_fresh` or `policy_gated` is not a boolean;
- `expected_rank` is present but `< 1`;
- `gate_kind` is set while `policy_gated` is `false`;
- **no** case carries an `expected_rank` — there is no expected ordering, so
  there is nothing to calibrate.

## What the current self-test run shows

Running the harness over the bundled synthetic corpus produces, on this
branch:

```
ranking version:   portfolio-v1
label source:      synthetic (SYNTHETIC SELF-TEST)

cases in labelled set:            14
ranked by heuristic:               9
unranked by heuristic:             5
heuristic abstentions:             3
deterministic policy refusals:     2
expected-ranked items:             14
expected-ranked but LEFT UNRANKED: 5 (opp-10, opp-11, opp-12, opp-13, opp-14)
  - because a policy gate forbids ranking (correct per Phase 8): 2 (opp-10, opp-11)
  - because the heuristic abstained (inputs unknown):              3 (opp-12, opp-13, opp-14)
ranked but NOT expected-ranked:   0

policy-blocked cases:              2
policy-blocked ranked (must be 0): 0
policy-blocked unranked:           2
invariant holds:                   True

RANK CORRELATION (kendall_tau_b)
  value:                             0.6857
  comparable cases:                  9
  concordant pairs:                  29
  discordant pairs:                  5
  pairs tied in expected ordering:   1
  pairs tied in produced ordering:   1
  pairs tied in both:                0
  note:                              tie-corrected tau-b; ... n0=36, n1=1, n2=1
  secondary check (spearman_rho): 0.7395
```

**Read that `0.6857` correctly.** It is a property of a corpus whose expected
ordering was written by the same people who wrote the heuristic. It is not
evidence that the heuristic is right, and it is not evidence that it is wrong.
Its only evidential value is that the harness reproduces the arithmetic it
claims to.

## Was `portfolio-v2` shipped? No — and why

**No. `portfolio-v1` is unchanged and no `portfolio-v2` exists.**

The rule from `docs/phase9.md` is: ship a new ranking version *only if
calibration shows a materially better ordering*, and record the comparison in
an ADR. That condition is not met, and the reason is not that the
disagreement is small — it is that **the disagreement is with a synthetic
ordering**.

The harness does surface a real disagreement: `opp-04` ("Strategic heavy
lift") is expected 4th and produced 9th, and it accounts for all five
discordant pairs. A reader could reasonably think "the heuristic underweights
strategic alignment, ship a v2 that fixes it". That inference would be
invalid, for three reasons:

1. **The label is mine.** I wrote `opp-04`'s expected rank. Re-weighting the
   heuristic to match a label I authored is fitting to my own fixture, not
   calibrating against expert judgement. It would raise the fixture's
   correlation to `1.0` and mean nothing whatsoever.
2. **There is no sampling frame.** The corpus is 14 hand-written cases. It
   cannot estimate how often the heuristic is wrong on real portfolios, and it
   certainly cannot estimate the effect of changing the weights.
3. **The one case that "disagrees" encodes a policy choice, not a bug.** That
   a reviewer might rank a high-alignment, high-effort opportunity above a
   lower-alignment one is exactly the kind of disagreement the weights encode
   as 40/35/25. Whether that trade-off is right is a **business decision**,
   and it needs real reviewers, not a fixture.

Fitting a new version to this corpus would be the exact failure mode this
project's own documentation warns about (`docs/evaluation.md`: *"a bigger
corpus is not a validated dataset"*; *"do not invent these measurements"*).
Shipping `portfolio-v2` here would also mean shipping a version string whose
justification is a number produced over synthetic labels — precisely the
thing that must never happen.

**What would justify a v2:** a labelled set with `"label_source": "field"`,
built from consented real assessments and blinded expert review (P8-09), on
which a candidate ordering shows a *materially* better rank correlation than
`portfolio-v1` **and** the improvement is not concentrated in a single case
whose expected position is a judgement call. At that point the procedure is:

1. Register the candidate in `RANKING_VERSIONS` under a new version string.
2. Run `python -m app.portfolio_calibration --labelled-set <real set> --compare`
   to obtain the side-by-side regression comparison against `portfolio-v1`.
3. Write the ADR recording both versions, the labelled set's provenance, the
   before/after correlation, and the guard results.
4. Only then change the version string the API reports.

`--compare` and the version registry exist now so that path is a matter of
supplying evidence, not of building tooling.

## Files

| File | Role |
| --- | --- |
| `app/services/calibration.py` | The harness: correlation, tie handling, loading/validation, calibration, policy guard, version registry. |
| `app/portfolio_calibration.py` | The CLI runner (`python -m app.portfolio_calibration`). |
| `evals/portfolio_calibration_cases.json` | The **synthetic** self-test corpus. |
| `tests/test_ranking_calibration.py` | Tests: hand-checked correlation, named mis-rankings, the policy guard, loud failure on bad input, and no `portfolio-v1` regression. |

`app/services/portfolio.py` is **not** modified by any of this. The harness
calls the shipped `priority_result` directly, so it measures the real
behaviour and cannot drift away from it. A test asserts that the 40/35/25
weights and the abstain/status behaviour are unchanged.

## Related

- `docs/phase8.md` — the `portfolio-v1` heuristic and the Phase 8 invariants.
- `docs/phase9.md` — P9-05 in the phase plan.
- `docs/evaluation.md` — how this repo talks about synthetic-vs-real evidence.
- `docs/decisions/` — ADR index; a version bump requires a new ADR here.
