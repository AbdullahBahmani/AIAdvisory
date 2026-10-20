# Per-organization approval policy

**Status: implementation note, not a production release approval.** The policy
can only make the platform's review gates *stricter*. It cannot authorize
anything, and it cannot weaken a hard safety rule.

## The problem

The review chain has three ordered stages — `technical`, `security`,
`business` — and the platform used to answer "who may approve this stage?"
with one fixed rule: a `reviewer` may approve the technical and security
stages, an `admin` approves the business stage. A security team that wants an
administrator (or a smaller group) to sign off the security stage had no way
to express that.

The policy gives each organization that control **without** letting it weaken
the platform's safety rules.

## The document

```json
{
  "stage_roles": {
    "technical": ["reviewer", "admin"],
    "security": ["admin"],
    "business": ["admin"]
  },
  "allow_self_approval_of_contributed_stage": false,
  "allow_same_person_two_stages": false,
  "require_distinct_reviewers": true,
  "forbid_contributor_approval_of_any_stage": false
}
```

* `stage_roles` — which roles may approve which stage. It may only **narrow**
  the platform default (`technical`/`security`: `reviewer`, `admin`;
  `business`: `admin`). Adding `analyst`, dropping the `admin` fallback or
  omitting a stage is refused.
* `allow_self_approval_of_contributed_stage` — a reviewer may not approve the
  stage they contributed to. **Hard rule: always `false`.**
* `allow_same_person_two_stages` — one person may not hold two stages of the
  same chain. **Hard rule: always `false`.**
* `require_distinct_reviewers` — the three stages need three different
  people. **Hard rule: always `true`.**
* `forbid_contributor_approval_of_any_stage` — a tightening knob: a
  contributor to the assessment may not approve *any* stage of it.

The API is `GET`/`PUT /api/v1/workspace/approval-policy`. Reading it needs a
verified member of the organization; writing it needs a verified
**administrator**. The synthetic demo identity can neither read nor write it
(`403`).

## What it may not do

The policy is organization configuration. It is **not** a way to relax the
platform's safety rules:

* The human-only, privacy, approval-required and severity gates of
  `app/services/rule_engine.py` and `app/services/recommendation.py` are not
  part of the policy document. A document that mentions them
  (`human_finalization_required`, `privacy_review_required`,
  `approval_required`, `severity_gate`, …) is **rejected** with `422`, not
  stored and not ignored.
* The separation-of-duties defaults are not configurable. Setting
  `allow_same_person_two_stages`, `allow_self_approval_of_contributed_stage`
  to `true` or `require_distinct_reviewers` to `false` is **rejected** with
  `422`.
* Unknown keys are rejected (`422`), so a typo never silently disables a
  control.
* A stage may never accept a role weaker than the platform default.

Every refusal is explicit: nothing is stored, and the policy in force stays
the previous one.

## Separation of duties (the default)

The default is **no**:

1. one person may not hold two stages of the same chain (the platform check
   in `app/api/workspace.py`, unchanged);
2. the three stages need three different people (`require_completed_approvals`,
   unchanged);
3. the reviewer who contributed to a stage may not approve that stage.

Point 3 needs a definition of "contributed", and the platform records exactly
one contribution per assessment today: the **assigned reviewer** performs the
technical (FDE) review. So the assigned reviewer may not approve the
technical stage. There is no server-side record of a security or business
contribution — the assessment `owner` is a free-text label and not an
identity — so the rule has no effect on those stages until the platform
records such a contribution. That is a documented limitation, not a silent
gap.

## What a policy change does

A policy change is audited and it invalidates the approvals it affected:

1. the new document is validated (a rejected document changes nothing);
2. the policy row is stored with a new version;
3. every approval of the organization whose **stage rules changed** — or whose
   actor no longer satisfies the new requirement of that stage — is
   **invalidated**: it stops counting towards the chain, and the stages after
   it stop counting as well, so the assessment has to be approved again;
4. the change is written to the organization audit ledger
   (`approval.policy_changed`) with the actor, the new version and the
   invalidated approvals.

Votes are bound to the policy version that applied to them
(`approval_vote_bindings`), which is what makes "the rules that were applied
to this approval" answerable. Votes cast before the platform recorded
bindings are looked up by their actor's role; an unknown role never satisfies
a rule, so the vote is invalidated when the stage rules changed.

The chain is evaluated against the **current** policy, so a tightened gate
also applies to approvals cast earlier.

## Storage

The policy lives in `approval_policies` (one row per organization, with a
version) and the vote bindings in `approval_vote_bindings`. Both are created
by migration `0012_scim_provisioning`. The organization-scoped audit ledger
(`organization_audit_events`) holds the change history; the assessment ledger
is untouched.

## Limits

* The role vocabulary is fixed by the platform (`viewer`, `analyst`,
  `reviewer`, `admin`). A policy may narrow it per stage, not extend it.
* A policy is per organization. There is no global or inherited policy, and
  one organization's policy never affects another organization's chain.
* The policy does not change the chain itself: the stages, their order and
  the revision binding stay where they are.
