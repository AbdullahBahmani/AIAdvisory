"""Single local release-gate command.

The repository deliberately has **no hosted CI** — the owner removed the GitHub Actions
workflow and the project relies on local runs instead. That decision left the local
workflow spread across seven unrelated commands, which is exactly how a gate rots: the
Alembic drift in `answers.confidence` survived for weeks because nothing ran
`alembic check` as part of a routine pass.

This module runs every local check in one place and reports a single verdict. It is
honest about what it does NOT cover: it runs no live AI provider call, no real IdP, no
production PostgreSQL, no penetration test and no external approval. Passing this
command means "the local gate is green", never "this is approved for production".

Usage:
    python -m scripts.verify_all              # run every check
    python -m scripts.verify_all --list       # list the checks and exit
    python -m scripts.verify_all --only lint  # run one check by name
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Check:
    name: str
    description: str
    command: tuple[str, ...]
    env_overrides: dict[str, str] | None = None


def _check(
    name: str,
    description: str,
    command: tuple[str, ...],
    env_overrides: dict[str, str] | None = None,
) -> Check:
    return Check(name, description, command, env_overrides)


CHECKS: tuple[Check, ...] = (
    _check(
        "compile",
        "Byte-compile every package (catches syntax errors without running anything)",
        (sys.executable, "-m", "compileall", "-q", "app", "ui", "scripts", "tests", "migrations"),
    ),
    _check(
        "lint",
        "Ruff lint and format verification (must be clean repo-wide)",
        (sys.executable, "-m", "ruff", "check", "."),
    ),
    _check(
        "format",
        "Ruff formatting verification",
        (sys.executable, "-m", "ruff", "format", "--check", "."),
    ),
    _check(
        "tests",
        "Full test suite (offline, no AI provider credentials required)",
        (sys.executable, "-m", "pytest", "-q"),
    ),
    _check(
        "migrations",
        "Apply the migration chain to a fresh database and assert no schema drift",
        (),  # run in-process by _run_migrations()
    ),
    _check(
        "evaluation",
        "Offline policy regression evaluation (31-case corpus, metrics and false-positive rate)",
        (sys.executable, "-m", "app.evaluation"),
    ),
    _check(
        "portfolio-eval",
        "Phase 8 portfolio synthetic regressions",
        (sys.executable, "-m", "app.portfolio_evaluation"),
    ),
    _check(
        "demo",
        "Run the three synthetic demo journeys and export the Markdown/HTML reports",
        (sys.executable, "-m", "scripts.demo", "--output", "demo_out"),
    ),
)


def _run(check: Check) -> tuple[bool, str]:
    env = dict(os.environ)
    env.update(check.env_overrides or {})
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    try:
        completed = subprocess.run(
            check.command,
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=1800,
        )
    except subprocess.TimeoutExpired:
        return False, "timed out after 30 minutes"
    output = (completed.stdout + completed.stderr).strip()
    return completed.returncode == 0, output


def _run_migrations() -> tuple[bool, str]:
    """Apply the chain to a throwaway database, then assert no drift."""
    with tempfile.TemporaryDirectory(prefix="aiadvisory-verify-") as tmp:
        url = f"sqlite:///{Path(tmp) / 'gate.sqlite3'}"
        env = {**os.environ, "DATABASE_URL": url}
        steps = (
            (sys.executable, "-m", "alembic", "upgrade", "head"),
            (sys.executable, "-m", "alembic", "check"),
        )
        transcripts: list[str] = []
        for command in steps:
            completed = subprocess.run(
                command,
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=600,
            )
            transcripts.append(completed.stdout + completed.stderr)
            if completed.returncode != 0:
                return False, "\n".join(transcripts)
    return True, "migration chain applied to a fresh database; no schema drift"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run every local release gate in one command")
    parser.add_argument("--list", action="store_true", help="list the checks and exit")
    parser.add_argument("--only", help="run a single check by name")
    args = parser.parse_args()

    if args.list:
        for check in CHECKS:
            print(f"{check.name:14s} {check.description}")
        return 0

    if args.only:
        selected = [c for c in CHECKS if c.name == args.only]
        if not selected:
            print(f"unknown check {args.only!r}; run with --list to see them", file=sys.stderr)
            return 2
    else:
        selected = list(CHECKS)

    print("=" * 78)
    print("AI Opportunity Advisor — LOCAL release gate")
    print("This is a local gate. It is NOT production approval, and it does not run")
    print("a live AI provider, a real IdP, production PostgreSQL, or any external review.")
    print("=" * 78)

    failures: list[str] = []
    for check in selected:
        print(f"\n>>> {check.name}: {check.description}")
        if check.name == "migrations":
            ok, output = _run_migrations()
        else:
            ok, output = _run(check)
        if ok:
            print(f"    PASS  {check.name}")
            if output:
                tail = output.splitlines()[-3:]
                for line in tail:
                    print(f"          {line}")
        else:
            print(f"    FAIL  {check.name}")
            failures.append(check.name)
            print(f"          {output[-4000:]}")

    print("\n" + "=" * 78)
    if failures:
        print(
            f"LOCAL GATE FAILED: {len(failures)} of {len(selected)} checks failed: "
            f"{', '.join(failures)}"
        )
        return 1
    print(f"LOCAL GATE PASSED: {len(selected)} of {len(selected)} checks passed.")
    print("This does not authorise a production deployment. See docs/release-verification.md.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
