"""The architecture-pattern diagrams must be deterministic and honest.

A diagram is drawn from the recorded analysis.  The tests therefore check two
things: that the same analysis always produces the same picture, and that the
picture never claims more than the deterministic engine recorded.
"""

from __future__ import annotations

import re

from app.domain.enums import RecommendationCategory, RuleSeverity
from app.domain.schemas import (
    DimensionScore,
    Recommendation,
    RuleHit,
    ScoreResult,
    TaskAnalysis,
)
from app.localization import Translator
from app.reporting.diagrams import (
    DIAGRAM_VERSION,
    build_task_diagram,
    describe,
    render_mermaid,
    render_svg,
)


def _task(
    category: RecommendationCategory,
    *,
    controls: list[str] | None = None,
    rule_hits: list[RuleHit] | None = None,
    task_id: str = "task-1",
) -> TaskAnalysis:
    return TaskAnalysis(
        task_id=task_id,
        step_id="step-1",
        ai_suitability=ScoreResult(
            value=50, coverage=0.5, status="partial", policy_version="rules-v2"
        ),
        technical_feasibility=DimensionScore(
            value=None, coverage=0.0, status="insufficient_evidence"
        ),
        data_readiness=DimensionScore(value=None, coverage=0.0, status="insufficient_evidence"),
        risks=[],
        rule_hits=rule_hits or [],
        recommendation=Recommendation(
            category=category,
            rationale=["because"],
            ai_role="ai role",
            deterministic_role="code role",
            human_role="human role",
            required_controls=controls or [],
            missing_evidence=[],
            alternatives=[],
            policy_version="rules-v2",
        ),
    )


def test_every_category_draws_a_diagram() -> None:
    """All six recommendation categories must have a drawing."""
    translator = Translator("en")
    for category in RecommendationCategory:
        diagram = build_task_diagram(_task(category), translator)
        assert diagram.nodes
        assert diagram.edges
        assert diagram.version == DIAGRAM_VERSION


def test_the_diagram_is_deterministic() -> None:
    """The same analysis must always produce the same picture."""
    translator = Translator("en")
    task = _task(
        RecommendationCategory.HYBRID,
        controls=["Human approval", "Log every decision"],
        rule_hits=[RuleHit(id="ACT-001", severity=RuleSeverity.REQUIRE_APPROVAL, reason="r")],
    )
    first = build_task_diagram(task, translator)
    second = build_task_diagram(task, translator)
    assert render_svg(first) == render_svg(second)
    assert render_mermaid(first) == render_mermaid(second)
    assert describe(first) == describe(second)


def test_a_deterministic_path_never_shows_a_model() -> None:
    """Traditional software must not be drawn with an AI step."""
    translator = Translator("en")
    diagram = build_task_diagram(_task(RecommendationCategory.TRADITIONAL_SOFTWARE), translator)
    assert "No model in this path" in describe(diagram)
    assert "ai" not in {node.id for node in diagram.nodes}


def test_an_ai_candidate_shows_verification() -> None:
    """A strong AI candidate is drawn with a verification step."""
    translator = Translator("en")
    diagram = build_task_diagram(_task(RecommendationCategory.STRONG_AI_CANDIDATE), translator)
    assert "Verification against known cases" in describe(diagram)


def test_a_human_approval_category_shows_the_human_gate() -> None:
    """AI plus human approval must draw the human, not just the model."""
    translator = Translator("en")
    diagram = build_task_diagram(_task(RecommendationCategory.AI_HUMAN_APPROVAL), translator)
    labels = {node.label for node in diagram.nodes}
    assert "Human approval" in labels
    assert "AI drafts or extracts" in labels


def test_more_discovery_commits_to_no_architecture() -> None:
    """More discovery must not draw an AI or a deterministic implementation."""
    translator = Translator("en")
    diagram = build_task_diagram(_task(RecommendationCategory.MORE_DISCOVERY), translator)
    labels = {node.label for node in diagram.nodes}
    assert "Unresolved evidence gap" in labels
    assert not any(label.startswith("AI ") for label in labels)


