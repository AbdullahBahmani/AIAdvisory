# ADR-0006 — OIDC membership, SQL throttling and deliberate release gate

**Status:** Accepted for controlled pilot development; NOT a production approval (2026-10-13)

## Context

Phase 5 originally issued static, locally provisioned opaque credentials. Those do not provide federated identity or MFA. The project also lacked distributed request limiting and scoped backup/deletion tooling.

## Decision

- Keep local opaque credentials for development, but allow RS256 OIDC access tokens only from an explicitly configured HTTPS issuer, audience and same-host JWKS URL. No dynamic provider discovery from user input.
- Never trust JWT role/tenant claims for authorization. Bind signed `sub` to operator-provisioned active `oidc_memberships` with a server-held organization/role. Require an explicit selected organization when multiple memberships exist.
- `ADVISORY_OIDC_REQUIRED=1` makes a pilot use OIDC only. No public self-provisioning endpoint.
- Use atomic SQL upsert per-minute request buckets for authenticated identities so limits work across PostgreSQL replicas; a separate edge limiter is required for unauthenticated traffic.
- Provide dry-run-first retention and SQLite online-backup CLI. Keep their actions strictly outside HTTP routes.
- Deliberately prevent `production` mode startup until external security, data-governance, migration/backup and deployment reviews provide evidence.

## Consequences and limitations

An IdP, MFA, web-login flow, DPA/residency compliance, key management, external monitoring and PostgreSQL drills cannot be manufactured by code changes. A tenant-approved OIDC membership does not prove live identity until a real IdP is integrated. Rate limiting at the application layer does not replace ingress DDoS protection. Manual hard purging may destroy related audit history; legal/retention review is mandatory. No GitHub runners or automatic deployments are enabled.
