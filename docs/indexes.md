# Index and constraint hardening (migration `0010_index_and_constraint_hardening`)

This document records the six findings of the P5-03 PostgreSQL catalogue review,
what was changed for each of them, and how the change was verified against a
real PostgreSQL 16 instance and against the default SQLite backend.

Nothing in this document is a production capacity, load or recovery guarantee.
It records a **local, disposable** drill (see `scripts/pg_drill.py`).

## 1. Scope

| Item | Value |
| --- | --- |
| Revision id | `0010_index_and_constraint_hardening` |
| `down_revision` | `0008_portfolio` |
| Files | `app/db/models.py`, `migrations/versions/0010_index_and_constraint_hardening.py`, `tests/test_postgres_drill.py` |
| Backends | PostgreSQL 16 (psycopg 3) and SQLite (default local backend) |

The revision id is 36 characters long. Alembic creates
`alembic_version.version_num` as `VARCHAR(32)`, so the migration widens that
column to `VARCHAR(64)` before Alembic writes the new revision into it. The
column is not narrowed again on downgrade, because Alembic only replaces the
stored revision id *after* the migration body has run.

`app/main.py` previously accepted exactly one schema revision
(`0008_portfolio`) in `GET /ready`. Both the old and the new revision are
supported now (`SUPPORTED_SCHEMA_REVISIONS`): the application code runs
unchanged on either schema, so neither is a "not ready" database.

## 2. The six findings

### Finding 1 — `inbox_notifications.assessment_id` had no index

PostgreSQL does not index foreign keys automatically, so deleting a parent
assessment had to scan the whole notification table.

**Change:** new index `ix_inbox_notifications_assessment_id` on
`inbox_notifications (assessment_id)`.

**Verification:** `tests/test_postgres_drill.py::
test_every_foreign_key_has_a_supporting_index` no longer accepts any gap; it
asserts that *no* single-column foreign key in the schema is without a
leading-column index. Before 0010 the test carried the explicit
`accepted_gaps = {"inbox_notifications_assessment_id_fkey"}` exemption.

### Finding 2 — no composite `(organization_id, created_at)` on `assessments`

`GET /api/v1/assessments` (`app/api/assessments.py`) and
`GET /api/v1/portfolio/opportunities` (`app/api/portfolio.py`) filter on
`organization_id` and order by `created_at DESC`. With only
`ix_assessments_organization_id` the planner filtered through that index and
then sorted the tenant's rows with a top-N heapsort on every request.

**Change:** new index `ix_assessments_organization_id_created_at` on
`assessments (organization_id, created_at)`. The index is ascending; the
planner scans it *backwards* for `created_at DESC`, so no `DESC` column
ordering is needed and the index stays portable between PostgreSQL and SQLite.

### Finding 3 — the inbox query filtered two columns and sorted a third

`GET /api/v1/workspace/inbox` (`app/api/workspace.py`) filters
`organization_id` + `recipient` and orders by `created_at`. Two single-column
indexes could not serve it, so the plan was an index scan, a heap filter and
a sort.

**Change:** new index
`ix_inbox_notifications_organization_id_recipient_created_at` on
`inbox_notifications (organization_id, recipient, created_at)`.

### Finding 4 — membership lookups only indexed `organization_id`

The request-path membership check in `app/api/workspace.py`
(`_active_member`) filters `organization_id` + `subject` (+ `active`) on
`oidc_memberships`, and the same shape on `api_credentials` for the
token-based fallback. The OIDC *login* lookup is already covered by
`uq_oidc_member_identity_org (issuer, subject, organization_id)`.

**Change:** new indexes `ix_oidc_memberships_organization_id_subject` and
`ix_api_credentials_organization_id_subject`.

### Finding 5 — `ix_answers_key` was unused

**Decision: dropped.**

Evidence — the whole application reads answers only through the assessment
relationship, which is served by `ix_answers_assessment_id`:

```
$ grep -rn "AnswerRow" app/ tests/ scripts/ evals/
app/db/models.py:60:    answers: Mapped[list["AnswerRow"]] = relationship(
app/db/models.py:61:        cascade="all, delete-orphan", back_populates="assessment", order_by="AnswerRow.created_at"
app/db/models.py:122:class AnswerRow(Base):
app/api/assessments.py:13:    AnswerRow,
app/api/assessments.py:71:        elif isinstance(row, AnswerRow):
app/api/assessments.py:325:        AnswerRow(
```

There is no `select(AnswerRow...)` anywhere in the codebase, and no query
filters on `answers.key`; `app/services/discovery.py` and `app/ai/service.py`
filter the already-loaded `row.answers` list in Python. The index therefore
only added write amplification on every answer insert.

