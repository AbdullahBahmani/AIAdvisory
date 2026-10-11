"""Thin Streamlit client for the stateless Phase 1 rules-only API."""

import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_API_BASE_URL = "http://127.0.0.1:8000"

DEMO_PAYLOAD = {
    "title": "Exact pricing calculation (demonstration)",
    "steps": [
        {
            "id": "step-1",
            "name": "Compute shipping fee",
            "order": 1,
            "tasks": [
                {
                    "id": "task-1",
                    "title": "Apply known shipping rates and tax",
                    "decision_type": "deterministic",
                    "signals": {
                        "deterministic_rules": True,
                        "exact_math": True,
                        "data_available": True,
                        "output_verifiable": True,
                    },
                }
            ],
        }
    ],
}


def fetch_api_health(base_url: str, timeout: float = 3.0) -> dict[str, str]:
    """Reach the API; UI never directly opens the persistence or provider layer."""
    url = f"{base_url.rstrip('/')}/health"
    with urlopen(url, timeout=timeout) as response:  # noqa: S310 - configured API
        if response.status != 200:
            raise ValueError(f"Unexpected health status: {response.status}")
        result = json.load(response)
    if not isinstance(result, dict) or result.get("status") != "ok":
        raise ValueError("Unexpected health response")
    return result


def preview_assessment(base_url: str, payload: dict, timeout: float = 12.0) -> dict:
    """Submit an explicit preview payload to the API, without local rule evaluation."""
    request = Request(  # noqa: S310 - configured API
        f"{base_url.rstrip('/')}/api/v1/analysis/preview",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - configured API
        if response.status != 200:
            raise ValueError(f"Unexpected analysis status: {response.status}")
        result = json.load(response)
    if not isinstance(result, dict) or result.get("advisory_only") is not True:
        raise ValueError("Unexpected analysis response")
    return result


def main() -> None:
    import streamlit as st

    st.set_page_config(page_title="AI Opportunity Advisor", page_icon="🧭", layout="wide")
    st.title("AI Opportunity Advisor")
    st.caption("Phase 1 · Stateless decision-engine preview | No LLM or storage")
    st.info("AI assists. Rules enforce. Data informs. Humans decide.")
    st.warning("Local developer demo only: use synthetic, non-sensitive data.")

    base_url = os.environ.get("API_BASE_URL", DEFAULT_API_BASE_URL)
    try:
        health = fetch_api_health(base_url)
    except (HTTPError, URLError, OSError, ValueError, json.JSONDecodeError) as exc:
        st.error(f"API unreachable at {base_url}. Start FastAPI first. ({type(exc).__name__})")
        return
    st.success(f"API connected · {health['service']} · {health['version']} · {health['mode']}")

    st.subheader("Rules-only analysis preview")
    st.write("Edit a JSON workflow. This runs deterministic rules per task and **does not save data**.")
    raw = st.text_area("Assessment JSON", value=json.dumps(DEMO_PAYLOAD, indent=2), height=300)
    if st.button("Analyze locally", type="primary"):
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("Assessment input must be a JSON object")
            result = preview_assessment(base_url, payload)
        except (ValueError, HTTPError, URLError, OSError, json.JSONDecodeError) as exc:
            st.error(f"Analysis failed: {exc}")
        else:
            for task in result.get("tasks", []):
                rec = task["recommendation"]
                st.markdown(f"**Task {task['task_id']}: {rec['category'].replace('_', ' ').title()}**")
                st.write("Reasons:", rec["rationale"])
                st.write("Controls:", rec["required_controls"])
                st.write("Missing evidence:", rec["missing_evidence"])
            st.json(result)
    st.caption("Persistence, authentication, the assessment wizard and reviewer sign-off are planned for later phases.")


if __name__ == "__main__":
    main()
