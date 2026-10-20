"""Dependency-free Markdown and self-contained HTML assessment reports.

Dynamic content is treated as untrusted plain text. This module does not call an
LLM or modify an assessment, and it never turns an advisory review into rollout
permission. The API checks analysis currency before calling it.

The report can be rendered in a locale (see :mod:`app.localization`).  Only the
*labels* of the report are localized: section headings, table headers and the
generic renderings of a value.  Everything that is part of the system's
contract - recommendation category values, rule ids, severities, statuses,
revisions, digests and ISO-8601 timestamps - is reproduced exactly as
recorded.  The English output is unchanged by the locale support.
"""

import json
import re
from dataclasses import dataclass, field
from html import escape as html_escape

from app.domain.persistence import AssessmentDetail
from app.domain.portfolio import ProvenanceView
from app.localization import DEFAULT_LOCALE, Translator
from app.reporting.diagrams import Diagram, build_task_diagram, describe, render_mermaid, render_svg

REPORT_VERSION = "4.1"


@dataclass(frozen=True)
class Block:
    kind: str  # "text", "bullets", "table", "code" or "diagram"
    value: object


@dataclass(frozen=True)
class Section:
    title: str
    blocks: list[Block] = field(default_factory=list)


def _value(translator: Translator, value: object) -> str:
    if value is None or value == "":
        return translator.t("report.value.unknown", "Unknown / not evidenced")
    if isinstance(value, bool):
        if value:
            return translator.t("report.value.yes", "Yes")
        return translator.t("report.value.no", "No")
    if isinstance(value, (list, tuple)):
        if not value:
            return translator.t("report.value.none_recorded", "None recorded")
        return ", ".join(_value(translator, item) for item in value)
    return str(value)


def _line(translator: Translator, value: object) -> str:
    return re.sub(r"\s+", " ", _value(translator, value)).strip()


def _text(value: str) -> Block:
    return Block("text", value)


def _bullets(values: list[str]) -> Block:
    return Block("bullets", values)


def _table(translator: Translator, headers: list[str], rows: list[list[object]]) -> Block:
    return Block(
        "table",
        (headers, [[_line(translator, cell) for cell in row] for row in rows]),
    )


def _diagram(diagram: Diagram) -> Block:
    return Block("diagram", diagram)


def _provenance_blocks(translator: Translator, provenance: ProvenanceView) -> list[Block]:
    """Render the decision ledger in the report's own fact/label style."""
    prompt_versions = sorted({item.prompt_version for item in provenance.ai_proposals})
    schema_versions = sorted({item.schema_version for item in provenance.ai_proposals})
    blocks: list[Block] = [
        _text(
            translator.t(
                "report.provenance.intro",
                "Inputs recorded when this decision was finalized. The ledger states what the "
                "system presented to the reviewer at that moment; it is not proof that the "
                "decision was correct, and it is neither an approval nor a validation.",
            )
        ),
        _table(
            translator,
            [
                translator.t("report.table.recorded_input", "Recorded input"),
                translator.t("report.table.value", "Value"),
            ],
            [
                [
                    translator.t("report.label.policy_version", "Policy version"),
                    provenance.policy_version,
                ],
                [
                    translator.t("report.label.scoring_weight_version", "Scoring weight version"),
                    provenance.scoring_weight_version,
                ],
                [
                    translator.t("report.label.analysis_revision", "Analysis revision"),
                    provenance.analysis_revision,
                ],
                [
                    translator.t("report.label.assessment_revision", "Assessment revision"),
                    provenance.assessment_revision,
                ],
                [
                    translator.t("report.label.analysis_digest", "Analysis digest"),
                    provenance.analysis_digest,
                ],
                [
                    translator.t(
                        "report.label.contributing_evidence", "Contributing evidence records"
                    ),
                    provenance.evidence_ids,
                ],
                [
                    translator.t("report.label.verified_evidence", "Verified evidence records"),
                    provenance.verified_evidence_ids,
                ],
                [
                    translator.t(
                        "report.label.contributing_value_scenario", "Contributing value scenario"
                    ),
                    provenance.value_scenario_id,
                ],
                [
                    translator.t(
                        "report.label.recorded_value_scenarios", "Recorded value scenarios"
                    ),
                    provenance.value_scenario_ids,
                ],
                [
                    translator.t("report.label.reviewer_subject", "Reviewer subject"),
                    provenance.reviewer_subject,
                ],
                [
                    translator.t("report.label.reviewer_verified", "Reviewer identity verified"),
                    _yes_no(translator, provenance.reviewer_identity_verified),
                ],
                [
                    translator.t("report.label.decision_maker_subject", "Decision maker subject"),
                    provenance.decision_maker_subject,
                ],
                [
                    translator.t(
                        "report.label.decision_maker_verified", "Decision maker identity verified"
                    ),
                    _yes_no(translator, provenance.decision_maker_identity_verified),
                ],
                [
                    translator.t("report.label.ai_proposal_recorded", "AI proposal recorded"),
                    _yes_no(translator, provenance.ai_contributed),
                ],
                [
                    translator.t("report.label.ai_prompt_versions", "AI prompt versions"),
                    prompt_versions,
                ],
                [
                    translator.t("report.label.ai_schema_versions", "AI schema versions"),
                    schema_versions,
                ],
                [
                    translator.t("report.label.recorded_at", "Recorded at"),
                    provenance.recorded_at,
                ],
                [
                    translator.t("report.label.basis_stale", "Basis stale"),
                    _yes_no(translator, provenance.stale),
                ],
            ],
        ),
    ]
    if provenance.stale:
        blocks.append(
            _text(
                translator.t(
                    "report.provenance.stale",
                    "Stale basis: the recorded inputs changed after this decision was finalized "
                    "({reasons}). The decision was taken against an earlier basis and must not "
                    "be presented as current.",
                    reasons=", ".join(provenance.stale_reasons),
                )
            )
        )
    return blocks