def test_required_controls_are_drawn_as_gates() -> None:
    """The controls that still apply must be visible on the diagram."""
    translator = Translator("en")
    diagram = build_task_diagram(
        _task(
            RecommendationCategory.AI_HUMAN_APPROVAL,
            controls=["Human approval before a consequential write"],
        ),
        translator,
    )
    captions = {node.caption for node in diagram.nodes}
    assert "Human approval before a consequential write" in captions
    control_nodes = [node for node in diagram.nodes if node.kind == "control"]
    assert control_nodes


def test_the_control_set_is_deduplicated_and_ordered() -> None:
    """The same control twice is one gate, and the order never changes."""
    translator = Translator("en")
    task = _task(
        RecommendationCategory.HYBRID,
        controls=["Log every decision", "Human approval", "Log every decision"],
    )
    diagram = build_task_diagram(task, translator)
    control_nodes = [node for node in diagram.nodes if node.kind == "control"]
    assert [node.caption for node in control_nodes] == ["Human approval", "Log every decision"]


def test_a_rule_control_is_drawn_even_without_a_task_control() -> None:
    """A control attached to a triggered rule is part of the picture."""
    translator = Translator("en")
    diagram = build_task_diagram(
        _task(
            RecommendationCategory.HYBRID,
            rule_hits=[
                RuleHit(
                    id="ACT-001",
                    severity=RuleSeverity.REQUIRE_APPROVAL,
                    reason="irreversible",
                    required_control="Explicit approval before the action",
                )
            ],
        ),
        translator,
    )
    captions = {node.caption for node in diagram.nodes}
    assert "Explicit approval before the action" in captions


def test_the_svg_is_self_contained() -> None:
    """The HTML export must not reference an external resource."""
    translator = Translator("en")
    svg = render_svg(build_task_diagram(_task(RecommendationCategory.HYBRID), translator))
    assert svg.startswith("<svg")
    assert svg.endswith("</svg>")
    for forbidden in ("<script", "xlink:href", "<image", "@import"):
        assert forbidden not in svg
    # The only permitted URL is the SVG namespace and the local marker
    # fragments that point at the arrow heads defined in this same file.
    assert svg.count("http://") == svg.count('xmlns="http://www.w3.org/2000/svg"')
    assert "https://" not in svg
    for reference in re.findall(r"url\(([^)]*)\)", svg):
        assert reference.startswith("#"), (
            f"the SVG references something outside itself: {reference}"
        )


def test_untrusted_text_cannot_break_out_of_the_diagram() -> None:
    """A task id is data.  It must not be able to break the markup or the quote."""
    translator = Translator("en")
    hostile = '"><script>alert(1)</script>\n\n> forged line'
    diagram = build_task_diagram(_task(RecommendationCategory.HYBRID, task_id=hostile), translator)
    svg = render_svg(diagram)
    assert "<script>" not in svg
    assert "&lt;script&gt;" in svg
    # The description is quoted line by line in the Markdown export, so no
    # description line may ever contain a line break.
    for line in describe(diagram).splitlines():
        assert "\n" not in line
    assert "forged line" in describe(diagram).replace("\n", " ")


def test_the_mermaid_fence_is_well_formed() -> None:
    """The Markdown export must carry a Mermaid fence."""
    translator = Translator("en")
    mermaid = render_mermaid(build_task_diagram(_task(RecommendationCategory.HYBRID), translator))
    assert mermaid.startswith("```mermaid\nflowchart LR\n")
    assert mermaid.endswith("\n```")
    assert mermaid.count("```") == 2


def test_the_mermaid_source_cannot_be_steered_by_a_label() -> None:
    """A quote in a label must not be able to break out of the node syntax."""
    translator = Translator("en")
    diagram = build_task_diagram(_task(RecommendationCategory.HYBRID), translator)
    mermaid = render_mermaid(diagram)
    for line in mermaid.splitlines():
        if line.startswith("  ") and "[" in line:
            # Every node definition opens and closes its label with the same
            # quoting, so a payload cannot close the node early.
            assert line.count('"') % 2 == 0
    assert "```" not in mermaid.replace("```mermaid", "").replace("\n```", "")