The index is dropped in the migration and removed from the model
(`key` is no longer `index=True`). `tests/test_postgres_drill.py::
test_expected_indexes_exist_in_the_postgres_catalogue` asserts that
`ix_answers_key` is *absent* from the catalogue, so a re-introduction has to be
a deliberate decision.

If a future query filters answers by `key` alone (for example a cross-
assessment report), the index has to be added back together with that query.

### Finding 6 — step order was only unique in Python

`app/api/assessments.py` rejects a duplicate step order with
`409 "Step order already used in this workflow"` before writing, but two
concurrent writers could both pass that check.

**Change:** a real database-level constraint

```sql
CONSTRAINT uq_workflow_steps_workflow_position UNIQUE (workflow_id, position)
```

declared in `app/db/models.py` and created by the migration through
`op.batch_alter_table`, because SQLite cannot `ALTER TABLE ... ADD CONSTRAINT`
and batch mode recreates the table there.

The constraint is a **tightening**: per workflow, per order. The application
already treated a duplicate order inside one workflow as invalid, so no
legitimate flow is affected. Different workflows keep reusing the same
ordinal positions, exactly as before.

#### Error contract

The friendly Python pre-check is unchanged and still answers
`409 {"detail": "Step order already used in this workflow"}`.

If the database rejects the row first (a lost race), the driver error is
translated by the SQLAlchemy `handle_error` listener in `app/db/models.py`
into the *same* `HTTPException(409)`. The client never sees an
`IntegrityError`, a 500, the constraint name, the SQL statement or any
parameter value:

```python
@event.listens_for(Dialect, "handle_error")
def _translate_step_order_violation(context) -> None:
    ...
    return HTTPException(status_code=409, detail=STEP_ORDER_CONFLICT_DETAIL)
```

The listener only reacts to *this* constraint (matched by constraint name on
PostgreSQL and by the offending column list on SQLite); every other database
error keeps its normal behaviour.

## 3. Index catalogue after `0010`

| Index | Table | Columns | Serves |
| --- | --- | --- | --- |
| `ix_assessments_organization_id_created_at` | `assessments` | `organization_id, created_at` | assessment list, portfolio list |
| `ix_inbox_notifications_assessment_id` | `inbox_notifications` | `assessment_id` | foreign key, parent delete |
| `ix_inbox_notifications_organization_id_recipient_created_at` | `inbox_notifications` | `organization_id, recipient, created_at` | workspace inbox |
| `ix_oidc_memberships_organization_id_subject` | `oidc_memberships` | `organization_id, subject` | membership lookup |
| `ix_api_credentials_organization_id_subject` | `api_credentials` | `organization_id, subject` | membership lookup |
| `uq_workflow_steps_workflow_position` | `workflow_steps` | `workflow_id, position` | step order uniqueness (constraint) |

Dropped: `ix_answers_key`.

The single-column indexes that already existed were kept: they still serve
the "all rows of one tenant" access patterns and the `ON DELETE` cascades.

## 4. Measured query plans

Measured with `EXPLAIN (ANALYZE, BUFFERS)` on the disposable PostgreSQL 16
container (`scripts/pg_drill.py`), against one tenant with 5,000 assessments
and 5,000 inbox notifications plus a second tenant with 20,000 assessments and
20,000 notifications, and 2,000 memberships in the tenant of the request.

### Q1 — `GET /api/v1/assessments` (tenant list)

**BEFORE** (revision `0008_portfolio`):

```
Limit  (cost=391.38..391.51 rows=50 width=1169) (actual time=0.987..0.991 rows=50 loops=1)
  Buffers: shared hit=107
  ->  Sort  (cost=391.38..403.88 rows=5000 width=1169) (actual time=0.986..0.988 rows=50 loops=1)
        Sort Key: created_at DESC
        Sort Method: top-N heapsort  Memory: 37kB
        Buffers: shared hit=107
        ->  Index Scan using ix_assessments_organization_id on assessments  (cost=0.29..225.29 rows=5000 width=1169) (actual time=0.011..0.502 rows=5000 loops=1)
              Index Cond: ((organization_id)::text = '11111111-1111-1111-1111-111111111111'::text)
              Filter: ((status)::text <> 'deleted'::text)
              Buffers: shared hit=107
Planning:
  Buffers: shared hit=86
Planning Time: 0.235 ms
Execution Time: 1.012 ms
```

**AFTER** (revision `0010_index_and_constraint_hardening`):

