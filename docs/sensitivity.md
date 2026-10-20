# Value sensitivity and scenario comparison (Phase 9, P9-04)

**Status:** source implementation on top of the Phase 8 value scenarios. Advisory only.
**Release state:** the Phase 6/7 production gate stays closed; nothing here changes it.

## What this is, and what it is not

A value scenario is a single point estimate. A point estimate hides exactly the
assumption risk this product exists to surface: *how wrong would we have to be about
volume, minutes or hourly cost before this stops being worth building?*

This document describes the sensitivity envelope and the side-by-side scenario
comparison that answer that question.

**It is a forecast.** A computed breakeven is the point where a *model* of labour
savings crosses zero. It is **not** verified ROI, not a validated business case, not
evidence that the assumptions hold, and not an approval to build. Every response
carries `advisory_only: true` and a disclaimer that says so in words. Nothing in the
envelope can change a rule hit, a recommendation category or a decision: the
sensitivity code is a pure function, it is never read by the rules engine, and the
comparison never mutates anything.

## Method: one-at-a-time variation with a stated band

`app/services/sensitivity.py` implements **`one_at_a_time_relative_band`**
(method version `sensitivity-v1`).

1. One assumption is varied at a time. Every other assumption stays at the value the
   analyst entered, so the effect of each assumption is isolated.
2. The varied assumptions are exactly the four declared ones: `cases_per_period`,
   `baseline_minutes`, `target_minutes`, `hourly_cost`. The cost assumptions
   (`recurring_cost`, `initial_cost`) are held fixed because they are not the
   quantities a business case is usually wrong about, and because varying them would
   change the meaning of a "per period" amount.
3. For each assumption the envelope reports:
   - the **band** that was analysed and where that band came from;
   - the **range of net savings** over that band (the projection is affine in each
     assumption, so the extremes are the band endpoints);
   - the **breakeven point** where net savings reach zero, and the direction in which
     the net turns negative;
   - the **range of the assumption that keeps net savings at or above the minimum net
     value threshold** — the headroom before the business case fails.
4. The dimensions are reported in a fixed order and every number is a `Decimal`, so
   the same inputs always produce byte-identical output.

### Where the bounds come from

This repository contains **no sourced range** for case volumes, handling times or
loaded hourly cost, and no customer evidence attached to a scenario other than a free
text `assumption_source`. Inventing market data would be dishonest, so the envelope
does not do it.

Two options are supported, and the response always says which one was used
(`band.source`):

| Source | Meaning |
| --- | --- |
| `modelling_convention_relative_band` | **Default.** A ±25 % relative band around the entered value (`0.75 ×` to `1.25 ×`). This is a *documented modelling convention*, not market data, not a distribution and not customer evidence. It exists so the product can always answer the question, and the response says so in `band.note` and in the envelope warnings. |
| `caller_supplied` | The caller passed an explicit range per assumption (typically from a cited source). The envelope records that the range was supplied, and the analyst remains responsible for its source. |

The default band is a single, stated constant (`BAND_FRACTION = Decimal("0.25")` in
`app/services/sensitivity.py`). It is deliberately *not* tuned per assumption: a
per-assumption band would need evidence this repository does not have. A ±25 % band is
exact for any four-decimal input (`0.75 ×` and `1.25 ×` of a four-decimal `Decimal`
are exact), so no band endpoint is ever rounded.

If a caller supplies a range, the endpoint requires **both** ends: one end alone is
rejected with HTTP 422, and an inverted range is rejected with HTTP 422 as well.

### Unknown stays unknown

If an assumption is missing, the affected dimension is reported as
`status: "unavailable"` with `reason: "assumption_not_provided"`. Its `base_value`,
`band`, `net_savings_range` and `breakeven` are `null`. A missing assumption is
**never** replaced by zero and **never** estimated. Because the net savings depend on
all four assumptions, a missing assumption also makes the base net savings and every
derived quantity unavailable, with the reason naming the missing assumption.

