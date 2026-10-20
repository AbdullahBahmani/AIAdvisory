# External notification delivery

**This is a message channel, not an approval channel.** A notification tells
a human that something happened. It never authorizes, approves, changes or
signs off anything, and it can never change an assessment, an approval or a
decision. A delivered message and a missing message have exactly the same
effect on the state of the system: none.

Until now the application only wrote notifications into the private in-app
inbox (`inbox_notifications`). A reviewer who was not looking at the
application had no way of learning that an approval was waiting for them.
This document describes the out-of-band half of planner item **P7-10**:
the notification is *generated* inside the application and *delivered* by a
dispatcher that runs out of band.

---

## 1. Two halves, deliberately separated

| | Generation | Delivery |
|---|---|---|
| Where | inside the request that produced the event | out of band (operator command, cron job or an administrator-triggered run) |
| What it does | writes the in-app inbox row and one delivery record per matching destination | performs the actual delivery attempt |
| Network | **never** opens a socket | talks to the configured destination |
| Failure | cannot fail the request | is recorded, retried with bounded backoff and surfaced as a delivery status |

The application never waits for a webhook. A slow, broken or hostile
destination therefore cannot slow down, fail or influence the request that
produced the notification - and a delivery failure can never change an
assessment, an approval or a decision.

The two halves are wired together by a SQLAlchemy listener
(`app/notifications/hooks.py`): whenever the application writes an in-app
notification, the same transaction also writes the matching delivery rows.
The in-app row and the queued deliveries are committed together, so there is
never a message the application forgot to queue.

### Events that are delivered today

| Event | Generated when |
|---|---|
| `reviewer.assigned` | an administrator assigns a reviewer to an assessment |
| `comment.added` | a comment is added to an assessment that has an assigned reviewer |
| `approval.recorded` | an approval stage is recorded for an assessment with an assigned reviewer |

A destination subscribes to a subset of these events (or to all of them).

---

## 2. Channels

Two channels are implemented. Both were chosen for a **low operational
footprint and no new project dependency**: the standard library is enough
for both, and no mail stack, agent or provider integration was added.

### 2.1 Webhook (signed JSON)

The dispatcher POSTs a small JSON document to the configured URL.

Headers:

| Header | Meaning |
|---|---|
| `Content-Type: application/json` | the body is JSON |
| `X-AIADVISORY-Event` | the event type |
| `X-AIADVISORY-Delivery-Id` | the stable delivery id |
| `X-AIADVISORY-Timestamp` | the event time (ISO 8601, UTC) |
| `X-AIADVISORY-Signature` | `sha256=<hex>` HMAC-SHA256 over `<timestamp>.<body>` |
| `Idempotency-Key` | the same delivery id, for receiver-side deduplication |

The signing secret comes from the environment variable
`ADVISORY_NOTIFICATION_WEBHOOK_SECRET` (at least 16 characters). It is
**never** stored in the database, never part of a payload and never written
to a log line. If the variable is unset, the webhook channel **fails
closed**: the delivery is recorded as failed with the reason
`secret_unavailable` rather than being sent unsigned.

A receiver verifies the message with:

```python
import hmac, hashlib

expected = "sha256=" + hmac.new(secret, f"{timestamp}.{body}".encode(), hashlib.sha256).hexdigest()
assert hmac.compare_digest(expected, signature_header)
```

### 2.2 File outbox (no mail stack)

The dispatcher appends the delivered message to a file below the operator's
outbox root:

```
$ADVISORY_NOTIFICATION_OUTBOX_DIR/<organization id>/<relative path>
```

The organization gets its own subdirectory, so one tenant's messages can
never end up in another tenant's file. The target is a *relative* path;
absolute paths, `..` traversal and symlink escapes are refused.

Each appended line is one JSON object:

```json
{"payload": { ... }, "signed_at": "...", "signature": "sha256=..."}
```

