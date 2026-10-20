# Metrics, alerting and the local check command

**Status: operator aid, not a validated observability system.** The metrics below are a
small, dependency-free set of counters and gauges that an operator can scrape and check
from a host. They do **not** replace the externally operated monitoring, dashboards and
provider-spend controls that the release gate in `docs/operations.md` still requires. The
thresholds in this document are **suggestions for an operator to tune**, not validated
SLOs: this repository has no measured traffic baseline, so no number here is a promise
about the system's behaviour.

## What is exposed

`GET /metrics` returns a **Prometheus text exposition** document
(`Content-Type: text/plain; version=0.0.4; charset=utf-8`). The format is written by hand
in `app/telemetry.py`; the project has no metrics-client dependency.

The endpoint is designed to be scraped by Prometheus or any compatible text scraper, and to
be read by the local check command described below.

### Authentication decision

`/metrics` is **authenticated** and **tenant scoped**. It uses the same dependency as the
rest of the API (`authenticate_request`, i.e. a server-provisioned credential with at least
the `viewer` role) and every database-derived gauge is scoped to the caller's organization.

Reasoning:

* `/health` and `/ready` are intentionally public because they carry no business
  information. `/metrics` is different: it carries tenant-derived business aggregates
  (assessment and review counts, FDE override counts, AI token and cost totals, and the
  rate at which safety rules fire). Leaving it unauthenticated would give an anonymous
  caller a cross-tenant view of a customer's usage and would make `/metrics` an
  enumeration oracle in a multi-tenant pilot.
* The endpoint is a `GET`, so the `viewer` role is enough, and it goes through the same
  per-principal rate limiter as every other authenticated route, so a scrape cannot be
  used to exhaust the database.
* **Demo mode is not widened.** `authenticate_request` already grants the explicit local
  demo principal when `ADVISORY_DEMO_MODE=1`; using that same dependency means the demo
  behaviour of `/metrics` is exactly the demo behaviour of every other route: reachable
  without a token in the local synthetic demo, and unreachable without a credential in
  every other mode. The demo bypass is never extended to a new surface.
* In demo mode the metrics are not tenant scoped, because the demo principal has no
  organization and the demo deployment has no tenant separation at all. In authenticated
  mode, assessments, AI runs and reviews are filtered by the caller's organization;
  legacy rows without an organization stay invisible, exactly as they are in the rest of
  the API.

Operational consequences:

* A Prometheus scraper must present a credential, for example with
  `authorization.credentials_file`. A `viewer` credential of the organization being
  observed is enough. There is no separate "metrics secret": `/metrics` uses exactly
  the same API credentials as the rest of the API.
* `python -m scripts.metrics_check --token <credential>` does the same for the local
  check command.
* Do not publish `/metrics` on a separate unauthenticated port or ingress path. If an
  operator wants an anonymous scrape, put a proxy in front of it that authenticates the
  scraper — do not change the application.

## Metric families

Every family below is a gauge or a counter in the Prometheus sense. `aiadvisory_http_*`
families are collected **in the API process**: they are reset on restart and are not
shared between replicas. The database-derived families are durable and are the ones to
alert on.

