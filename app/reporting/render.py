"""Dependency-free Markdown and self-contained HTML assessment reports.

Dynamic content is treated as untrusted plain text. This module does not call an
LLM or modify an assessment, and it never turns an advisory review into rollout
permission. The API checks analysis currency before calling it.
"""

from dataclasses import dataclass, field
from html import escape as html_escape
import json
import re

from app.domain.persistence import AssessmentDetail

REPORT_VERSION = "4.0"


@dataclass(frozen=True)
class Block:
    kind: str  # "text", "bullets", "table", or "code"
    value: object


@dataclass(frozen=True)
class Section:
    title: str
    blocks: list[Block] = field(default_factory=list)


def _value(value: object) -> str:
    if value is None or value == "":
        return "Unknown / not evidenced"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (list, tuple)):
        return ", ".join(_value(item) for item in value) if value else "None recorded"
    return str(value)


def _line(value: object) -> str:
    return re.sub(r"\s+", " ", _value(value)).strip()


def _text(value: str) -> Block:
    return Block("text", value)


def _bullets(values: list[str]) -> Block:
    return Block("bullets", values)


def _table(headers: list[str], rows: list[list[object]]) -> Block:
    return Block("table", (headers, [[_line(cell) for cell in row] for row in rows]))


def build_sections(detail: AssessmentDetail, proposals: list[dict] | None = None) -> list[Section]:
    """Construct one view model for both safe output formats."""
    if detail.analysis is None or detail.analysis_revision != detail.revision:
        raise ValueError("Current deterministic analysis is required to export a report")

    analysis = detail.analysis
    final_reviews = [r for r in detail.reviews if r.is_final and r.analysis_revision == detail.revision]
    latest_final = final_reviews[-1] if final_reviews else None
    summaries = [a.recommendation.category.value for a in analysis.tasks]
    counts = {category: summaries.count(category) for category in sorted(set(summaries))}
    summary_rows = [[key.replace("_", " "), count] for key, count in counts.items()]

    sections = [
        Section("Executive summary", [
            _text("Advisory-only business assessment. An FDE is accountable for decisions. This report does not authorize a production rollout or any external business action."),
            _table(["Field", "Value"], [
                ["Organization", detail.organization], ["Business problem", detail.business_problem],
                ["Owner", detail.owner], ["Status", detail.status],
                ["Assessment revision", detail.revision], ["Analysis revision", detail.analysis_revision],
                ["Policy version", analysis.policy_version], ["Report version", REPORT_VERSION],
                ["Local demo only", detail.local_demo_only],
            ]),
            _table(["Recommendation category", "Task count"], summary_rows),
        ]),
    ]

    value = analysis.value
    if value is None:
        value_blocks = [_text("No measurable financial baseline was submitted. ROI and labor savings cannot be claimed.")]
    else:
        value_blocks = [
            _text(f"All values below are hypothetical or user-provided projections, not measured ROI. Unit period: {_line(value.period)}. Evaluation status: {_line(value.status)}."),
            _table(["Metric", "Value"], [
                ["Baseline hours", value.baseline_hours],
                ["Potential hours saved", value.potential_hours_saved],
                ["Potential gross labor savings", value.potential_gross_labor_savings],
                ["Proposed operating cost", value.proposed_operating_cost],
                ["Potential net savings", value.potential_net_savings],
            ]),
            _bullets([f"Assumption: {assumption}" for assumption in value.assumptions]
                     or ["No validated value assumptions recorded."]),
        ]
    sections.append(Section("Business value and limitations", value_blocks))

    sections.append(Section("Discovery evidence", [
        _text("Answers are assertions from recorded sources; confidence is self-reported and is not an independently verified probability."),
        _table(["Question key", "Answer", "Evidence type", "Source", "Reported confidence"], [
            [a.key, a.value, a.kind.value, a.source, a.confidence]
            for a in detail.answers
        ]) if detail.answers else _text("No discovery answers are recorded; critical inputs may be missing."),
    ]))

    lookup = {item.task_id: item for item in analysis.tasks}
    workflow_blocks: list[Block] = []
    for workflow in detail.workflows:
        workflow_blocks.append(_text(f"Workflow: {workflow.name}"))
        for step in sorted(workflow.steps, key=lambda s: s.order):
            workflow_blocks.append(_text(f"Step {step.order}: {step.name}. Owner: {_line(step.owner)}; system: {_line(step.system)}; inputs: {_line(step.input_description)}; outputs: {_line(step.output_description)}."))
            for task in step.tasks:
                result = lookup.get(task.id)
                if result is None:
                    continue
                rec = result.recommendation
                workflow_blocks.extend([
                    _text(f"Task: {task.title} [ID: {task.id}] — {task.description or 'No description recorded'}"),
                    _table(["Dimension", "Evidence-based result"], [
                        ["Recommendation", rec.category.value],
                        ["AI suitability", _value(result.ai_suitability.value) + f" (coverage {result.ai_suitability.coverage:.0%}; {result.ai_suitability.status})"],
                        ["Technical feasibility", _value(result.technical_feasibility.value) + f" (coverage {result.technical_feasibility.coverage:.0%})"],
                        ["Data readiness", _value(result.data_readiness.value) + f" (coverage {result.data_readiness.coverage:.0%})"],
                        ["Policy version", rec.policy_version],
                    ]),
                    _bullets([f"Reason: {reason}" for reason in rec.rationale] or ["No rationale available"]),
                    _table(["Responsibility", "Boundary"], [
                        ["AI", rec.ai_role], ["Conventional software / rules", rec.deterministic_role],
                        ["Human / approvals", rec.human_role],
                    ]),
                    _bullets([f"Required control: {control}" for control in rec.required_controls]
                             + [f"Missing evidence: {item}" for item in rec.missing_evidence]
                             + [f"Alternative: {item}" for item in rec.alternatives]
                             or ["No additional controls or alternatives recorded"]),
                    _bullets([f"Rule {hit.id} ({hit.severity.value}): {hit.reason}; control: {_line(hit.required_control)}"
                              for hit in result.rule_hits] or ["No triggered deterministic policy rules"]),
                    _bullets([f"Risk {risk.category.value}, score {risk.score}/25 ({risk.band.value}): {risk.description}; controls: {_line(risk.required_controls)}"
                              for risk in result.risks] or ["No scored risk entries supplied; this does not prove absence of risk"]),
                ])
    sections.append(Section("Workflow, task scores, risks, and decisions", workflow_blocks or [
        _text("No persisted workflows or tasks found.")
    ]))
    sections.append(Section("Analysis warnings and evidence gaps", [
        _bullets(analysis.warnings or ["No warnings returned; evidence should still be reviewed"]),
    ]))

    proposals = proposals or []
    pattern_by_category = {
        "traditional_software": "Deterministic typed code and tested rules; do not use an LLM for exact outputs.",
        "strong_ai_candidate": "Small, verifiable classification/extraction or retrieval PoC; add evaluation before rollout.",
        "hybrid": "AI only for language interpretation; trusted source APIs and deterministic workflow for actions.",
        "ai_human_approval": "AI drafts or extracts; deterministic checks and explicit human approval for consequential actions.",
        "human_led": "Human-led decision; no automation without new evidence and governance review.",
        "more_discovery": "No architecture commitment until data permissions and critical evidence gaps are resolved.",
    }
    proposal_blocks: list[Block] = [
        _text("Deterministic starting patterns below are provisional design suggestions, not an implementation or validated PoC."),
        _table(["Task", "Simplest adequate starting pattern"], [
            [item.task_id, pattern_by_category[item.recommendation.category.value]]
            for item in analysis.tasks
        ]),
        _bullets([
            "Proposed PoC scope: select one representative workflow step; do not connect irreversible or external write actions.",
            "Proposed evaluation: establish a human-reviewed ground-truth set and observed baselines first; no numeric threshold can be inferred without data.",
            "Go/no-go gates: approved data use, measured output correctness, acceptable latency/cost, verifiable fallback, and accountable sign-off.",
        ]),
        _text("The following are optional, unverified AI proposals. They are neither accepted requirements nor definitive architectural, evaluation, or regulatory conclusions."),
    ]
    for entry in proposals:
        stale = entry.get("assessment_revision") != detail.revision
        proposal_blocks.append(_text(
            f"Feature: {_line(entry.get('feature'))}; provider/model: {_line(entry.get('provider'))} / {_line(entry.get('model'))}; "
            f"prompt: {_line(entry.get('prompt_version'))}; schema: {_line(entry.get('schema_version'))}; "
            f"assessment revision: {_line(entry.get('assessment_revision'))}; stale: {'yes' if stale else 'no'}."
        ))
        proposal_blocks.append(Block("code", json.dumps(entry.get("proposal", {}), ensure_ascii=False, indent=2, sort_keys=True)))
    if not proposals:
        proposal_blocks.append(_text("No optional AI proposals were saved. All deterministic recommendations remain available without an external AI provider."))
    sections.append(Section("Architecture, PoC, and evaluation proposals", proposal_blocks))

    # Review entries are deliberately not presented as authenticated governance decisions.
    review_blocks: list[Block] = [
        _text("Reviewer names are self-reported in this local demo. A recorded final decision is not production authorization, and required controls are never waived by an override."),
    ]
    review_blocks.extend([_text(
        f"Reviewer: {r.reviewer_id}; category: {r.selected_category.value}; "
        f"final: {'yes' if r.is_final else 'no'}; analysis revision: {r.analysis_revision}; "
        f"rationale: {r.rationale}; override rationale: {_line(r.override_reason)}; "
        f"identity verified: {'no' if r.identity_unverified else 'yes'}."
    ) for r in detail.reviews])
    if not detail.reviews:
        review_blocks.append(_text("No FDE review exists; final decision is pending."))
    if latest_final:
        review_blocks.append(_text(f"Local demo recorded final category: {latest_final.selected_category.value}. Human identity remains unverified."))
    sections.append(Section("FDE review and final decision", review_blocks))
    sections.append(Section("Next steps and no-rollout disclaimer", [
        _bullets([
            "Validate unknowns, provenance, expected quality, data permissions, and financial baseline with responsible owners.",
            "Define measurable evaluation cases and go/no-go thresholds before committing to a proof of concept.",
            "Review policy controls and alternatives, including the possibility of traditional software or no project.",
            "Do not treat this local demo report, a model proposal, or a reviewer token as production authorization.",
        ]),
    ]))
    return sections