```
Limit  (cost=0.41..12.59 rows=50 width=1169) (actual time=0.015..0.022 rows=50 loops=1)
  Buffers: shared hit=1 read=3
  ->  Index Scan Backward using ix_assessments_organization_id_created_at on assessments  (cost=0.41..1218.22 rows=5000 width=1169) (actual time=0.015..0.019 rows=50 loops=1)
        Index Cond: ((organization_id)::text = '11111111-1111-1111-1111-111111111111'::text)
        Filter: ((status)::text <> 'deleted'::text)
        Buffers: shared hit=1 read=3
Planning:
  Buffers: shared hit=71 read=1
Planning Time: 0.222 ms
Execution Time: 0.032 ms
```

The sort node is gone. 1.012 ms → 0.032 ms, 107 buffers → 4 buffers.

### Q2 — `GET /api/v1/portfolio/opportunities` (tenant list)

**BEFORE**:

```
Limit  (cost=416.38..416.63 rows=100 width=1169) (actual time=0.973..0.980 rows=100 loops=1)
  Buffers: shared hit=107
  ->  Sort  (cost=416.38..428.88 rows=5000 width=1169) (actual time=0.972..0.975 rows=100 loops=1)
        Sort Key: created_at DESC, id
        Sort Method: top-N heapsort  Memory: 50kB
        Buffers: shared hit=107
        ->  Index Scan using ix_assessments_organization_id on assessments  (cost=0.29..225.29 rows=5000 width=1169) (actual time=0.010..0.472 rows=5000 loops=1)
              Index Cond: ((organization_id)::text = '11111111-1111-1111-1111-111111111111'::text)
              Filter: ((status)::text <> 'deleted'::text)
              Buffers: shared hit=107
Planning Time: 0.039 ms
Execution Time: 0.998 ms
```

**AFTER**:

```
Limit  (cost=0.67..29.43 rows=100 width=1169) (actual time=0.034..0.063 rows=100 loops=1)
  Buffers: shared hit=5
  ->  Incremental Sort  (cost=0.67..1438.61 rows=5000 width=1169) (actual time=0.033..0.059 rows=100 loops=1)
        Sort Key: created_at DESC, id
        Presorted Key: created_at
        Full-sort Groups: 4  Sort Method: quicksort  Average Memory: 33kB  Peak Memory: 33kB
        Buffers: shared hit=5
        ->  Index Scan Backward using ix_assessments_organization_id_created_at on assessments  (cost=0.41..1218.22 rows=5000 width=1169) (actual time=0.009..0.019 rows=101 loops=1)
              Index Cond: ((organization_id)::text = '11111111-1111-1111-1111-111111111111'::text)
              Filter: ((status)::text <> 'deleted'::text)
              Buffers: shared hit=5
Planning Time: 0.037 ms
Execution Time: 0.080 ms
```

The full top-N heapsort over 5,000 rows is gone. What remains is an
*incremental* sort that only orders the `id` tie-break inside the already
ordered `created_at` prefix (4 groups, 33 kB, 101 rows fetched instead of
5,000). 0.998 ms → 0.080 ms.

### Q3 — `GET /api/v1/workspace/inbox`

**BEFORE**:

```
Limit  (cost=285.51..285.76 rows=100 width=175) (actual time=0.772..0.780 rows=100 loops=1)
  Buffers: shared hit=128
  ->  Sort  (cost=285.51..288.01 rows=1000 width=175) (actual time=0.771..0.775 rows=100 loops=1)
        Sort Key: created_at DESC
        Sort Method: top-N heapsort  Memory: 50kB
        Buffers: shared hit=128
        ->  Index Scan using ix_inbox_notifications_organization_id on inbox_notifications  (cost=0.29..247.29 rows=1000 width=175) (actual time=0.009..0.514 rows=5000 loops=1)
              Index Cond: ((organization_id)::text = '11111111-1111-1111-1111-111111111111'::text)
              Filter: ((recipient)::text = 'drill-analyst'::text)
              Buffers: shared hit=128
Planning:
  Buffers: shared hit=51
Planning Time: 0.176 ms
Execution Time: 0.807 ms
```

**AFTER**:

```
Limit  (cost=0.41..105.39 rows=100 width=175) (actual time=0.012..0.028 rows=100 loops=1)
  Buffers: shared hit=4 read=4
  ->  Index Scan Backward using ix_inbox_notifications_organization_id_recipient_created_at on inbox_notifications  (cost=0.41..1050.16 rows=1000 width=175) (actual time=0.012..0.024 rows=100 loops=1)
        Index Cond: (((organization_id)::text = '11111111-1111-1111-1111-111111111111'::text) AND ((recipient)::text = 'drill-analyst'::text))
        Buffers: shared hit=4 read=4
Planning:
  Buffers: shared hit=31 read=2
Planning Time: 0.148 ms
Execution Time: 0.035 ms
```

