"""Deterministic architecture-pattern diagrams for the assessment report.

A diagram is *derived* from the recorded analysis: the recommendation category
decides the shape of the flow and the triggered rules decide which control
gates are drawn.  Nothing here is invented, nothing is inferred from an image,
and no diagram ever implies a capability the deterministic engine did not
record.  The product rule stays visible in the picture: **AI assists. Rules
enforce. Humans decide.**

The module owns one *specification* (:class:`Diagram`) and three renderers:

* :func:`render_mermaid` for the Markdown export (GitHub-flavoured Markdown
  renders Mermaid fences natively);
* :func:`render_svg` for the self-contained HTML export (inline SVG, no
  external resource, so the report's strict Content-Security-Policy holds);
* :func:`draw` for the PDF export, which draws the same spec with PDF
  primitives instead of embedding an image.

Keeping a single spec is what makes the three exports agree; each renderer is
a pure function of the spec, so the picture is reproducible byte for byte.
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape as html_escape
import re

from app.domain.enums import RecommendationCategory
from app.domain.schemas import TaskAnalysis
from app.localization import Translator

#: Bumped when the shape of a diagram changes, so a report can state which
#: version of the picture it contains.
DIAGRAM_VERSION = "diagram-v1"

#: Node kinds.  The kind decides the visual treatment, never the meaning.
NodeKind = str

#: The maximum number of control gates drawn on one diagram.  The remaining
#: controls are counted in the caption instead of being silently dropped.
MAX_CONTROL_NODES = 3

#: The maximum number of characters on one SVG line.
SVG_LINE_CHARS = 22

#: The node every pattern starts at.  A required control gates the whole flow,
#: so the control edges attach here rather than to an arbitrary step.
FLOW_ENTRY = "input"


@dataclass(frozen=True)
class DiagramNode:
    """One box in a diagram."""

    id: str
    label: str
    kind: NodeKind
    caption: str = ""


@dataclass(frozen=True)
class DiagramEdge:
    """One arrow in a diagram."""

    source: str
    target: str
    label: str = ""
    dashed: bool = False


@dataclass(frozen=True)
class Diagram:
    """A complete, renderer-independent diagram specification."""

    version: str
    title: str
    caption: str
    nodes: tuple[DiagramNode, ...]
    edges: tuple[DiagramEdge, ...]
    notes: tuple[str, ...] = ()


def _node(
    node_id: str, label: str, kind: NodeKind, caption: str = ""
) -> DiagramNode:
    return DiagramNode(id=node_id, label=_single_line(label), kind=kind, caption=_single_line(caption))


def _single_line(text: str) -> str:
    """Collapse whitespace so untrusted text cannot break the layout.

    A task id or a control description is data, not markup.  Flattening it to
    one line keeps it inside the box in the SVG, inside the node syntax in
    Mermaid, and inside the block quote of the Markdown export.
    """
    return re.sub(r"\s+", " ", str(text)).strip()


def _chain(nodes: list[DiagramNode]) -> tuple[DiagramNode, ...]:
    """Connect nodes left to right and return them as an immutable tuple."""
    return tuple(nodes)


def _edges(
    nodes: list[DiagramNode], edges: list[tuple[str, str, str, bool]] | None = None
) -> tuple[DiagramEdge, ...]:
    """Chain the flow nodes and append the explicit (usually dashed) edges.

    Only the nodes that carry the *flow* are chained.  A control gate and the
    "no model" annotation are annotations on that flow, never steps in it, so
    they are attached with their own dashed edges instead.
    """
    flow = [node for node in nodes if node.kind not in {"control", "excluded"}]
    result: list[DiagramEdge] = []
    for first, second in zip(flow, flow[1:]):
        result.append(DiagramEdge(source=first.id, target=second.id))
    for source, target, label, dashed in edges or []:
        result.append(DiagramEdge(source=source, target=target, label=label, dashed=dashed))
    return tuple(result)


def _pattern_nodes(
    category: RecommendationCategory, translator: Translator
) -> list[DiagramNode]:
    """The main flow for a recommendation category.

    The category is the contract value; the labels around it are text.
    """
    if category is RecommendationCategory.TRADITIONAL_SOFTWARE:
        return [
            _node("input", translator.t("report.diagram.input", "Task input"), "input"),
            _node(
                "logic",
                translator.t("report.diagram.deterministic", "Deterministic rules and code"),
                "deterministic",
            ),
            _node("output", translator.t("report.diagram.output", "Output"), "output"),
        ]
    if category is RecommendationCategory.STRONG_AI_CANDIDATE:
        return [
            _node("input", translator.t("report.diagram.input", "Task input"), "input"),
            _node(
                "ai",
                translator.t("report.diagram.ai_extract", "AI extraction or classification"),
                "ai",
            ),
            _node(
                "verify",
                translator.t("report.diagram.verification", "Verification against known cases"),
                "deterministic",
            ),
            _node("output", translator.t("report.diagram.output", "Output"), "output"),
        ]
    if category is RecommendationCategory.HYBRID:
        return [
            _node("input", translator.t("report.diagram.input", "Task input"), "input"),
            _node(
                "ai",
                translator.t("report.diagram.ai_interpret", "AI interprets language only"),
                "ai",
            ),
            _node(
                "lookup",
                translator.t("report.diagram.lookup", "Trusted source-system lookup"),
                "deterministic",
            ),
            _node(
                "policy",
                translator.t("report.diagram.policy_rules", "Deterministic policy rules"),
                "deterministic",
            ),
            _node("output", translator.t("report.diagram.output", "Output"), "output"),
        ]
    if category is RecommendationCategory.AI_HUMAN_APPROVAL:
        return [
            _node("input", translator.t("report.diagram.input", "Task input"), "input"),
            _node("ai", translator.t("report.diagram.ai_draft", "AI drafts or extracts"), "ai"),
            _node(
                "checks",
                translator.t("report.diagram.checks", "Deterministic checks"),
                "deterministic",
            ),
            _node(
                "approval",
                translator.t("report.diagram.human_approval", "Human approval"),
                "human",
            ),
            _node("output", translator.t("report.diagram.output", "Output"), "output"),
        ]
    if category is RecommendationCategory.HUMAN_LED:
        return [
            _node("input", translator.t("report.diagram.input", "Task input"), "input"),
            _node(
                "human",
                translator.t("report.diagram.human_decision", "Human decision"),
                "human",
            ),
            _node("output", translator.t("report.diagram.output", "Output"), "output"),
        ]
    # MORE_DISCOVERY: no architecture commitment is drawn at all.
    return [
        _node("input", translator.t("report.diagram.input", "Task input"), "input"),
        _node(
            "gap",
            translator.t("report.diagram.evidence_gap", "Unresolved evidence gap"),
            "evidence",
        ),
        _node(
            "assess",
            translator.t("report.diagram.fde_assessment", "FDE assesses the evidence"),
            "human",
        ),
        _node(
            "output",
            translator.t("report.diagram.continue_or_stop", "Continue, or stop"),
            "output",
        ),
    ]


def _control_nodes(
    task: TaskAnalysis, translator: Translator
) -> tuple[list[DiagramNode], list[tuple[str, str, str, bool]], int]:
    """The control gates that apply to this task.

    The distinct required controls of the task and of every triggered rule are
    collected, de-duplicated and sorted so that the same analysis always
    produces the same picture.  At most :data:`MAX_CONTROL_NODES` gates are
    drawn; the rest are counted in the caption.
    """
    controls: set[str] = set()
    for control in task.recommendation.required_controls:
        text = str(control).strip()
        if text:
            controls.add(text)
    for hit in task.rule_hits:
        if hit.required_control:
            text = str(hit.required_control).strip()
            if text:
                controls.add(text)
    ordered = sorted(controls)
    shown = ordered[:MAX_CONTROL_NODES]
    hidden = len(ordered) - len(shown)

    nodes: list[DiagramNode] = []
    edges: list[tuple[str, str, str, bool]] = []
    for index, control in enumerate(shown):
        node_id = f"control{index + 1}"
        nodes.append(
            _node(
                node_id,
                translator.t("report.diagram.control", "Required control"),
                "control",
                caption=control,
            )
        )
        edges.append((node_id, FLOW_ENTRY, "", True))
    return nodes, edges, hidden


def build_task_diagram(task: TaskAnalysis, translator: Translator) -> Diagram:
    """Build the architecture-pattern diagram of one analysed task.

    The diagram is advisory: it shows the *simplest adequate starting pattern*
    for the recorded category, plus the controls that still apply.  It never
    states that the pattern is implemented, approved or validated.
    """
    category = task.recommendation.category
    nodes = _pattern_nodes(category, translator)
    control_nodes, control_edges, hidden = _control_nodes(task, translator)

    # A deterministic path never routes content through a model.
    if category is RecommendationCategory.TRADITIONAL_SOFTWARE:
        nodes.append(
            _node(
                "nolm",
                translator.t("report.diagram.no_llm", "No model in this path"),
                "excluded",
            )
        )
        control_edges.append(("nolm", "logic", "", True))

    all_nodes = nodes + control_nodes
    edges = _edges(nodes, control_edges)
    notes = [
        translator.t(
            "report.diagram.note.advisory",
            "Starting pattern only. It is not an implementation, a commitment, "
            "or a validated design.",
        )
    ]
    if hidden > 0:
        notes.append(
            translator.t(
                "report.diagram.note.more_controls",
                "{count} further required control(s) are listed in the table above.",
                count=hidden,
            )
        )
    caption = _single_line(
        translator.t(
            "report.diagram.caption",
            "Architecture pattern for task {task} (category: {category}).",
            task=task.task_id,
            category=category.value,
        )
    )
    return Diagram(
        version=DIAGRAM_VERSION,
        title=_single_line(
            translator.t(
                "report.diagram.title",
                "Architecture pattern: {task}",
                task=task.task_id,
            )
        ),
        caption=caption,
        nodes=_chain(all_nodes),
        edges=edges,
        notes=tuple(notes),
    )


# --- rendering -----------------------------------------------------------------

#: Fill and stroke per node kind.  The palette is deliberately quiet: the
#: picture must read as documentation, not as a dashboard.
_NODE_STYLE: dict[str, tuple[str, str, str]] = {
    # kind: (fill, stroke, text)
    "input": ("#eef2f7", "#7a8899", "#1d2939"),
    "output": ("#eef2f7", "#7a8899", "#1d2939"),
    "ai": ("#e5f0fb", "#2f6fa8", "#12314d"),
    "deterministic": ("#e6f4ea", "#2e7d43", "#123d1d"),
    "human": ("#fdf3e2", "#a5762a", "#4a3410"),
    "control": ("#fbeaea", "#a34040", "#5c1f1f"),
    "evidence": ("#f3ecfa", "#6f4b9e", "#301a4d"),
    "excluded": ("#f7f7f8", "#9aa3ad", "#5b636d"),
}

_SVG_BOX_WIDTH = 150
_SVG_BOX_HEIGHT = 52
_SVG_CONTROL_HEIGHT = 80
_SVG_GAP = 30
_SVG_PADDING = 12


def _wrap(text: str, width: int = SVG_LINE_CHARS) -> list[str]:
    """Split a label into lines that fit the box, deterministically."""
    words = str(text).split()
    if not words:
        return [""]
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        if len(current) + 1 + len(word) <= width:
            current = f"{current} {word}"
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def _svg_escape(text: str) -> str:
    return html_escape(str(text), quote=True)


def _flow_nodes(diagram: Diagram) -> list[DiagramNode]:
    """The nodes that carry the flow, in order."""
    return [node for node in diagram.nodes if node.kind not in {"control", "excluded"}]


def render_svg(diagram: Diagram) -> str:
    """Render the diagram as a self-contained inline SVG fragment.

    The layout is a horizontal flow with a *gate bar* above it: the required
    controls sit on the bar and the bar spans the whole flow, so a reader sees
    at once that the control gates the pattern instead of one of its steps.

    No external resource is referenced, so the report's ``default-src 'none'``
    Content-Security-Policy is preserved.
    """
    flow = _flow_nodes(diagram)
    controls = [node for node in diagram.nodes if node.kind == "control"]
    excluded = [node for node in diagram.nodes if node.kind == "excluded"]

    columns = max(len(flow), 1)
    flow_width = columns * _SVG_BOX_WIDTH + (columns - 1) * _SVG_GAP
    width = _SVG_PADDING * 2 + flow_width

    control_y = _SVG_PADDING
    gate_y = control_y + _SVG_CONTROL_HEIGHT + 24 if controls else 0
    flow_y = (gate_y + 26) if controls else _SVG_PADDING
    excluded_y = flow_y + _SVG_BOX_HEIGHT + 30 if excluded else 0
    height = (
        excluded_y + _SVG_BOX_HEIGHT + _SVG_PADDING
        if excluded
        else flow_y + _SVG_BOX_HEIGHT + _SVG_PADDING
    )

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" role="img" '
        f'aria-label="{_svg_escape(diagram.caption)}">',
        '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" '
        'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
        '<path d="M 0 0 L 10 5 L 0 10 z" fill="#5b636d"/></marker>'
        '<marker id="arrow-dashed" viewBox="0 0 10 10" refX="9" refY="5" '
        'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
        '<path d="M 0 0 L 10 5 L 0 10 z" fill="#a34040"/></marker></defs>',
    ]

    positions: dict[str, tuple[int, int, int, int]] = {}
    for index, node in enumerate(flow):
        x = _SVG_PADDING + index * (_SVG_BOX_WIDTH + _SVG_GAP)
        positions[node.id] = (x, flow_y, _SVG_BOX_WIDTH, _SVG_BOX_HEIGHT)

    if controls:
        count = len(controls)
        # Spread the controls evenly across the width of the flow.
        slack = max(flow_width - count * _SVG_BOX_WIDTH, 0)
        step = slack / (count + 1)
        for index, node in enumerate(controls):
            x = round(_SVG_PADDING + step * (index + 1) + index * _SVG_BOX_WIDTH)
            positions[node.id] = (x, control_y, _SVG_BOX_WIDTH, _SVG_CONTROL_HEIGHT)

    for node in excluded:
        # The annotation is centred under the step it qualifies.
        anchor = flow[1] if len(flow) > 1 else flow[0]
        ax, _ay, aw, _ah = positions[anchor.id]
        x = ax + aw // 2 - _SVG_BOX_WIDTH // 2
        positions[node.id] = (x, excluded_y, _SVG_BOX_WIDTH, _SVG_BOX_HEIGHT)

    # The gate bar: a horizontal line the controls hang from.
    if controls:
        parts.append(
            f'<line x1="{_SVG_PADDING}" y1="{gate_y}" '
            f'x2="{width - _SVG_PADDING}" y2="{gate_y}" stroke="#a34040" '
            f'stroke-width="1.3" stroke-dasharray="5 4"/>'
        )
        for node in controls:
            x, _y, w, _h = positions[node.id]
            parts.append(
                f'<line x1="{x + w // 2}" y1="{control_y + _SVG_CONTROL_HEIGHT}" '
                f'x2="{x + w // 2}" y2="{gate_y}" stroke="#a34040" '
                f'stroke-width="1.3" stroke-dasharray="5 4"/>'
            )

    # Edges next, so the boxes paint over the arrow heads.  A control edge is
    # represented by the gate bar, so it is not drawn a second time as an
    # arrow that would cross the whole picture.
    for edge in diagram.edges:
        if edge.source in positions and positions[edge.source][3] == _SVG_CONTROL_HEIGHT:
            continue
        if edge.source not in positions or edge.target not in positions:
            continue
        sx, sy, sw, sh = positions[edge.source]
        tx, ty, tw, th = positions[edge.target]
        if abs(sy - ty) < 2:
            x1, y1 = sx + sw, sy + sh // 2
            x2, y2 = tx, ty + th // 2
        else:
            x1, y1 = sx + sw // 2, sy + sh
            x2, y2 = tx + tw // 2, ty
        marker = "arrow-dashed" if edge.dashed else "arrow"
        dash = ' stroke-dasharray="5 4"' if edge.dashed else ""
        stroke = "#a34040" if edge.dashed else "#5b636d"
        parts.append(
            f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{stroke}" '
            f'stroke-width="1.5"{dash} marker-end="url(#{marker})"/>'
        )

    for node in diagram.nodes:
        if node.id not in positions:
            continue
        x, y, w, h = positions[node.id]
        fill, stroke, text_color = _NODE_STYLE.get(node.kind, _NODE_STYLE["input"])
        dash = ' stroke-dasharray="5 4"' if node.kind in {"control", "excluded"} else ""
        parts.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="7" fill="{fill}" '
            f'stroke="{stroke}" stroke-width="1.3"{dash}/>'
        )
        lines = _wrap(node.label)
        caption_lines = _wrap(node.caption, 26) if node.caption else []
        if caption_lines:
            # The caption belongs inside the box, so it can never collide with
            # the gate bar or the flow below it.
            label_y = y + 22
            for line_index, line in enumerate(lines):
                parts.append(
                    f'<text x="{x + w // 2}" y="{label_y + line_index * 15}" '
                    f'font-family="system-ui,sans-serif" font-size="12" fill="{text_color}" '
                    f'text-anchor="middle">{_svg_escape(line)}</text>'
                )
            caption_y = label_y + len(lines) * 15 + 4
            for line_index, line in enumerate(caption_lines):
                parts.append(
                    f'<text x="{x + w // 2}" y="{caption_y + line_index * 13}" '
                    f'font-family="system-ui,sans-serif" font-size="10" fill="{text_color}" '
                    f'text-anchor="middle">{_svg_escape(line)}</text>'
                )
            continue
        start_y = y + h // 2 - (len(lines) - 1) * 8 + 4
        for line_index, line in enumerate(lines):
            parts.append(
                f'<text x="{x + w // 2}" y="{start_y + line_index * 16}" '
                f'font-family="system-ui,sans-serif" font-size="12" fill="{text_color}" '
                f'text-anchor="middle">{_svg_escape(line)}</text>'
            )

    parts.append("</svg>")
    return "".join(parts)

def render_mermaid(diagram: Diagram) -> str:
    """Render the diagram as a Mermaid flowchart for the Markdown export."""
    lines = ["```mermaid", "flowchart LR"]
    shape: dict[str, str] = {}
    for node in diagram.nodes:
        if node.kind == "ai":
            shape[node.id] = f'  {node.id}["{_mermaid_text(node.label)}"]'
        elif node.kind == "human":
            shape[node.id] = f'  {node.id}(("{_mermaid_text(node.label)}"))'
        elif node.kind == "control":
            shape[node.id] = f'  {node.id}[/"{_mermaid_text(node.label)}"/]'
        elif node.kind == "excluded":
            shape[node.id] = f'  {node.id}[("{_mermaid_text(node.label)}")]'
        elif node.kind == "evidence":
            shape[node.id] = f'  {node.id}{{"{_mermaid_text(node.label)}"}}'
        else:
            shape[node.id] = f'  {node.id}["{_mermaid_text(node.label)}"]'
    lines.extend(shape[node.id] for node in diagram.nodes)
    for edge in diagram.edges:
        arrow = "-.->" if edge.dashed else "-->"
        if edge.label:
            lines.append(f'  {edge.source} {arrow} |"{_mermaid_text(edge.label)}"| {edge.target}')
        else:
            lines.append(f"  {edge.source} {arrow} {edge.target}")
    lines.append("```")
    return "\n".join(lines)


def _mermaid_text(text: str) -> str:
    """Quote a label so Mermaid cannot be steered by untrusted content."""
    return str(text).replace('"', "'").replace("\n", " ").strip()


def describe(diagram: Diagram) -> str:
    """A plain-text description of the diagram, used as an accessible fallback."""
    lines = [diagram.caption]
    for node in diagram.nodes:
        caption = f" — {node.caption}" if node.caption else ""
        lines.append(f"- {node.label}{caption}")
    lines.extend(f"- {note}" for note in diagram.notes)
    return "\n".join(lines)
