"""Phase 2 guided local Streamlit UI. HTTP API is the sole data/analysis boundary."""

import json
import os
from contextvars import ContextVar
from ui.reporting import render_report_tab
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_API_BASE_URL = "http://127.0.0.1:8000"
_API_BEARER: ContextVar[str | None] = ContextVar("api_bearer", default=None)

DEMO_PAYLOAD = {
    "title": "Exact pricing calculation (demonstration)",
    "steps": [{"id": "step-1", "name": "Compute shipping fee", "order": 1, "tasks": [{
        "id": "task-1", "title": "Apply known shipping rates and tax",
        "decision_type": "deterministic",
        "signals": {"deterministic_rules": True, "exact_math": True,
                    "data_available": True, "output_verifiable": True},
    }]}],
}

RECOMMENDATION_CATEGORIES = [
    "traditional_software", "strong_ai_candidate", "hybrid", "ai_human_approval",
    "human_led", "more_discovery",
]
ACTIONS = [
    "knowledge_search", "summarize", "draft", "read_customer_data", "create_ticket",
    "modify_customer_record", "cancel_order", "issue_refund", "transfer_money",
    "delete_data", "legal_commitment", "employment_decision", "medical_decision",
]


def fetch_api_health(base_url: str, timeout: float = 3.0) -> dict[str, str]:
    """Reach the API; UI never directly opens persistence or AI-provider connections."""
    url = f"{base_url.rstrip('/')}/health"
    with urlopen(url, timeout=timeout) as response:  # noqa: S310 - configured local API
        if response.status != 200:
            raise ValueError(f"Unexpected health status: {response.status}")
        result = json.load(response)
    if not isinstance(result, dict) or result.get("status") != "ok":
        raise ValueError("Unexpected health response")
    return result


def api_request(
    base_url: str, method: str, path: str, payload: dict | None = None,
    *, reviewer_token: str | None = None, ai_access_token: str | None = None,
    timeout: float = 12.0,
):
    """Make a validated JSON request; errors propagate for visible Streamlit feedback."""
    if not path.startswith("/api/v1/"):
        raise ValueError("API path must start with /api/v1/")
    headers = {"Accept": "application/json"}
    bearer = _API_BEARER.get()
    if bearer:
        headers["Authorization"] = "Bearer " + bearer
    if reviewer_token:
        headers["X-Reviewer-Token"] = reviewer_token
    if ai_access_token:
        headers["X-AI-Access-Token"] = ai_access_token
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    request = Request(f"{base_url.rstrip('/')}{path}", data=data, headers=headers, method=method)
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - configured local API
        if response.status >= 400:
            raise ValueError(f"Unexpected HTTP status {response.status}")
        if response.status == 204:
            return None
        return json.load(response)


def preview_assessment(base_url: str, payload: dict, timeout: float = 12.0) -> dict:
    """Preserve the Phase 1 stateless preview as an independent testing endpoint."""
    result = api_request(base_url, "POST", "/api/v1/analysis/preview", payload, timeout=timeout)
    if not isinstance(result, dict) or result.get("advisory_only") is not True:
        raise ValueError("Unexpected analysis response")
    return result


def _api_error(exc: Exception) -> str:
    if isinstance(exc, HTTPError):
        try:
            body = json.loads(exc.read())
            detail = body.get("detail", "Unspecified error")
        except (ValueError, OSError):
            detail = exc.reason
        return f"API {exc.code}: {detail}"
    return str(exc)


def _change(st, base_url: str, method: str, path: str, payload: dict | None = None, token: str | None = None):
    try:
        result = api_request(base_url, method, path, payload, reviewer_token=token)
    except (HTTPError, URLError, OSError, ValueError, json.JSONDecodeError) as exc:
        st.error(_api_error(exc))
        return None
    st.rerun()
    return result


def _json_object(value: str, label: str) -> dict:
    obj = json.loads(value)
    if not isinstance(obj, dict):
        raise ValueError(f"{label} must contain a JSON object")
    return obj