def _yes_no(translator: Translator, value: bool) -> str:
    """Render a recorded boolean; the wording is a label, the value is not."""
    if value:
        return translator.t("report.value.yes_lowercase", "yes")
    return translator.t("report.value.no_lowercase", "no")


def build_sections(
    detail: AssessmentDetail,
    proposals: list[dict] | None = None,
    provenance: ProvenanceView | None = None,
    locale: str = DEFAULT_LOCALE,
    translator: Translator | None = None,
) -> list[Section]:
    """Construct one view model for both safe output formats."""
    if detail.analysis is None or detail.analysis_revision != detail.revision:
        raise ValueError("Current deterministic analysis is required to export a report")

    translator = translator or Translator(locale)
    analysis = detail.analysis
    final_reviews = [
        r for r in detail.reviews if r.is_final and r.analysis_revision == detail.revision
    ]
    latest_final = final_reviews[-1] if final_reviews else None
    summaries = [a.recommendation.category.value for a in analysis.tasks]
    counts = {category: summaries.count(category) for category in sorted(set(summaries))}
    # The category value is a contract value: only the label around it is text.
    summary_rows = [[key.replace("_", " "), count] for key, count in counts.items()]
    summary_fields = [
        [translator.t("report.label.organization", "Organization"), detail.organization],
        [
            translator.t("report.label.business_problem", "Business problem"),
            detail.business_problem,
        ],
        [translator.t("report.label.owner", "Owner"), detail.owner],
        [translator.t("report.label.status", "Status"), detail.status],
        [
            translator.t("report.label.assessment_revision", "Assessment revision"),
            detail.revision,
        ],
        [
            translator.t("report.label.analysis_revision", "Analysis revision"),
            detail.analysis_revision,
        ],
        [translator.t("report.label.policy_version", "Policy version"), analysis.policy_version],
        [translator.t("report.label.report_version", "Report version"), REPORT_VERSION],
        [translator.t("report.label.local_demo_only", "Local demo only"), detail.local_demo_only],
    ]
    if provenance is not None:
        summary_fields += [
            [
                translator.t("report.label.decision_inputs_recorded", "Decision inputs recorded"),
                translator.t("report.value.yes_lowercase", "yes"),
            ],
            [
                translator.t("report.label.decision_basis_stale", "Decision basis stale"),
                _yes_no(translator, provenance.stale),
            ],
        ]

    sections = [
        Section(
            translator.t("report.section.executive_summary", "Executive summary"),
            [
                _text(
                    translator.t(
                        "report.summary.intro",
                        "Advisory-only business assessment. An FDE is accountable for decisions. "
                        "This report does not authorize a production rollout "
                        "or any external business action.",
                    )
                ),
                _table(
                    translator,
                    [
                        translator.t("report.table.field", "Field"),
                        translator.t("report.table.value", "Value"),
                    ],
                    summary_fields,
                ),
                _table(
                    translator,
                    [
                        translator.t(
                            "report.table.recommendation_category", "Recommendation category"
                        ),
                        translator.t("report.table.task_count", "Task count"),
                    ],
                    summary_rows,
                ),
            ],
        ),
    ]

    value = analysis.value
    if value is None:
        value_blocks = [
            _text(
                translator.t(
                    "report.value.no_baseline",
                    "No measurable financial baseline was submitted. "
                    "ROI and labor savings cannot be claimed.",
                )
            )
        ]
    else:
        value_blocks = [
            _text(
                translator.t(
                    "report.value.intro",
                    "All values below are hypothetical or user-provided projections, "
                    "not measured ROI. Unit period: {period}. "
                    "Evaluation status: {status}.",
                    period=_line(translator, value.period),
                    status=_line(translator, value.status),
                )
            ),
            _table(
                translator,
                [
                    translator.t("report.table.metric", "Metric"),
                    translator.t("report.table.value", "Value"),
                ],
                [
                    [
                        translator.t("report.label.baseline_hours", "Baseline hours"),
                        value.baseline_hours,
                    ],
                    [
                        translator.t("report.label.potential_hours_saved", "Potential hours saved"),
                        value.potential_hours_saved,
                    ],
                    [
                        translator.t(
                            "report.label.potential_gross_labor_savings",
                            "Potential gross labor savings",
                        ),
                        value.potential_gross_labor_savings,
                    ],
                    [
                        translator.t(
                            "report.label.proposed_operating_cost", "Proposed operating cost"
                        ),
                        value.proposed_operating_cost,
                    ],
                    [
                        translator.t("report.label.potential_net_savings", "Potential net savings"),
                        value.potential_net_savings,
                    ],
                ],
            ),
            _bullets(
                [
                    translator.t("report.value.assumption_prefix", "Assumption: ") + assumption
                    for assumption in value.assumptions
                ]
                or [
                    translator.t(
                        "report.value.no_value_assumptions",
                        "No validated value assumptions recorded.",
                    )
                ]
            ),
        ]
    sections.append(
        Section(
            translator.t("report.section.business_value", "Business value and limitations"),
            value_blocks,
        )
    )

    sections.append(
        Section(
            translator.t("report.section.discovery_evidence", "Discovery evidence"),
            [
                _text(
                    translator.t(
                        "report.discovery.intro",
                        "Answers are assertions from recorded sources; "
                        "confidence is self-reported and is not an "
                        "independently verified probability.",
                    )
                ),
                _table(
                    translator,
                    [
                        translator.t("report.table.question_key", "Question key"),
                        translator.t("report.table.answer", "Answer"),
                        translator.t("report.table.evidence_type", "Evidence type"),
                        translator.t("report.table.source", "Source"),
                        translator.t("report.table.reported_confidence", "Reported confidence"),
                    ],
                    [
                        [a.key, a.value, a.kind.value, a.source, a.confidence]
                        for a in detail.answers
                    ],
                )
                if detail.answers
                else _text(
                    translator.t(
                        "report.discovery.no_answers",
                        "No discovery answers are recorded; critical inputs may be missing.",
                    )
                ),
            ],
        )
    )

    lookup = {item.task_id: item for item in analysis.tasks}
    workflow_blocks: list[Block] = []
    for workflow in detail.workflows:
        workflow_blocks.append(
            _text(translator.t("report.workflow.label", "Workflow: ") + workflow.name)
        )
        for step in sorted(workflow.steps, key=lambda s: s.order):
            workflow_blocks.append(
                _text(
                    translator.t(
                        "report.workflow.step",
                        "Step {order}: {name}. Owner: {owner}; system: {system}; "
                        "inputs: {inputs}; outputs: {outputs}.",
                        order=step.order,
                        name=step.name,
                        owner=_line(translator, step.owner),
                        system=_line(translator, step.system),
                        inputs=_line(translator, step.input_description),
                        outputs=_line(translator, step.output_description),
                    )
                )
            )
            for task in step.tasks:
                result = lookup.get(task.id)
                if result is None:
                    continue
                rec = result.recommendation
                workflow_blocks.extend(
                    [
                        _text(
                            translator.t(
                                "report.workflow.task",
                                "Task: {title} ",
                                title=task.title,
                            )
                            + f"[ID: {task.id}] — "
                            + (
                                task.description
                                or translator.t(
                                    "report.workflow.no_description", "No description recorded"
                                )
                            )
                        ),
                        _table(
                            translator,
                            [
                                translator.t("report.table.dimension", "Dimension"),
                                translator.t(
                                    "report.table.evidence_based_result", "Evidence-based result"
                                ),
                            ],
                            [
                                [
                                    translator.t("report.label.recommendation", "Recommendation"),
                                    rec.category.value,
                                ],
                                [
                                    translator.t("report.label.ai_suitability", "AI suitability"),
                                    _value(translator, result.ai_suitability.value)
                                    + translator.t(
                                        "report.workflow.coverage",
                                        " (coverage {coverage}; {status})",
                                        coverage=translator.percent(result.ai_suitability.coverage),
                                        status=result.ai_suitability.status,
                                    ),
                                ],
                                [
                                    translator.t(
                                        "report.label.technical_feasibility",
                                        "Technical feasibility",
                                    ),
                                    _value(translator, result.technical_feasibility.value)
                                    + translator.t(
                                        "report.workflow.coverage_only",
                                        " (coverage {coverage})",
                                        coverage=translator.percent(
                                            result.technical_feasibility.coverage
                                        ),
                                    ),
                                ],
                                [
                                    translator.t("report.label.data_readiness", "Data readiness"),
                                    _value(translator, result.data_readiness.value)
                                    + translator.t(
                                        "report.workflow.coverage_only",
                                        " (coverage {coverage})",
                                        coverage=translator.percent(result.data_readiness.coverage),
                                    ),
                                ],
                                [
                                    translator.t("report.label.policy_version", "Policy version"),
                                    rec.policy_version,
                                ],
                            ],
                        ),
                        _bullets(
                            [
                                translator.t("report.workflow.reason", "Reason: ") + reason
                                for reason in rec.rationale
                            ]
                            or [
                                translator.t(
                                    "report.workflow.no_rationale", "No rationale available"
                                )
                            ]
                        ),
                        _table(
                            translator,
                            [
                                translator.t("report.table.responsibility", "Responsibility"),
                                translator.t("report.table.boundary", "Boundary"),
                            ],
                            [
                                ["AI", rec.ai_role],
                                [
                                    translator.t(
                                        "report.label.conventional_software",
                                        "Conventional software / rules",
                                    ),
                                    rec.deterministic_role,
                                ],
                                [
                                    translator.t(
                                        "report.label.human_approvals", "Human / approvals"
                                    ),
                                    rec.human_role,
                                ],
                            ],
                        ),
                        _bullets(
                            [
                                translator.t(
                                    "report.workflow.required_control", "Required control: "
                                )
                                + control
                                for control in rec.required_controls
                            ]
                            + [
                                translator.t(
                                    "report.workflow.missing_evidence", "Missing evidence: "
                                )
                                + item
                                for item in rec.missing_evidence
                            ]
                            + [
                                translator.t("report.workflow.alternative", "Alternative: ") + item
                                for item in rec.alternatives
                            ]
                            or [
                                translator.t(
                                    "report.workflow.no_controls",
                                    "No additional controls or alternatives recorded",
                                )
                            ]
                        ),
                        _bullets(
                            [
                                translator.t(
                                    "report.workflow.rule",
                                    "Rule {id} ({severity}): {reason}; control: {control}",
                                    id=hit.id,
                                    severity=hit.severity.value,
                                    reason=hit.reason,
                                    control=_line(translator, hit.required_control),
                                )
                                for hit in result.rule_hits
                            ]
                            or [
                                translator.t(
                                    "report.workflow.no_rule_hits",
                                    "No triggered deterministic policy rules",
                                )
                            ]
                        ),
                        _bullets(
                            [
                                translator.t(
                                    "report.workflow.risk",
                                    "Risk {category}, score {score}/25 ({band}): {description}; "
                                    "controls: {controls}",
                                    category=risk.category.value,
                                    score=risk.score,
                                    band=risk.band.value,
                                    description=risk.description,
                                    controls=_line(translator, risk.required_controls),
                                )
                                for risk in result.risks
                            ]
                            or [
                                translator.t(
                                    "report.workflow.no_scored_risks",
                                    "No scored risk entries supplied; "
                                    "this does not prove absence of risk",
                                )
                            ]
                        ),
                    ]
                )
    sections.append(
        Section(
            translator.t("report.section.workflow", "Workflow, task scores, risks, and decisions"),
            workflow_blocks
            or [
                _text(
                    translator.t("report.workflow.none", "No persisted workflows or tasks found.")
                )
            ],
        )
    )
    sections.append(
        Section(
            translator.t("report.section.warnings", "Analysis warnings and evidence gaps"),
            [
                _bullets(
                    analysis.warnings
                    or [
                        translator.t(
                            "report.warnings.none",
                            "No warnings returned; evidence should still be reviewed",
                        )
                    ]
                ),
            ],
        )
    )

    proposals = proposals or []
    # The starting patterns are policy guidance tied to a category value; they
    # are part of the contract and are never translated.
    pattern_by_category = {
        "traditional_software": "Deterministic typed code and tested rules; "
        "do not use an LLM for exact outputs.",
        "strong_ai_candidate": "Small, verifiable classification/extraction or "
        "retrieval PoC; add evaluation before rollout.",
        "hybrid": "AI only for language interpretation; trusted source APIs "
        "and deterministic workflow for actions.",
        "ai_human_approval": "AI drafts or extracts; deterministic checks and "
        "explicit human approval for consequential actions.",
        "human_led": "Human-led decision; no automation without new evidence "
        "and governance review.",
        "more_discovery": "No architecture commitment until data permissions "
        "and critical evidence gaps are resolved.",
    }
    proposal_blocks: list[Block] = [
        _text(
            translator.t(
                "report.proposals.intro",
                "Deterministic starting patterns below are provisional design suggestions, "
                "not an implementation or validated PoC.",
            )
        ),
        _table(
            translator,
            [
                translator.t("report.table.task", "Task"),
                translator.t("report.table.starting_pattern", "Simplest adequate starting pattern"),
            ],
            [
                [item.task_id, pattern_by_category[item.recommendation.category.value]]
                for item in analysis.tasks
            ],
        ),
        _bullets(
            [
                translator.t(
                    "report.proposals.poc_scope",
                    "Proposed PoC scope: select one representative workflow step; "
                    "do not connect irreversible or external write actions.",
                ),
                translator.t(
                    "report.proposals.evaluation",
                    "Proposed evaluation: establish a human-reviewed ground-truth set and "
                    "observed baselines first; no numeric threshold can be inferred "
                    "without data.",
                ),
                translator.t(
                    "report.proposals.gates",
                    "Go/no-go gates: approved data use, measured output correctness, "
                    "acceptable latency/cost, verifiable fallback, and accountable sign-off.",
                ),
            ]
        ),
        _text(
            translator.t(
                "report.proposals.diagram_intro",
                "The diagrams below show the simplest adequate starting pattern for each "
                "task, drawn deterministically from the recorded recommendation category "
                "and the triggered rules. A diagram is guidance, not an implementation.",
            )
        ),
    ]
    for task in analysis.tasks:
        proposal_blocks.append(_diagram(build_task_diagram(task, translator)))
        proposal_blocks.append(
            _text(
                translator.t(
                    "report.proposals.diagram_notes",
                    "Required controls for {task}: {controls}.",
                    task=task.task_id,
                    controls=(
                        ", ".join(task.recommendation.required_controls)
                        if task.recommendation.required_controls
                        else translator.t("report.value.none_recorded", "None recorded")
                    ),
                )
            )
        )
    proposal_blocks.extend(
        [
            _text(
                translator.t(
                    "report.proposals.unverified_intro",
                    "The following are optional, unverified AI proposals. "
                    "They are neither accepted requirements nor definitive architectural, "
                    "evaluation, or regulatory conclusions.",
                )
            ),
        ]
    )
    for entry in proposals:
        stale = entry.get("assessment_revision") != detail.revision
        proposal_blocks.append(
            _text(
                translator.t(
                    "report.proposals.entry",
                    "Feature: {feature}; provider/model: {provider} / {model}; "
                    "prompt: {prompt}; schema: {schema}; assessment revision: {revision}; "
                    "stale: {stale}.",
                    feature=_line(translator, entry.get("feature")),
                    provider=_line(translator, entry.get("provider")),
                    model=_line(translator, entry.get("model")),
                    prompt=_line(translator, entry.get("prompt_version")),
                    schema=_line(translator, entry.get("schema_version")),
                    revision=_line(translator, entry.get("assessment_revision")),
                    stale=(
                        translator.t("report.value.yes_lowercase", "yes")
                        if stale
                        else translator.t("report.value.no_lowercase", "no")
                    ),
                )
            )
        )
        proposal_blocks.append(
            Block(
                "code",
                json.dumps(entry.get("proposal", {}), ensure_ascii=False, indent=2, sort_keys=True),
            )
        )
    if not proposals:
        proposal_blocks.append(
            _text(
                translator.t(
                    "report.proposals.none",
                    "No optional AI proposals were saved. "
                    "All deterministic recommendations remain available "
                    "without an external AI provider.",
                )
            )
        )
    sections.append(
        Section(
            translator.t("report.section.proposals", "Architecture, PoC, and evaluation proposals"),
            proposal_blocks,
        )
    )

    # Review entries are deliberately not presented as authenticated governance decisions.
    review_blocks: list[Block] = [
        _text(
            translator.t(
                "report.review.intro",
                "Reviewer names are self-reported in this local demo. "
                "A recorded final decision is not production authorization, "
                "and required controls are never waived by an override.",
            )
        ),
    ]
    review_blocks.extend(
        [
            _text(
                translator.t(
                    "report.review.entry",
                    "Reviewer: {reviewer}; category: {category}; "
                    "final: {final}; analysis revision: {revision}; "
                    "rationale: {rationale}; override rationale: {override}; "
                    "identity verified: {verified}.",
                    reviewer=r.reviewer_id,
                    category=r.selected_category.value,
                    final=(
                        translator.t("report.value.yes_lowercase", "yes")
                        if r.is_final
                        else translator.t("report.value.no_lowercase", "no")
                    ),
                    revision=r.analysis_revision,
                    rationale=r.rationale,
                    override=_line(translator, r.override_reason),
                    verified=(
                        translator.t("report.value.no_lowercase", "no")
                        if r.identity_unverified
                        else translator.t("report.value.yes_lowercase", "yes")
                    ),
                )
            )
            for r in detail.reviews
        ]
    )
    if not detail.reviews:
        review_blocks.append(
            _text(
                translator.t(
                    "report.review.none", "No FDE review exists; final decision is pending."
                )
            )
        )
    if latest_final:
        review_blocks.append(
            _text(
                translator.t(
                    "report.review.final_category",
                    "Local demo recorded final category: {category}. "
                    "Human identity remains unverified.",
                    category=latest_final.selected_category.value,
                )
            )
        )
    if provenance is not None:
        review_blocks.extend(_provenance_blocks(translator, provenance))
    else:
        review_blocks.append(
            _text(
                translator.t(
                    "report.review.no_ledger",
                    "No decision input ledger is recorded for this assessment.",
                )
            )
        )
    sections.append(
        Section(
            translator.t("report.section.review", "FDE review and final decision"),
            review_blocks,
        )
    )
    sections.append(
        Section(
            translator.t("report.section.next_steps", "Next steps and no-rollout disclaimer"),
            [
                _bullets(
                    [
                        translator.t(
                            "report.next_steps.validate",
                            "Validate unknowns, provenance, expected quality, data permissions, "
                            "and financial baseline with responsible owners.",
                        ),
                        translator.t(
                            "report.next_steps.evaluate",
                            "Define measurable evaluation cases and go/no-go thresholds "
                            "before committing to a proof of concept.",
                        ),
                        translator.t(
                            "report.next_steps.review",
                            "Review policy controls and alternatives, including the possibility "
                            "of traditional software or no project.",
                        ),
                        translator.t(
                            "report.next_steps.authorization",
                            "Do not treat this local demo report, a model proposal, "
                            "or a reviewer token as production authorization.",
                        ),
                    ]
                ),
            ],
        )
    )
    return sections