An operator wires that file (or the directory) to whatever mail
infrastructure they already run - a `sendmail` wrapper, a queue consumer, a
`mailpipe` - without this project taking on a mail stack, a mail credential
or a mail dependency. The append is **idempotent**: a delivery id that is
already in the file is not written again, so a retry after a crash between
the write and the status update cannot duplicate a message.

> The file channel is deliberately dumb. It moves bytes to disk and gets out
> of the way. Turning a file into a mail message is the operator's job, on
> the operator's infrastructure, with the operator's credentials.

### Why not SMTP?

SMTP would have added a mail stack (credentials, a relay, a retry policy, a
queue format) to a project that does not otherwise send mail. The file
outbox gives the operator the same freedom - their mail system, their
credentials, their rules - with none of that surface, and it keeps the
delivery payload auditable on disk. If an operator wants real SMTP, they
point the outbox at their own relay.

---

## 3. The payload

```json
{
  "schema": "aiadvisory.notification.v1",
  "delivery_id": "0f1c…",
  "event_type": "reviewer.assigned",
  "assessment_id": "9a3b…",
  "organization_id": "77d2…",
  "occurred_at": "2026-10-20T09:41:07.512000+00:00",
  "link_path": "/api/v1/assessments/9a3b…"
}
```

That is the whole message. The `link_path` is a **path**, not a URL: the
receiver resolves it against its own base address.

### What the payload deliberately excludes

* **Assessment content** - no title, business problem, answers, analysis
  snapshot, scores, recommendations, evidence, value scenarios or PoC data.
* **Reviewer content** - no rationale, no override reason, no comment text,
  no reviewer name and **no person identifier at all**. The recipient is
  resolved by the receiving system (from its own directory, or by calling
  back into the API with the assessment id), not by this payload.
* **Secrets** - no API token, no credential, no signing secret, and not the
  target URL either (a webhook URL often embeds a secret of the *receiving*
  system).
* **Anything actionable** - a notification carries no approval, no decision
  and no state change. Receiving one changes nothing.

The same exclusions apply to the log lines and to the operator commands: a
destination is shown by its host only, and the log line carries the event,
the channel, a closed-vocabulary reason code, an attempt counter and the
random delivery id - never an identifier of a person, an assessment or an
organization, and never an error message.

---

## 4. Security controls

### 4.1 Tenant isolation

* A destination belongs to exactly one organization.
* A delivery is created for the organization that owns the notification.
* **Before every attempt** the dispatcher re-reads the destination and
  refuses the delivery if the destination no longer belongs to the owning
  organization (`tenant_mismatch`). A delivery can therefore never leave the
  tenant that owns it, even if a row is edited by hand.
* The status API is scoped to the caller's organization, and the file
  outbox writes into a per-organization directory.

### 4.2 Egress safety (the SSRF surface)

A webhook destination is operator-configured, so it is trusted as a
*configuration* but never as a *network peer*. Every destination is
re-validated immediately before a request is made
(`app/notifications/egress.py`):

| Control | Behaviour |
|---|---|
| Scheme | HTTPS is required. Plain HTTP is accepted only for an explicitly enabled loopback development target (`ADVISORY_NOTIFICATION_ALLOW_INSECURE_LOOPBACK=1`) and only for `http://`. |
| Credentials in the URL | refused (`credentials_in_url`) - a userinfo part would leak into logs and redirects. |
| Address resolution | the hostname is resolved and **every** returned address is classified. A single private, link-local, multicast, shared, reserved or unspecified address refuses the whole target, so a partially private resolution cannot slip through. |
| Metadata addresses | `169.254.169.254` and every other link-local address are refused (`blocked_address`), as are `10/8`, `172.16/12`, `192.168/16`, `100.64/10` (CGNAT), `::1`, `0.0.0.0`, `240/4` and the IPv4-mapped forms of all of them. |
| DNS pinning | the socket is connected to a **validated address** and TLS is still validated against the requested hostname, so a second DNS answer cannot move the connection somewhere else. |
| Redirects | followed only inside the original origin (scheme, host and port) and only after the new target passes the same checks. A redirect to another host is refused (`redirect_refused`), never followed. At most three hops. |
| Timeouts | connect 5 s, read 10 s (bounded). |
| Response size | capped at 64 KiB; a larger response fails the delivery (`response_too_large`). |
| Credentials | never sent: the request carries only the minimal payload and the signature. |