| Metric | Type | Labels | Meaning |
| --- | --- | --- | --- |
| `aiadvisory_http_requests_total` | counter | `route`, `method`, `status_class` | Requests served by this process, by route template, method and status class (`2xx`, `3xx`, `4xx`, `5xx`). |
| `aiadvisory_http_request_duration_seconds` | histogram | `route`, `method` (plus `le` on the buckets) | Request latency. Buckets: 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10 seconds and `+Inf`. Emits `_bucket`, `_sum` and `_count`. |
| `aiadvisory_build_info` | gauge | `version` | Always `1`. The version of the running application. |
| `aiadvisory_policy_version_info` | gauge | `policy_version` | Always `1`. The policy version of the running deterministic engine (currently `rules-v2`). |
| `aiadvisory_ai_prompt_version_info` | gauge | `prompt_version` | Always `1`. The prompt version of the running AI advisory prompts (currently `3.0`). |
| `aiadvisory_assessments_total` | gauge | `status` | Stored assessments by lifecycle status (`draft`, `analyzed`, `in_review`, `finalized`, `deleted`, …). |
| `aiadvisory_analyses_total` | gauge | — | Assessments whose stored analysis is current (not invalidated by a later edit). |
| `aiadvisory_rule_hits_total` | gauge | `rule_id` | Policy rule hits recorded in the current analyses, by rule id. |
| `aiadvisory_rule_firing_analyses` | gauge | `rule_id` | Current analyses in which a rule fired at least once, by rule id. This is the safety-relevant series: a rule that silently stops firing shows up as a value of `0`. |
| `aiadvisory_analyses_policy_version` | gauge | `policy_version` | Current analyses grouped by the policy version that produced them. A non-current value means stored analyses were produced by an older policy. |
| `aiadvisory_reviews_total` | gauge | — | Stored FDE reviews, all revisions. |
| `aiadvisory_fde_overrides_total` | gauge | — | FDE reviews that record an override of the deterministic recommendation (a review with a non-empty override reason). |
| `aiadvisory_ai_runs_total` | gauge | `feature`, `provider`, `model` | Stored AI proposal runs. |
| `aiadvisory_ai_input_tokens_total` | gauge | — | Input tokens reported by the provider, summed over the runs that reported a token count. |
| `aiadvisory_ai_output_tokens_total` | gauge | — | Output tokens reported by the provider, summed over the runs that reported a token count. |
| `aiadvisory_ai_runs_tokens_unknown_total` | gauge | — | AI runs whose provider token counts are unknown. These runs are **excluded** from the two token totals. |
| `aiadvisory_ai_cost_usd_total` | gauge | — | Total of the **operator-supplied** costs of the priced AI runs. |
| `aiadvisory_ai_cost_priced_runs_total` | gauge | — | AI runs that carry a usable operator-supplied cost. |
| `aiadvisory_ai_cost_unpriced_runs_total` | gauge | — | AI runs **without** a usable operator-supplied cost. Reported as unpriced. |
| `aiadvisory_metrics_database_up` | gauge | — | `1` when the database-derived metrics could be collected, `0` when the database could not be reached. The database-derived families are then absent, not zero. |
| `aiadvisory_metrics_scrape_timestamp_seconds` | gauge | — | Unix timestamp of the scrape. Use it to detect a stale scrape. |

### The cost split is reported honestly

Cost is operator supplied: the application only stores a cost when an operator configured
a price (`ADVISORY_AI_INPUT_USD_PER_MILLION`, `ADVISORY_AI_OUTPUT_USD_PER_MILLION`,
`ADVISORY_AI_PRICING_SOURCE`). The metrics therefore report:

* the sum of the costs the operator supplied (`aiadvisory_ai_cost_usd_total`),
* how many runs are priced (`aiadvisory_ai_cost_priced_runs_total`),
* how many runs are unpriced (`aiadvisory_ai_cost_unpriced_runs_total`).

An unknown cost is **never estimated and never silently omitted**: it is counted as
unpriced. A run whose stored cost cannot be parsed as a non-negative number is also
counted as unpriced, because a malformed cost is an unknown cost. A cost total is
therefore a lower bound on the money spent, and the priced/unpriced split says by how
much. The same rule applies to token counts: a run with an unknown token count is
excluded from the totals and counted in `aiadvisory_ai_runs_tokens_unknown_total`.

## Redaction rules applied to the metrics output

The endpoint is an information-leak surface, so the following rules are enforced in code
(`app/telemetry.py`), not by convention:

1. **No content, ever.** No assessment title, business problem, workflow, task, answer,
   evidence, rationale, override reason, risk description, report text, AI prompt,
   provider output or error message appears in the output. The metrics are counts, sums
   and labels only.
2. **No identifiers.** No assessment id, organization id, user id, subject, reviewer
   name, credential, API token or request id appears in the output. The per-request id
   that the request log and the `X-Request-ID` response header carry is deliberately
   **not** exported as a metric: a request id is a per-request identifier, and using it
   as a label would be unbounded cardinality.
3. **Low-cardinality labels only.** Labels come from closed, code-controlled
   vocabularies: route templates, HTTP methods, status classes, assessment status
   values, rule ids, AI feature/provider/model names, policy and prompt versions. Every
   label value is pattern-checked against a fixed regular expression and the number of
   distinct values per label is capped (16 to 64 depending on the label); anything else
   is replaced by the literal `other`. Unmatched request paths collapse to the route
   template `unknown`, so a URL can never become a label.
4. **No path echo.** Route labels are the *route templates* (`/api/v1/assessments/
   {assessment_id}`), never the requested path and never the query string.
5. **No error text.** There is no metric carrying an exception message, a stack trace, a
   provider error or a database error string. Failures are visible as counts (status
   classes, `aiadvisory_metrics_database_up`).
6. **Tenant scoping.** In authenticated mode the database-derived families are filtered
   by the caller's organization, so one tenant cannot observe another tenant's usage.
7. **The request log is unchanged.** The JSON request log still records only the route
   template, method, status, request id and elapsed milliseconds.

## Alert signals and suggested thresholds

These are **suggestions for an operator to tune**, not validated SLOs. They are
implemented by `python -m scripts.metrics_check`, which prints `PASS`/`WARN`/`FAIL` per
check and exits non-zero when a check fails.

