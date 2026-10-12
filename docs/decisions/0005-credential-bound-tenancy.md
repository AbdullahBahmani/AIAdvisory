# ADR-0005 — Fail-closed scoped access before any network pilot

**Status:** Adopted for development/pilot gating, not approval for production. **Date:** 2026-10-12.

## Decision

The previous unauthenticated demonstration mode is now **explicitly opt-in** by setting `ADVISORY_DEMO_MODE=1`; otherwise all `/api/v1` routes require a random expiring revocable organization-bound API token. Tokens are provisioned with a local operator CLI, hashed at rest, and resolved to a fixed subject/role/org by the server. Reads and nested writes check the server-validated `organization_id`, reports and AI proposals cannot cross tenant boundaries, and legacy records are quarantined. The hierarchy viewer/analyst/reviewer/admin separates reads, writes, review and administrative deletion. Authenticated review logs use the credential subject. No existing AI recommendations can suppress deterministic safeguards.

This avoids inventing an OIDC provider/client secret when none is configured. It is **not** a replacement for OIDC, federated MFA, scoped human sessions or governance. Simple API bearer tokens are suitable only for tightly controlled internal experiments. The public production rollout gate remains closed. Logs exclude token contents; JSON format captures request ID/status/latency without confidential bodies.

## Alternatives considered

- Leave demonstration endpoints publicly accessible: rejected as unsafe.
- Trust `X-Organization-ID`, role or reviewer label headers: rejected; user-controlled headers do not establish authorization.
- Claim production OAuth/OIDC works without credentials or an IdP: rejected; live identity verification is not possible in this isolated build.
- Assign all legacy assessments to a default tenant: rejected; accidental cross-tenant disclosure risk.

## Residual risks and next steps

Require verified SSO/OIDC, actual organization membership provisioning and MFA; harden browser session/CSRF, server-to-server credentials, distributed rate limits, optional outbound AI processing governance, and PostgreSQL/backup/restore tests. Do not expose Docker Compose beyond loopback. See `docs/operations.md` and `docs/security.md`.