def _json_array(value: str, label: str) -> list:
    obj = json.loads(value)
    if not isinstance(obj, list):
        raise ValueError(f"{label} must contain a JSON array")
    return obj


def _show_recommendations(st, result: dict) -> None:
    st.caption(f"Rules version: {result['policy_version']} · Advisory only · No external actions")
    for task in result.get("tasks", []):
        rec = task["recommendation"]
        st.markdown(f"**{task['task_id']}: {rec['category'].replace('_', ' ').title()}**")
        st.write("AI suitability:", task["ai_suitability"].get("value"),
                 "| Evidence coverage:", task["ai_suitability"].get("coverage"))
        st.write("Why:", rec.get("rationale", []))
        st.write("Code / deterministic boundary:", rec.get("deterministic_role"))
        st.write("AI boundary:", rec.get("ai_role"))
        st.write("Human boundary:", rec.get("human_role"))
        st.write("Controls:", rec.get("required_controls", []))
        st.write("Open evidence:", rec.get("missing_evidence", []))
        with st.expander("Rule hits, factors and risks"):
            st.json({"rules": task["rule_hits"], "factors": task["ai_suitability"]["factors"],
                     "risks": task["risks"], "technical": task["technical_feasibility"],
                     "data_readiness": task["data_readiness"]})
    if result.get("value"):
        st.subheader("Illustrative business value (not validated ROI)")
        st.json(result["value"])
    for warning in result.get("warnings", []):
        st.warning(warning)


def _dashboard(st, base_url: str) -> dict | None:
    st.subheader("Assessments")
    with st.expander("Create a new assessment", expanded=False):
        with st.form("create_assessment"):
            title = st.text_input("Assessment title")
            organization = st.text_input("Organization (synthetic data only)")
            owner = st.text_input("FDE / owner")
            problem = st.text_area("Business problem")
            if st.form_submit_button("Create assessment"):
                if not title.strip():
                    st.error("Title is required")
                else:
                    try:
                        created = api_request(base_url, "POST", "/api/v1/assessments", {
                            "title": title, "organization": organization or None,
                            "owner": owner or None, "business_problem": problem or None,
                        })
                    except (HTTPError, URLError, ValueError, OSError) as exc:
                        st.error(_api_error(exc))
                    else:
                        st.session_state["assessment_id"] = created["id"]
                        st.rerun()
    rows = api_request(base_url, "GET", "/api/v1/assessments")
    if not rows:
        st.info("Create an assessment to begin guided discovery.")
        return None
    by_id = {row["id"]: row for row in rows}
    current = st.session_state.get("assessment_id")
    options = list(by_id)
    selection = st.selectbox("Open assessment", options=options, index=options.index(current) if current in options else 0,
                             format_func=lambda rid: f"{by_id[rid]['title']} — {by_id[rid]['status']}")
    st.session_state["assessment_id"] = selection
    return api_request(base_url, "GET", f"/api/v1/assessments/{selection}")


def _intake(st, base_url: str, assessment: dict):
    aid = assessment["id"]
    with st.form(f"intake-{aid}"):
        title = st.text_input("Title", value=assessment["title"])
        organization = st.text_input("Organization", value=assessment["organization"] or "")
        owner = st.text_input("Owner", value=assessment["owner"] or "")
        problem = st.text_area("Problem", value=assessment["business_problem"] or "")
        st.caption("Optional financial assumptions: leave blank if unknown. Do not enter invented figures.")
        value = assessment.get("value_inputs") or {}
        fields = (
            ("cases_per_period", "Cases per period"),
            ("baseline_minutes_per_case", "Current minutes per case"),
            ("target_minutes_per_case", "Target minutes per case"),
            ("loaded_hourly_cost", "Loaded hourly cost"),
            ("proposed_operating_cost_per_period", "Operating cost per period"),
        )
        numeric = {key: st.text_input(label, value=str(value.get(key) if value.get(key) is not None else ""))
                   for key, label in fields}
        period = st.text_input("Period", value=value.get("period", "month"))
        if st.form_submit_button("Save intake", disabled=assessment["status"] == "finalized"):
            try:
                from decimal import Decimal

                for field, text in numeric.items():
                    if text.strip() and (not Decimal(text.strip()).is_finite() or Decimal(text.strip()) < 0):
                        raise ValueError(f"Invalid nonnegative value for {field}")
                _change(st, base_url, "PATCH", f"/api/v1/assessments/{aid}", {
                    "title": title, "organization": organization or None, "owner": owner or None,
                    "business_problem": problem or None,
                    "value_inputs": {**{key: numeric[key].strip() or None for key, _ in fields},
                                     "period": period or "month"},
                })
            except (ValueError, ArithmeticError) as exc:
                st.error(str(exc))


