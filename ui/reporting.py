"""Read-only Streamlit report export adapter; no direct database access.

The exported report carries the locale of the user interface: the API is
asked for the same locale, so the headings and labels of the export are the
ones the reader saw.
"""

from urllib.parse import quote
from urllib.request import Request, urlopen

from app.localization import current
from ui.accessibility import heading

# --- widget labels ------------------------------------------------------------

LABEL_DOWNLOAD_MARKDOWN = "Download Markdown report"
LABEL_DOWNLOAD_HTML = "Download HTML report"
LABEL_DOWNLOAD_PDF = "Download PDF report"

#: The export formats the adapter may request.
REPORT_FORMATS = ("markdown", "html", "pdf")

#: The media type of each export format.
_MEDIA_TYPES = {
    "markdown": "text/plain",
    "html": "text/html",
    "pdf": "application/pdf",
}


def fetch_report(
    base_url: str,
    assessment_id: str,
    format: str = "markdown",
    timeout: float = 12.0,
    *,
    bearer_token: str | None = None,
    locale: str | None = None,
) -> bytes:
    if format not in REPORT_FORMATS:
        raise ValueError("Report format must be markdown, html or pdf")
    if not assessment_id or any(c in assessment_id for c in "?#/\\"):
        raise ValueError("Invalid assessment identifier")
    path = f"/api/v1/assessments/{quote(assessment_id, safe='')}/report?format={format}"
    if locale:
        path += f"&locale={quote(locale, safe='')}"
    headers = {"Accept": _MEDIA_TYPES[format]}
    if bearer_token:
        headers["Authorization"] = "Bearer " + bearer_token
    request = Request(f"{base_url.rstrip('/')}{path}", headers=headers)
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - configured local API
        if response.status != 200:
            raise ValueError(f"Report endpoint returned {response.status}")
        data = response.read(4_000_001)
    if len(data) > 4_000_000:
        raise ValueError("Report exceeds local download size limit")
    return data


def render_report_tab(st, base_url: str, assessment: dict, level: int = 3) -> None:
    from app.localization import t

    heading(st, t("ui.reporting.tab", "Assessment report and export"), level)
    st.caption(
        t(
            "ui.reporting.caption",
            "Evidence-labeled snapshot of the current deterministic analysis and optional "
            "unverified AI suggestions. No rollout authorization.",
        )
    )
    if assessment.get("analysis") is None or assessment.get("analysis_revision") != assessment.get(
        "revision"
    ):
        st.warning(
            t(
                "ui.reporting.stale",
                "Run deterministic analysis for the current revision before exporting.",
            )
        )
        return
    assessment_id = assessment["id"]
    locale = current().locale_code
    try:
        token = st.session_state.get("api_bearer") or None
        markdown = fetch_report(
            base_url, assessment_id, "markdown", bearer_token=token, locale=locale
        )
        html = fetch_report(base_url, assessment_id, "html", bearer_token=token, locale=locale)
        pdf = fetch_report(base_url, assessment_id, "pdf", bearer_token=token, locale=locale)
    except (OSError, ValueError) as exc:
        st.error(t("ui.reporting.failed", "Report retrieval failed: {error}", error=exc))
        return
    st.download_button(
        LABEL_DOWNLOAD_MARKDOWN,
        data=markdown,
        file_name=f"assessment-{assessment_id}.md",
        mime="text/markdown",
    )
    st.download_button(
        LABEL_DOWNLOAD_HTML,
        data=html,
        file_name=f"assessment-{assessment_id}.html",
        mime="text/html",
    )
    st.download_button(
        LABEL_DOWNLOAD_PDF,
        data=pdf,
        file_name=f"assessment-{assessment_id}.pdf",
        mime="application/pdf",
    )
    with st.expander(t("ui.reporting.preview", "Preview report (Markdown)")):
        st.caption(
            t(
                "ui.reporting.preview_caption",
                "Markdown source of the current report. The preview is scrollable with the "
                "keyboard: focus the code block and use the arrow keys.",
            )
        )
        st.code(markdown.decode("utf-8"), language="markdown")
