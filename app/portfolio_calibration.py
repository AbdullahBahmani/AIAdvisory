"""Offline ranking calibration runner (Phase 9, P9-05).

    python -m app.portfolio_calibration
    python -m app.portfolio_calibration --labelled-set path/to/set.json
    python -m app.portfolio_calibration --json

What this is
------------
A **harness** that measures the shipped ``portfolio-v1`` priority heuristic
against an independently supplied expected ordering, and reports how well the
two agree, which portfolios are ordered incorrectly, and whether the Phase 8
invariant (policy-blocked and discovery-only proposals stay unranked) still
holds.

What this is NOT
----------------
It is **not** calibration against reality. The labelled data required for that
does not exist yet: it needs consented real assessments and blinded expert
review (P8-09). The bundled corpus is synthetic, so a run against it is a
**harness self-test** — it proves the harness computes what it claims to
compute, not that the heuristic agrees with any expert. The runner prints that
warning prominently on every run and the report carries the label source, so a
synthetic result can never be presented as field calibration.

Exit codes
----------
* ``0`` — the run completed and the Phase 8 ranking invariant holds.
* ``1`` — the ranking invariant was violated (a policy-blocked or
  discovery-only opportunity was ranked). This is a failure, not a scoring
  nuance.
* ``2`` — the supplied labelled set is malformed, empty or missing. This fails
  loudly on purpose: an unusable labelled set must never be reported as
  "nothing to calibrate".
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.services.calibration import (
    RANKING_VERSION,
    CalibrationInputError,
    CalibrationReport,
    calibrate,
    compare_versions,
    format_report,
    load_labelled_set,
)

DEFAULT_LABELLED_SET = (
    Path(__file__).resolve().parent.parent / "evals" / "portfolio_calibration_cases.json"
)


def _banner(report: CalibrationReport) -> str:
    """The honesty banner printed at the top of every run."""
    lines = [
        "!" * 78,
        "!! RANKING CALIBRATION HARNESS",
        "!!",
    ]
    if report.synthetic:
        lines += [
            "!! THIS RUN IS A HARNESS SELF-TEST OVER SYNTHETIC LABELS.",
            "!! IT IS **NOT** CALIBRATION AGAINST REALITY.",
            "!! No expert agreement, ROI, safety or business-impact number may be",
            "!! quoted from this output. A real labelled set (consented assessments",
            "!! + blinded expert review, P8-09) does not exist yet.",
        ]
    else:
        lines += [
            "!! THIS RUN USES AN EXTERNALLY SUPPLIED LABELLED SET.",
            "!! Read the dataset provenance note before quoting any number:",
            "!! agreement with the supplied ordering is not ROI, safety or impact.",
        ]
    lines.append("!" * 78)
    return "\n".join(lines)


def format_comparison(comparison: dict) -> str:
    """Render the cross-version comparison as plain text."""
    lines = [
        "=" * 78,
        "RANKING VERSION COMPARISON (same labelled set, same expectations)",
        "=" * 78,
        f"WARNING: {comparison['warning']}",
        "",
        f"baseline version: {comparison['baseline_version']}",
        "",
    ]
    for version, stats in comparison["versions"].items():
        tau = "n/a" if stats["tau_b"] is None else f"{stats['tau_b']:.4f}"
        delta = stats["delta_tau_b_vs_baseline"]
        delta_text = "n/a" if delta is None else f"{delta:+.4f}"
        lines += [
            f"  {version}:",
            f"    kendall_tau_b:                 {tau}",
            "    spearman_rho:                  "
            + ("n/a" if stats["spearman_rho"] is None else f"{stats['spearman_rho']:.4f}"),
            f"    delta tau_b vs baseline:       {delta_text}",
            f"    comparable cases:              {stats['comparable_cases']}",
            f"    discordant pairs:              {stats['discordant_pairs']}",
            "    mis-ranked ids:                " + (", ".join(stats["mis_ranked_ids"]) or "none"),
            f"    policy invariant holds:        {stats['invariant_holds']}",
            "",
        ]
    lines.append("=" * 78)
    return "\n".join(lines)


def run(
    path: str | Path | None = None,
    *,
    as_json: bool = False,
    compare: bool = False,
) -> tuple[int, CalibrationReport | None]:
    """Run the calibration harness and return ``(exit_code, report)``.

    A malformed or empty labelled set raises :class:`CalibrationInputError`,
    which the CLI converts into a loud failure (exit code 2). The exception is
    deliberately not swallowed here so callers see it.
    """
    source = Path(path) if path else DEFAULT_LABELLED_SET
    labelled = load_labelled_set(source)
    report = calibrate(labelled, ranking_version=RANKING_VERSION)
    if as_json:
        payload = report.as_dict()
        if compare:
            payload["version_comparison"] = compare_versions(labelled)
        print(json.dumps(payload, indent=2))
    else:
        print(_banner(report))
        print()
        print(format_report(report))
        if compare:
            print()
            print(format_comparison(compare_versions(labelled)))
    if not report.invariant_holds:
        return 1, report
    return 0, report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Offline ranking calibration harness for the portfolio priority heuristic. "
            "Runs over a labelled set; synthetic labels mean a harness self-test, "
            "never calibration against reality."
        )
    )
    parser.add_argument(
        "--labelled-set",
        type=Path,
        default=DEFAULT_LABELLED_SET,
        help=(
            "path to a labelled set in the documented format "
            "(default: the bundled synthetic corpus)"
        ),
    )
    parser.add_argument("--json", action="store_true", help="print the raw report as JSON")
    parser.add_argument(
        "--compare",
        action="store_true",
        help="also report every registered ranking version measured on the same labelled set",
    )
    args = parser.parse_args(argv)
    try:
        code, _ = run(args.labelled_set, as_json=args.json, compare=args.compare)
    except CalibrationInputError as exc:
        print(
            "CALIBRATION INPUT ERROR: the supplied labelled set is unusable and the run "
            "FAILED. It was NOT treated as 'nothing to calibrate'.",
            file=sys.stderr,
        )
        print(f"  reason: {exc}", file=sys.stderr)
        return 2
    return code


if __name__ == "__main__":
    raise SystemExit(main())
