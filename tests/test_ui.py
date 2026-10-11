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
