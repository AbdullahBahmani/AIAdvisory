# ADR-0012: Value and score thresholds stay provisional and unconfigured

**Date:** 2026-10-20
**Status:** Accepted
**Answers:** `main-planner.md` §13 open decision "value/score thresholds after reviewing sample
assessments".

## Context

The blueprint's scoring weights and policy thresholds are, in its own words, *initial
hypotheses* — to be versioned and tested with representative assessments before treating any
recommendation as reliable.

The implementation followed that instruction: the weights live in a single versioned constant
(`WEIGHTS` in `app/services/scoring.py`), the policy carries a version string (`rules-v2`), and
the thresholds are visible in one place (`app/services/recommendation.py`). That structure was
deliberate — it makes a future recalibration a reviewed change rather than a scattered edit.

The temptation now is to tune the weights so the outputs "look right" on the evaluation corpus.
That would be a serious error. The corpus is 31 hand-written synthetic cases; tuning a policy
to fit them measures the policy against the author's assumptions and nothing else. The planner
already records the correct sequencing: field-quality calibration is P8-09, which needs
consented, privacy-approved, representative assessments and blinded expert outcomes.

There is also a structural reason not to expose thresholds as configuration: a threshold that an
operator can tune without evidence is a threshold that can be tuned to make a weak opportunity
look strong. `VALUE-001` and the minimum-net-value concept are safety-relevant, not cosmetic.

## Decision

**Thresholds and weights remain fixed, versioned constants. They are not operator-configurable,
and they are not tuned against the synthetic corpus.**

1. The AI-suitability weights and the `>= 65` strong-AI threshold stay as published constants in
   `app/services/scoring.py` and `app/services/recommendation.py`.
2. The `minimum_net_value_per_period` threshold remains a **per-assessment input** — it is a
   business decision made by the organization for that assessment, recorded with its
   assumption source — not a global tunable. This distinction is preserved deliberately: a
   threshold the organization states and sources is evidence; a global constant an operator can
   nudge is not.
3. The suitability score remains advisory. It never overrides a hard gate, and the
   `strong_ai_candidate` category additionally requires an observed language-oriented signal
   (ADR-0009) precisely so that a verdict is never derived from one number.
4. Any recalibration is a **policy version bump** with an ADR recording the old and new values
   and the evidence for the change — the same discipline applied when moving to `rules-v2`.

## Conditions to revise this decision

Recalibration is justified when, and only when, P8-09 supplies:

- privacy-approved, representative field assessments from real work;
- blinded expert outcomes for those assessments;
- an agreed metric for what "correct" means (expert agreement, outcome improvement, or a
  combination);
- enough cases to distinguish a real signal from noise, and an analysis of the false-positive
  cost of any proposed change.

The synthetic corpus may be used to check that a recalibration does not regress known
behaviour. It may not be used to justify one.

## Consequences

- Recommendations remain explainable and reproducible: the same inputs and the same policy
  version always produce the same verdict, and the factor breakdown is meaningful because the
  weights are stable.
- The product may occasionally be conservative — recommending `more_discovery` where a
  practitioner would have been confident. That bias is intentional and is the safer direction.
- Anyone tempted to tune the thresholds is directed to P8-09 and to the ADR process, rather than
  to a config file.

## References

- `main-planner.md` §0 (weights are initial hypotheses), §5.2 (scoring and explainability),
  §13 (open decisions).
- `app/services/scoring.py`, `app/services/recommendation.py`, `app/domain/portfolio.py`.
- ADR-0009 (language-signal gate), `docs/evaluation.md`.
