"""PDF export of the assessment report.

The PDF is rendered from the *same* view model as the Markdown and the HTML
export (:func:`app.reporting.render.build_sections`), so the three artefacts
cannot drift apart: a reader of the PDF reads the same statements, the same
labels and the same caveats as a reader of the Markdown export.

The export stays advisory.  The notice, the "no rollout authorization" wording
and the label of every value are carried over unchanged; nothing is summarised
away and nothing is invented.

Two properties are deliberate:

* **Text is never silently lost.**  The PDF uses the Bitstream Vera font that
  ReportLab ships, which covers Latin, Greek and Cyrillic.  A character outside
  that range is replaced by a visible placeholder and *counted*, and the count
  is returned to the caller so the API can state it in a response header and
  the PDF can state it in a visible note.  The Markdown export always contains
  the complete text.
* **The diagram is drawn, not embedded.**  The architecture diagram is drawn
  with PDF primitives from the same specification the SVG renderer uses, so the
  PDF carries no external image and no external resource.
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Flowable,
    Frame,
    LongTable,
    PageTemplate,
    Paragraph,
    Preformatted,
    Spacer,
    TableStyle,
)

from app.domain.persistence import AssessmentDetail
from app.domain.portfolio import ProvenanceView
from app.localization import DEFAULT_LOCALE, Translator
from app.reporting.diagrams import Diagram, DiagramNode
from app.reporting.render import Block, build_sections

#: Bumped when the PDF layout changes.
PDF_VERSION = "1.0"

#: The character that stands in for a glyph the PDF font cannot draw.
UNRENDERABLE = "?"

#: The palette, shared with the SVG renderer so the two exports look alike.
_FILL = {
    "input": colors.HexColor("#eef2f7"),
    "output": colors.HexColor("#eef2f7"),
    "ai": colors.HexColor("#e5f0fb"),
    "deterministic": colors.HexColor("#e6f4ea"),
    "human": colors.HexColor("#fdf3e2"),
    "control": colors.HexColor("#fbeaea"),
    "evidence": colors.HexColor("#f3ecfa"),
    "excluded": colors.HexColor("#f7f7f8"),
}
_STROKE = {
    "input": colors.HexColor("#7a8899"),
    "output": colors.HexColor("#7a8899"),
    "ai": colors.HexColor("#2f6fa8"),
    "deterministic": colors.HexColor("#2e7d43"),
    "human": colors.HexColor("#a5762a"),
    "control": colors.HexColor("#a34040"),
    "evidence": colors.HexColor("#6f4b9e"),
    "excluded": colors.HexColor("#9aa3ad"),
}
_TEXT = {
    "input": colors.HexColor("#1d2939"),
    "output": colors.HexColor("#1d2939"),
    "ai": colors.HexColor("#12314d"),
    "deterministic": colors.HexColor("#123d1d"),
    "human": colors.HexColor("#4a3410"),
    "control": colors.HexColor("#5c1f1f"),
    "evidence": colors.HexColor("#301a4d"),
    "excluded": colors.HexColor("#5b636d"),
}

_ARROW = colors.HexColor("#5b636d")
_ARROW_CONTROL = colors.HexColor("#a34040")


@dataclass(frozen=True)
class PdfResult:
    """The rendered PDF and what the caller must be told about it."""

    data: bytes
    unrenderable_characters: int


class _Coverage:
    """Knows which characters the PDF font can draw.

    The font is registered once per process.  The coverage check is the set of
    code points the font actually has a glyph for, so a character is only
    replaced when the font genuinely cannot draw it.
    """

    _registered = False

    def __init__(self) -> None:
        self._coverage: set[int] | None = None

    def register(self) -> None:
        if self._registered:
            return
        import reportlab

        fonts = f"{reportlab.__path__[0]}/fonts"
        # ``asciiReadable`` keeps the ASCII code points in the subset, so the
        # text stays readable to a text extractor as well as to a reader.  The
        # ToUnicode map is written for the remaining characters.
        pdfmetrics.registerFont(TTFont("Vera", f"{fonts}/Vera.ttf", asciiReadable=True))
        pdfmetrics.registerFont(TTFont("VeraBd", f"{fonts}/VeraBd.ttf", asciiReadable=True))
        pdfmetrics.registerFont(TTFont("VeraIt", f"{fonts}/VeraIt.ttf", asciiReadable=True))
        pdfmetrics.registerFont(TTFont("VeraBI", f"{fonts}/VeraBI.ttf", asciiReadable=True))
        pdfmetrics.registerFontFamily(
            "Vera", normal="Vera", bold="VeraBd", italic="VeraIt", boldItalic="VeraBI"
        )
        self._registered = True

    def _load(self) -> set[int]:
        if self._coverage is None:
            self.register()
            face = pdfmetrics.getFont("Vera").face
            self._coverage = set(face.charToGlyph.keys())
        return self._coverage

    def fits(self, character: str) -> bool:
        return ord(character) in self._load()

    def scrub(self, text: str) -> tuple[str, int]:
        """Replace the characters the font cannot draw, and count them."""
        coverage = self._load()
        missing = 0
        out: list[str] = []
        for character in text:
            if ord(character) in coverage:
                out.append(character)
            else:
                missing += 1
                out.append(UNRENDERABLE)
        return "".join(out), missing


_COVERAGE = _Coverage()


class _Counter:
    """Counts the characters that could not be drawn across the whole document."""

    def __init__(self) -> None:
        self.total = 0

    def scrub(self, text: str) -> str:
        cleaned, missing = _COVERAGE.scrub(text)
        self.total += missing
        return cleaned


def _styles() -> dict[str, ParagraphStyle]:
    body = ParagraphStyle(
        "body",
        fontName="Vera",
        fontSize=9,
        leading=13,
        alignment=TA_LEFT,
        spaceAfter=5,
        textColor=colors.HexColor("#1d2939"),
    )
    return {
        "title": ParagraphStyle(
            "title",
            parent=body,
            fontName="VeraBd",
            fontSize=17,
            leading=21,
            spaceAfter=4,
        ),
        "subtitle": ParagraphStyle(
            "subtitle",
            parent=body,
            fontName="VeraIt",
            fontSize=9,
            textColor=colors.HexColor("#5b636d"),
            spaceAfter=8,
        ),
        "notice": ParagraphStyle(
            "notice",
            parent=body,
            fontName="VeraBd",
            textColor=colors.HexColor("#7d2b2b"),
            spaceAfter=10,
        ),
        "heading": ParagraphStyle(
            "heading",
            parent=body,
            fontName="VeraBd",
            fontSize=12,
            leading=16,
            spaceBefore=12,
            spaceAfter=6,
        ),
        "body": body,
        "bullet": ParagraphStyle(
            "bullet",
            parent=body,
            leftIndent=11,
            bulletIndent=0,
            spaceAfter=2,
        ),
        "cell": ParagraphStyle(
            "cell",
            parent=body,
            fontSize=8,
            leading=10.5,
            spaceAfter=0,
        ),
        "cellhead": ParagraphStyle(
            "cellhead",
            parent=body,
            fontName="VeraBd",
            fontSize=8,
            leading=10.5,
            spaceAfter=0,
        ),
        "caption": ParagraphStyle(
            "caption",
            parent=body,
            fontName="VeraIt",
            fontSize=8,
            textColor=colors.HexColor("#5b636d"),
            spaceBefore=2,
            spaceAfter=8,
        ),
        "code": ParagraphStyle(
            "code",
            parent=body,
            fontName="Vera",
            fontSize=7.5,
            leading=10,
            textColor=colors.HexColor("#243244"),
            spaceAfter=6,
        ),
    }


def _paragraph(text: str, style: ParagraphStyle, counter: _Counter) -> Paragraph:
    """Build a paragraph, replacing the characters the font cannot draw."""
    return Paragraph(escape(counter.scrub(str(text))), style)


def _flow_nodes(diagram: Diagram) -> list[DiagramNode]:
    return [node for node in diagram.nodes if node.kind not in {"control", "excluded"}]


def _wrap(text: str, width: int) -> list[str]:
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


_BOX_W, _BOX_H, _CONTROL_H, _GAP = 150.0, 52.0, 80.0, 30.0


@dataclass(frozen=True)
class _Layout:
    """The geometry of one diagram, in unscaled units."""

    flow: list[DiagramNode]
    controls: list[DiagramNode]
    excluded: list[DiagramNode]
    control_y: float
    gate_y: float
    flow_y: float
    excluded_y: float
    width: float
    height: float


def _layout(diagram: Diagram) -> _Layout:
    flow = _flow_nodes(diagram)
    controls = [node for node in diagram.nodes if node.kind == "control"]
    excluded = [node for node in diagram.nodes if node.kind == "excluded"]
    columns = max(len(flow), 1)
    width = columns * _BOX_W + (columns - 1) * _GAP
    control_y = 0.0
    gate_y = _CONTROL_H + 24 if controls else 0.0
    flow_y = gate_y + 26 if controls else 0.0
    excluded_y = flow_y + _BOX_H + 30 if excluded else 0.0
    height = (excluded_y + _BOX_H) if excluded else (flow_y + _BOX_H)
    return _Layout(
        flow=flow,
        controls=controls,
        excluded=excluded,
        control_y=control_y,
        gate_y=gate_y,
        flow_y=flow_y,
        excluded_y=excluded_y,
        width=width,
        height=height,
    )


def _draw_diagram(
    diagram: Diagram,
    canvas,
    x: float,
    y: float,
    width: float,
    counter: _Counter,
    scale: float = 1.0,
) -> None:
    """Draw the diagram with PDF primitives.

    PDF coordinates grow *upwards* from the bottom-left corner, so every
    offset of the layout, which is measured from the top of the picture, is
    mirrored here.  The drawing is scaled to the available width, so a wide
    pattern is never clipped and a narrow one is never blown up beyond
    readability.
    """
    plan = _layout(diagram)
    scale = min(scale, width / plan.width, 1.0)
    # The font must exist before anything is drawn with it.
    _COVERAGE.register()
    height = plan.height * scale

    canvas.saveState()
    canvas.translate(x, y)

    def place(offset_top: float, box_width: float, box_height: float):
        """Return the PDF box (x, bottom, width, height) for a layout offset."""
        top = height - offset_top * scale
        return (0, top - box_height * scale, box_width * scale, box_height * scale)

    positions: dict[str, tuple[float, float, float, float]] = {}
    for index, node in enumerate(plan.flow):
        px, py, pw, ph = place(plan.flow_y, _BOX_W, _BOX_H)
        positions[node.id] = (index * (_BOX_W + _GAP) * scale, py, pw, ph)
    if plan.controls:
        count = len(plan.controls)
        slack = max(plan.width - count * _BOX_W, 0.0)
        step = slack / (count + 1)
        for index, node in enumerate(plan.controls):
            _px, py, pw, ph = place(plan.control_y, _BOX_W, _CONTROL_H)
            positions[node.id] = (
                (step * (index + 1) + index * _BOX_W) * scale,
                py,
                pw,
                ph,
            )
    for node in plan.excluded:
        anchor = plan.flow[1] if len(plan.flow) > 1 else plan.flow[0]
        ax, _ay, aw, _ah = positions[anchor.id]
        _px, py, pw, ph = place(plan.excluded_y, _BOX_W, _BOX_H)
        positions[node.id] = (ax + aw / 2 - pw / 2, py, pw, ph)

    if plan.controls:
        canvas.setStrokeColor(_ARROW_CONTROL)
        canvas.setLineWidth(1.0)
        canvas.setDash(4, 3)
        gate_y = height - plan.gate_y * scale
        canvas.line(0, gate_y, plan.width * scale, gate_y)
        for node in plan.controls:
            nx, ny, nw, _nh = positions[node.id]
            canvas.line(nx + nw / 2, ny + _CONTROL_H * scale, nx + nw / 2, gate_y)
        canvas.setDash()

    control_ids = {node.id for node in plan.controls}
    for edge in diagram.edges:
        if edge.source not in positions or edge.target not in positions:
            continue
        if edge.source in control_ids:
            # The gate bar already shows that a control gates the flow.
            continue
        sx, sy, sw, sh = positions[edge.source]
        tx, ty, tw, th = positions[edge.target]
        if abs(sy - ty) < 1:
            x1, y1 = sx + sw, sy + sh / 2
            x2, y2 = tx, ty + th / 2
        else:
            x1, y1 = sx + sw / 2, sy + sh
            x2, y2 = tx + tw / 2, ty
        colour = _ARROW_CONTROL if edge.dashed else _ARROW
        canvas.setStrokeColor(colour)
        canvas.setFillColor(colour)
        canvas.setLineWidth(1.2)
        if edge.dashed:
            canvas.setDash(4, 3)
        canvas.line(x1, y1, x2, y2)
        canvas.setDash()
        _arrow_head(canvas, x1, y1, x2, y2, colour)

    for node in diagram.nodes:
        if node.id not in positions:
            continue
        nx, ny, nw, nh = positions[node.id]
        canvas.setFillColor(_FILL.get(node.kind, _FILL["input"]))
        canvas.setStrokeColor(_STROKE.get(node.kind, _STROKE["input"]))
        canvas.setLineWidth(1.0)
        if node.kind in {"control", "excluded"}:
            canvas.setDash(3, 2)
        canvas.roundRect(nx, ny, nw, nh, 5, stroke=1, fill=1)
        canvas.setDash()
        canvas.setFillColor(_TEXT.get(node.kind, _TEXT["input"]))
        canvas.setFont("Vera", 6.4 * scale if scale < 1 else 6.4)
        lines = _wrap(counter.scrub(node.label), 24)
        if node.caption:
            label_y = ny + nh - 8
        else:
            label_y = ny + nh / 2 + 2
        for index, line in enumerate(lines):
            canvas.drawCentredString(nx + nw / 2, label_y - index * 7.5, line)
        if node.caption:
            canvas.setFont("Vera", 5.6)
            caption_y = label_y - len(lines) * 7.5 - 4
            for index, line in enumerate(_wrap(counter.scrub(node.caption), 28)):
                canvas.drawCentredString(nx + nw / 2, caption_y - index * 6.5, line)

    canvas.restoreState()


def _arrow_head(canvas, x1: float, y1: float, x2: float, y2: float, colour) -> None:
    """Draw the head of an arrow at the end point."""
    import math

    angle = math.atan2(y2 - y1, x2 - x1)
    size = 5.0
    left = (x2 - size * math.cos(angle - math.pi / 6), y2 - size * math.sin(angle - math.pi / 6))
    right = (x2 - size * math.cos(angle + math.pi / 6), y2 - size * math.sin(angle + math.pi / 6))
    path = canvas.beginPath()
    path.moveTo(x2, y2)
    path.lineTo(*left)
    path.lineTo(*right)
    path.close()
    canvas.setFillColor(colour)
    canvas.drawPath(path, stroke=0, fill=1)


def _table_flowable(
    headers: list[str],
    rows: list[list[str]],
    styles: dict[str, ParagraphStyle],
    available: float,
    counter: _Counter,
):
    """A table whose cells wrap instead of overflowing the page."""
    head = [Paragraph(escape(counter.scrub(h)), styles["cellhead"]) for h in headers]
    body = [
        [Paragraph(escape(counter.scrub(str(cell))), styles["cell"]) for cell in row]
        for row in rows
    ]
    columns = len(headers) or 1
    weights = []
    for index in range(columns):
        longest = max(
            [len(str(headers[index]))]
            + [len(str(row[index])) if index < len(row) else 0 for row in rows]
        )
        weights.append(max(longest, 8))
    total = sum(weights)
    col_widths = [max(available * weight / total, 42) for weight in weights]
    table = LongTable([head] + body, colWidths=col_widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c8d0da")),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8eef5")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    return table


class _DiagramFlowable(Flowable):
    """Draws one architecture diagram into the document flow."""

    def __init__(self, diagram: Diagram, counter: _Counter, scale: float = 1.0) -> None:
        super().__init__()
        self.diagram = diagram
        self.counter = counter
        self._scale = scale

    def wrap(self, available_width: float, available_height: float) -> tuple[float, float]:
        plan = _layout(self.diagram)
        self._scale = min(self._scale, available_width / plan.width, 1.0)
        # ReportLab does not assign the width of a custom flowable, so the
        # drawing code must not rely on ``self.width`` being set for it.
        self.width = available_width
        self.height = plan.height * self._scale
        return self.width, self.height

    def draw(self) -> None:
        _draw_diagram(self.diagram, self.canv, 0, 0, self.width, self.counter, self._scale)


def _block_flowables(
    block: Block,
    styles: dict[str, ParagraphStyle],
    counter: _Counter,
    width: float,
    diagram_scale: float = 1.0,
) -> list:
    """Turn one report block into PDF flowables."""
    if block.kind == "text":
        return [_paragraph(str(block.value), styles["body"], counter)]
    if block.kind == "bullets":
        out = []
        for item in block.value:
            out.append(
                Paragraph(
                    escape(counter.scrub(str(item))),
                    styles["bullet"],
                    bulletText="•",
                )
            )
        return out
    if block.kind == "table":
        headers, rows = block.value
        return [
            _table_flowable(
                [str(h) for h in headers],
                [[str(c) for c in row] for row in rows],
                styles,
                width,
                counter,
            )
        ]
    if block.kind == "code":
        text = counter.scrub(str(block.value))
        return [
            Preformatted(
                escape(text),
                styles["code"],
                maxLineLength=110,
                newLineChars="\n",
            )
        ]
    if block.kind == "diagram":
        diagram = block.value
        return [
            _DiagramFlowable(diagram, counter, diagram_scale),
            _paragraph(diagram.caption, styles["caption"], counter),
        ]
    return []


def _on_page(canvas, document) -> None:
    """The footer of every page: the advisory notice and the page number."""
    canvas.saveState()
    canvas.setFont("Vera", 7)
    canvas.setFillColor(colors.HexColor("#7d2b2b"))
    canvas.drawString(
        18 * mm, 10 * mm, "AI Opportunity Advisor - advisory only - not rollout authorization"
    )
    canvas.setFillColor(colors.HexColor("#5b636d"))
    canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {canvas.getPageNumber()}")
    canvas.restoreState()


def render_pdf(
    detail: AssessmentDetail,
    proposals: list[dict] | None = None,
    provenance: ProvenanceView | None = None,
    locale: str = DEFAULT_LOCALE,
) -> PdfResult:
    """Render the assessment report as a PDF document."""
    _COVERAGE.register()
    translator = Translator(locale)
    styles = _styles()
    counter = _Counter()
    sections = build_sections(detail, proposals, provenance, translator=translator)

    margin = 18 * mm
    available = A4[0] - 2 * margin
    buffer = BytesIO()
    document = BaseDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=margin,
        rightMargin=margin,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=f"{detail.title} - AI Opportunity Advisor",
        author="AI Opportunity Advisor",
        subject="Advisory-only AI opportunity assessment report",
    )
    frame = Frame(
        margin, 16 * mm, available, A4[1] - 32 * mm, id="body", leftPadding=0, rightPadding=0
    )
    document.addPageTemplates([PageTemplate(id="report", frames=[frame], onPage=_on_page)])

    # Every diagram of the document is drawn at the same scale, so two
    # diagrams in one report look like two drawings of the same kind.
    diagram_scale = 1.0
    for section in sections:
        for block in section.blocks:
            if block.kind == "diagram":
                diagram_scale = min(diagram_scale, available / _layout(block.value).width)

    story: list = [
        _paragraph(detail.title, styles["title"], counter),
        _paragraph(
            translator.t(
                "report.subtitle", "AI Opportunity Advisor - local demo assessment report"
            ),
            styles["subtitle"],
            counter,
        ),
        _paragraph(
            translator.t(
                "report.notice",
                "AI Opportunity Advisor · advisory only · local demo · not rollout authorization",
            ),
            styles["notice"],
            counter,
        ),
    ]
    for section in sections:
        story.append(_paragraph(section.title, styles["heading"], counter))
        for block in section.blocks:
            story.extend(_block_flowables(block, styles, counter, available, diagram_scale))
            if block.kind == "diagram":
                story.append(Spacer(1, 4))
    if counter.total:
        story.append(
            _paragraph(
                translator.t(
                    "report.pdf.unrenderable",
                    "Note: {count} character(s) could not be drawn in this PDF and are shown as "
                    "'{placeholder}'. The Markdown export contains the complete text.",
                    count=counter.total,
                    placeholder=UNRENDERABLE,
                ),
                styles["caption"],
                counter,
            )
        )

    document.build(story)
    return PdfResult(data=buffer.getvalue(), unrenderable_characters=counter.total)
