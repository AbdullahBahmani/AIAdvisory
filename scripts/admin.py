"""Local operator provisioning; no public token-management endpoints.

Every lifecycle action follows the policy in ``docs/credential-lifecycle.md``:

* a credential always has an expiry; a credential that never expires needs the
  explicit ``--unlimited`` opt-in and is labelled as such;
* rotation replaces a credential without leaving two usable credentials behind;
* reducing or deactivating a membership de-provisions the credentials and
  reviewer assignments that depended on the old role;
* the sweep reports by default and only changes something with ``--execute``;
* every change is written through the shared audit gate.

Examples:
  python -m scripts.admin issue --organization Example --subject alice --role admin
  python -m scripts.admin issue-local --organization Example --subject alice --role reviewer
  python -m scripts.admin revoke --token-id <id>
  python -m scripts.admin rotate --token-id <id> --days 30
  python -m scripts.admin list --organization Example
  python -m scripts.admin sweep-membership-lifecycle --organization Example --execute
  python -m scripts.admin access-review --organization Example --format json
"""

import argparse
import getpass
import os

from sqlalchemy import delete, select

from app.db.models import ApiCredentialRow, OidcMembershipRow, OrganizationRow, RequestLimitRow
from app.db.session import SessionLocal
from app.security import ROLE_LEVEL
from app.services.credential_lifecycle import (
    DEFAULT_CREDENTIAL_LIFETIME_DAYS,
    MAX_CREDENTIAL_LIFETIME_DAYS,
    MAX_ROTATION_OVERLAP_SECONDS,
    LifecycleError,
    access_review,
    change_membership_role,
    clear_stale_assignments,
    deprovision_subject,
    describe_credential,
    disable_membership,
    effective_membership_role,
    issue_credential,
    render_access_review,
    revoke_credential,
    rotate_credential,
    sweep_lifecycle,
)


def _operator_actor() -> str:
    """The actor recorded for operator commands.

    An operator command is not a verified human identity: the audit events it
    writes stay marked unverified on purpose.
    """
    try:
        user = getpass.getuser()
    except Exception:  # pragma: no cover - a container without a passwd entry
        user = "unknown"
    return f"operator:{user}"


def _organization(db, name: str) -> OrganizationRow:
    org = db.scalar(select(OrganizationRow).where(OrganizationRow.name == name))
    if org is None:
        org = OrganizationRow(name=name)
        db.add(org)
        db.flush()
    return org


def _issue(
    db, organization: str, subject: str, role: str, days: int | None, unlimited: bool, reason
):
    """Create the organization if needed and store only the hash of a new token."""
    org = _organization(db, organization)
    row, token = issue_credential(
        db,
        organization_id=org.id,
        subject=subject,
        role=role,
        days=days,
        unlimited=unlimited,
        reason=reason,
        actor=_operator_actor(),
    )
    # A credential whose role the membership does not cover is revoked by the
    # next de-provisioning pass; say so before the operator finds out later.
    membership_role = effective_membership_role(db, org.id, subject)
    if membership_role is not None and ROLE_LEVEL[role] > ROLE_LEVEL[membership_role]:
        print(
            f"WARNING: the membership of {subject} holds '{membership_role}', so the "
            f"'{role}' credential exceeds it. The lifecycle sweep will revoke it."
        )
    return row, org, token