The heap filter over 5,000 notifications and the sort are both gone:
0.807 ms → 0.035 ms.

### Q4 / Q5 — workspace membership lookups

**BEFORE** (`oidc_memberships`):

```
Index Scan using ix_oidc_memberships_organization_id on oidc_memberships  (cost=0.29..90.31 rows=1 width=124) (actual time=0.010..0.123 rows=1 loops=1)
  Index Cond: ((organization_id)::text = '11111111-1111-1111-1111-111111111111'::text)
  Filter: ((active IS TRUE) AND ((subject)::text = 'drill-analyst'::text))
  Rows Removed by Filter: 2000
  Buffers: shared hit=41
Planning:
  Buffers: shared hit=45
Planning Time: 0.167 ms
Execution Time: 0.129 ms
```

**AFTER**:

```
Index Scan using ix_oidc_memberships_organization_id_subject on oidc_memberships  (cost=0.41..8.43 rows=1 width=124) (actual time=0.011..0.011 rows=1 loops=1)
  Index Cond: (((organization_id)::text = '11111111-1111-1111-1111-111111111111'::text) AND ((subject)::text = 'drill-analyst'::text))
  Filter: (active IS TRUE)
  Buffers: shared hit=1 read=3
Planning:
  Buffers: shared hit=25 read=1
Planning Time: 0.087 ms
Execution Time: 0.015 ms
```

The same holds for `api_credentials` (before: 2,000 rows removed by the
filter, 48 buffers, 0.143 ms; after: both conditions in the index condition,
4 buffers, 0.017 ms).

## 5. How to reproduce

```bash
# disposable PostgreSQL 16
python -m scripts.pg_drill up
python -m scripts.pg_drill migrate      # alembic upgrade head + alembic check
python -m scripts.pg_drill fresh        # fresh database: upgrade, check, downgrade base, upgrade
python -m scripts.pg_drill backup-restore
python -m scripts.pg_drill down          # removes both containers and the dump

# the drill test module against a running instance
ADVISORY_PG_DRILL_URL=postgresql+psycopg://aiadvisory:***@127.0.0.1:15432/aiadvisory \
    python -m pytest -q tests/test_postgres_drill.py
```

The SQL used for the plans above is the SQL the API emits; it is reproduced in
`tests/test_postgres_drill.py` (`test_tenant_list_ordering_uses_the_composite_index`,
`test_portfolio_listing_no_longer_sorts_the_whole_tenant`,
`test_inbox_listing_uses_the_composite_index`,
`test_membership_lookup_uses_the_composite_index`).

## 6. Verification results

| Check | Result |
| --- | --- |
| `alembic upgrade head` on a fresh PostgreSQL database | OK |
| `alembic check` on PostgreSQL | `No new upgrade operations detected.` |
| `alembic upgrade head` → `alembic downgrade base` → `upgrade head` on PostgreSQL | OK (fresh-database cycle) |
| `alembic upgrade head` on a fresh SQLite database | OK |
| `alembic check` on SQLite | `No new upgrade operations detected.` |
| `alembic upgrade 0008_portfolio` → `upgrade head` on a SQLite database **with data** | OK, data and foreign keys intact (`PRAGMA foreign_key_check` empty) |
| `alembic downgrade 0008_portfolio` → `upgrade head` on SQLite | OK, no drift |
| `pytest -q` (SQLite) | 148 passed, 22 skipped |
| `pytest -q tests/test_postgres_drill.py` (PostgreSQL 16) | 22 passed |
| `python -m scripts.pg_drill down` | both containers and the dump removed |

## 7. Known limits

* The composite index only helps the **tenant-scoped** listing. In synthetic
  demo mode (`ADVISORY_DEMO_MODE=1`) no `organization_id` filter is applied,
  so the planner cannot use the leading column of the index and the listing
  stays a sequential scan plus sort over the whole table. That path is a local
  demo, not a multi-tenant deployment.
* The portfolio listing keeps a residual *incremental* sort for its `id`
  tie-break (see Q2). Adding `id` to the composite index would remove it at
  the cost of a wider index; it was not worth the extra write amplification
  for the measured gain of ~0.03 ms.
* The plans above were measured on one disposable container with synthetic
  data. They are a local drill result, not a production capacity or load test.
* `ix_answers_key` was dropped because nothing used it. A new query that
  filters answers by `key` has to bring the index back together with that
  query.