def _discovery(st, base_url: str, assessment: dict):
    aid = assessment["id"]
    findings = api_request(base_url, "GET", f"/api/v1/assessments/{aid}/next-question")
    question = findings.get("next_question")
    if question:
        st.info(f"Next: {question['text']} — {question['reason']}")
    else:
        st.success("All applicable stock questions have answers. Review assumptions and contradictions.")
    questions = api_request(base_url, "GET", "/api/v1/discovery/questions")
    by_key = {q["key"]: q for q in questions}
    keys = list(by_key)
    default = keys.index(question["key"]) if question and question["key"] in keys else 0
    with st.form(f"answer-{aid}"):
        key = st.selectbox("Question", options=keys, index=default,
                           format_func=lambda k: by_key[k]["text"])
        answer_text = st.text_input("Answer (type true/false for yes/no questions)")
        kind = st.selectbox("Evidence kind", ["fact", "assumption", "unknown"])
        source = st.text_input("Evidence source / stakeholder")
        confidence = st.slider("Confidence (reported by source)", min_value=0.0, max_value=1.0, value=0.5)
        if st.form_submit_button("Record answer", disabled=assessment["status"] == "finalized"):
            value = {"true": True, "false": False}.get(answer_text.strip().lower(), answer_text.strip() or None)
            _change(st, base_url, "POST", f"/api/v1/assessments/{aid}/answers", {
                "key": key, "value": value, "kind": kind,
                "source": source or None, "confidence": confidence,
            })
    st.caption(f"Missing applicable evidence: {len(findings['missing_question_keys'])} questions")
    for issue in findings.get("findings", []):
        st.write(f"**{issue['kind']} · {issue['key']}** — {issue['detail']}")
    with st.expander("Saved source answers"):
        st.json(assessment["answers"])


