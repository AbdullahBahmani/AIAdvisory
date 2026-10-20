"""The PDF export must agree with the Markdown and the HTML export.

The PDF is a different *presentation* of the same assessment report, so the
tests check that it carries the same statements, that it never turns a
recommendation into an approval, and that it does not lose text silently.
"""

from __future__ import annotations

import base64
import re
import zlib

from app.reporting.pdf import UNRENDERABLE, render_pdf
from app.reporting.render import render_markdown
from tests.test_persistence import add_workflow_task, client, create_assessment  # noqa: F401


def _inflate(raw: bytes) -> bytes:
    """Decode one PDF stream, which may be ASCII85 and/or Flate encoded."""
    data = raw
    stripped = raw.strip()
    if stripped.endswith(b"~>"):
        try:
            body = stripped
            if not body.startswith(b"<~"):
                body = b"<~" + body
            data = base64.a85decode(body, adobe=True)
        except (ValueError, TypeError):
            data = raw
    try:
        return zlib.decompress(data)
    except zlib.error:
        return data


def _streams(data: bytes) -> list[bytes]:
    """Return the decoded content of every stream in the PDF.

    The PDF is walked object by object and the length of each stream is read
    from its dictionary.  Searching for the literal ``endstream`` would be
    wrong: the embedded font program is binary and can contain those bytes.
    """
    headers = list(re.finditer(rb"(\d+)\s+0\s+obj\b", data))
    out: list[bytes] = []
    for index, header in enumerate(headers):
        start = header.end()
        end = headers[index + 1].start() if index + 1 < len(headers) else len(data)
        body = data[start:end]
        marker = re.search(rb"stream\r?\n", body)
        if marker is None:
            continue
        length = re.search(rb"/Length\s+(\d+)", body[: marker.start()])
        if length is None:
            continue
        raw = body[marker.end() : marker.end() + int(length.group(1))]
        out.append(_inflate(raw))
    return out


def pdf_text(data: bytes) -> str:
    """Extract the text a reader would see from a PDF.

    Only the page content streams are read: the embedded font programs are
    binary and would otherwise contribute noise.  The string literals of the
    text-showing operators are collected.
    """
    parts: list[str] = []
    for stream in _streams(data):
        if b"BT" not in stream or b"Tj" not in stream and b"TJ" not in stream:
            continue
        for literal in re.findall(rb"\((?:\\.|[^\\()])*\)", stream):
            body = literal[1:-1]
            body = re.sub(rb"\\([()\\])", rb"\1", body)
            parts.append(body.decode("latin-1"))
    return "\n".join(parts)


def _pdf_bytes(client, aid: str) -> bytes:
    response = client.get(f"/api/v1/assessments/{aid}/report?format=pdf")
    assert response.status_code == 200, response.text
    return response.content


def _analyzed(client, title: str | None = None) -> str:
    if title is None:
        aid = create_assessment(client)["id"]
    else:
        response = client.post("/api/v1/assessments", json={"title": title})
        assert response.status_code == 201, response.text
        aid = response.json()["id"]
    add_workflow_task(
        client,
        aid,
        title="Determine refund amount",
        signals={"deterministic_rules": True, "exact_math": True, "output_verifiable": True},
    )
    assert client.post(f"/api/v1/assessments/{aid}/analyze").status_code == 200
    return aid


def test_the_api_serves_a_pdf(client):
    """The export endpoint must return a real PDF."""
    aid = _analyzed(client)
    response = client.get(f"/api/v1/assessments/{aid}/report?format=pdf")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert ".pdf" in response.headers["content-disposition"]
    assert response.headers["cache-control"] == "no-store"
    assert response.content.startswith(b"%PDF-")
    assert response.content.rstrip().endswith(b"%%EOF")


def test_the_pdf_rejects_a_missing_or_stale_analysis(client):
    """A PDF may never be rendered from an out-of-date analysis."""
    aid = create_assessment(client)["id"]
    assert client.get(f"/api/v1/assessments/{aid}/report?format=pdf").status_code == 409
    add_workflow_task(
        client,
        aid,
        title="Determine refund amount",
        signals={"deterministic_rules": True, "exact_math": True, "output_verifiable": True},
    )
    assert client.post(f"/api/v1/assessments/{aid}/analyze").status_code == 200
    assert client.get(f"/api/v1/assessments/{aid}/report?format=pdf").status_code == 200
    assert client.patch(f"/api/v1/assessments/{aid}", json={"title": "Changed"}).status_code == 200
    assert client.get(f"/api/v1/assessments/{aid}/report?format=pdf").status_code == 409


