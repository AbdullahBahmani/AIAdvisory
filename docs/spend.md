# Local provider-spend reporting (P5-04, provider-spend half)

**What this is:** a local, offline, **read-only** command that aggregates the AI
cost and token usage already stored in `ai_runs` and prints a spend estimate.

**What this is not:** a billing reconciliation, an invoice, a chargeback
statement, an alerting system or a dashboard. It is a local estimate built from
prices **the operator supplied**, used for trend and anomaly spotting.

The production startup guard is untouched by this work and
`ADVISORY_DEPLOYMENT_MODE=production` still refuses to boot.

## How cost gets into the database (and why "unknown" is common)

The application never prices anything by itself. `app/ai/service.py` writes
`ai_runs.estimated_cost_usd` and `ai_runs.pricing_source` only when **all** of
these are present on the API host at request time:

| Environment variable | Meaning |
| --- | --- |
| `ADVISORY_AI_INPUT_USD_PER_MILLION` | operator-verified input rate |
| `ADVISORY_AI_OUTPUT_USD_PER_MILLION` | operator-verified output rate |
| `ADVISORY_AI_PRICING_SOURCE` | where that rate sheet came from |

If any of them is missing (or the provider reported no token counts), the run is
stored with **no cost**. That is the normal state of an instance where nobody
has supplied a price sheet, and it is the case the report has to be honest
about.

The report therefore **never**:

* applies a price of its own, or re-prices historical runs with today's rates;
* estimates, extrapolates or infers a missing price;
* drops an unpriced run from the denominator.

## The command

```bash
# single tenant (default shape of the report)
python -m scripts.spend_report --organization "Example Co"

# explicit cross-tenant view
python -m scripts.spend_report --all-tenants

# a period, a grouping and machine-readable output
python -m scripts.spend_report --organization "Example Co" \
    --since 30d --until 2026-11-01 --group-by day --format json
```

With `uv`:

```bash
DATABASE_URL=sqlite:///./aiadvisory.sqlite3 \
  uv run --no-sync python -m scripts.spend_report --organization "Example Co"
```

Options:

| Option | Meaning |
| --- | --- |
| `--organization NAME_OR_UUID` | scope to exactly one organization (name or UUID) |
| `--all-tenants` | explicitly request the cross-tenant view |
| `--since` | relative window (`30d`, `12h`, `4w`, `3m`) or an ISO timestamp. Default `30d` |
| `--until` | ISO timestamp. Default: now |
| `--group-by` | `model` (default), `organization`, `feature`, `day`, `assessment` |
| `--format` | `text` (default) or `json` |

### Tenant scoping: which one is it?

**Single tenant by default.** The report refuses to run unless the operator
states the scope explicitly:

* `--organization "Example Co"` → one tenant only. Runs of other tenants are
  not read. Legacy assessments that have **no** tenant assignment cannot be
  attributed to this tenant, so they are excluded; the count of excluded
  unattributed runs is printed.
* `--all-tenants` → the cross-tenant view. This is the only way to see every
  tenant at once, and it must be asked for by name. Rows without a tenant are
  grouped under `(unassigned)`.

Passing both, or neither, is an error (exit code 2). The scope is printed in
every report.

### Read-only

The command only issues `SELECT` statements. On PostgreSQL it additionally
marks the transaction `READ ONLY` (`SET TRANSACTION READ ONLY`) before the first
query; if the database refuses that, the command warns on stderr and continues
with `SELECT`-only statements. It never writes, migrates or repairs anything.

## Output semantics

| Column | Meaning |
| --- | --- |
| `RUNS` | AI runs in the window and scope. Includes unpriced and malformed runs. |
| `PRICED` | runs whose stored cost was read as a finite decimal |
| `UNPRICED` | runs stored with **no** cost (no operator price at run time) → **unknown cost** |
| `MALFORMED` | runs whose stored cost string is not a finite decimal → **unknown cost** |
| `COST USD` | sum of the stored prices of the **priced** runs only. `-` means no priced run in the group, so the cost is *unknown*, not zero. |
| `IN TOK` / `OUT TOK` | token totals, with the number of runs that reported each count |