def _workflow(st, base_url: str, assessment: dict):
    aid = assessment["id"]
    locked = assessment["status"] == "finalized"
    st.caption("Create workflow → ordered step → one or more atomic tasks. Each task is evaluated separately.")
    with st.form(f"workflow-create-{aid}"):
        name = st.text_input("New workflow name")
        if st.form_submit_button("Add workflow", disabled=locked):
            _change(st, base_url, "POST", f"/api/v1/assessments/{aid}/workflows", {"name": name})
    for workflow in assessment["workflows"]:
        wid = workflow["id"]
        st.markdown(f"### {workflow['name']}")
        with st.form(f"step-create-{wid}"):
            step_name = st.text_input("New step name")
            position = st.number_input("Step order", min_value=0, value=max([s["order"] for s in workflow["steps"]], default=-1) + 1)
            step_owner = st.text_input("Step owner (optional)")
            step_system = st.text_input("System (optional)")
            if st.form_submit_button("Add workflow step", disabled=locked):
                _change(st, base_url, "POST", f"/api/v1/workflows/{wid}/steps", {
                    "name": step_name, "order": int(position), "owner": step_owner or None,
                    "system": step_system or None,
                })
        for step in workflow["steps"]:
            sid = step["id"]
            with st.expander(f"Step {step['order']} · {step['name']} — {len(step['tasks'])} tasks", expanded=False):
                with st.form(f"step-edit-{sid}"):
                    ename = st.text_input("Step name", value=step["name"])
                    updated_order = st.number_input("Step order", min_value=0, value=step["order"])
                    owner = st.text_input("Owner", value=step.get("owner") or "")
                    system = st.text_input("System", value=step.get("system") or "")
                    input_text = st.text_area("Inputs", value=step.get("input_description") or "")
                    output_text = st.text_area("Outputs", value=step.get("output_description") or "")
                    if st.form_submit_button("Save step", disabled=locked):
                        _change(st, base_url, "PATCH", f"/api/v1/workflow-steps/{sid}", {
                            "name": ename, "order": int(updated_order), "owner": owner or None, "system": system or None,
                            "input_description": input_text or None, "output_description": output_text or None,
                        })
                with st.form(f"task-add-{sid}"):
                    task_title = st.text_input("New atomic task title")
                    decision_type = st.selectbox("Decision type", ["unknown", "deterministic", "judgment", "mixed"])
                    task_signals = st.text_area("Signals JSON (unknown fields may be omitted)", value='{"data_available": true, "output_verifiable": true}')
                    actions = st.multiselect("Business actions", ACTIONS)
                    if st.form_submit_button("Add task", disabled=locked):
                        try:
                            signals = _json_object(task_signals, "Signals")
                        except ValueError as exc:
                            st.error(str(exc))
                        else:
                            _change(st, base_url, "POST", f"/api/v1/workflow-steps/{sid}/tasks", {
                                "title": task_title, "decision_type": decision_type,
                                "signals": signals, "actions": actions,
                            })
                for task in step["tasks"]:
                    tid = task["id"]
                    st.write(f"**Task: {task['title']}** · {task['decision_type']}")
                    with st.form(f"task-edit-{tid}"):
                        name = st.text_input("Task title", value=task["title"])
                        description = st.text_area("Task details", value=task["description"])
                        signals_json = st.text_area("Suitability signals JSON", value=json.dumps({k: v for k, v in task["signals"].items() if v is not None}, indent=2))
                        risks_json = st.text_area("Risks JSON (list of category/description/impact/likelihood)", value=json.dumps(task["risks"], indent=2))
                        task_actions = st.multiselect("Task actions", ACTIONS, default=task["actions"])
                        if st.form_submit_button("Save task", disabled=locked):
                            try:
                                signals = _json_object(signals_json, "Signals")
                                risks = _json_array(risks_json, "Risks")
                            except (json.JSONDecodeError, ValueError) as exc:
                                st.error(str(exc))
                            else:
                                _change(st, base_url, "PATCH", f"/api/v1/tasks/{tid}", {
                                    "title": name, "description": description,
                                    "signals": signals, "risks": risks, "actions": task_actions,
                                })


def _analysis(st, base_url: str, assessment: dict):
    aid = assessment["id"]
    if st.button("Analyze saved workflows", disabled=assessment["status"] == "finalized", type="primary"):
        _change(st, base_url, "POST", f"/api/v1/assessments/{aid}/analyze")
    result = assessment.get("analysis")
    if result is None:
        st.info("No current analysis. All workflow steps must have at least one task.")
    else:
        st.caption(f"Assessment revision {assessment['revision']} · analysis revision {assessment['analysis_revision']}")
        _show_recommendations(st, result)



AI_FEATURES = ["discovery", "workflow", "risk", "architecture", "evaluation", "challenge"]


def request_ai_proposal(base_url: str, assessment_id: str, feature: str, *,
                        approved: bool, access_token: str | None = None) -> dict:
    """Request untrusted advisory JSON; never apply it to canonical tasks or scores."""
    if feature not in AI_FEATURES:
        raise ValueError("Unsupported AI feature")
    if not approved:
        raise ValueError("Explicit approval for synthetic provider data required")
    if not access_token:
        raise ValueError("Local AI access token required")
    result = api_request(base_url, "POST",
                         f"/api/v1/assessments/{assessment_id}/ai/{feature}",
                         {"send_to_provider": True, "approved_non_sensitive_data": True},
                         ai_access_token=access_token, timeout=30.0)
    if not isinstance(result, dict) or result.get("advisory_only") is not True:
        raise ValueError("Invalid advisory response")
    return result