def test_the_pdf_is_never_rollout_authorization(client):
    """The advisory notice must be on every page of the PDF."""
    aid = _analyzed(client)
    text = pdf_text(_pdf_bytes(client, aid))
    assert "advisory only" in text
    assert "not rollout authorization" in text


def test_the_pdf_carries_the_same_decision_values(client):
    """The contract values of the analysis must survive into the PDF."""
    aid = _analyzed(client)
    markdown = client.get(f"/api/v1/assessments/{aid}/report?format=markdown").text
    text = pdf_text(_pdf_bytes(client, aid))
    assert "traditional_software" in markdown
    assert "traditional_software" in text
    assert "rules-v2" in text


def test_the_pdf_renders_the_architecture_diagram(client):
    """The diagram must be drawn in the PDF, not dropped."""
    aid = _analyzed(client)
    text = pdf_text(_pdf_bytes(client, aid))
    assert "Architecture pattern" in text
    # The drawn nodes carry their own labels.  A node label is wrapped over
    # several lines in the drawing, so the check is on the first line.
    assert "Deterministic rules" in text
    assert "No model in this path" in text


def test_the_pdf_escapes_untrusted_text(client):
    """A hostile title must not become markup or a script in the PDF."""
    aid = _analyzed(client, title='<script>alert("leak")</script> & "quotes"')
    data = _pdf_bytes(client, aid)
    # The hostile title is rendered as *text*, not as markup, and the document
    # carries no script, no action and no embedded file.
    text = pdf_text(data)
    assert "alert(" in text
    for forbidden in (b"/JavaScript", b"/OpenAction", b"/EmbeddedFile", b"/Launch"):
        assert forbidden not in data


def test_the_pdf_reports_characters_it_cannot_draw(client):
    """Text the font cannot draw is replaced by a placeholder and counted.

    The point is that the loss is *visible*: the count is reported and a note
    is printed, so nobody reads a silently truncated document.
    """
    aid = _analyzed(client)
    hostile = "Sensitivitet for 東京 kontroll"
    assert (
        client.patch(f"/api/v1/assessments/{aid}", json={"business_problem": hostile}).status_code
        == 200
    )
    assert client.post(f"/api/v1/assessments/{aid}/analyze").status_code == 200
    response = client.get(f"/api/v1/assessments/{aid}/report?format=pdf")
    assert response.status_code == 200
    count = int(response.headers.get("X-PDF-Unrenderable-Characters", "0"))
    assert count == len("東京")
    text = pdf_text(response.content)
    assert UNRENDERABLE in text
    assert "could not be drawn in this PDF" in text


def test_the_markdown_export_keeps_the_complete_text(client):
    """The Markdown export is the complete record; the PDF may not be."""
    aid = _analyzed(client)
    hostile = "Sensitivitet for 東京 kontroll"
    assert (
        client.patch(f"/api/v1/assessments/{aid}", json={"business_problem": hostile}).status_code
        == 200
    )
    assert client.post(f"/api/v1/assessments/{aid}/analyze").status_code == 200
    markdown = client.get(f"/api/v1/assessments/{aid}/report?format=markdown").text
    assert hostile in markdown


def test_the_pdf_is_stable_for_the_same_analysis(client):
    """The same analysis must produce the same PDF text."""
    aid = _analyzed(client)
    first = pdf_text(_pdf_bytes(client, aid))
    second = pdf_text(_pdf_bytes(client, aid))
    assert first == second


def test_the_pdf_renders_in_every_locale(client):
    """A locale must not break the PDF."""
    aid = _analyzed(client)
    headings = {"en": "Executive summary", "nb": "Sammendrag for ledelsen"}
    for locale, heading in headings.items():
        response = client.get(f"/api/v1/assessments/{aid}/report?format=pdf&locale={locale}")
        assert response.status_code == 200
        assert response.content.startswith(b"%PDF-")
        assert response.headers["x-report-locale"] == locale
        assert heading in pdf_text(response.content)


def test_the_pdf_is_built_from_the_same_view_model(client):
    """The PDF and the Markdown export must carry the same category value."""
    from app.domain.persistence import AssessmentDetail

    aid = _analyzed(client)
    detail = AssessmentDetail.model_validate(client.get(f"/api/v1/assessments/{aid}").json())
    markdown = render_markdown(detail)
    result = render_pdf(detail)
    assert result.data.startswith(b"%PDF-")
    assert "traditional_software" in markdown
    assert "traditional_software" in pdf_text(result.data)
