"""Local operator provisioning; no public token-management endpoints.

Examples:
  python -m scripts.admin issue --organization Example --subject alice --role admin
  python -m scripts.admin revoke --token-id <id>
"""

import argparse
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select

from app.db.models import ApiCredentialRow, OidcMembershipRow, OrganizationRow, RequestLimitRow
from app.db.session import SessionLocal
from app.security import ROLE_LEVEL, digest_token, make_token


def main():
    parser = argparse.ArgumentParser(
        description="Provision scoped API tokens (requires upgraded database)"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    issue = sub.add_parser("issue", help="Issue a credential and display it exactly once")
    issue.add_argument("--organization", required=True)
    issue.add_argument("--subject", required=True)
    issue.add_argument("--role", choices=list(ROLE_LEVEL), required=True)
    issue.add_argument("--days", type=int, default=30)
    revoke = sub.add_parser("revoke", help="Revoke an existing credential by ID")
    revoke.add_argument("--token-id", required=True)
    create_org = sub.add_parser(
        "create-org", help="Provision an organization without issuing API token"
    )
    create_org.add_argument("--organization", required=True)
    member = sub.add_parser("grant-oidc", help="Trusted operator adds identity provider membership")
    member.add_argument("--organization", required=True)
    member.add_argument("--issuer", required=True)
    member.add_argument("--subject", required=True)
    member.add_argument("--role", choices=list(ROLE_LEVEL), required=True)
    disable = sub.add_parser("disable-oidc", help="Disable OIDC membership by ID")
    disable.add_argument("--membership-id", required=True)
    # The parser handle is not needed: no prune-specific arguments are registered.
    sub.add_parser("prune-ratelimits", help="Delete request counters older than one day")
    args = parser.parse_args()
    with SessionLocal.begin() as db:
        if args.command == "issue":
            if (
                not (1 <= args.days <= 365)
                or not (1 <= len(args.subject) <= 200)
                or not (1 <= len(args.organization) <= 200)
            ):
                parser.error(
                    "Days must be 1..365 and organization/subject must have 1..200 characters"
                )
            org = db.scalar(
                select(OrganizationRow).where(OrganizationRow.name == args.organization)
            )
            if org is None:
                org = OrganizationRow(name=args.organization)
                db.add(org)
                db.flush()
            token = make_token()
            row = ApiCredentialRow(
                organization_id=org.id,
                subject=args.subject,
                role=args.role,
                token_hash=digest_token(token),
                expires_at=datetime.now(UTC) + timedelta(days=args.days),
            )
            db.add(row)
            db.flush()
            print(
                f"Credential ID: {row.id}\nOrganization ID: {org.id}\nRole: {args.role}\n"
                f"Token (save now; never stored in plaintext): {token}"
            )
        elif args.command == "revoke":
            row = db.get(ApiCredentialRow, args.token_id)
            if row is None:
                parser.error("Credential ID not found")
            row.revoked_at = datetime.now(UTC)
            print("Credential revoked")
        elif args.command == "create-org":
            if not 1 <= len(args.organization) <= 200:
                parser.error("Organization must have 1..200 characters")
            org = db.scalar(
                select(OrganizationRow).where(OrganizationRow.name == args.organization)
            )
            if org is None:
                org = OrganizationRow(name=args.organization)
                db.add(org)
            db.flush()
            print(f"Organization ID: {org.id}")
        elif args.command == "grant-oidc":
            from urllib.parse import urlparse

            issuer = args.issuer.rstrip("/")
            if urlparse(issuer).scheme != "https" or not urlparse(issuer).hostname:
                parser.error("OIDC issuer must be HTTPS")
            org = db.scalar(
                select(OrganizationRow).where(OrganizationRow.name == args.organization)
            )
            if org is None:
                parser.error("Provision organization via 'create-org' command first")
            if not 1 <= len(args.subject) <= 200:
                parser.error("Subject must be 1..200 characters")
            row = db.scalar(
                select(OidcMembershipRow).where(
                    OidcMembershipRow.issuer == issuer,
                    OidcMembershipRow.subject == args.subject,
                    OidcMembershipRow.organization_id == org.id,
                )
            )
            if row is None:
                row = OidcMembershipRow(
                    issuer=issuer, subject=args.subject, organization_id=org.id, role=args.role
                )
                db.add(row)
            else:
                row.role, row.active = args.role, True
            db.flush()
            print(f"Membership ID: {row.id}")
        elif args.command == "disable-oidc":
            row = db.get(OidcMembershipRow, args.membership_id)
            if row is None:
                parser.error("Membership not found")
            row.active = False
            print("Membership disabled")
        elif args.command == "prune-ratelimits":
            import time

            cutoff = int(time.time()) // 60 - 1440
            deleted = db.execute(
                delete(RequestLimitRow).where(RequestLimitRow.minute_bucket < cutoff)
            )
            print(f"Removed {deleted.rowcount} expired rate-limit buckets")


if __name__ == "__main__":
    main()
