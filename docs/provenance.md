# Decision provenance ledger (Phase 9, P9-02)

A final FDE decision should answer, without guesswork: **which** policy version, **which**
analysis revision, **which** evidence records and **which** value scenario produced it — and
whether that basis still holds today.

This document describes the ledger that answers those questions.

## What the ledger is, and what it is not

The ledger is a **factual record of the inputs the system presented at the moment a human
finalized a decision**.

It is **not**:

- proof that the decision was correct, justified or optimal;
- an approval, a validation, a sign-off or an authorization of any kind;
- a claim about model quality, field accuracy or measured ROI;
- a substitute for the human finalization gate, which is unchanged.

Every user-facing string that mentions the ledger states this explicitly. The word
"provenance" is never used to imply endorsement.

## Where the ledger is stored, and why there is no migration

Migration numbering is contended between parallel workstreams, so the ledger is stored in an
**existing JSON column**:

| Storage | Column | Notes |
| --- | --- | --- |
| `audit_events.details` (JSON) | `app/db/models.py` | One append-only row per finalized decision, `event_type = "provenance.recorded"` |

The `audit_events` table is already the append-only, tenant-bound audit trail of this
product: rows belong to one `assessment_id`, are never mutated through the API, and survive
soft deletion of the assessment. A ledger row is therefore written in exactly the same way as
an audit event, and it is read back through the same tenant-scoped path.

**Why not `assessments.analysis_snapshot`?** The analysis snapshot is deliberately cleared
whenever the assessment revision changes (`change_inputs`, `change_reopen`). A ledger that
is erased on reopen cannot show that a decision was taken against a now-stale basis, which
is the whole point of drift detection. The audit trail keeps the entry, so the basis can be
compared with the current state of the assessment.

**Why not a new table?** A new table requires an Alembic revision, which is out of scope
for this item. The ledger fits the existing JSON column without any schema change.

## What is captured

Captured at finalization, in `app/services/provenance.py::capture_provenance`:

| Field | Meaning |
| --- | --- |
| `schema_version` | Ledger payload version (`provenance-v1`). |
| `policy_version` | The policy version that produced the analysis, read from the stored analysis snapshot (not from the current code constant). |
| `scoring_weight_version` | Derived fingerprint of the scoring weight table (`rules-v2-weights-<sha256-12>`). It states which weights were in the table; it is not a released version number. |
| `analysis_revision` | The analysis revision the decision was taken against. |
| `assessment_revision` | The assessment revision at finalization. |
| `analysis_digest` | SHA-256 (16 hex chars) of the canonical analysis snapshot, so a re-run analysis is detectable. |
| `evidence_ids` | Ids of the evidence records in the portfolio registry that belonged to the decision revision. |
| `verified_evidence_ids` | The subset that had been verified by a second human at decision time. |
| `value_scenario_id` | The value scenario that contributed (most recently created one at the decision revision), or `null`. |
| `value_scenario_ids` | Every value scenario recorded at the decision revision. |
| `reviewer_subject` | The reviewer identity carried by the review row. |
| `reviewer_identity_verified` | `true` only when the server authenticated that identity; the local demo shared secret always yields `false`. |
| `decision_maker_subject` | The **server-verified** principal that performed the finalization. A client-supplied label is never promoted to an identity. |
| `decision_maker_identity_verified` | `false` in local demo mode. |
| `ai_contributed` | `true` when at least one AI proposal was recorded against the decision revision. |
| `ai_proposals` | Feature, provider, model, prompt version and schema version of each recorded proposal. AI proposals never feed the deterministic analysis; their presence is recorded, not endorsed. |
| `recorded_at` | Timestamp of the ledger entry. |

The view adds `assessment_id`, `stale`, `stale_reasons`, `advisory_only` and `note`.

## API surface

```
GET /api/v1/assessments/{assessment_id}/provenance  ->  ProvenanceView | null
```

- Read-only, so it writes no audit event; it uses the same tenant scoping as every sibling
  read route (`get_or_404`), so another organization receives `404`, never the ledger.
- Authentication is enforced by the shared API dependency: an unauthenticated request is
  rejected with `401` before any tenant is resolved.
- `null` means "no finalized decision has been ledgered yet" (the same convention as
  `GET .../portfolio-profile`).
- The route is also visible in the audit history, because the ledger is an audit event
  (`event_type = "provenance.recorded"`).

## Drift detection

The `stale` flag mirrors the Phase 8 convention in `app/domain/portfolio.py`: a record
belongs to the revision it was created against, and it is stale once that revision is no
longer the current one.

`stale_reasons` is a list of stable codes:

| Code | Meaning |
| --- | --- |
| `analysis_revision_changed` | The current analysis is not the analysis the decision was taken against. |
| `analysis_changed` | The stored analysis snapshot no longer matches the recorded digest. |
| `policy_version_changed` | The policy version in the code differs from the one that produced the analysis. |
| `evidence_changed` | A recorded evidence record is missing, or no longer belongs to the current revision. |
| `evidence_added` | Evidence exists at the current revision that was not part of the recorded basis. |
| `value_scenario_changed` | A recorded value scenario is missing, or no longer belongs to the current revision. |
| `value_scenario_added` | A value scenario exists at the current revision that was not part of the recorded basis. |

Because a finalized assessment is frozen (`check_mutable`), the recorded evidence and
scenario can only change by **reopening** the assessment, which increments the revision.
A reopen therefore marks the earlier decision as taken against a now-stale basis, and the
ledger says so rather than presenting the old basis as current.

## Report export

`app/reporting/render.py` renders the ledger inside the existing **"FDE review and final
decision"** section, using the report's own fact/label style: a `Recorded input` / `Value`
table plus a `Basis stale: yes|no` row, and — when the basis is stale — an explicit
sentence naming the reasons. The executive summary gains `Decision inputs recorded` and
`Decision basis stale` rows. No new section heading is introduced, so the versioned report
golden (`evals/report_sections.json`) is unchanged.

Both the Markdown and the HTML export contain the same ledger, because both are rendered
from the same section view model.

## Tests

`tests/test_provenance.py` proves, on a local SQLite database with no external provider:

1. finalization records the complete provenance set;
2. the recorded policy version and analysis revision match the analysis actually used;
3. changing the evidence and the value scenario afterwards marks the ledger stale, with
   reasons;
4. another organization receives `404` for the ledger (tenant isolation fails closed) and an
   unauthenticated request receives `401`;
5. the exported Markdown and HTML reports contain the ledger and label a stale basis as
   stale.

## Known limits

- The ledger records **what the system knew**, not that the decision was right. No field of
  the ledger is evidence of correctness, adequacy or fitness for purpose.
- The ledger is stored in the audit trail, which is append-only by application convention.
  It is **not** cryptographically tamper-evident, and it inherits the audit trail's
  retention and deletion behaviour.
- `scoring_weight_version` is a derived fingerprint, not a released version. It only says
  which weight table was in the code at decision time.
- Drift detection compares the recorded basis with the current revision. It cannot detect a
  change that leaves the revision untouched, because no such change is currently possible:
  finalized assessments are frozen against evidence, scenario and analysis edits.
- The ledger does not change a recommendation category, an approval outcome or the human
  finalization gate, and it never calls a model or a provider.
