# SCIM 2.0 provisioning

**Status: implementation note, not a production release approval.** SCIM
provisioning creates and deactivates identities. It is an *additional*
automated path next to the manual one (`python -m scripts.admin grant-oidc`);
it never replaces it, and it does not authorize anything by itself.

## What it is for

The OIDC path requires an operator to pre-provision an `oidc_memberships` row
before a person can sign in: a valid OIDC token has **no** tenant privileges
until a local operator provisioned an active membership for it. SCIM is how an
enterprise directory does that automatically.

The provisioned identity is exactly the same record as the manual one — an
`oidc_memberships` row — so the OIDC path, the workspace member list, the
reviewer assignment and the approval chain behave identically whether the
membership came from an operator or from the directory.

## The endpoint

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/scim/v2/Users` | List the users of the SCIM organization (`filter`, `startIndex`, `count`) |
| `POST` | `/scim/v2/Users` | Provision a new user |
| `GET` | `/scim/v2/Users/{id}` | Read one user |
| `PUT` | `/scim/v2/Users/{id}` | Replace a user (role, activation, directory attributes) |
| `PATCH` | `/scim/v2/Users/{id}` | Patch a user (SCIM `Operations` array) |
| `DELETE` | `/scim/v2/Users/{id}` | **Deactivate** a user (see below) |
| `GET` | `/scim/v2/Groups` | List the groups of the organization |
| `GET` | `/scim/v2/Groups/{id}` | Read one group |

Responses use `application/scim+json`. Collections use the SCIM list envelope
(`schemas`, `totalResults`, `startIndex`, `itemsPerPage`, `Resources`) and
failures use the SCIM error schema (`schemas`, `status`, `detail`, and
`scimType` where one applies: `invalidValue`, `invalidFilter`, `invalidPath`,
`invalidSyntax`, `uniqueness`, `mutability`).

### Supported filter grammar

`attribute eq "value"` for `userName`, `externalId`, `id` and `active`,
optionally combined with `and`. Anything else is rejected with
`invalidFilter` — a filter is never silently ignored, so a directory never
believes it filtered something it did not.

`startIndex` is 1-based, `count` defaults to 100 and is capped at 500.

## Authentication

The endpoint provisions identities, so it must not be reachable with a weak or
borrowed credential.

* **A dedicated bearer token.** `ADVISORY_SCIM_TOKEN` (at least 32 characters)
  is the only credential the endpoint accepts. It is *not* a user API
  credential (`aia_…`), *not* an OIDC access token and *not* the synthetic
  `local_demo` identity.
* **Constant-time comparison.** The presented token and the configured token
  are hashed with SHA-256 and compared with `secrets.compare_digest`, so the
  comparison runs over the same number of bytes and has no early exit. Neither
  the token nor its length can be recovered from the response time.
* **No demo bypass.** `ADVISORY_DEMO_MODE=1` does not open the endpoint: a
  request without the SCIM token is rejected with `401` even in the local
  demo. The ordinary user routes keep their existing demo behaviour; the SCIM
  credential works in the local demo mode as well.
* **One organization.** `ADVISORY_SCIM_ORGANIZATION` names the single
  organization this credential serves. The organization is **never** taken from
  the request body, so a SCIM request cannot address another tenant: a user
  of another organization is a `404`, not a hint.
* **Not configured means not available.** Without both the token and the
  organization, the endpoint answers `503` and provisions nothing.
* **Rate limited.** The provisioning service account uses the platform's
  SQL-backed per-minute limit; use an ingress limit as well.

### Configuration

| Variable | Meaning | Default |
| --- | --- | --- |
| `ADVISORY_SCIM_TOKEN` | The dedicated SCIM bearer token (≥ 32 characters) | unset (endpoint disabled) |
| `ADVISORY_SCIM_ORGANIZATION` | The organization this credential provisions | unset (endpoint disabled) |
| `ADVISORY_SCIM_ISSUER` | The issuer of the provisioned memberships | `ADVISORY_OIDC_ISSUER` |
| `ADVISORY_SCIM_ALLOW_ADMIN` | `1` lets this credential grant `admin` | unset (`admin` cannot be granted) |
| `ADVISORY_SCIM_ROLES` | Comma separated roles the credential may grant | `viewer,analyst,reviewer` |
| `ADVISORY_SCIM_DEFAULT_ROLE` | Role used when a request names none | `viewer` |
| `ADVISORY_SCIM_GROUP_ROLE_MAP` | JSON object mapping a group name to a role | unset (groups grant nothing) |

## Mapping

| SCIM | Stored identity |
| --- | --- |
| `externalId` | The IdP subject: `oidc_memberships.subject`, the value the identity provider puts in the OIDC `sub` claim. Immutable. |
| `userName` | The directory login. It resolves to the same membership subject and is stored as the directory attribute (`scim_users.user_name`, unique per organization). |
| `groups` | A **projection** of the organization and the role: `role-<role>` plus the organization group. |
| `active` | `oidc_memberships.active`. |
| `name`, `emails` | Directory attributes, stored in the `scim_users` overlay. |

`externalId` is the canonical identity key. If a directory sends only
`userName`, it is used as the subject. `userName` is resolved against both the
stored directory name and the membership subject, so `userName eq "<subject>"`
finds the membership as well.

### Roles and escalation

The role of a provisioned user is only ever taken from an **allowlist**:

1. an explicit role attribute — `urn:aiadvisory:params:scim:User:1.0:role`
   (a plain `role` attribute is accepted as well);
2. a SCIM group, but only through an operator-configured group-to-role map
   (`ADVISORY_SCIM_GROUP_ROLE_MAP`); **without that map a group grants
   nothing**;
3. otherwise the configured default role (`viewer`).

A role outside the allowlist is refused with `invalidValue`, and `admin` is
refused unless the deployment set `ADVISORY_SCIM_ALLOW_ADMIN=1` explicitly.
An operator that lists `admin` in `ADVISORY_SCIM_ROLES` without the opt-in
still cannot grant it.

**A SCIM-provisioned user can never escalate their own role.** Roles are only
written by the SCIM service credential (a server-side secret that a person
does not hold), the ordinary user API has no endpoint that changes a role,
and the SCIM endpoint is not reachable with a user credential. Every role
change is audited.

### Group-to-role mapping is opt-in (documented decision)

Mapping a directory group to a role is a policy decision, not a technical
one, so it is **off by default**: a group is a read-only projection of the
organization and the role, and a group in a provisioning request never grants
anything. An operator who wants group-driven roles configures an explicit map;
the mapped role still has to be inside the grantable allowlist, and the map
cannot name an unknown role.

## Deactivation, not deletion

**A SCIM `DELETE` deactivates the membership; it never deletes it.** The
membership is referenced by review history, reviewer assignments and audit
events; deleting the row would orphan that history and make past decisions
unexplainable. The endpoint therefore sets `active = false`, keeps the row
and returns `204`. The user stays readable through SCIM with
`"active": false`, and the deactivation is recorded in the audit trail.

Hard deletion of an identity remains an operator decision, outside SCIM.

## Audit trail

Every SCIM mutation is written through the single audit gate
(`app/services/audit.py`) to the organization-scoped ledger
(`organization_audit_events`), with the server-side actor — the provisioning
service account `scim_provisioner`, `actor_verified = true` — never anything
from the request body. Event types: `scim.user_created`, `scim.user_updated`,
`scim.user_deactivated`. The details record the subject, the role and the
previous role/activation, so a reviewer can reconstruct who changed what.

Reads are not audited: a directory polls, and a poll is not an event an
auditor needs.

## Storage

Migration `0012_scim_provisioning` adds four tables and changes no existing
one:

| Table | Purpose |
| --- | --- |
| `scim_users` | The SCIM attribute overlay of a provisioned membership (unique per membership and per organization/user name). |
| `organization_audit_events` | The organization-scoped audit ledger (the assessment ledger requires an assessment; these events are not about one). |
| `approval_policies` | The per-organization approval policy and its version. |
| `approval_vote_bindings` | Which policy version applied to an approval vote, the role the actor held, and whether a later policy change invalidated it. |

Apply it with `python -m alembic upgrade head` before enabling SCIM.

## Limits

* Only the subset above is implemented. There is no `POST`/`PATCH`/`DELETE` on
  groups, no `ServiceProviderConfig` discovery endpoint, no bulk operation and
  no SCIM event subscription.
* The role vocabulary is fixed by the platform (`viewer`, `analyst`,
  `reviewer`, `admin`); a directory cannot invent a role.
* SCIM provisions *memberships*. It does not create API credentials, does not
  create organizations and does not grant access to any tenant other than the
  one the credential is scoped to.