def _md(translator: Translator, value: object) -> str:
    return re.sub(r"([\\`*_{}\[\]()#+.!|>~-])", r"\\\1", _line(translator, value))


def render_markdown(
    detail: AssessmentDetail,
    proposals: list[dict] | None = None,
    provenance: ProvenanceView | None = None,
    locale: str = DEFAULT_LOCALE,
) -> str:
    translator = Translator(locale)
    lines = [
        "# " + _md(translator, detail.title),
        "",
        "*"
        + translator.t("report.subtitle", "AI Opportunity Advisor — local demo assessment report")
        + "*",
        "",
    ]
    for section in build_sections(detail, proposals, provenance, translator=translator):
        lines += ["## " + _md(translator, section.title), ""]
        for block in section.blocks:
            if block.kind == "text":
                lines += [_md(translator, block.value), ""]
            elif block.kind == "bullets":
                lines += ["- " + _md(translator, s) for s in block.value] + [""]
            elif block.kind == "table":
                heads, rows = block.value
                lines += [
                    "| " + " | ".join(_md(translator, x) for x in heads) + " |",
                    "| " + " | ".join("---" for _ in heads) + " |",
                ]
                lines += ["| " + " | ".join(_md(translator, x) for x in row) + " |" for row in rows]
                lines.append("")
            elif block.kind == "code":
                lines += ["    " + line for line in str(block.value).splitlines()] + [""]
            elif block.kind == "diagram":
                diagram = block.value
                lines += [render_mermaid(diagram), ""]
                # A plain-text description keeps the picture readable in any
                # Markdown renderer, including one without Mermaid support.
                lines += ["> " + line for line in describe(diagram).splitlines()] + [""]
    return "\n".join(lines).rstrip() + "\n"


