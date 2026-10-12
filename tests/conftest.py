import pytest

@pytest.fixture(autouse=True)
def synthetic_demo_for_legacy_tests(monkeypatch):
    monkeypatch.setenv("ADVISORY_DEMO_MODE", "1")
