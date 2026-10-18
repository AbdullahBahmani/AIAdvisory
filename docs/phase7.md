# Phase 7 — Enterprise product experience

## Delivered source

- Workspace navigation available even without an existing assessment, portfolio
  status totals, search/status filtering and personal reviewer inbox.
- Admin-only member directory (only trusted, active credential/OIDC subjects;
  no arbitrary grants, passwords or tokens exposed).
- Assigned reviewer workflow with membership checks and audit event.
- Revision-stamped discussion comments and stable author attribution.
- Three-stage human approvals: technical, security, business. Stage order,
  distinct approvers, role requirements, revision invalidation and evidence
  rationale are enforced server-side.
- In-app notifications scoped by organization and recipient, with read receipts.
- Phase 7 migration 0007, new contract schemas, Streamlit screens and negative
  authorization/approval regression tests.

## API

All paths start with `/api/v1`. Tenant authentication applies to every route.

| Route | Purpose |
| --- | --- |
| `GET /workspace/summary` | Organization-scoped status counts and personal queue metrics |
| `GET /workspace/inbox` | Assigned assessments and in-app notifications for caller |
| `GET /workspace/members` | Administrator-only active directory (read-only) |
| `POST /workspace/notifications/{id}/read` | Mark only caller's notification as read |
| `GET /assessments/{id}/reviewer` | Assigned reviewer |
| `PUT /assessments/{id}/reviewer` | Admin assigns active org reviewer |
| `GET/POST /assessments/{id}/comments` | Revision-stamped discussion |
| `GET/POST /assessments/{id}/approvals` | Ordered human approval ledger |

The established `/fde-review` and `/final-decision` endpoints remain.
Pilot finalization additionally checks the complete current-revision approval
chain, rather than delegating authorization to the LLM. API tenants do not share
assessments, comments, assignments, member directories or inboxes.

## Local verification (no hosted runners)

```bash
python -m pip install -e '.[dev]'
python -m alembic upgrade head
python -m alembic check
python -m pytest -q tests/test_phase7_collaboration.py tests/test_phase6_foundation.py tests/test_security.py
python -m pytest -q
ruff check .
ruff format --check .
```

Migrate a real PostgreSQL instance with `alembic upgrade head`, and test
concurrent submissions, multi-user login, MFA and a full review path before
enabling authenticated external pilots. Do not run the demo authentication
bypass on a public host.

## Not delivered / production gate

There is no verified browser SSO/MFA/session lifecycle, SCIM provisioning,
fine-grained security-officer permissions, encrypted off-site backup restore,
external notification delivery, independent accessibility audit, enterprise
UI rewrite, fully executed merged test suite, or production deployment
certification. `ADVISORY_DEPLOYMENT_MODE=production` still refuses to boot.
