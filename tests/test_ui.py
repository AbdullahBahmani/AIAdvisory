"""Check the UI-to-API adapter without Streamlit or a live server."""

import io
import json
from unittest.mock import patch

import pytest

from ui.streamlit_app import fetch_api_health


class FakeResponse(io.BytesIO):
    status = 200


def test_health_adapter_uses_api_boundary() -> None:
    payload = {"status": "ok", "service": "test-api", "version": "0.1", "mode": "rules-only"}
    response = FakeResponse(json.dumps(payload).encode())
    with patch("ui.streamlit_app.urlopen", return_value=response) as call:
        assert fetch_api_health("http://127.0.0.1:8000/") == payload
    call.assert_called_once_with("http://127.0.0.1:8000/health", timeout=3.0)


def test_health_adapter_rejects_invalid_contract() -> None:
    with patch("ui.streamlit_app.urlopen", return_value=FakeResponse(b'{"status":"failed"}')):
        with pytest.raises(ValueError, match="Unexpected health response"):
            fetch_api_health("http://127.0.0.1:8000")


def test_analysis_adapter_posts_json_to_api():
    from ui.streamlit_app import DEMO_PAYLOAD, preview_assessment

    response = FakeResponse(json.dumps({"advisory_only": True, "tasks": []}).encode())
    with patch("ui.streamlit_app.urlopen", return_value=response) as mocked:
        result = preview_assessment("http://127.0.0.1:8000", DEMO_PAYLOAD)
    assert result["advisory_only"] is True
    request = mocked.call_args.args[0]
    assert request.full_url == "http://127.0.0.1:8000/api/v1/analysis/preview"
    assert json.loads(request.data) == DEMO_PAYLOAD


def test_generic_api_adapter_sends_payload_and_reviewer_token():
    from ui.streamlit_app import api_request

    output = FakeResponse(json.dumps({"is_final": True}).encode())
    with patch("ui.streamlit_app.urlopen", return_value=output) as mocked:
        assert api_request("http://127.0.0.1:8000", "POST", "/api/v1/assessments/abc/final-decision",
                           {"note": "demo"}, reviewer_token="local-token") == {"is_final": True}
    request = mocked.call_args.args[0]
    assert request.get_method() == "POST"
    assert request.get_header("X-reviewer-token") == "local-token"
    assert json.loads(request.data)["note"] == "demo"


def test_generic_api_adapter_rejects_path_outside_v1():
    from ui.streamlit_app import api_request

    with pytest.raises(ValueError, match="/api/v1/"):
        api_request("http://127.0.0.1:8000", "GET", "/health")


def test_json_helpers_require_correct_shapes():
    from ui.streamlit_app import _json_array, _json_object

    assert _json_array("[]", "Risks") == []
    assert _json_object("{}", "Signals") == {}
    with pytest.raises(ValueError):
        _json_object("[]", "Signals")