`PRICED + UNPRICED + MALFORMED = RUNS`, always. The report states the priced
share ("cost coverage") and, when any run has unknown cost, says plainly that
the USD figure is a **lower bound** for the window rather than the full spend.

### Why `model` is the default grouping

Spend follows the *priced model*, because the operator's rate sheet is per
model: the same model billed to two tenants is one price, and a model switch is
the change that actually moves the bill. `organization` is the right grouping
for chargeback questions but only makes sense in the `--all-tenants` view,
`day` is the trend view, and `assessment` is the drill-down view. All four
plus `feature` are available.

## Worked local example (synthetic data)

The following was run on 2026-10-20 on a local, synthetic SQLite database. The
seed contains three priced runs, two unpriced runs and one deliberately
corrupted cost string, across two tenants plus one legacy assessment with no
tenant assignment.

Seed it (synthetic only; no provider call, no real prices):

```python
# /tmp/spend-demo/seed_spend_demo.py
"""Seed a local synthetic database so the spend report can be exercised offline."""
import os
from datetime import UTC, datetime

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.db.models import AIRunRow, AssessmentRow, Base, OrganizationRow

URL = os.environ["DATABASE_URL"]
engine = create_engine(URL, connect_args={"check_same_thread": False})


@event.listens_for(engine, "connect")
def _fk(dbapi_connection, _connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


Base.metadata.create_all(engine)


def at(day, hour):
    return datetime(2026, 10, day, hour, 0, tzinfo=UTC)


def assessment(aid, org_id, org_name):
    return AssessmentRow(
        id=aid, organization_id=org_id, organization=org_name, title=f"Synthetic {aid}"
    )


def run(rid, aid, feature, model, tokens, cost, source, when):
    return AIRunRow(
        id=rid,
        assessment_id=aid,
        assessment_revision=1,
        feature=feature,
        proposal={},
        provider="openai",
        model=model,
        prompt_version="3.0",
        schema_version="3.0",
        elapsed_ms=420,
        input_tokens=tokens[0],
        output_tokens=tokens[1],
        estimated_cost_usd=cost,
        pricing_source=source,
        created_at=when,
    )


with Session(engine) as db:
    db.add_all([OrganizationRow(id="org-1", name="Example Co"),
                OrganizationRow(id="org-2", name="Other Co")])
    db.flush()
    db.add_all([assessment("a-1", "org-1", "Example Co"),
                assessment("a-2", "org-2", "Other Co"),
                assessment("a-legacy", None, None)])
    db.flush()
    db.add_all([
        # Priced: the operator supplied a rate sheet.
        run("r-1", "a-1", "discovery", "gpt-4.1-mini", (1200, 300),
            "0.004800", "operator-reviewed-rate-sheet-2026-10", at(5, 9)),
        run("r-2", "a-1", "workflow", "gpt-4.1-mini", (800, 150),
            "0.002800", "operator-reviewed-rate-sheet-2026-10", at(6, 11)),
        # Unpriced: recorded before the operator configured pricing.
        run("r-3", "a-1", "risk", "gpt-4.1-mini", (640, 90), None, None, at(7, 10)),
        # Unpriced, different tenant.
        run("r-4", "a-2", "challenge", "gpt-4.1", (2100, 480), None, None, at(9, 15)),
        # Priced, different tenant and a different model.
        run("r-5", "a-2", "evaluation", "gpt-4.1", (1500, 400),
            "0.011000", "operator-reviewed-rate-sheet-2026-10", at(12, 8)),
        # Legacy assessment with no tenant assignment, and a corrupted cost
        # string, to show that a malformed value is never added to the total.
        run("r-6", "a-legacy", "discovery", "gpt-4.1-mini", (400, 60), "n/a", None, at(13, 14)),
    ])
    db.commit()
print("seeded", URL)
```

