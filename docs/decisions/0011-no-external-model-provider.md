# ADR-0011: No external model provider, and no data leaving the boundary

**Date:** 2026-10-20
**Status:** Accepted
**Answers:** `main-planner.md` §13 open decision "model provider and permitted data regions".

## Context

The blueprint designed the AI layer as strictly optional: a provider adapter behind an
interface, disabled by default, with a rules-only fallback that keeps the whole product
functional. The adapter exists (`app/ai/provider.py`), the provider is off by default, and the
rules-only path is the one exercised by the test suite.

What has never been settled is the governance question: *which* provider, *which* regions, and
*what data may leave the boundary*. The blueprint's rule was explicit — "Do not send a private
assessment to a provider if policy, retention, residency, or authorization is unresolved" — and
the repository enforces the mechanical half of that (per-task privacy blocks, sensitive-context
screening, explicit per-request consent, a spend cap). It cannot enforce the governance half,
because governance is not a property of code.

The distinction matters: `app/ai/provider.py` uses a regex-based sensitive-context check, and
`docs/release-verification.md` already states plainly that "a regex is not a substitute for DLP".
Shipping a provider decision without DLP, residency and contractual basis would let the product
claim a data-protection posture it does not have.

## Decision

**No external model provider is provisioned, approved or configured by this repository, and no
data leaves the machine boundary.** Specifically:

1. **The provider stays disabled by default and is not enabled in any shipped configuration.**
   `ADVISORY_AI_ENABLED` is `0` in the Compose file and unset in local development. The product
   is fully functional without it.
2. **No provider credentials are committed, and no provider is named as "the" provider.** The
   adapter is provider-agnostic; `ADVISORY_AI_MODEL` is operator-supplied and documented as such.
3. **Permitted data regions are currently empty.** No region is approved, because no provider is
   approved. The rule "sensitive data may not be sent to an external model without proven
   permission" (`PRIV-001`) is therefore enforced conservatively for every user, by default,
   everywhere.
4. **The AI feature is a local experiment only.** It is for synthetic-data experimentation and
   for the operator's own judgement, on the operator's own machine, with data the operator has
   confirmed may be sent.
5. **Cost accounting stays optional and operator-supplied.** Without operator-verified pricing
   the cost remains *unknown* — never an invented figure.

## Conditions to revise this decision

A provider may be enabled only when an organization has, in writing and independently reviewed:

- a DPA/DPIA and a contractual basis for processing assessment content with that provider;
- a documented residency/region decision and confirmation that the chosen region satisfies the
  organization's obligations;
- a retention and deletion schedule, including legal hold and sub-processor handling;
- a DLP/redaction control that is more than a regex, validated against real assessment content;
- a redaction policy for logs, telemetry and provider-side error text (note that
  `app/ai/provider.py` deliberately raises provider errors with `from None`, so the provider's
  response body cannot reach the client — that behaviour must be preserved);
- an accountable owner for the provider relationship and a spend control that is monitored, not
  merely capped.

## Consequences

- The rules-only path is the reference implementation, and it is the one under test. The
  deterministic policy engine can never be bypassed by a provider outage, a quota error or a
  prompt-injection attempt.
- The product's central safety claim is enforceable by construction: with no provider, there is
  no path by which assessment data leaves the host.
- The optional AI layer remains available for a local synthetic experiment, which is how it was
  designed and tested.

## References

- `main-planner.md` §1 (hard constraints), §7 (AI integration plan), §13 (open decisions).
- `docs/security.md`, `app/ai/provider.py`, `app/ai/service.py`, `app/services/rule_engine.py`
  (`PRIV-001`).
- ADR-0003 (guarded structured AI), ADR-0005 (credential-bound tenancy).
