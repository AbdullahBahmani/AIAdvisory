"""Read-only Streamlit report export adapter; no direct database access."""

from urllib.parse import quote
from urllib.request import Request, urlopen


def fetch_report(base_url: str, assessment_id: str, format: str = "markdown", timeout: float = 12.0, *, bearer_token: str | None = None) -> bytes:
    if format not in ("markdown", "html"):
        raise ValueError("Report format must be markdown or html")
    if not assessment_id or any(c in assessment_id for c in "?#/\\"):
        raise ValueError("Invalid assessment identifier")
    path = f"/api/v1/assessments/{quote(assessment_id, safe='')}/report?format={format}"
    headers = {"Accept": "text/plain" if format == "markdown" else "text/html"}
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


def render_report_tab(st, base_url: str, assessment: dict) -> None:
    st.subheader("Assessment report and export")
    st.caption("Evidence-labeled snapshot of the current deterministic analysis and optional unverified AI suggestions. No rollout authorization.")
    if assessment.get("analysis") is None or assessment.get("analysis_revision") != assessment.get("revision"):
        st.warning("Run deterministic analysis for the current revision before exporting.")
        return
    assessment_id = assessment["id"]
    try:
        token = st.session_state.get("api_bearer") or None
        markdown = fetch_report(base_url, assessment_id, "markdown", bearer_token=token)
        html = fetch_report(base_url, assessment_id, "html", bearer_token=token)
    except (OSError, ValueError) as exc:
        st.error(f"Report retrieval failed: {exc}")
        return
    st.download_button("Download Markdown report", data=markdown,
                       file_name=f"assessment-{assessment_id}.md", mime="text/markdown")
    st.download_button("Download HTML report", data=html,
                       file_name=f"assessment-{assessment_id}.html", mime="text/html")
    with st.expander("Preview report (Markdown)"):
        st.code(markdown.decode("utf-8"), language="markdown")
