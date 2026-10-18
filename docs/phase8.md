# Phase 8 — AI Opportunity Portfolio Intelligence

**Code version:** 0.8.0. **Release state:** source implementation; production gate CLOSED.

## Implemented

- Organization-scoped portfolio list, ordered by deterministic *planning* heuristic
  only when three human-entered 1–5 signals, at least one independently verified
  evidence record and a fresh rules-engine analysis exist.
- Versioned heuristic `portfolio-v1`: strategic alignment 40%, urgency 35%,
  inverse delivery effort 25%. This is NOT an AI suitability score, ROI forecast,
  risk approval, or empirically validated priority ranking.
- Evidence registry: descriptive source reference, creation actor, revision,
  separate verification actor and timestamp. Old revision records are retained
  but not counted as current evidence. No file uploads, automatic verification,
  document retrieval or DLP is provided.
- Named, immutable illustrative value scenarios with explicit currency and
  time period. All math uses Decimal; no defaulted input measurements or FX
  conversions. Gross labor value and projected net value are distinct.
- PoC plans with KPI name, units, baseline, target, direction and acceptance
  criteria. Tracking state changes and subsequent measurements require an
  authenticated reviewer (or synthetic local demo). Observations are append-only,
  sourced and compared against the stated target.
- Streamlit portfolio dashboard, evidence register, scenario entry and outcome
  tracking using the existing authenticated FastAPI boundary.

## API (all under /api/v1)

| Endpoint | Role | Purpose |
| --- | --- | --- |
| GET `/portfolio/opportunities` | Viewer | Scoped portfolio ordering and evidence gaps |
| GET/PUT `/assessments/{id}/portfolio-profile` | Viewer / Analyst | Three 1–5 planning factors |
| GET/POST `/assessments/{id}/evidence` | Viewer / Analyst | Reviewable evidence ledger |
| POST `/assessments/{id}/evidence/{evidence_id}/verify` | Reviewer | Independent attestation on current revision |
| GET/POST `/assessments/{id}/value-scenarios` | Viewer / Analyst | Immutable financial scenarios |
| GET/POST `/assessments/{id}/pocs` | Viewer / Analyst | Version-bound PoC plans |
| POST `/assessments/{id}/pocs/{poc_id}/start` | Reviewer | Start tracking, NOT execution |
| POST `/assessments/{id}/pocs/{poc_id}/observations` | Reviewer | Sourced outcomes |

Assessment ownership is validated on every path. Mutations honor Phase 6
revision preconditions (`If-Match: "<revision>"` in authenticated pilot),
and update the row version to detect concurrent writes. In pilot mode, PoC
tracking start additionally requires the Phase 7 three-stage approval chain
for that exact assessment revision. No route calls an external business system.

## Financial math

For the same entered currency and period:

```text
Saved hours = Cases * (Baseline minutes - Target minutes) / 60
Gross = Saved hours * Loaded hourly cost
Net = Gross - Recurring operating/model cost
Simple payback (periods) = Initial cost / Net, only if Net > 0
```

Payback is null when net savings are zero/negative. A negative net result
is retained, never hidden. These are *illustrative labor economics*, not
realized cash returns. Implementation success, adoption, retained hours,
taxes, financing, time value of money, non-labor costs and causal attribution
must be validated independently; never describe a scenario as verified ROI.

Values in different currencies or periods are never converted, summed or
ranked by this implementation. Mixed-currency forecasts stay separate.

## Offline checks (without hosted runners)

```bash
python -m pip install -e '.[dev]'
python -m alembic upgrade head
python -m alembic check
python -m pytest -q tests/test_phase8_portfolio.py
python -m pytest -q
ruff check .
ruff format --check .
python -m app.evaluation
```

The new regression tests exercise tenant isolation, evidence provenance,
reviewer roles, stale-revision handling, negative-value scenarios and PoC
measurements. Tests were added to the repository, not claimed to have been
executed on a production stack. No GitHub Actions runners are enabled.

## Remaining before any real customer data or production use

The Phase 6 and 7 checks remain open: real OIDC/MFA browser sessions,
PostgreSQL deployment/load/concurrency/restore, TLS ingress/WAF, independent
pen test, vulnerability scanning, GDPR retention/deletion/provider approval,
accessibility review, tested deployment rollback and named release approval.
Additional Phase 8 validation requires representative expert-reviewed
opportunity outcomes, quantified override errors, ground-truth KPI sampling,
economic sensitivity, privacy-cleared evidence ingestion and risk calibration.
Synthetic results cannot establish field accuracy or ROI.

PoC targets must be directionally consistent with the submitted baseline.