The operator CLI refuses an obviously unsafe target *before* it is stored
(and never resolves a name while doing so, so configuring a destination
never turns into a network call). The same checks run again at delivery
time, because a name that was safe yesterday may not be safe today.

### 4.3 A notification can never authorize anything

The delivery path only ever writes to `notification_deliveries` and to the
destination. It never touches `assessments`, `approval_votes`, `reviews`,
`audit_events` or the provenance ledger, and there is no code path from a
received message back into the application. The API exposes no endpoint
that a webhook receiver could call to act on a notification.

---

## 5. Retry, idempotency and ordering

* **Bounded.** A delivery is attempted at most
  `ADVISORY_NOTIFICATION_MAX_ATTEMPTS` times (default 5, clamped to 1..10).
  After the last attempt the delivery becomes terminally `failed`.
* **Backoff.** The next attempt is scheduled with exponential backoff:
  30 s, 60 s, 120 s, ... capped at one hour.
* **Recorded, never swallowed.** Every attempt updates the delivery row:
  `status`, `attempts`, `next_attempt_at` and a `last_error` reason code
  from a closed vocabulary (`http_500`, `timeout`, `blocked_address`,
  `redirect_refused`, `tenant_mismatch`, `secret_unavailable`, ...). The
  reason is a code, never a message, a URL or a stack trace.
* **Idempotent.** The delivery id is generated once, when the message is
  queued, and every attempt reuses it and re-sends the **byte-identical**
  payload (the payload is stored with the delivery). A receiver can
  deduplicate on the delivery id or on the `Idempotency-Key` header. A
  delivery that already succeeded is never sent again, and the file channel
  never appends the same delivery id twice.
* **Ordered.** Deliveries carry a monotonic database sequence number. For one
  destination the dispatcher works through that sequence in order and stops
  at the first message that could not be delivered, so the retry of an
  approval request is still delivered **before** the decision that follows
  it. Ordering is per destination, so one broken destination never blocks
  another tenant's traffic.
* **Permanent versus transient.** A refused destination (blocked address,
  missing secret, cross-tenant destination) is not retried: the
  configuration has to change first. A connection error, a timeout and a 5xx
  are retried.

---

## 6. Operator runbook

Configuration and inspection are operator commands, in the spirit of
`scripts/admin.py`. There is **no** HTTP endpoint that configures a
destination or hands out a secret.

```bash
# Configure a destination for one organization
python -m scripts.notifications configure-destination \
    --organization "Example Co" --channel webhook \
    --target https://hooks.example.org/aiadvisory

# Only the events you care about (default: every event)
python -m scripts.notifications configure-destination \
    --organization "Example Co" --channel file \
    --target messages.jsonl --events reviewer.assigned,approval.recorded

# Inspect (the target is masked unless --reveal-target is given)
python -m scripts.notifications list-destinations --organization "Example Co"
python -m scripts.notifications list-deliveries --organization "Example Co" --status failed

# Deliver the queued messages - a dry run unless --execute is given
python -m scripts.notifications dispatch --organization "Example Co"
python -m scripts.notifications dispatch --organization "Example Co" --execute

# Re-queue one failed message (same delivery id) - dry run by default
python -m scripts.notifications retry --delivery-id <id> --execute

# Remove failed delivery rows - dry run by default
python -m scripts.notifications purge-failed --organization "Example Co" \
    --before 2026-09-01T00:00:00+00:00 --execute
```

