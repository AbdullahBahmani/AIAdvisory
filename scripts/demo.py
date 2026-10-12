"""Offline three-scenario end-to-end demo using an isolated temporary database.

Uses FastAPI TestClient (no network, external provider, or hosted runner). It
creates an assessment, workflow, tasks, analysis, unverified local FDE review,
and Markdown/HTML exports for each synthetic case. It never signs off a rollout.
"""

import argparse
import os
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.models import Base
from app.db.session import get_db
from app.main import app
from tests.fixtures.cases import AI_SUPPORT, EXACT_PRICING, HYBRID_REFUND

CASES = [
    ("ai-support", AI_SUPPORT),
    ("exact-pricing", EXACT_PRICING),
    ("hybrid-refund", HYBRID_REFUND),
]


def run_demo(output: Path) -> dict[str, str]:
    output.mkdir(parents=True, exist_ok=True)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _record):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)

    def override_db():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    previous_demo_mode = os.environ.get("ADVISORY_DEMO_MODE")
    os.environ["ADVISORY_DEMO_MODE"] = "1"  # isolated, synthetic in-process demonstration
    results = {}
    try:
        with TestClient(app) as client:
            for name, scenario in CASES:
                assessment = client.post("/api/v1/assessments", json={"title": scenario["title"]})
                assessment.raise_for_status()
                assessment_id = assessment.json()["id"]
                workflow = client.post(f"/api/v1/assessments/{assessment_id}/workflows", json={"name": "Synthetic workflow"})
                workflow.raise_for_status()
                workflow_id = workflow.json()["id"]
                for source_step in scenario["steps"]:
                    step = client.post(f"/api/v1/workflows/{workflow_id}/steps", json={"name": source_step["name"], "order": source_step["order"]})
                    step.raise_for_status()
                    step_id = step.json()["id"]
                    for source_task in source_step["tasks"]:
                        task = client.post(f"/api/v1/workflow-steps/{step_id}/tasks", json={
                            "title": source_task["title"],
                            "description": source_task.get("description", "Synthetic evaluation fixture"),
                            "decision_type": source_task.get("decision_type", "unknown"),
                            "signals": source_task.get("signals", {}),
                            "actions": source_task.get("actions", []),
                            "risks": source_task.get("risks", []),
                        })
                        task.raise_for_status()
                analyzed = client.post(f"/api/v1/assessments/{assessment_id}/analyze")
                analyzed.raise_for_status()
                category = analyzed.json()["tasks"][0]["recommendation"]["category"]
                review = client.post(f"/api/v1/assessments/{assessment_id}/fde-review", json={
                    "reviewer_id": "synthetic-demo-FDE", "selected_category": category,
                    "rationale": "Synthetic regression demonstration; no real-world rollout approval.",
                })
                review.raise_for_status()
                for format_name, suffix in [("markdown", "md"), ("html", "html")]:
                    report = client.get(f"/api/v1/assessments/{assessment_id}/report?format={format_name}")
                    report.raise_for_status()
                    (output / f"{name}.{suffix}").write_text(report.text, encoding="utf-8")
                results[name] = category
    finally:
        if previous_demo_mode is None:
            os.environ.pop("ADVISORY_DEMO_MODE", None)
        else:
            os.environ["ADVISORY_DEMO_MODE"] = previous_demo_mode
        app.dependency_overrides.clear()
        engine.dispose()
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("demo_out"))
    args = parser.parse_args()
    for name, category in run_demo(args.output).items():
        print(f"{name}: {category} — exported Markdown and HTML")
    print("Synthetic local demonstration only. No final identity verification or rollout approval.")


if __name__ == "__main__":
    main()