| Signal | Suggested threshold | Severity | Notes |
| --- | --- | --- | --- |
| `/health` not reachable or not `status: ok` | any occurrence | FAIL | The liveness probe is the cheapest possible check. |
| `/ready` not `200` | any occurrence | FAIL | The database or the migrations are not ready. |
| `/metrics` not reachable, not `200`, or not parsable | any occurrence | FAIL | Includes the case where the endpoint asks for a credential (`--token`). |
| `aiadvisory_metrics_database_up == 0` | any occurrence | FAIL | The database-derived families are missing, so the durable signals are blind. |
| 5xx share of requests | > 5 % FAIL, > 1 % WARN | FAIL / WARN | Suggested minimum sample size 20 requests; below that the ratio is not judged (WARN). |
| 4xx share of requests | > 25 % | WARN | Usually credential or client errors rather than a service fault; never a FAIL by default. |
| A watched safety rule stopped firing | a rule that fired in the baseline now fires in 0 analyses | FAIL | Watched rules: `SAFE-001`, `PRIV-001`, `PRIV-002`, `ACT-001`, `ACT-002`, `HUM-001`. This needs a baseline, because a rule may legitimately not fire when no input triggers it. |
| A rule id outside the documented format | any occurrence | FAIL | Reported in the `other` bucket; such a rule would be invisible to the check above. |
| Unpriced cost share | > 50 % of AI runs | WARN | Cost visibility is missing for those runs. |
| Unpriced cost share growth | more than +25 points against the baseline | WARN | Suggests that pricing configuration was lost. |
| Override rate | > 50 %, or a change of more than ±20 points against the baseline | WARN | A sudden change in the FDE override rate is a meaningful signal. Needs at least 5 reviews to be judged. |

The baseline is a small JSON file the check command writes with `--update-baseline`
(default `~/.local/state/aiadvisory/metrics-baseline.json`). Without a baseline the
change-detection checks report `WARN` ("no baseline available") rather than pretending
that nothing changed.

### Running the local check

```bash
# against a local demo (no credential needed in demo mode)
python -m scripts.metrics_check --base-url http://127.0.0.1:8000

# against an authenticated pilot, and record a baseline for the change checks
# (the token is an ordinary API credential; there is no separate metrics secret)
python -m scripts.metrics_check --base-url https://api.example.internal \
    --token "$ADVISORY_API_TOKEN" --update-baseline

# machine readable
python -m scripts.metrics_check --json
```

Exit status is `0` when no check failed, `1` when at least one check failed. A `WARN`
does not fail the command. A cron entry such as

```cron
*/15 * * * * cd /srv/aiadvisory && .venv/bin/python -m scripts.metrics_check --json \
  >> /var/log/aiadvisory/metrics-check.log 2>&1
```

is a reasonable *interim* arrangement. It is not a substitute for an external monitoring
stack with retention, on-call routing and a real availability objective.

## What is deliberately not instrumented

* **No content-derived metrics.** A metric such as "number of assessments mentioning a
  given term", or any per-task or per-signal breakdown, would require reading assessment
  content. It is not implemented, and it must not be added by logging content.
* **No per-tenant or per-user labels.** They would be unbounded, and a label is a
  permanent, queryable copy of the data it names.
* **No error message or exception metric.** It would carry customer data and free text
  into a scrapeable surface.
* **No request-id metric and no request-id label.** The request id exists to correlate a
  log line with a response; it is not a metric label.
* **No latency per individual request.** Only aggregated histograms by route template.
* **No provider-spend integration.** The application only knows the operator-supplied
  price; it cannot reconcile a bill with a provider invoice. Provider-spend monitoring
  remains an open release-gate item.
* **No cross-replica aggregation.** The HTTP counters live in the process. A multi-replica
  deployment needs a shared or per-replica scrape with a sum, and the durable gauges for
  alerting.
* **No histogram of business data, no distribution of assessment scores.** A score
  distribution would describe the customer's assessments.
* **No database-derived cost estimate.** An unpriced run stays unpriced.

## Known limitations

* The rule-hit and review metrics are computed by reading the stored analysis snapshots
  and review rows at scrape time. The cost of a scrape therefore grows with the number of
  analysed assessments. At pilot scale this is small; at high volume a materialised
  counter table would be needed, and adding one is a schema change outside the scope of
  this item.
* `aiadvisory_http_*` counters are per process and reset on restart. A restart looks
  like a traffic drop to an alerting rule that uses `rate()` over a short window.
* The rule vocabulary, the policy version and the prompt version are code constants.
  A deployment that runs two versions at once (during a rollout) will report both; that
  is intended.
* These metrics measure the *service*, not the *advice*. A rule that fires on every
  assessment and a rule that never fires are both plausible; only the evaluation corpus
  (`python -m app.evaluation`) judges whether the policy still behaves. The metrics make
  a sudden change visible; they do not certify correctness.