def render_html(
    detail: AssessmentDetail,
    proposals: list[dict] | None = None,
    provenance: ProvenanceView | None = None,
    locale: str = DEFAULT_LOCALE,
) -> str:
    translator = Translator(locale)
    parts = [
        '<!doctype html><html lang="' + translator.locale_code + '"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
        "style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'\">",
        "<title>" + html_escape(detail.title) + " — AI Opportunity Advisor</title>",
        "<style>body{max-width:920px;margin:48px auto;padding:0 18px;font:16px/1.5 "
        "system-ui,sans-serif;color:#1d2939;}h1,h2{line-height:1.25;}h2{margin-top:36px;"
        "border-bottom:1px solid #cdd5df;padding-bottom:8px;}table{width:100%;"
        "border-collapse:collapse;margin:14px 0;}th,td{border:1px solid #d0d7e2;padding:8px;"
        "text-align:left;vertical-align:top;overflow-wrap:anywhere;}th{background:#edf2f7;}"
        "pre{overflow:auto;background:#f3f6fa;padding:14px;}li{margin:5px 0;} "
        "figure{margin:18px 0;padding:12px;border:1px solid #d0d7e2;border-radius:8px;}"
        "figure svg{max-width:100%;height:auto;display:block;}"
        "figcaption{margin-top:10px;font-size:13px;color:#5b636d;} "
        ".notice{color:#7d2b2b;font-weight:700;}</style>",
        "</head><body><h1>" + html_escape(detail.title) + "</h1>",
        '<p class="notice">'
        + html_escape(
            translator.t(
                "report.notice",
                "AI Opportunity Advisor · advisory only · local demo · not rollout authorization",
            )
        )
        + "</p>",
    ]
    for section in build_sections(detail, proposals, provenance, translator=translator):
        parts.append("<section><h2>" + html_escape(section.title) + "</h2>")
        for block in section.blocks:
            if block.kind == "text":
                parts.append("<p>" + html_escape(str(block.value)) + "</p>")
            elif block.kind == "bullets":
                parts.append(
                    "<ul>"
                    + "".join("<li>" + html_escape(s) + "</li>" for s in block.value)
                    + "</ul>"
                )
            elif block.kind == "table":
                heads, rows = block.value
                parts.append(
                    "<table><thead><tr>"
                    + "".join("<th>" + html_escape(x) + "</th>" for x in heads)
                    + "</tr></thead><tbody>"
                )
                parts.extend(
                    "<tr>" + "".join("<td>" + html_escape(x) + "</td>" for x in row) + "</tr>"
                    for row in rows
                )
                parts.append("</tbody></table>")
            elif block.kind == "code":
                parts.append("<pre>" + html_escape(str(block.value)) + "</pre>")
            elif block.kind == "diagram":
                diagram = block.value
                parts.append(
                    "<figure>"
                    + render_svg(diagram)
                    + "<figcaption>"
                    + html_escape(diagram.caption)
                    + "</figcaption></figure>"
                )
        parts.append("</section>")
    parts.append("</body></html>\n")
    return "\n".join(parts)