def _md(value: object) -> str:
    return re.sub(r"([\\`*_{}\[\]()#+.!|>~-])", r"\\\1", _line(value))


def render_markdown(detail: AssessmentDetail, proposals: list[dict] | None = None) -> str:
    lines = ["# " + _md(detail.title), "", "*AI Opportunity Advisor — local demo assessment report*", ""]
    for section in build_sections(detail, proposals):
        lines += ["## " + _md(section.title), ""]
        for block in section.blocks:
            if block.kind == "text":
                lines += [_md(block.value), ""]
            elif block.kind == "bullets":
                lines += ["- " + _md(s) for s in block.value] + [""]
            elif block.kind == "table":
                heads, rows = block.value
                lines += ["| " + " | ".join(_md(x) for x in heads) + " |",
                          "| " + " | ".join("---" for _ in heads) + " |"]
                lines += ["| " + " | ".join(_md(x) for x in row) + " |" for row in rows]
                lines.append("")
            elif block.kind == "code":
                lines += ["    " + line for line in str(block.value).splitlines()] + [""]
    return "\n".join(lines).rstrip() + "\n"


def render_html(detail: AssessmentDetail, proposals: list[dict] | None = None) -> str:
    parts = ["<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">",
             "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">",
             "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'\">",
             "<title>" + html_escape(detail.title) + " — AI Opportunity Advisor</title>",
             "<style>body{max-width:920px;margin:48px auto;padding:0 18px;font:16px/1.5 system-ui,sans-serif;color:#1d2939;}h1,h2{line-height:1.25;}h2{margin-top:36px;border-bottom:1px solid #cdd5df;padding-bottom:8px;}table{width:100%;border-collapse:collapse;margin:14px 0;}th,td{border:1px solid #d0d7e2;padding:8px;text-align:left;vertical-align:top;overflow-wrap:anywhere;}th{background:#edf2f7;}pre{overflow:auto;background:#f3f6fa;padding:14px;}li{margin:5px 0;} .notice{color:#7d2b2b;font-weight:700;}</style>",
             "</head><body><h1>" + html_escape(detail.title) + "</h1>",
             "<p class=\"notice\">AI Opportunity Advisor · advisory only · local demo · not rollout authorization</p>"]
    for section in build_sections(detail, proposals):
        parts.append("<section><h2>" + html_escape(section.title) + "</h2>")
        for block in section.blocks:
            if block.kind == "text":
                parts.append("<p>" + html_escape(str(block.value)) + "</p>")
            elif block.kind == "bullets":
                parts.append("<ul>" + "".join("<li>" + html_escape(s) + "</li>" for s in block.value) + "</ul>")
            elif block.kind == "table":
                heads, rows = block.value
                parts.append("<table><thead><tr>" + "".join("<th>" + html_escape(x) + "</th>" for x in heads) + "</tr></thead><tbody>")
                parts.extend("<tr>" + "".join("<td>" + html_escape(x) + "</td>" for x in row) + "</tr>" for row in rows)
                parts.append("</tbody></table>")
            elif block.kind == "code":
                parts.append("<pre>" + html_escape(str(block.value)) + "</pre>")
        parts.append("</section>")
    parts.append("</body></html>\n")
    return "\n".join(parts)