**Everything that sends, changes or deletes is a dry run by default** and
needs an explicit `--execute`. The dry run prints what it would do and
changes nothing.

Run the dispatcher from cron if you want deliveries without an operator
present:

```cron
*/2 * * * * cd /srv/aiadvisory && DATABASE_URL=... \
  python -m scripts.notifications dispatch --execute >> /var/log/aiadvisory-notify.log 2>&1
```

### HTTP endpoints (read-only apart from the explicit trigger)

| Endpoint | Who | What |
|---|---|---|
| `GET /api/v1/notifications/destinations` | organization admin | the tenant's destinations (host only, never the full target) |
| `GET /api/v1/notifications/deliveries` | organization admin | the tenant's delivery status |
| `POST /api/v1/notifications/dispatch` | organization admin | deliver the queued messages; `{"dry_run": true}` by default |

### Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `ADVISORY_NOTIFICATION_WEBHOOK_SECRET` | unset | the webhook signing secret (≥ 16 characters). Unset disables the webhook channel (fail closed). |
| `ADVISORY_NOTIFICATION_OUTBOX_DIR` | `var/notification-outbox` | the root of the on-disk outbox |
| `ADVISORY_NOTIFICATION_MAX_ATTEMPTS` | `5` | the attempt budget per delivery (1..10) |
| `ADVISORY_NOTIFICATION_ALLOW_INSECURE_LOOPBACK` | unset | `1` permits a plain-HTTP loopback development target. **Never** set this outside a local demo. |

---

## 7. Schema

Migration `0013_notification_delivery` adds two tables:

* `notification_destinations` - the operator-configured destinations of one
  organization (unique per organization, channel and target).
* `notification_deliveries` - the queued outbound messages, with the stable
  delivery id, the ordering sequence, the attempt state and the exact
  payload that was signed.

> The revision id of the migration is provisional. It is chained onto
> `0010_index_and_constraint_hardening` on this branch and is expected to be
> re-pointed into the chain when the branch is integrated. The table names
> and their shape do not depend on the position in the chain.

Both tables are included in the backup, retention, export and deletion
reviews: a delivery row references the notification it came from and is
removed with it (foreign key, `ON DELETE CASCADE`).

---

## 8. What this does **not** do

* It is **not** a mail service, a chat client or a push service.
* It does **not** tell the delivery system *who* to notify - the payload
  carries no person. The receiving system resolves the audience.
* It does **not** guarantee delivery. A delivery can fail, be retried a
  bounded number of times and end up `failed`. The status is visible; the
  guarantee is not.
* It does **not** provide alerting, dashboards or delivery metrics in
  `/metrics`. The operator command and the log lines are the status
  surface today.
* It does **not** provide an inbound webhook, a reply channel, a two-way
  conversation or any way for a receiver to act on a notification.
* It does **not** replace a mail provider, an identity provider, a
  certificate, a reverse proxy or any other production control. Those
  release gates stay open.
* The default test run never contacts an external host: the webhook tests
  use a local HTTP server on `127.0.0.1`.

---

## 9. Tests

`tests/test_notifications.py` covers, against SQLite and PostgreSQL:

* a generated notification produces an in-app row **and** an outbound
  delivery record, and generation opens no socket;
* a failing external delivery does not fail the originating request and is
  recorded as `failed` with a reason code;
* retries are bounded, keep the delivery id and re-send a byte-identical
  payload; a delivered message is never sent twice;
* the payload carries the minimum and nothing else - no title, no rationale,
  no comment, no token - and the signature verifies;
* a destination that belongs to another organization is refused and nothing
  is sent;
* link-local, private, loopback and metadata addresses are refused, by the
  validator, by the operator CLI and by the dispatcher;
* a redirect to another host is not followed;
* the file channel writes one message per organization and never
  duplicates one;
* every destructive operator command is a dry run without `--execute`.