## Arithmetic: exact, and explicit about the one rounding that remains

* **Net savings** at every reported point come from the existing Phase 8 helper
  `value_projection` in `app/services/portfolio.py`. The envelope can therefore never
  disagree with the ROI simulator: the same inputs give the same number in both
  places. The service pins the decimal context to 28 significant digits (the
  process default) so the result cannot depend on an ambient context.
* **Boundaries** (breakeven and threshold) are solved in *exact rational arithmetic*
  over the same `Decimal` inputs: `Decimal.as_integer_ratio()` converts each input to
  an exact integer pair, the boundary is solved with integer arithmetic and reduced
  with `gcd`. No division and no rounding happen while solving.
  - A boundary whose exact value has a terminating decimal expansion is reported
    **exactly** (`value_is_exact: true`).
  - A boundary without one (for example `3/7`) is reported as a clearly labelled
    rendering (`value_is_exact: false`) **together with the exact integer ratio**
    (`exact_ratio: {"numerator": "3", "denominator": "7"}`). The ratio is
    authoritative; the decimal is a convenience.
* **No floating point anywhere.** No `float`, no `round()`, and every `Decimal`
  literal is built from a string. `tests/test_value_sensitivity.py` parses the new
  modules with `ast` and fails if a float literal, `float()` or `round()` appears, so
  float arithmetic cannot creep in unnoticed.
* One honest caveat: the Phase 8 projection divides by 60 before multiplying, so a
  non-terminating case can show a residue at the ~1e-24 level in a *net savings*
  figure. That is pre-existing Phase 8 behaviour, it is inherited unchanged, and the
  sensitivity service applies no additional rounding of its own. The *boundaries* are
  unaffected: they are exact by construction.

### The minimum net value threshold

`minimum_net_value_per_period` (the assessment-level threshold used by the Phase 8
`VALUE-001` rule) is applied to the envelope as the level the business case must
still clear. Its basis is always stated, because the assessment stores the threshold
as a bare per-period amount:

| Situation | Result |
| --- | --- |
| Threshold set and its period matches the scenario period | Threshold range is computed; `threshold.basis` and `threshold.currency_basis` state how it was applied. |
| Threshold set but the assessment records no comparable period | `status: "unavailable"`, `reason: "minimum_net_value_period_basis_unknown"`. |
| Threshold set for a different period | `status: "unavailable"`, `reason: "minimum_net_value_period_basis_mismatch"`. |
| No threshold set | `status: "unavailable"`, `reason: "minimum_net_value_threshold_not_set"`. |
| Threshold supplied by the caller on the request | Used, with `basis: "caller_supplied_query_parameter"` and the scenario period. |

The threshold carries **no currency** in this repository, so it is applied in the
scenario's declared currency and `threshold.currency_basis` says exactly that. If
that assumption is wrong, the threshold comparison is invalid — it is stated, not
hidden.

## API