def _ai_advisor(st, base_url: str, assessment: dict):
    aid = assessment["id"]
    st.warning("Experimental AI suggestions only. Synthetic or explicitly approved non-sensitive "
               "data; no production use, autonomous actions or rollout approvals.")
    st.info("AI must be enabled on the API server. Rules and required approvals always take precedence.")
    feature = st.selectbox("Advisory task", AI_FEATURES)
    access_token = st.text_input("Local AI access token (API server ADVISORY_AI_ACCESS_TOKEN)",
                                 type="password")
    approved = st.checkbox("I explicitly approve sending this assessment's synthetic/non-sensitive "
                           "content to the configured external AI provider for this request")
    if st.button("Generate AI suggestion", disabled=not approved or not access_token or assessment["status"] == "finalized"):
        try:
            request_ai_proposal(base_url, aid, feature, approved=approved,
                                access_token=access_token)
        except (HTTPError, URLError, OSError, ValueError, json.JSONDecodeError) as exc:
            st.error(_api_error(exc))
        else:
            st.success("Proposal saved for review. It has not modified the workflow or final decision.")
            st.rerun()
    runs = api_request(base_url, "GET", f"/api/v1/assessments/{aid}/ai-runs")
    if not runs:
        st.info("No AI proposals recorded. The complete rules-only workflow remains available.")
    for run in runs:
        with st.expander(f"{run['feature']} · {run['model']} · assessment revision {run['assessment_revision']}"):
            if run.get("stale"):
                st.warning("Stale proposal: underlying assessment was edited. Re-evaluate before use.")
            st.json(run["proposal"])
            st.caption("Advisory only — manually verify and, if appropriate, enter proposed tasks or "
                       "risks using the normal workflow forms. No automatic acceptance or approvals.")
            if run["feature"] == "workflow" and not run.get("stale") and assessment["status"] != "finalized":
                drafts = run["proposal"].get("suggested_tasks", [])
                with st.form(f"accept-task-proposals-{run['id']}"):
                    st.caption("Select individual drafts after checking them. Their signals and actions "
                               "will remain unknown/empty until reviewed in the workflow editor.")
                    indices = [i for i, draft in enumerate(drafts)
                               if st.checkbox(f"Accept: {draft['title']} (step {draft['step_id']})", key=f"draft-{run['id']}-{i}")]
                    reviewer = st.text_input("FDE reviewer label (unverified)", key=f"reviewer-{run['id']}")
                    rationale = st.text_area("Reason for accepting selected drafts", key=f"why-{run['id']}")
                    token = st.text_input("Local reviewer token", type="password", key=f"accept-token-{run['id']}")
                    if st.form_submit_button("Accept selected task drafts"):
                        try:
                            if not indices or not token or not rationale.strip() or not reviewer.strip():
                                raise ValueError("Select drafts and supply reviewer, rationale and reviewer token")
                            api_request(base_url, "POST", f"/api/v1/assessments/{aid}/ai-runs/{run['id']}/accept-tasks",
                                        {"selected_indices": indices, "reviewer_label": reviewer, "rationale": rationale},
                                        reviewer_token=token)
                        except (HTTPError, URLError, OSError, ValueError, json.JSONDecodeError) as exc:
                            st.error(_api_error(exc))
                        else:
                            st.success("Accepted task drafts. Analysis is stale; re-run deterministic analysis.")
                            st.rerun()
            if run.get("estimated_cost_usd") is not None:
                st.caption(f"Estimated provider cost: USD {run['estimated_cost_usd']} "
                           f"(operator pricing source: {run['pricing_source']})")
            else:
                st.caption("Cost unknown: no verified pricing rates configured.")
            st.caption(f"Prompt {run['prompt_version']} · schema {run['schema_version']} "
                       f"· {run['elapsed_ms']} ms · tokens {run.get('input_tokens')}/"
                       f"{run.get('output_tokens')}")

