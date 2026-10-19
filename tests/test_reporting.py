"""Report integration and safe rendering regression tests, no external services."""

import json
from io import BytesIO
from unittest.mock import patch

import pytest

from app.evaluation import evaluate_dataset
from app.reporting.render import render_html, render_markdown
from tests.test_persistence import add_workflow_task, client, create_assessment  # noqa: F401
from ui.reporting import fetch_report


class FakeResponse(BytesIO):
    status = 200


def analyzed_id(client, title: str = "Report fixture") -> str:
    assessment = client.post("/api/v1/assessments", json={"title": title})
    assert assessment.status_code == 201
    aid = assessment.json()["id"]
    add_workflow_task(
        client,
        aid,
        title="Determine refund amount",
        signals={"deterministic_rules": True, "exact_math": True, "output_verifiable": True},
    )
    response = client.post(f"/api/v1/assessments/{aid}/analyze")
    assert response.status_code == 200
    return aid


def test_reports_reject_missing_or_stale_analysis(client):
    aid = create_assessment(client)["id"]
    assert client.get(f"/api/v1/assessments/{aid}/report").status_code == 409
    assert client.get("/api/v1/assessments/unavailable/report").status_code == 404
    add_workflow_task(client, aid, title="Deterministic sum", signals={"exact_math": True})
    assert client.post(f"/api/v1/assessments/{aid}/analyze").status_code == 200
    assert client.get(f"/api/v1/assessments/{aid}/report").status_code == 200
    assert client.patch(f"/api/v1/assessments/{aid}", json={"title": "Changed"}).status_code == 200
    assert client.get(f"/api/v1/assessments/{aid}/report").status_code == 409


def test_markdown_and_html_include_same_decisions_and_controls(client):
    aid = analyzed_id(client)
    md = client.get(f"/api/v1/assessments/{aid}/report?format=markdown")
    html = client.get(f"/api/v1/assessments/{aid}/report?format=html")
    assert md.status_code == html.status_code == 200
    for phrase in ("traditional_software", "deterministic", "FDE review", "Evidence", "ROI", "not"):
        assert phrase.lower() in md.text.replace("\\", "").lower()
        assert phrase.lower() in html.text.lower()
    assert md.headers["cache-control"] == "no-store"
    assert "attachment" in md.headers["content-disposition"]
    assert "text/markdown" in md.headers["content-type"]
    assert "text/html" in html.headers["content-type"]
    assert "default-src 'none'" in html.headers["content-security-policy"]
    assert md.text == client.get(f"/api/v1/assessments/{aid}/report?format=markdown").text
    assert client.get(f"/api/v1/assessments/{aid}/report?format=pdf").status_code == 422


def test_html_escapes_untrusted_input_and_markdown_does_not_inject_headings(client):
    title = '<script>alert("leak")</script> # Illegal heading'
    aid = analyzed_id(client, title=title)
    md = client.get(f"/api/v1/assessments/{aid}/report?format=markdown").text
    html = client.get(f"/api/v1/assessments/{aid}/report?format=html").text
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "<h1><script>" not in html
    assert "\\# Illegal heading" in md


def test_review_remains_unverified_in_report(client):
    aid = analyzed_id(client)
    reviewer = client.post(
        f"/api/v1/assessments/{aid}/fde-review",
        json={
            "reviewer_id": "Example FDE",
            "selected_category": "traditional_software",
            "rationale": "A predictable calculation must be implemented with code.",
        },
    )
    assert reviewer.status_code == 201
    html = client.get(f"/api/v1/assessments/{aid}/report?format=html").text
    assert "Example FDE" in html
    assert "identity verified: no" in html
    assert "not production authorization" in html


def test_untrusted_ai_proposals_remain_labeled_and_escaped(client):
    from app.domain.persistence import AssessmentDetail

    aid = analyzed_id(client)
    detail = AssessmentDetail.model_validate(client.get(f"/api/v1/assessments/{aid}").json())
    proposals = [
        {
            "assessment_revision": detail.revision,
            "feature": "evaluation",
            "provider": "test",
            "model": "stub",
            "prompt_version": "3.0",
            "schema_version": "3.0",
            "proposal": {"poc_scope": "<script>exploit()</script>", "metrics": ["fake score"]},
        },
        {
            "assessment_revision": detail.revision - 1,
            "feature": "challenge",
            "provider": "test",
            "model": "stub",
            "prompt_version": "3.0",
            "schema_version": "3.0",
            "proposal": {"reason": "stale"},
        },
    ]
    markdown = render_markdown(detail, proposals)
    html = render_html(detail, proposals)
    assert "stale: yes" in markdown
    assert "stale: no" in markdown
    assert "unverified AI proposals" in markdown
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "stale: yes" in html
    assert markdown == render_markdown(detail, proposals)


def test_ui_download_adapter_preserves_content_bytes_and_format():
    content = b"# Fake report\ncontent"
    with patch("ui.reporting.urlopen", return_value=FakeResponse(content)) as called:
        assert fetch_report("http://127.0.0.1:8000/", "abc123", "markdown") == content
    url = called.call_args.args[0].full_url
    assert url == "http://127.0.0.1:8000/api/v1/assessments/abc123/report?format=markdown"
    with pytest.raises(ValueError):
        fetch_report("http://127.0.0.1:8000", "abc", "pdf")
    with pytest.raises(ValueError):
        fetch_report("http://127.0.0.1:8000", "../../other", "markdown")
    with patch("ui.reporting.urlopen", return_value=FakeResponse(b"x" * 4_000_001)):
        with pytest.raises(ValueError, match="limit"):
            fetch_report("http://127.0.0.1:8000", "abc", "markdown")


def test_offline_evaluation_and_expected_categories():
    result = evaluate_dataset()
    assert result["passed"] == result["total"] == 4
    assert result["failed"] == 0
    assert {x["id"]: x["actual"][0] for x in result["cases"]} == {
        "ai-support": "strong_ai_candidate",
        "exact-pricing": "traditional_software",
        "hybrid-refund": "ai_human_approval",
        "privacy-blocked": "more_discovery",
    }


def test_regression_evaluation_returns_failure_for_wrong_ground_truth(tmp_path):
    from app.evaluation import DEFAULT_DATASET

    data = json.loads(DEFAULT_DATASET.read_text())
    data["cases"][0]["expected_category"] = "human_led"
    path = tmp_path / "bad-goldens.json"
    path.write_text(json.dumps(data))
    result = evaluate_dataset(path)
    assert result["failed"] == 1
    assert result["total"] == 4


def test_demo_recreates_these_reports_without_external_provider(tmp_path):
    from scripts.demo import run_demo

    results = run_demo(tmp_path)
    assert results == {
        "ai-support": "strong_ai_candidate",
        "exact-pricing": "traditional_software",
        "hybrid-refund": "ai_human_approval",
    }
    for name in results:
        assert "FDE review" in (tmp_path / f"{name}.md").read_text()
        assert "<html" in (tmp_path / f"{name}.html").read_text()


def test_report_section_snapshot_matches_versioned_golden(client):
    from pathlib import Path

    from app.domain.persistence import AssessmentDetail
    from app.reporting.render import build_sections

    path = Path(__file__).resolve().parent.parent / "evals" / "report_sections.json"
    golden = json.loads(path.read_text(encoding="utf-8"))
    aid = analyzed_id(client)
    detail = AssessmentDetail.model_validate(client.get(f"/api/v1/assessments/{aid}").json())
    sections = build_sections(detail)
    assert [section.title for section in sections] == golden["headings"]
    assert "Required control" in render_markdown(detail) or "Policy" in render_markdown(detail)
