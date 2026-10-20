# Credential and membership lifecycle

**Status: operator policy and runbook.** This document describes what the
repository *does* enforce for API credentials and memberships. It is not a
claim of enterprise identity: a credential proves possession of a secret, not a
human identity. See [What is not covered](#what-is-not-covered).

The lifecycle closes the gaps that planner item **P5-01** left open after
P2-07 / ADR-0014 established that a local principal resolves to a server-verified
subject, organization and role:

> Local principals are operator-provisioned: no self-service, no automatic
> de-provisioning on role change, no per-person rotation policy.

## Schema

The lifecycle needs three things the credential table could not express before.
They are added by the Alembic revision **`0014_credential_lifecycle`**:

* `api_credentials.expires_at` becomes nullable and is paired with
  `api_credentials.unlimited`. `NULL` means "never expires" and is only
  accepted together with the explicit opt-in; a row with `expires_at IS NULL`
  and `unlimited = false` is refused by the authentication path.
* `api_credentials.overlap_until` and `api_credentials.replaced_by_id` record
  the bounded rotation overlap and the rotation lineage.
* `audit_events.assessment_id` becomes nullable and `audit_events.organization_id`
  is added, so a credential or membership event can be scoped to the
  organization instead of to one assessment.

Apply it before starting the API or running the operator CLI:

```
python -m alembic upgrade head
```

The downgrade removes the organization-scoped audit events (the old schema
cannot express them) and turns an unlimited credential into a far-future
expiry.

## The policy in one page

| Rule | Where it is enforced |
| --- | --- |
| Every credential has an expiry. The default is **30 days**, the maximum is **365 days**. | `scripts/admin.py issue`, `app/services/credential_lifecycle.py` |
| A credential that never expires is an **explicit opt-in** (`--unlimited` plus `--unlimited-reason`) and is labelled `UNLIMITED` in every listing and in the access review. It is never the default. | `app/services/credential_lifecycle.py`, `scripts/admin.py list` |
| A credential stops working **30 seconds before** its stated expiry (`CREDENTIAL_EXPIRY_LEEWAY`). This is a strict leeway, not a grace period. | `app/security.py` |
| A revoked, expired or overlap-ended credential is refused on **every** authenticated route, and the attempt is audited. | `app/security.py`, `app/services/audit.py` |
| Rotation replaces a credential and retires the previous one in **one transaction**: exactly one credential is usable afterwards. | `app/services/credential_lifecycle.py` |
| A rotation overlap is possible only when the operator asks for it, is bounded to **900 seconds**, and is reported by the listing. | `scripts/admin.py rotate`, `scripts/admin.py list` |
| Reducing the role of a membership or deactivating it **de-provisions** the credentials and reviewer assignments that depended on the old role. | `app/services/credential_lifecycle.py` |
| A revocation that would leave an organization **without any administrator** is refused unless the operator overrides it explicitly; the override is audited. | `app/services/credential_lifecycle.py` |
| The lifecycle sweep **reports by default** and only changes something with `--execute`. Every action is audited individually. | `scripts/admin.py sweep-membership-lifecycle` |
| Every lifecycle change is written through the shared audit gate. | `app/services/audit.py` |

### The expiry leeway is strict, and it is 30 seconds

`CREDENTIAL_EXPIRY_LEEWAY` in `app/security.py` is **30 seconds**, and it is
applied in the *safe* direction: a credential is refused from 30 seconds
**before** its stated expiry, never after it.

The reason is clock skew. If the server clock runs slow (or the caller's clock
runs fast), a naive `expires_at > now` comparison would let a credential live
longer than it should. Refusing early bounds the damage to exactly the
documented 30 seconds: a credential can never be used more than 30 seconds past
its real expiry, and it never gets a grace period that a skewed clock could
abuse.

There is deliberately **no grace period**. A credential that expired one second
ago is already refused. The constant is part of the policy: changing it is a
policy change and must be reflected here and in the test suite.

### The rejection is audited, but the audit cannot be flooded

Every attempt to use a revoked or expired credential writes a
`credential.use_rejected` event into the audit trail, scoped to the
organization of the credential. The write is throttled per credential
(`REJECTION_AUDIT_WINDOW_SECONDS`, 60 seconds): the first use of a credential
is always audited, repeats inside the window are collapsed into that event.
Without the throttle, anybody holding a revoked credential could fill the audit
table with one row per request. An unknown token is not audited at all - it
cannot be attributed to a tenant, and it must not cost a write per attempt.

### An expiry is mandatory

`expires_at` may only be `NULL` for a credential the operator explicitly
created with `--unlimited`. A row with `expires_at IS NULL` and `unlimited =
false` is refused by the authentication path: the system fails closed instead
of silently turning a row into a standing key.

```
python -m scripts.admin issue --organization Acme --subject alice --role analyst
python -m scripts.admin issue --organization Acme --subject break-glass --role admin \
    --unlimited --unlimited-reason "incident commander key, reviewed 2026-10-20"
```

The unlimited credential is announced loudly on the console, recorded in the
audit trail (`credential.issued` with `unlimited: true`) and labelled
`UNLIMITED (never expires)` in `list` and in the access review.

### Rotation never leaves two valid credentials

`rotate` retires the previous credential **first** and issues the replacement
afterwards, inside one transaction:

1. the previous credential is revoked (or, with an explicit overlap, its
   lifetime is cut to the end of the overlap so that it dies by itself);
2. the replacement credential is created with a fresh token;
3. both are written in the same transaction, so either both happen or neither
   does.

If the issue step fails - the token service is unavailable, the database
rejects the row - the transaction is rolled back. The previous credential stays
exactly as it was and **no** second credential exists. The operator never ends
up with two working credentials and never ends up with none.

```
python -m scripts.admin rotate --token-id <id> --days 30
```

#### The overlap window

The operator may ask for a short overlap so that a service can pick up the new
credential before the old one is dropped:

```
python -m scripts.admin rotate --token-id <id> --overlap-seconds 300
```

* the overlap is **explicit** (it is never a default) and **bounded** to 900
  seconds (15 minutes);
* during the overlap the previous credential keeps working and the replacement
  works as well - this is the only situation in which two credentials of one
  person are valid at the same time, and it is the operator's deliberate choice;
* the previous credential expires by itself at the end of the overlap: its
  expiry is cut to that moment, so it cannot outlive the window;
* the overlap is reported by `list` (`rotation overlap until ...`) and shows up
  in the access review.

### De-provisioning on role change

A membership is the operator's record of what a person is allowed to do. When
that record changes, the credentials and assignments that relied on the old
role must not silently keep working.

**The rule.** For a subject in an organization:

* the *effective membership role* is the highest role among the subject's
  **active** memberships;
* a live credential whose role is **higher** than the effective membership role
  is revoked - the credential exceeded what the membership allows;
* when the subject has **no active membership any more**, all of their live
  credentials in that organization are revoked;
* a subject that has no membership record at all is a local principal: their
  credential *is* the membership and stands on its own until it is revoked or
  expires;
* a **reviewer assignment** is cleared when the assigned reviewer can no
  longer act as a reviewer - they lost the reviewer role, their membership was
  deactivated, or their only reviewer credential was revoked.

**The risk this closes.** An assessment whose reviewer can no longer act waits
for a review that can never happen: the assessment is stuck in review, the
reviewer sees nothing to do, and nobody is told. Every de-provisioning pass
clears such an assignment so that an administrator can assign somebody who can
act.

**The last administrator.** A revocation that would leave an organization
without any administrator is refused:

```
$ python -m scripts.admin set-membership-role --membership-id <id> --role analyst
error: Refusing to remove the last administrator of organization <org-id>: grant
another administrator first or pass --override-last-admin to proceed deliberately.
```

The override is deliberate, explicit and audited (`last_admin_override: true` in
the `membership.role_changed` event).

### The lifecycle sweep

The sweep finds the drift that a role change, a manual revocation or a crashed
operator command can leave behind:

* credentials whose membership is inactive or whose role no longer covers the
  credential;
* expired credentials that are still present;
* reviewer assignments that point at people who can no longer act;
* memberships with no live credential (reported, never changed - an
  identity-provider member does not need a local credential).

**The default is a report.** Nothing is changed and nothing is audited until
the operator passes `--execute`:

```
python -m scripts.admin sweep-membership-lifecycle --organization Acme
python -m scripts.admin sweep-membership-lifecycle --organization Acme --execute
```

The same sweep is available as an offline maintenance command, with the same
dry-run default:

```
python -m scripts.maintenance sweep-membership-lifecycle --organization-id <id>
python -m scripts.maintenance sweep-membership-lifecycle --organization-id <id> --execute
```

With `--execute`, every finding is acted on and audited individually
(`credential.revoked`, `reviewer.assignment_cleared`), and a revocation that
would remove the last administrator is reported as `BLOCKED` unless
`--override-last-admin` is passed.

### The access review

The access review is the artefact an auditor asks for: who holds which role,
which credentials are live, when they expire, which assessments a person can
act on and which assignments are outstanding.

```
python -m scripts.admin access-review --organization Acme
python -m scripts.admin access-review --organization Acme --format json
python -m scripts.admin access-review --organization Acme --format csv --output review.csv
```

The text form is meant to be read; the JSON and CSV forms are meant to be
attached to an export or a ticket.

## Operator runbook

### A new person joins

```
python -m scripts.admin create-org --organization Acme        # once per organization
python -m scripts.admin issue --organization Acme --subject alice --role analyst
python -m scripts.admin grant-oidc --organization Acme \
    --issuer https://idp.example.com --subject alice --role analyst
```

The token is printed **once** and only its SHA-256 hash is stored. If it is
lost, rotate it; do not try to recover it.

### A person's role changes

```
python -m scripts.admin set-membership-role --membership-id <id> --role reviewer \
    --reason "now leads the review team"
```

The credentials that exceeded the old role are revoked and the reviewer
assignments that can no longer be acted on are cleared. The command refuses to
remove the last administrator unless `--override-last-admin` is passed.

### Somebody leaves

```
python -m scripts.admin disable-oidc --membership-id <id> --reason "left the company"
```

Their credentials are revoked and their reviewer assignments are cleared, so no
assessment is left waiting for them.

### A key is suspected to be leaked

```
python -m scripts.admin revoke --token-id <id> --reason "leaked in a ticket"
```

The credential stops working on the next request and the attempt to use it is
audited. The outstanding reviewer assignments of that person are cleared as
well.

### A key has to be rotated without downtime

```
python -m scripts.admin rotate --token-id <id> --days 30
python -m scripts.admin rotate --token-id <id> --days 30 --overlap-seconds 300
```

### Periodic housekeeping

```
python -m scripts.admin sweep-membership-lifecycle --organization Acme
python -m scripts.admin access-review --organization Acme --format json --output review.json
python -m scripts.admin prune-ratelimits
```

Run the sweep first (it is a report), read it, and only then run it again with
`--execute`.

## What is not covered

This repository does **not** implement the following, and no statement in this
document should be read as a claim that it does:

* **No self-service.** There is no sign-up, no password, no invitation, no
  "request access" flow. Every credential and every membership is provisioned
  by an operator with the command line, on the server.
* **No identity-provider-driven de-provisioning.** Nothing polls the identity
  provider and nothing reacts to a group change or a disable event there. The
  de-provisioning above happens when an *operator* changes the membership in
  this system, or when the sweep finds the drift.
* **No MFA, no step-up authentication, no device binding.** A credential is a
  bearer secret; whoever holds it can act as the subject.
* **No session management.** There are no sessions, no cookies, no refresh
  tokens, no idle timeouts and no revocation lists beyond the credential row
  itself. Every request is authenticated from scratch.
* **No human identity proof.** The audit actor of an operator command is
  `operator:<os user>` and is marked `actor_verified = false`; the local demo
  identity stays `identity_unverified` forever.
* **No automatic key rotation schedule.** Rotation is an operator procedure.
  The sweep reports credentials that are close to their expiry, but it does not
  rotate them.
* **No per-person policy.** The lifetime, the role and the expiry are
  organization-wide defaults with operator overrides, not per-person rules.
* **No credential escrow or break-glass process** beyond the explicit
  `--unlimited` opt-in, which is deliberately loud.

The production release gate stays closed: `ADVISORY_DEPLOYMENT_MODE=production`
refuses to start while the external release gates are unverified.