Both routes are read-only. They are served by the existing portfolio router
(`/api/v1`, tag `portfolio-intelligence`), they resolve assessment ownership on the
server exactly like their siblings (`get_or_404`), they never write, and they
therefore produce **no audit event** — the same convention the existing
`GET /assessments/{id}/value-scenarios` route follows. No new database table, no new
column and **no Alembic migration** is required.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/v1/assessments/{id}/value-scenarios/{scenario_id}/sensitivity` | Sensitivity envelope for one scenario. |
| `GET /api/v1/assessments/{id}/value-scenarios/comparison?scenario_ids=…` | Side-by-side comparison of scenarios of the same assessment. |

### `GET …/value-scenarios/{scenario_id}/sensitivity`

Optional query parameters (all `Decimal`, parsed from the raw query string, never
through a float):

* `cases_per_period_low`, `cases_per_period_high`
* `baseline_minutes_low`, `baseline_minutes_high`
* `target_minutes_low`, `target_minutes_high`
* `hourly_cost_low`, `hourly_cost_high`
* `minimum_net_value` — overrides the assessment threshold for this request only.

The response is a `SensitivityEnvelopeView`: the base projection, the method
description, one entry per assumption (band, net savings range, breakeven, threshold
range), warnings, `advisory_only` and the disclaimer. A scenario from an older
assessment revision is returned with `stale: true` and a warning, exactly like the
Phase 8 scenario list.

### `GET …/value-scenarios/comparison`

`scenario_ids` is a repeated query parameter (1–25 ids). Every id must belong to the
assessment in the path; an id from another assessment or another organization is a
404, never a leak.

The response lists one row per scenario with gross savings, net savings, payback,
staleness and a rank by modelled net savings, plus `currency`, `period`,
`comparable: true`, `conversion_applied: false` and the disclaimer. The ranking is an
advisory ordering of modelled amounts, not a validated priority ranking.

## Mixed currency and mixed period are refused, not approximated

Comparing scenarios of **different currencies** is refused with **HTTP 400** and an
explicit message:

> Refused (mixed_currency): mixed-currency comparison is not supported (scenarios use
> EUR, NOK). No exchange rate is applied because this repository has no sourced rate
> and rate date; compare scenarios that share one currency.

Nothing is converted, approximated or silently dropped, and no scenario is quietly
preferred over another. There is no rate policy in this repository, so cross-currency
comparison is not supported at all — not approximately, and not "for the ranking
only".

Comparing scenarios of **different periods** is refused the same way (HTTP 400,
`mixed_period`). The Phase 8 projection is defined per declared period, so a monthly
amount and a yearly amount are not comparable and no annualisation is applied. This
is consistent with the rest of the Phase 8 code, which never normalises a period:
`value_projection` ignores the period entirely and the amount is "per the declared
period". Normalising periods here would have introduced a convention the rest of the
codebase does not have, and a normalised period sitting next to a non-normalised one
invites false comparability.

## What is deliberately **not** implemented

* **No cross-currency conversion, ranking or aggregation.** It needs a sourced rate
  and a rate date, and a rate policy that this repository has not decided on. Until
  then a mixed-currency request is refused.
* **No period normalisation or annualisation.** See above.
* **No probability, confidence interval, Monte Carlo or distribution.** The envelope
  is a deterministic band over entered assumptions. A probability would require
  evidence about the *shape* of the uncertainty, and this repository has none.
* **No discount rate, NPV, tax, financing or adoption modelling.** The envelope uses
  the same simple per-period labour arithmetic as the Phase 8 projection and nothing
  else.
* **No new persistence.** The envelope is computed on read; nothing is stored, so
  there is no migration to write and no stale envelope to invalidate.
* **No cross-assessment comparison endpoint.** Comparison is scoped to one
  assessment, which keeps the tenant scoping identical to the sibling portfolio
  routes and avoids inventing a portfolio-wide currency and period policy.
* **No change to the rules engine, the recommendation service or the report export.**
  The envelope is advisory output only; it is never an input to a recommendation.
* **No new UI screen.** The existing ROI scenario simulator in `ui/portfolio.py`
  gained two collapsed sections (sensitivity per scenario, and a comparison panel).
  There is no redesign and no new workflow.

## Checks

```bash
python -m pytest -q tests/test_value_sensitivity.py
python -m pytest -q
ruff check .
ruff format --check .
```

`tests/test_value_sensitivity.py` proves: the envelope agrees with `value_projection`
for the base case and at the band endpoints; the breakeven lands exactly where net
savings reach zero, including the exact integer ratio for a repeating boundary; the
threshold headroom is reported; identical inputs reproduce byte-identical output; a
missing assumption is reported as unavailable and never as zero; a mixed-currency
comparison is refused with a clear error, as is a mixed-period comparison; no
floating point is used; the routes are read-only, tenant-scoped and advisory; and a
sensitivity request cannot change a recommendation category.

These are **regression** checks about arithmetic and contracts. They are not field
accuracy, not measured ROI and not a validated business case.