def _review(st, base_url: str, assessment: dict):
    aid = assessment["id"]
    st.warning("Local demo review only. A typed reviewer name is NOT authenticated identity. No rollout approval.")
    if not assessment.get("analysis"):
        st.info("Run analysis before submitting an FDE review.")
        return
    recs = {t["recommendation"]["category"] for t in assessment["analysis"]["tasks"]}
    with st.form(f"review-{aid}"):
        reviewer = st.text_input("Reviewer label (unverified)")
        category = st.selectbox("Advisory decision category", RECOMMENDATION_CATEGORIES,
                                index=RECOMMENDATION_CATEGORIES.index(sorted(recs)[0]) if recs else 0)
        rationale = st.text_area("Why are you recommending this decision?")
        override = st.text_area("Override rationale (required when selected category isn't recommended)")
        if st.form_submit_button("Record review", disabled=assessment["status"] == "finalized"):
            _change(st, base_url, "POST", f"/api/v1/assessments/{aid}/fde-review", {
                "reviewer_id": reviewer, "selected_category": category,
                "rationale": rationale, "override_reason": override or None,
            })
    st.json(assessment.get("reviews", []))
    token = st.text_input("Local reviewer token (server must define ADVISORY_REVIEW_TOKEN)", type="password")
    if assessment["status"] == "finalized":
        if st.button("Reopen finalized local demo", disabled=not token):
            _change(st, base_url, "POST", f"/api/v1/assessments/{aid}/reopen", token=token)
    elif st.button("Finalize local demo review", disabled=not token or not assessment.get("reviews")):
        _change(st, base_url, "POST", f"/api/v1/assessments/{aid}/final-decision", token=token)
    with st.expander("Assessment audit history"):
        history = api_request(base_url, "GET", f"/api/v1/assessments/{aid}/audit")
        st.json(history)


def main() -> None:
    import streamlit as st

    st.set_page_config(page_title="AI Opportunity Advisor", page_icon="🧭", layout="wide")
    st.title("AI Opportunity Advisor")
    st.caption("Phase 4 · Persistent advisory assessments, optional AI proposals and report exports")
    st.info("AI assists. Rules enforce. Data informs. Humans decide.")
    st.warning("Local developer demo only — synthetic, non-sensitive data. No authentication or tenancy.")
    base_url = os.environ.get("API_BASE_URL", DEFAULT_API_BASE_URL)
    st.sidebar.text_input("Organization API credential (not a login session)", type="password", key="api_bearer")
    _API_BEARER.set(st.session_state.get("api_bearer") or None)
    try:
        health = fetch_api_health(base_url)
        st.success(f"API connected · {health['service']} · {health['version']} · {health['mode']}")
        assessment = _dashboard(st, base_url)
        if assessment:
            st.caption(f"Status: {assessment['status']} · Revision: {assessment['revision']} · ID: {assessment['id']}")
            intake, discovery, workflow, analysis, advisor, review, report = st.tabs([
                "1 · Intake", "2 · Discovery", "3 · Workflow", "4 · Analysis", "5 · AI advisor", "6 · FDE review", "7 · Reports",
            ])
            with intake:
                _intake(st, base_url, assessment)
            with discovery:
                _discovery(st, base_url, assessment)
            with workflow:
                _workflow(st, base_url, assessment)
            with analysis:
                _analysis(st, base_url, assessment)
            with advisor:
                _ai_advisor(st, base_url, assessment)
            with review:
                _review(st, base_url, assessment)
            with report:
                render_report_tab(st, base_url, assessment)
        with st.expander("Stateless rules-only preview (legacy Phase 1)"):
            raw = st.text_area("Preview JSON", value=json.dumps(DEMO_PAYLOAD, indent=2), height=220)
            if st.button("Run preview"):
                try:
                    result = preview_assessment(base_url, _json_object(raw, "Preview input"))
                except (HTTPError, URLError, OSError, ValueError, json.JSONDecodeError) as exc:
                    st.error(_api_error(exc))
                else:
                    _show_recommendations(st, result)
    except (HTTPError, URLError, OSError, ValueError, json.JSONDecodeError) as exc:
        st.error(f"API request failed. Start FastAPI and apply migrations first. {_api_error(exc)}")


if __name__ == "__main__":
    main()