def main():
    parser = argparse.ArgumentParser(
        description="Provision scoped API tokens (requires upgraded database)"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    issue = sub.add_parser("issue", help="Issue a credential and display it exactly once")
    issue.add_argument("--organization", required=True)
    issue.add_argument("--subject", required=True)
    issue.add_argument("--role", choices=list(ROLE_LEVEL), required=True)
    issue.add_argument(
        "--days",
        type=int,
        default=None,
        help=f"Lifetime in days (default {DEFAULT_CREDENTIAL_LIFETIME_DAYS}, max "
        f"{MAX_CREDENTIAL_LIFETIME_DAYS})",
    )
    issue.add_argument(
        "--unlimited",
        action="store_true",
        help="Explicitly issue a credential that NEVER expires (standing key)",
    )
    issue.add_argument(
        "--unlimited-reason",
        default=None,
        help="Why an unlimited credential is justified (required with --unlimited)",
    )
    local = sub.add_parser(
        "issue-local",
        help="Issue a named local principal credential for the local/demo workflow",
    )
    local.add_argument("--organization", required=True)
    local.add_argument("--subject", required=True)
    local.add_argument("--role", choices=list(ROLE_LEVEL), required=True)
    local.add_argument("--days", type=int, default=None)
    local.add_argument(
        "--unlimited",
        action="store_true",
        help="Explicitly issue a credential that NEVER expires (standing key)",
    )
    local.add_argument(
        "--unlimited-reason",
        default=None,
        help="Why an unlimited credential is justified (required with --unlimited)",
    )
    revoke = sub.add_parser("revoke", help="Revoke an existing credential by ID")
    revoke.add_argument("--token-id", required=True)
    revoke.add_argument("--reason", default=None)
    rotate = sub.add_parser(
        "rotate",
        help="Replace a credential with a new one and retire the previous one atomically",
    )
    rotate.add_argument("--token-id", required=True)
    rotate.add_argument("--days", type=int, default=DEFAULT_CREDENTIAL_LIFETIME_DAYS)
    rotate.add_argument(
        "--role",
        choices=list(ROLE_LEVEL),
        default=None,
        help="Role of the replacement credential (default: the role of the old one)",
    )
    rotate.add_argument(
        "--overlap-seconds",
        type=int,
        default=0,
        help=(
            "Explicitly bounded overlap in which both credentials work "
            f"(0..{MAX_ROTATION_OVERLAP_SECONDS} seconds)"
        ),
    )
    rotate.add_argument("--reason", default=None)
    listing = sub.add_parser("list", help="List the credentials of an organization")
    listing.add_argument("--organization", required=True)
    listing.add_argument(
        "--include-revoked",
        action="store_true",
        help="Also show credentials that are already revoked",
    )
    create_org = sub.add_parser(
        "create-org", help="Provision an organization without issuing API token"
    )
    create_org.add_argument("--organization", required=True)
    member = sub.add_parser("grant-oidc", help="Trusted operator adds identity provider membership")
    member.add_argument("--organization", required=True)
    member.add_argument("--issuer", required=True)
    member.add_argument("--subject", required=True)
    member.add_argument("--role", choices=list(ROLE_LEVEL), required=True)
    member.add_argument("--reason", default=None)
    disable = sub.add_parser("disable-oidc", help="Disable OIDC membership by ID")
    disable.add_argument("--membership-id", required=True)
    disable.add_argument("--reason", default=None)
    disable.add_argument(
        "--override-last-admin",
        action="store_true",
        help="Deliberately remove the last administrator of the organization",
    )
    set_role = sub.add_parser(
        "set-membership-role", help="Change the role of a membership and de-provision the old one"
    )
    set_role.add_argument("--membership-id", required=True)
    set_role.add_argument("--role", choices=list(ROLE_LEVEL), required=True)
    set_role.add_argument("--reason", default=None)
    set_role.add_argument(
        "--override-last-admin",
        action="store_true",
        help="Deliberately remove the last administrator of the organization",
    )
    sweep = sub.add_parser(
        "sweep-membership-lifecycle",
        help="Report (and with --execute repair) credential and membership drift",
    )
    sweep.add_argument("--organization", default=None, help="Limit the sweep to one organization")
    sweep.add_argument(
        "--execute", action="store_true", help="Required to actually change anything"
    )
    sweep.add_argument(
        "--override-last-admin",
        action="store_true",
        help="Deliberately remove the last administrator of the organization",
    )
    review = sub.add_parser("access-review", help="Produce the access review of one organization")
    review.add_argument("--organization", required=True)
    review.add_argument("--format", choices=["text", "json", "csv"], default="text")
    review.add_argument("--output", default=None, help="Write the review to this file")
    # The parser handle is not needed: add_parser registers the command as a side effect.
    sub.add_parser("prune-ratelimits", help="Delete request counters older than one day")
    args = parser.parse_args()
    actor = _operator_actor()
    with SessionLocal.begin() as db:
        if args.command == "issue":
            _check_common(parser, args)
            days, unlimited, reason = _lifetime(parser, args)
            row, org, token = _issue(
                db, args.organization, args.subject, args.role, days, unlimited, reason
            )
            _print_issued(row, org, args.role, token, unlimited=unlimited, reason=reason)
        elif args.command == "issue-local":
            _check_common(parser, args)
            days, unlimited, reason = _lifetime(parser, args)
            mode = os.getenv("ADVISORY_DEPLOYMENT_MODE", "local").lower()
            if mode not in ("local", ""):
                parser.error(
                    "Local principals are for local/demo use only; use 'issue' outside local mode"
                )
            row, org, token = _issue(
                db, args.organization, args.subject, args.role, days, unlimited, reason
            )
            print(
                f"Credential ID: {row.id}\nOrganization ID: {org.id}\n"
                f"Subject: {args.subject}\nRole: {args.role}\n"
                f"Token (save now; never stored in plaintext): {token}\n"
                f"Use as 'Authorization: Bearer <token>'. The server resolves the subject, "
                f"organization and role from this credential, so reviews and audit events "
                f"are attributed to '{args.subject}' instead of a typed name. It is not a "
                f"proof of human identity."
            )
            if unlimited:
                _print_unlimited_warning(row, reason)
        elif args.command == "revoke":
            row = db.get(ApiCredentialRow, args.token_id)
            if row is None:
                parser.error("Credential ID not found")
            revoke_credential(db, args.token_id, reason=args.reason, actor=actor)
            # A revoked credential can no longer act, so its outstanding
            # reviewer assignments are cleared instead of blocking an
            # assessment forever.
            cleared = clear_stale_assignments(
                db, row.organization_id, row.subject, actor=actor, reason="credential revoked"
            )
            print("Credential revoked")
            for action in cleared:
                print(f"Cleared reviewer assignment on assessment {action['assessment_id']}")
        elif args.command == "rotate":
            if not 1 <= args.days <= MAX_CREDENTIAL_LIFETIME_DAYS:
                parser.error(f"Days must be 1..{MAX_CREDENTIAL_LIFETIME_DAYS}")
            if not 0 <= args.overlap_seconds <= MAX_ROTATION_OVERLAP_SECONDS:
                parser.error(f"Overlap must be 0..{MAX_ROTATION_OVERLAP_SECONDS} seconds")
            result = rotate_credential(
                db,
                args.token_id,
                days=args.days,
                role=args.role,
                overlap_seconds=args.overlap_seconds,
                reason=args.reason,
                actor=actor,
            )
            expires = (
                result.credential.expires_at.isoformat()
                if result.credential.expires_at
                else "never"
            )
            print(
                f"Previous credential ID: {result.previous.id} (retired)\n"
                f"Credential ID: {result.credential.id}\n"
                f"Subject: {result.credential.subject}\n"
                f"Role: {result.credential.role}\n"
                f"Expires: {expires}\n"
                f"Token (save now; never stored in plaintext): {result.token}"
            )
            if result.overlap_until is not None:
                print(
                    f"Overlap: the previous credential keeps working until "
                    f"{result.overlap_until.isoformat()} and then expires by itself."
                )
            else:
                print("Overlap: none. The previous credential is revoked immediately.")
        elif args.command == "list":
            org = db.scalar(
                select(OrganizationRow).where(OrganizationRow.name == args.organization)
            )
            if org is None:
                parser.error("Organization not found")
            rows = db.scalars(
                select(ApiCredentialRow)
                .where(ApiCredentialRow.organization_id == org.id)
                .order_by(ApiCredentialRow.created_at, ApiCredentialRow.id)
            ).all()
            printed = 0
            for row in rows:
                view = describe_credential(row)
                if view.state == "revoked" and not args.include_revoked:
                    continue
                printed += 1
                print(f"{view.id}  {view.subject}  {view.role}  {view.label}")
                if view.overlap_until is not None:
                    print(
                        f"    rotation overlap until {view.overlap_until.isoformat()}; "
                        f"replaced by {view.replaced_by_id}"
                    )
            if not printed:
                print(f"No live credentials for organization {args.organization}")
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
                parser.error("Subject must have 1..200 characters")
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
                db.flush()
                print(f"Membership ID: {row.id}")
            elif not row.active:
                row.active = True
                row.role = args.role
                db.flush()
                print(f"Membership ID: {row.id} (reactivated)")
                _deprovision(db, row, actor, args.reason)
            elif row.role != args.role:
                membership, result = change_membership_role(
                    db, row.id, args.role, reason=args.reason, actor=actor
                )
                print(f"Membership ID: {membership.id}")
                _print_deprovision(result)
            else:
                print(f"Membership ID: {row.id} (unchanged)")
        elif args.command == "disable-oidc":
            row = db.get(OidcMembershipRow, args.membership_id)
            if row is None:
                parser.error("Membership not found")
            membership, result = disable_membership(
                db,
                args.membership_id,
                reason=args.reason,
                actor=actor,
                override_last_admin=args.override_last_admin,
            )
            print(f"Membership ID: {membership.id} disabled")
            _print_deprovision(result)
        elif args.command == "set-membership-role":
            membership, result = change_membership_role(
                db,
                args.membership_id,
                args.role,
                reason=args.reason,
                actor=actor,
                override_last_admin=args.override_last_admin,
            )
            print(f"Membership ID: {membership.id} role set to {membership.role}")
            if not membership.active:
                print(
                    "The membership is inactive: it stays inactive and the credentials "
                    "that relied on it are de-provisioned. Use 'grant-oidc' to reactivate."
                )
            _print_deprovision(result)
        elif args.command == "sweep-membership-lifecycle":
            organization_id = None
            if args.organization:
                org = db.scalar(
                    select(OrganizationRow).where(OrganizationRow.name == args.organization)
                )
                if org is None:
                    parser.error("Organization not found")
                organization_id = org.id
            report = sweep_lifecycle(
                db,
                organization_id=organization_id,
                actor=actor,
                execute=args.execute,
                override_last_admin=args.override_last_admin,
            )
            print(
                f"{'Sweep executed' if args.execute else 'Dry run:'} "
                f"{len(report.findings)} finding(s)"
            )
            for finding in report.findings:
                status = finding.action_taken or finding.severity
                blocked = f" [BLOCKED: {finding.blocked_reason}]" if finding.blocked_reason else ""
                print(f"- {finding.kind}: {finding.description} -> {status}{blocked}")
            if not args.execute:
                print("Nothing was changed. Re-run with --execute to apply the findings.")
        elif args.command == "access-review":
            org = db.scalar(
                select(OrganizationRow).where(OrganizationRow.name == args.organization)
            )
            if org is None:
                parser.error("Organization not found")
            review = access_review(db, org.id)
            rendered = render_access_review(review, output_format=args.format)
            if args.output:
                with open(args.output, "w", encoding="utf-8") as handle:
                    handle.write(rendered)
                    if not rendered.endswith("\n"):
                        handle.write("\n")
                print(f"Access review written to {args.output}")
            else:
                print(rendered)
        elif args.command == "prune-ratelimits":
            import time

            cutoff = int(time.time()) // 60 - 1440
            deleted = db.execute(
                delete(RequestLimitRow).where(RequestLimitRow.minute_bucket < cutoff)
            )
            print(f"Removed {deleted.rowcount} expired rate-limit buckets")


def _lifetime(parser, args) -> tuple[int | None, bool, str | None]:
    """Resolve the expiry of a credential to be issued."""
    if args.unlimited:
        if args.days is not None:
            parser.error("--unlimited and --days cannot be combined")
        if not (args.unlimited_reason or "").strip():
            parser.error("--unlimited requires --unlimited-reason")
        return None, True, args.unlimited_reason
    days = DEFAULT_CREDENTIAL_LIFETIME_DAYS if args.days is None else args.days
    if not 1 <= days <= MAX_CREDENTIAL_LIFETIME_DAYS:
        parser.error(f"Days must be 1..{MAX_CREDENTIAL_LIFETIME_DAYS}")
    return days, False, None


def _check_common(parser, args) -> None:
    if not (1 <= len(args.subject) <= 200):
        parser.error("Subject must have 1..200 characters")
    if not (1 <= len(args.organization) <= 200):
        parser.error("Organization must have 1..200 characters")


def _print_issued(row, org, role: str, token: str, *, unlimited: bool, reason: str | None) -> None:
    print(
        f"Credential ID: {row.id}\nOrganization ID: {org.id}\n"
        f"Role: {role}\n"
        f"Token (save now; never stored in plaintext): {token}"
    )
    if unlimited:
        _print_unlimited_warning(row, reason)


def _print_unlimited_warning(row, reason: str | None) -> None:
    print(
        "!! UNLIMITED CREDENTIAL !!\n"
        f"Credential {row.id} NEVER expires. This is a standing key: it works until it\n"
        "is revoked by hand. Rotate it regularly and prefer an expiry.\n"
        f"Reason: {reason or 'not recorded'}"
    )


def _deprovision(db, membership: OidcMembershipRow, actor: str, reason: str | None) -> None:
    result = deprovision_subject(
        db,
        membership.organization_id,
        membership.subject,
        actor=actor,
        reason=reason or "membership changed",
    )
    _print_deprovision(result)


def _print_deprovision(result) -> None:
    for action in result.actions:
        if action["kind"] == "credential_revoked":
            print(f"Revoked credential {action['credential_id']} ({action.get('reason', '')})")
        elif action["kind"] == "assignment_cleared":
            print(f"Cleared reviewer assignment on assessment {action['assessment_id']}")
    for action in result.blocked:
        print(f"BLOCKED: {action.get('reason', '')} ({action.get('credential_id', '')})")


if __name__ == "__main__":
    try:
        main()
    except LifecycleError as error:
        raise SystemExit(f"error: {error}") from None