```bash
export DATABASE_URL="sqlite:////tmp/spend-demo/spend.sqlite3"
uv run --no-sync python /tmp/spend-demo/seed_spend_demo.py
uv run --no-sync python -m scripts.spend_report --organization "Example Co" \
    --since 2026-09-01 --until 2026-11-01
```

Actual output (single tenant, grouped by model):

```
==============================================================================
LOCAL ESTIMATE from operator-supplied prices - NOT a billing reconciliation
==============================================================================
Window (UTC)   : 2026-09-01T00:00:00+00:00  ->  2026-11-01T00:00:00+00:00
Scope          : organization 'Example Co' (id org-1) - single tenant
Grouping       : model
Database       : sqlite (read-only)
Runs in scope  : 3
Excluded       : 1 run(s) with no tenant assignment (visible only in --all-tenants)

GROUP                               RUNS  PRICED  UNPRICED  MALFORMED      COST USD    IN TOK   OUT TOK
-------------------------------------------------------------------------------------------------------
openai/gpt-4.1-mini                    3       2         1          0      0.007600      2640       540
-------------------------------------------------------------------------------------------------------
TOTAL                                  3       2         1          0      0.007600      2640       540
COST USD is the sum of the operator-supplied prices actually stored on the priced runs only.
'-' means no run in that group carried a stored price, so the cost is unknown, not zero.

Cost coverage  : 2 of 3 runs priced (66.7%); 1 unpriced (no operator price recorded), 0 malformed.
                 1 run(s) have UNKNOWN cost. They are counted in every
                 run total above but excluded from the USD figure, so the USD figure is a
                 lower bound for this window - not the full spend. This tool never estimates
                 a missing price; supply ADVISORY_AI_* pricing on the API host to record costs.
Token usage    : 2640 input tokens (3 run(s) reported, 0 without),
                 540 output tokens (3 run(s) reported, 0 without).
Price sources  : operator-reviewed-rate-sheet-2026-10

Reminder: these numbers come from prices the operator supplied at run time. They are not provider
invoices, not a reconciliation and not a basis for external billing claims.
```

Cross-tenant view (`--all-tenants --group-by organization`), same data:

```
Runs in scope  : 6

GROUP                               RUNS  PRICED  UNPRICED  MALFORMED      COST USD    IN TOK   OUT TOK
-------------------------------------------------------------------------------------------------------
Other Co                               2       1         1          0      0.011000      3600       880
Example Co                             3       2         1          0      0.007600      2640       540
(unassigned)                           1       0         0          1             -       400        60
-------------------------------------------------------------------------------------------------------
TOTAL                                  6       3         2          1      0.018600      6640      1480
```

Note that the `(unassigned)` row shows `-`, not `0.000000`: the cost of that run
is unknown, and a corrupted cost string is never counted as a price.

## Tests

```bash
uv run --no-sync python -m pytest -q tests/test_spend_report.py
```

The new module covers the priced/unpriced/malformed split, the guarantee that
the USD total is exactly the sum of the stored prices, token totals and missing
token counts, every grouping dimension, the window filter, single-tenant vs
cross-tenant scoping (including the exclusion of unattributed legacy runs),
the "no zero-cost for unknown cost" rendering, and the CLI scope guards.

## Limitations (still open)

* No alerting, thresholds or notifications of any kind. This is a command an
  operator runs; nothing watches the numbers for you.
* No dashboard, no time-series store, no scheduled job.
* The numbers are only as good as the rate sheet the operator supplied, and
  only for runs recorded while that sheet was configured. Historical unpriced
  runs stay unpriced.
* No reconciliation against provider invoices, and no currency handling beyond
  the single USD estimate column.
* The cross-tenant view is an operator tool; it is not an API and it does not
  weaken the tenant isolation of the application itself.
