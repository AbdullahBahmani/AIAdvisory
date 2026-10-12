"""Local operator provisioning; no public token-management endpoints.

Examples:
  python -m scripts.admin issue --organization Example --subject alice --role admin
  python -m scripts.admin revoke --token-id <id>
"""
import argparse
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db.models import ApiCredentialRow, OrganizationRow
from app.db.session import SessionLocal
from app.security import ROLE_LEVEL, digest_token, make_token


def main():
    parser = argparse.ArgumentParser(description="Provision scoped API tokens (requires upgraded database)")
    sub = parser.add_subparsers(dest="command", required=True)
    issue = sub.add_parser("issue", help="Issue a credential and display it exactly once")
    issue.add_argument("--organization", required=True)
    issue.add_argument("--subject", required=True)
    issue.add_argument("--role", choices=list(ROLE_LEVEL), required=True)
    issue.add_argument("--days", type=int, default=30)
    revoke = sub.add_parser("revoke", help="Revoke an existing credential by ID")
    revoke.add_argument("--token-id", required=True)
    args = parser.parse_args()
    with SessionLocal.begin() as db:
        if args.command == "issue":
            if not (1 <= args.days <= 365) or not (1 <= len(args.subject) <= 200) or not (1 <= len(args.organization) <= 200):
                parser.error("Days must be 1..365 and organization/subject must have 1..200 characters")
            org = db.scalar(select(OrganizationRow).where(OrganizationRow.name == args.organization))
            if org is None:
                org = OrganizationRow(name=args.organization)
                db.add(org)
                db.flush()
            token = make_token()
            row = ApiCredentialRow(organization_id=org.id, subject=args.subject, role=args.role,
                                   token_hash=digest_token(token),
                                   expires_at=datetime.now(timezone.utc) + timedelta(days=args.days))
            db.add(row)
            db.flush()
            print(f"Credential ID: {row.id}\nOrganization ID: {org.id}\nRole: {args.role}\nToken (save now; never stored in plaintext): {token}")
        else:
            row = db.get(ApiCredentialRow, args.token_id)
            if row is None:
                parser.error("Credential ID not found")
            row.revoked_at = datetime.now(timezone.utc)
            print("Credential revoked")


if __name__ == "__main__":
    main()
