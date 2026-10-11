"""Phase 0 Streamlit smoke page. All application data will flow through the API."""

import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

DEFAULT_API_BASE_URL = "http://127.0.0.1:8000"


def fetch_api_health(base_url: str, timeout: float = 3.0) -> dict[str, str]:
    """Query the API health endpoint; intentionally use no direct DB or AI access."""
    url = f"{base_url.rstrip('/')}/health"
    with urlopen(url, timeout=timeout) as response:  # noqa: S310 - configured local API
        if response.status != 200:
            raise ValueError(f"Unexpected health status: {response.status}")
        result = json.load(response)
    if not isinstance(result, dict) or result.get("status") != "ok":
        raise ValueError("Unexpected health response")
    return result


def main() -> None:
    import streamlit as st

    st.set_page_config(page_title="AI Opportunity Advisor", page_icon="🧭")
    st.title("AI Opportunity Advisor")
    st.caption("Phase 0 · Foundation | Rules-only; AI is disabled")
    st.info("AI assists. Rules enforce. Data informs. Humans decide.")

    base_url = os.environ.get("API_BASE_URL", DEFAULT_API_BASE_URL)
    try:
        health = fetch_api_health(base_url)
    except (HTTPError, URLError, OSError, ValueError, json.JSONDecodeError) as exc:
        st.error(f"API unreachable at {base_url}. Start FastAPI first. ({type(exc).__name__})")
    else:
        st.success(f"API connected · {health['service']} · {health['version']}")
        st.write("Current mode:", health["mode"])

    st.subheader("Next milestone")
    st.write("Phase 1 adds discovery, workflow mapping, deterministic policy rules, and scores.")
    st.caption("No business assessment or AI decision engine is implemented yet.")


if __name__ == "__main__":
    main()
