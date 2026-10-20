"""Phase 2 guided local Streamlit UI. HTTP API is the sole data/analysis boundary.

Every user-facing string is a catalogue entry: a widget label is a module
constant (the accessibility audit reads labels from the source), a message is
a :func:`app.localization.t` call with the English text as its default.  The
ids are stable, the English text stays in this file and the Norwegian
catalogue lives in ``app/localization/locales``.
"""

import json
import os
from contextvars import ContextVar
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.localization import current, t
from ui.accessibility import apply_accessibility, heading
from ui.i18n import activate, report_catalogue
from ui.reporting import render_report_tab

DEFAULT_API_BASE_URL = "http://127.0.0.1:8000"
_API_BEARER: ContextVar[str | None] = ContextVar("api_bearer", default=None)
_API_ORGANIZATION: ContextVar[str | None] = ContextVar("api_org", default=None)
_ASSESSMENT_REVISION: ContextVar[int | None] = ContextVar("assessment_revision", default=None)

# --- widget labels ------------------------------------------------------------
# The accessibility audit reads the label of every control from the source, so
# the labels are module constants.  Their catalogue id is the qualified
# constant name ("ui.streamlit_app.LABEL_ASSESSMENT_TITLE").

LABEL_ASSESSMENT_TITLE = "Assessment title"
LABEL_ORGANIZATION_SYNTHETIC = "Organization (synthetic data only)"
LABEL_OWNER = "FDE / owner"
LABEL_OWNER_NAME = "Owner"
LABEL_BUSINESS_PROBLEM = "Business problem"
LABEL_TITLE = "Title"
LABEL_ORGANIZATION = "Organization"
LABEL_PROBLEM = "Problem"
LABEL_CASES_PER_PERIOD = "Cases per period"
LABEL_CURRENT_MINUTES = "Current minutes per case"
LABEL_TARGET_MINUTES = "Target minutes per case"
LABEL_LOADED_HOURLY_COST = "Loaded hourly cost"
LABEL_OPERATING_COST = "Operating cost per period"
LABEL_PERIOD = "Period"
LABEL_QUESTION = "Question"
LABEL_ANSWER = "Answer (type true/false for yes/no questions)"
LABEL_EVIDENCE_KIND = "Evidence kind"
LABEL_EVIDENCE_SOURCE = "Evidence source / stakeholder"
LABEL_CONFIDENCE = "Confidence (reported by source)"
LABEL_NEW_WORKFLOW_NAME = "New workflow name"
LABEL_NEW_STEP_NAME = "New step name"
LABEL_STEP_NAME = "Step name"
LABEL_STEP_ORDER = "Step order"
LABEL_STEP_OWNER = "Step owner (optional)"
LABEL_STEP_SYSTEM = "System (optional)"
LABEL_TASK_TITLE = "New atomic task title"
LABEL_DECISION_TYPE = "Decision type"
LABEL_SIGNALS_JSON = "Signals JSON (unknown fields may be omitted)"
LABEL_BUSINESS_ACTIONS = "Business actions"
LABEL_TASK_TITLE_EDIT = "Task title"
LABEL_TASK_DETAILS = "Task details"
LABEL_INPUTS = "Inputs"
LABEL_OUTPUTS = "Outputs"
LABEL_FDE_REVIEWER = "FDE reviewer label (unverified)"
LABEL_ACCEPT_RATIONALE = "Reason for accepting selected drafts"
LABEL_ACCEPT_DRAFTS = "Accept selected task drafts"
LABEL_LOCAL_REVIEWER_TOKEN = "Local reviewer token"
LABEL_SUITABILITY_SIGNALS = "Suitability signals JSON"
LABEL_RISKS_JSON = "Risks JSON (list of category/description/impact/likelihood)"
LABEL_TASK_ACTIONS = "Task actions"
LABEL_SEARCH_ASSESSMENTS = "Search assessments"
LABEL_STATUS_FILTER = "Status filter"
LABEL_OPEN_ASSESSMENT = "Open assessment"
LABEL_ADVISORY_TASK = "Advisory task"
LABEL_AI_ACCESS_TOKEN = "Local AI access token (API server ADVISORY_AI_ACCESS_TOKEN)"
LABEL_APPROVE_AI = (
    "I explicitly approve sending this assessment's synthetic/non-sensitive "
    "content to the configured external AI provider for this request"
)
LABEL_REVIEWER_LABEL = "Reviewer label (unverified)"
LABEL_DECISION_CATEGORY = "Advisory decision category"
LABEL_REVIEW_RATIONALE = "Why are you recommending this decision?"
LABEL_OVERRIDE_RATIONALE = "Override rationale (required when selected category isn't recommended)"
LABEL_REVIEWER_TOKEN = "Local reviewer token (server must define ADVISORY_REVIEW_TOKEN)"
LABEL_PREVIEW_JSON = "Preview JSON"
LABEL_API_CREDENTIAL = "Organization API credential (not a login session)"
LABEL_ORGANIZATION_ID = "Organization ID (only when OIDC has multiple memberships)"
LABEL_NAVIGATION = "Navigation"
LABEL_CREATE_ASSESSMENT = "Create assessment"
LABEL_SAVE_INTAKE = "Save intake"
LABEL_RECORD_ANSWER = "Record answer"
LABEL_ADD_WORKFLOW = "Add workflow"
LABEL_ADD_WORKFLOW_STEP = "Add workflow step"
LABEL_SAVE_STEP = "Save step"
LABEL_ADD_TASK = "Add task"
LABEL_SAVE_TASK = "Save task"
LABEL_ANALYZE = "Analyze saved workflows"
LABEL_GENERATE_AI = "Generate AI suggestion"
LABEL_RECORD_REVIEW = "Record review"
LABEL_REOPEN = "Reopen finalized local demo"
LABEL_FINALIZE = "Finalize local demo review"
LABEL_RUN_PREVIEW = "Run preview"

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

RECOMMENDATION_CATEGORIES = [
    "traditional_software",
    "strong_ai_candidate",
    "hybrid",
    "ai_human_approval",
    "human_led",
    "more_discovery",
]
ACTIONS = [
    "knowledge_search",
    "summarize",
    "draft",
    "read_customer_data",
    "create_ticket",
    "modify_customer_record",
    "cancel_order",
    "issue_refund",
    "transfer_money",
    "delete_data",
    "legal_commitment",
    "employment_decision",
    "medical_decision",
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
    base_url: str,
    method: str,
    path: str,
    payload: dict | None = None,
    *,
    reviewer_token: str | None = None,
    ai_access_token: str | None = None,
    timeout: float = 12.0,
):
    """Make a validated JSON request; errors propagate for visible Streamlit feedback."""
    if not path.startswith("/api/v1/"):
        raise ValueError("API path must start with /api/v1/")
    headers = {"Accept": "application/json"}
    bearer = _API_BEARER.get()
    if bearer:
        headers["Authorization"] = "Bearer " + bearer
    tenant = _API_ORGANIZATION.get()
    if tenant:
        headers["X-Organization-ID"] = tenant
    revision = _ASSESSMENT_REVISION.get()
    if (
        revision is not None
        and method in {"POST", "PUT", "PATCH", "DELETE"}
        and path != "/api/v1/assessments"
    ):
        headers["If-Match"] = f'"{revision}"'
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
            detail = body.get(
                "detail", t("ui.streamlit_app.unspecified_error", "Unspecified error")
            )
        except (ValueError, OSError):
            detail = exc.reason
        return t("ui.streamlit_app.api_error", "API {code}: {detail}", code=exc.code, detail=detail)
    return str(exc)


def _change(
    st, base_url: str, method: str, path: str, payload: dict | None = None, token: str | None = None
):
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
        raise ValueError(
            t(
                "ui.streamlit_app.json_object",
                "{label} must contain a JSON object",
                label=label,
            )
        )
    return obj


def _json_array(value: str, label: str) -> list:
    obj = json.loads(value)
    if not isinstance(obj, list):
        raise ValueError(
            t(
                "ui.streamlit_app.json_array",
                "{label} must contain a JSON array",
                label=label,
            )
        )
    return obj


def _show_recommendations(st, result: dict) -> None:
    st.caption(
        t(
            "ui.streamlit_app.rules_version",
            "Rules version: {version} · Advisory only · No external actions",
            version=result["policy_version"],
        )
    )
    for task in result.get("tasks", []):
        rec = task["recommendation"]
        st.markdown(f"**{task['task_id']}: {rec['category'].replace('_', ' ').title()}**")
        st.write(
            t("ui.streamlit_app.ai_suitability", "AI suitability:"),
            current().number(task["ai_suitability"].get("value")),
            "|",
            t("ui.streamlit_app.evidence_coverage", "Evidence coverage:"),
            current().percent(task["ai_suitability"].get("coverage")),
        )
        st.write(t("ui.streamlit_app.why", "Why:"), rec.get("rationale", []))
        st.write(
            t("ui.streamlit_app.deterministic_boundary", "Code / deterministic boundary:"),
            rec.get("deterministic_role"),
        )
        st.write(t("ui.streamlit_app.ai_boundary", "AI boundary:"), rec.get("ai_role"))
        st.write(t("ui.streamlit_app.human_boundary", "Human boundary:"), rec.get("human_role"))
        st.write(t("ui.streamlit_app.controls", "Controls:"), rec.get("required_controls", []))
        st.write(
            t("ui.streamlit_app.open_evidence", "Open evidence:"), rec.get("missing_evidence", [])
        )
        with st.expander(t("ui.streamlit_app.rule_hits", "Rule hits, factors and risks")):
            st.json(
                {
                    "rules": task["rule_hits"],
                    "factors": task["ai_suitability"]["factors"],
                    "risks": task["risks"],
                    "technical": task["technical_feasibility"],
                    "data_readiness": task["data_readiness"],
                }
            )
    if result.get("value"):
        heading(
            st,
            t(
                "ui.streamlit_app.illustrative_value",
                "Illustrative business value (not validated ROI)",
            ),
            4,
        )
        st.json(result["value"])
    for warning in result.get("warnings", []):
        st.warning(warning)


def _dashboard(st, base_url: str) -> dict | None:
    heading(st, t("ui.streamlit_app.assessments", "Assessments"), 2)
    with st.expander(
        t("ui.streamlit_app.create_assessment", "Create a new assessment"), expanded=False
    ):
        with st.form("create_assessment"):
            title = st.text_input(LABEL_ASSESSMENT_TITLE)
            organization = st.text_input(LABEL_ORGANIZATION_SYNTHETIC)
            owner = st.text_input(LABEL_OWNER)
            problem = st.text_area(LABEL_BUSINESS_PROBLEM)
            if st.form_submit_button(LABEL_CREATE_ASSESSMENT):
                if not title.strip():
                    st.error(t("ui.streamlit_app.title_required", "Title is required"))
                else:
                    try:
                        created = api_request(
                            base_url,
                            "POST",
                            "/api/v1/assessments",
                            {
                                "title": title,
                                "organization": organization or None,
                                "owner": owner or None,
                                "business_problem": problem or None,
                            },
                        )
                    except (HTTPError, URLError, ValueError, OSError) as exc:
                        st.error(_api_error(exc))
                    else:
                        st.session_state["assessment_id"] = created["id"]
                        st.rerun()
    rows = api_request(base_url, "GET", "/api/v1/assessments")
    if not rows:
        st.info(
            t(
                "ui.streamlit_app.dashboard_empty",
                "Create an assessment to begin guided discovery.",
            )
        )
        return None
    search = st.text_input(
        LABEL_SEARCH_ASSESSMENTS,
        placeholder=t(
            "ui.streamlit_app.search_placeholder", "Filter by title, owner or organization"
        ),
    )
    statuses = ["All"] + sorted({row["status"] for row in rows})
    chosen_status = st.selectbox(
        LABEL_STATUS_FILTER,
        statuses,
        format_func=lambda status: (
            t("ui.streamlit_app.filter_all", "All") if status == "All" else status
        ),
    )
    if search.strip():
        term = search.casefold().strip()
        rows = [
            r
            for r in rows
            if term
            in " ".join(
                [r.get("title") or "", r.get("owner") or "", r.get("organization") or ""]
            ).casefold()
        ]
    if chosen_status != "All":
        rows = [r for r in rows if r["status"] == chosen_status]
    if not rows:
        st.info(t("ui.streamlit_app.no_matches", "No assessments match the current filters."))
        return None
    by_id = {row["id"]: row for row in rows}
    current = st.session_state.get("assessment_id")
    options = list(by_id)
    selection = st.selectbox(
        LABEL_OPEN_ASSESSMENT,
        options=options,
        index=options.index(current) if current in options else 0,
        format_func=lambda rid: f"{by_id[rid]['title']} — {by_id[rid]['status']}",
    )
    st.session_state["assessment_id"] = selection
    return api_request(base_url, "GET", f"/api/v1/assessments/{selection}")


def _intake(st, base_url: str, assessment: dict):
    aid = assessment["id"]
    heading(st, t("ui.streamlit_app.intake", "Intake"), 3)
    with st.form(f"intake-{aid}"):
        title = st.text_input(LABEL_TITLE, value=assessment["title"])
        organization = st.text_input(LABEL_ORGANIZATION, value=assessment["organization"] or "")
        owner = st.text_input(LABEL_OWNER_NAME, value=assessment["owner"] or "")
        problem = st.text_area(LABEL_PROBLEM, value=assessment["business_problem"] or "")
        st.caption(
            t(
                "ui.streamlit_app.value_hint",
                "Optional financial assumptions: leave blank if unknown. "
                "Do not enter invented figures.",
            )
        )
        value = assessment.get("value_inputs") or {}
        numeric = {
            "cases_per_period": st.text_input(
                LABEL_CASES_PER_PERIOD,
                value=str(
                    value.get("cases_per_period")
                    if value.get("cases_per_period") is not None
                    else ""
                ),
            ),
            "baseline_minutes_per_case": st.text_input(
                LABEL_CURRENT_MINUTES,
                value=str(
                    value.get("baseline_minutes_per_case")
                    if value.get("baseline_minutes_per_case") is not None
                    else ""
                ),
            ),
            "target_minutes_per_case": st.text_input(
                LABEL_TARGET_MINUTES,
                value=str(
                    value.get("target_minutes_per_case")
                    if value.get("target_minutes_per_case") is not None
                    else ""
                ),
            ),
            "loaded_hourly_cost": st.text_input(
                LABEL_LOADED_HOURLY_COST,
                value=str(
                    value.get("loaded_hourly_cost")
                    if value.get("loaded_hourly_cost") is not None
                    else ""
                ),
            ),
            "proposed_operating_cost_per_period": st.text_input(
                LABEL_OPERATING_COST,
                value=str(
                    value.get("proposed_operating_cost_per_period")
                    if value.get("proposed_operating_cost_per_period") is not None
                    else ""
                ),
            ),
        }
        period = st.text_input(LABEL_PERIOD, value=value.get("period", "month"))
        if st.form_submit_button(LABEL_SAVE_INTAKE, disabled=assessment["status"] == "finalized"):
            try:
                from decimal import Decimal

                for field, text in numeric.items():
                    if text.strip() and (
                        not Decimal(text.strip()).is_finite() or Decimal(text.strip()) < 0
                    ):
                        raise ValueError(
                            t(
                                "ui.streamlit_app.invalid_value",
                                "Invalid nonnegative value for {field}",
                                field=field,
                            )
                        )
                _change(
                    st,
                    base_url,
                    "PATCH",
                    f"/api/v1/assessments/{aid}",
                    {
                        "title": title,
                        "organization": organization or None,
                        "owner": owner or None,
                        "business_problem": problem or None,
                        "value_inputs": {
                            **{key: text.strip() or None for key, text in numeric.items()},
                            "period": period or "month",
                        },
                    },
                )
            except (ValueError, ArithmeticError) as exc:
                st.error(str(exc))


def _discovery(st, base_url: str, assessment: dict):
    aid = assessment["id"]
    heading(st, t("ui.streamlit_app.discovery", "Discovery"), 3)
    findings = api_request(base_url, "GET", f"/api/v1/assessments/{aid}/next-question")
    question = findings.get("next_question")
    if question:
        st.info(
            t(
                "ui.streamlit_app.next_question",
                "Next: {text} — {reason}",
                text=question["text"],
                reason=question["reason"],
            )
        )
    else:
        st.success(
            t(
                "ui.streamlit_app.discovery_complete",
                "All applicable stock questions have answers. "
                "Review assumptions and contradictions.",
            )
        )
    questions = api_request(base_url, "GET", "/api/v1/discovery/questions")
    by_key = {q["key"]: q for q in questions}
    keys = list(by_key)
    default = keys.index(question["key"]) if question and question["key"] in keys else 0
    with st.form(f"answer-{aid}"):
        key = st.selectbox(
            LABEL_QUESTION, options=keys, index=default, format_func=lambda k: by_key[k]["text"]
        )
        answer_text = st.text_input(LABEL_ANSWER)
        kind = st.selectbox(LABEL_EVIDENCE_KIND, ["fact", "assumption", "unknown"])
        source = st.text_input(LABEL_EVIDENCE_SOURCE)
        confidence = st.slider(LABEL_CONFIDENCE, min_value=0.0, max_value=1.0, value=0.5)
        if st.form_submit_button(LABEL_RECORD_ANSWER, disabled=assessment["status"] == "finalized"):
            value = {"true": True, "false": False}.get(
                answer_text.strip().lower(), answer_text.strip() or None
            )
            _change(
                st,
                base_url,
                "POST",
                f"/api/v1/assessments/{aid}/answers",
                {
                    "key": key,
                    "value": value,
                    "kind": kind,
                    "source": source or None,
                    "confidence": confidence,
                },
            )
    st.caption(
        t(
            "ui.streamlit_app.missing_evidence",
            "Missing applicable evidence: {count} questions",
            count=len(findings["missing_question_keys"]),
        )
    )
    for issue in findings.get("findings", []):
        st.write(f"**{issue['kind']} · {issue['key']}** — {issue['detail']}")
    with st.expander(t("ui.streamlit_app.saved_answers", "Saved source answers")):
        st.json(assessment["answers"])


def _workflow(st, base_url: str, assessment: dict):
    aid = assessment["id"]
    locked = assessment["status"] == "finalized"
    heading(st, t("ui.streamlit_app.workflow", "Workflow"), 3)
    st.caption(
        t(
            "ui.streamlit_app.workflow_hint",
            "Create workflow → ordered step → one or more atomic tasks. "
            "Each task is evaluated separately.",
        )
    )
    with st.form(f"workflow-create-{aid}"):
        name = st.text_input(LABEL_NEW_WORKFLOW_NAME)
        if st.form_submit_button(LABEL_ADD_WORKFLOW, disabled=locked):
            _change(st, base_url, "POST", f"/api/v1/assessments/{aid}/workflows", {"name": name})
    for workflow in assessment["workflows"]:
        wid = workflow["id"]
        heading(st, workflow["name"], 4)
        with st.form(f"step-create-{wid}"):
            step_name = st.text_input(LABEL_NEW_STEP_NAME)
            position = st.number_input(
                LABEL_STEP_ORDER,
                min_value=0,
                value=max([s["order"] for s in workflow["steps"]], default=-1) + 1,
            )
            step_owner = st.text_input(LABEL_STEP_OWNER)
            step_system = st.text_input(LABEL_STEP_SYSTEM)
            if st.form_submit_button(LABEL_ADD_WORKFLOW_STEP, disabled=locked):
                _change(
                    st,
                    base_url,
                    "POST",
                    f"/api/v1/workflows/{wid}/steps",
                    {
                        "name": step_name,
                        "order": int(position),
                        "owner": step_owner or None,
                        "system": step_system or None,
                    },
                )
        for step in workflow["steps"]:
            sid = step["id"]
            with st.expander(
                t(
                    "ui.streamlit_app.step_expander",
                    "Step {order} · {name} — {count} tasks",
                    order=step["order"],
                    name=step["name"],
                    count=len(step["tasks"]),
                ),
                expanded=False,
            ):
                with st.form(f"step-edit-{sid}"):
                    ename = st.text_input(LABEL_STEP_NAME, value=step["name"])
                    updated_order = st.number_input(
                        LABEL_STEP_ORDER, min_value=0, value=step["order"]
                    )
                    owner = st.text_input(LABEL_OWNER_NAME, value=step.get("owner") or "")
                    system = st.text_input(LABEL_STEP_SYSTEM, value=step.get("system") or "")
                    input_text = st.text_area(
                        LABEL_INPUTS, value=step.get("input_description") or ""
                    )
                    output_text = st.text_area(
                        LABEL_OUTPUTS,
                        value=step.get("output_description") or "",
                    )
                    if st.form_submit_button(LABEL_SAVE_STEP, disabled=locked):
                        _change(
                            st,
                            base_url,
                            "PATCH",
                            f"/api/v1/workflow-steps/{sid}",
                            {
                                "name": ename,
                                "order": int(updated_order),
                                "owner": owner or None,
                                "system": system or None,
                                "input_description": input_text or None,
                                "output_description": output_text or None,
                            },
                        )
                with st.form(f"task-add-{sid}"):
                    task_title = st.text_input(LABEL_TASK_TITLE)
                    decision_type = st.selectbox(
                        LABEL_DECISION_TYPE, ["unknown", "deterministic", "judgment", "mixed"]
                    )
                    task_signals = st.text_area(
                        LABEL_SIGNALS_JSON,
                        value='{"data_available": true, "output_verifiable": true}',
                    )
                    actions = st.multiselect(LABEL_BUSINESS_ACTIONS, ACTIONS)
                    if st.form_submit_button(LABEL_ADD_TASK, disabled=locked):
                        try:
                            signals = _json_object(
                                task_signals, t("ui.streamlit_app.signals", "Signals")
                            )
                        except ValueError as exc:
                            st.error(str(exc))
                        else:
                            _change(
                                st,
                                base_url,
                                "POST",
                                f"/api/v1/workflow-steps/{sid}/tasks",
                                {
                                    "title": task_title,
                                    "decision_type": decision_type,
                                    "signals": signals,
                                    "actions": actions,
                                },
                            )
                for task in step["tasks"]:
                    tid = task["id"]
                    st.write(
                        t(
                            "ui.streamlit_app.task_line",
                            "**Task: {title}** · {decision_type}",
                            title=task["title"],
                            decision_type=task["decision_type"],
                        )
                    )
                    with st.form(f"task-edit-{tid}"):
                        name = st.text_input(LABEL_TASK_TITLE_EDIT, value=task["title"])
                        description = st.text_area(LABEL_TASK_DETAILS, value=task["description"])
                        signals_json = st.text_area(
                            LABEL_SUITABILITY_SIGNALS,
                            value=json.dumps(
                                {k: v for k, v in task["signals"].items() if v is not None},
                                indent=2,
                            ),
                        )
                        risks_json = st.text_area(
                            LABEL_RISKS_JSON,
                            value=json.dumps(task["risks"], indent=2),
                        )
                        task_actions = st.multiselect(
                            LABEL_TASK_ACTIONS, ACTIONS, default=task["actions"]
                        )
                        if st.form_submit_button(LABEL_SAVE_TASK, disabled=locked):
                            try:
                                signals = _json_object(
                                    signals_json, t("ui.streamlit_app.signals", "Signals")
                                )
                                risks = _json_array(
                                    risks_json, t("ui.streamlit_app.risks", "Risks")
                                )
                            except (json.JSONDecodeError, ValueError) as exc:
                                st.error(str(exc))
                            else:
                                _change(
                                    st,
                                    base_url,
                                    "PATCH",
                                    f"/api/v1/tasks/{tid}",
                                    {
                                        "title": name,
                                        "description": description,
                                        "signals": signals,
                                        "risks": risks,
                                        "actions": task_actions,
                                    },
                                )


def _analysis(st, base_url: str, assessment: dict):
    aid = assessment["id"]
    heading(st, t("ui.streamlit_app.analysis", "Analysis"), 3)
    if st.button(LABEL_ANALYZE, disabled=assessment["status"] == "finalized", type="primary"):
        _change(st, base_url, "POST", f"/api/v1/assessments/{aid}/analyze")
    result = assessment.get("analysis")
    if result is None:
        st.info(
            t(
                "ui.streamlit_app.no_analysis",
                "No current analysis. All workflow steps must have at least one task.",
            )
        )
    else:
        st.caption(
            t(
                "ui.streamlit_app.revisions",
                "Assessment revision {revision} · analysis revision {analysis_revision}",
                revision=assessment["revision"],
                analysis_revision=assessment["analysis_revision"],
            )
        )
        _show_recommendations(st, result)


AI_FEATURES = ["discovery", "workflow", "risk", "architecture", "evaluation", "challenge"]


def request_ai_proposal(
    base_url: str,
    assessment_id: str,
    feature: str,
    *,
    approved: bool,
    access_token: str | None = None,
) -> dict:
    """Request untrusted advisory JSON; never apply it to canonical tasks or scores."""
    if feature not in AI_FEATURES:
        raise ValueError("Unsupported AI feature")
    if not approved:
        raise ValueError("Explicit approval for synthetic provider data required")
    if not access_token:
        raise ValueError("Local AI access token required")
    result = api_request(
        base_url,
        "POST",
        f"/api/v1/assessments/{assessment_id}/ai/{feature}",
        {"send_to_provider": True, "approved_non_sensitive_data": True},
        ai_access_token=access_token,
        timeout=30.0,
    )
    if not isinstance(result, dict) or result.get("advisory_only") is not True:
        raise ValueError("Invalid advisory response")
    return result


def _ai_advisor(st, base_url: str, assessment: dict):
    aid = assessment["id"]
    heading(st, t("ui.streamlit_app.ai_advisor", "AI advisor"), 3)
    st.warning(
        t(
            "ui.streamlit_app.ai_warning",
            "Experimental AI suggestions only. Synthetic or explicitly approved non-sensitive "
            "data; no production use, autonomous actions or rollout approvals.",
        )
    )
    st.info(
        t(
            "ui.streamlit_app.ai_info",
            "AI must be enabled on the API server. Rules and required approvals always "
            "take precedence.",
        )
    )
    feature = st.selectbox(LABEL_ADVISORY_TASK, AI_FEATURES)
    access_token = st.text_input(LABEL_AI_ACCESS_TOKEN, type="password")
    approved = st.checkbox(LABEL_APPROVE_AI)
    if st.button(
        LABEL_GENERATE_AI,
        disabled=not approved or not access_token or assessment["status"] == "finalized",
    ):
        try:
            request_ai_proposal(
                base_url, aid, feature, approved=approved, access_token=access_token
            )
        except (HTTPError, URLError, OSError, ValueError, json.JSONDecodeError) as exc:
            st.error(_api_error(exc))
        else:
            st.success(
                t(
                    "ui.streamlit_app.proposal_saved",
                    "Proposal saved for review. It has not modified the workflow "
                    "or final decision.",
                )
            )
            st.rerun()
    runs = api_request(base_url, "GET", f"/api/v1/assessments/{aid}/ai-runs")
    if not runs:
        st.info(
            t(
                "ui.streamlit_app.no_proposals",
                "No AI proposals recorded. The complete rules-only workflow remains available.",
            )
        )
    for run in runs:
        with st.expander(
            t(
                "ui.streamlit_app.run_expander",
                "{feature} · {model} · assessment revision {revision}",
                feature=run["feature"],
                model=run["model"],
                revision=run["assessment_revision"],
            )
        ):
            if run.get("stale"):
                st.warning(
                    t(
                        "ui.streamlit_app.stale_proposal",
                        "Stale proposal: underlying assessment was edited. Re-evaluate before use.",
                    )
                )
            st.json(run["proposal"])
            st.caption(
                t(
                    "ui.streamlit_app.proposal_caption",
                    "Advisory only — manually verify and, if appropriate, enter proposed tasks or "
                    "risks using the normal workflow forms. No automatic acceptance or approvals.",
                )
            )
            if (
                run["feature"] == "workflow"
                and not run.get("stale")
                and assessment["status"] != "finalized"
            ):
                drafts = run["proposal"].get("suggested_tasks", [])
                accept_prefix = t("ui.streamlit_app.accept_draft_prefix", "Accept: ")
                step_label = t("ui.streamlit_app.step_label", "step")
                with st.form(f"accept-task-proposals-{run['id']}"):
                    st.caption(
                        t(
                            "ui.streamlit_app.draft_hint",
                            "Select individual drafts after checking them. Their signals "
                            "and actions will remain unknown/empty until reviewed in the "
                            "workflow editor.",
                        )
                    )
                    indices = [
                        i
                        for i, draft in enumerate(drafts)
                        if st.checkbox(
                            f"{accept_prefix}{draft['title']} ({step_label} {draft['step_id']})",
                            key=f"draft-{run['id']}-{i}",
                        )
                    ]
                    reviewer = st.text_input(
                        LABEL_FDE_REVIEWER,
                        key=f"reviewer-{run['id']}",
                    )
                    rationale = st.text_area(
                        LABEL_ACCEPT_RATIONALE,
                        key=f"why-{run['id']}",
                    )
                    token = st.text_input(
                        LABEL_LOCAL_REVIEWER_TOKEN,
                        type="password",
                        key=f"accept-token-{run['id']}",
                    )
                    if st.form_submit_button(LABEL_ACCEPT_DRAFTS):
                        try:
                            if (
                                not indices
                                or not token
                                or not rationale.strip()
                                or not reviewer.strip()
                            ):
                                raise ValueError(
                                    t(
                                        "ui.streamlit_app.accept_error",
                                        "Select drafts and supply reviewer, rationale "
                                        "and reviewer token",
                                    )
                                )
                            api_request(
                                base_url,
                                "POST",
                                f"/api/v1/assessments/{aid}/ai-runs/{run['id']}/accept-tasks",
                                {
                                    "selected_indices": indices,
                                    "reviewer_label": reviewer,
                                    "rationale": rationale,
                                },
                                reviewer_token=token,
                            )
                        except (
                            HTTPError,
                            URLError,
                            OSError,
                            ValueError,
                            json.JSONDecodeError,
                        ) as exc:
                            st.error(_api_error(exc))
                        else:
                            st.success(
                                t(
                                    "ui.streamlit_app.accepted_drafts",
                                    "Accepted task drafts. Analysis is stale; "
                                    "re-run deterministic analysis.",
                                )
                            )
                            st.rerun()
            if run.get("estimated_cost_usd") is not None:
                st.caption(
                    t(
                        "ui.streamlit_app.estimated_cost",
                        "Estimated provider cost: USD {cost} (operator pricing source: {source})",
                        cost=run["estimated_cost_usd"],
                        source=run["pricing_source"],
                    )
                )
            else:
                st.caption(
                    t(
                        "ui.streamlit_app.cost_unknown",
                        "Cost unknown: no verified pricing rates configured.",
                    )
                )
            st.caption(
                t(
                    "ui.streamlit_app.prompt_meta",
                    "Prompt {prompt} · schema {schema} · {elapsed} ms · tokens {input}/{output}",
                    prompt=run["prompt_version"],
                    schema=run["schema_version"],
                    elapsed=run["elapsed_ms"],
                    input=run.get("input_tokens"),
                    output=run.get("output_tokens"),
                )
            )


def _review(st, base_url: str, assessment: dict):
    aid = assessment["id"]
    heading(st, t("ui.streamlit_app.fde_review", "FDE review"), 3)
    st.warning(
        t(
            "ui.streamlit_app.review_warning",
            "Local demo review only. A typed reviewer name is NOT authenticated identity. "
            "No rollout approval.",
        )
    )
    if not assessment.get("analysis"):
        st.info(
            t(
                "ui.streamlit_app.review_needs_analysis",
                "Run analysis before submitting an FDE review.",
            )
        )
        return
    recs = {t_["recommendation"]["category"] for t_ in assessment["analysis"]["tasks"]}
    with st.form(f"review-{aid}"):
        reviewer = st.text_input(LABEL_REVIEWER_LABEL)
        category = st.selectbox(
            LABEL_DECISION_CATEGORY,
            RECOMMENDATION_CATEGORIES,
            index=RECOMMENDATION_CATEGORIES.index(sorted(recs)[0]) if recs else 0,
        )
        rationale = st.text_area(LABEL_REVIEW_RATIONALE)
        override = st.text_area(LABEL_OVERRIDE_RATIONALE)
        if st.form_submit_button(LABEL_RECORD_REVIEW, disabled=assessment["status"] == "finalized"):
            _change(
                st,
                base_url,
                "POST",
                f"/api/v1/assessments/{aid}/fde-review",
                {
                    "reviewer_id": reviewer,
                    "selected_category": category,
                    "rationale": rationale,
                    "override_reason": override or None,
                },
            )
    st.json(assessment.get("reviews", []))
    token = st.text_input(LABEL_REVIEWER_TOKEN, type="password")
    if assessment["status"] == "finalized":
        if st.button(LABEL_REOPEN, disabled=not token):
            _change(st, base_url, "POST", f"/api/v1/assessments/{aid}/reopen", token=token)
    elif st.button(LABEL_FINALIZE, disabled=not token or not assessment.get("reviews")):
        _change(st, base_url, "POST", f"/api/v1/assessments/{aid}/final-decision", token=token)
    with st.expander(t("ui.streamlit_app.audit_history", "Assessment audit history")):
        history = api_request(base_url, "GET", f"/api/v1/assessments/{aid}/audit")
        st.json(history)


def main() -> None:
    import streamlit as st

    st.set_page_config(page_title="AI Opportunity Advisor", page_icon="🧭", layout="wide")
    apply_accessibility(st)
    translator = activate(st)
    report_catalogue(st, translator)
    st.title("AI Opportunity Advisor")
    st.caption(
        t(
            "ui.streamlit_app.subtitle",
            "Phase 8 · Opportunity portfolios, value scenarios and measurable PoC outcomes",
        )
    )
    st.info(
        t("ui.streamlit_app.principle", "AI assists. Rules enforce. Data informs. Humans decide.")
    )
    st.warning(
        t(
            "ui.streamlit_app.pilot_warning",
            "Pilot code only — no production approval or verified SSO browser session. "
            "Use only synthetic, non-sensitive data.",
        )
    )
    base_url = os.environ.get("API_BASE_URL", DEFAULT_API_BASE_URL)
    st.sidebar.text_input(LABEL_API_CREDENTIAL, type="password", key="api_bearer")
    _API_BEARER.set(st.session_state.get("api_bearer") or None)
    st.sidebar.text_input(LABEL_ORGANIZATION_ID, key="api_org")
    _API_ORGANIZATION.set(st.session_state.get("api_org") or None)
    try:
        health = fetch_api_health(base_url)
        st.success(
            t(
                "ui.streamlit_app.api_connected",
                "API connected · {service} · {version} · {mode}",
                service=health["service"],
                version=health["version"],
                mode=health["mode"],
            )
        )
        _ASSESSMENT_REVISION.set(None)
        from ui.collaboration import render_workspace
        from ui.portfolio import render_assessment_portfolio, render_portfolio_dashboard

        pending_view = st.session_state.pop("next_view", None)
        if pending_view in {"Assessments", "Workspace", "Portfolio"}:
            st.session_state["primary_view"] = pending_view
        view = st.sidebar.radio(
            LABEL_NAVIGATION, ["Assessments", "Workspace", "Portfolio"], key="primary_view"
        )
        if view == "Workspace":
            render_workspace(st, base_url, level=2)
            return
        if view == "Portfolio":
            render_portfolio_dashboard(st, base_url, level=2)
            return
        assessment = _dashboard(st, base_url)
        if assessment:
            _ASSESSMENT_REVISION.set(assessment["revision"])
            st.caption(
                t(
                    "ui.streamlit_app.status_line",
                    "Status: {status} · Revision: {revision} · ID: {id}",
                    status=assessment["status"],
                    revision=assessment["revision"],
                    id=assessment["id"],
                )
            )
            from ui.collaboration import render_collaboration, render_workspace

            (
                intake,
                discovery,
                workflow,
                analysis,
                advisor,
                review,
                report,
                collaboration,
                workspace,
                portfolio,
            ) = st.tabs(
                [
                    "1 · Intake",
                    "2 · Discovery",
                    "3 · Workflow",
                    "4 · Analysis",
                    "5 · AI advisor",
                    "6 · FDE review",
                    "7 · Reports",
                    "8 · Collaboration",
                    "9 · Workspace",
                    "10 · Portfolio",
                ]
            )
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
                render_report_tab(st, base_url, assessment, level=3)
            with collaboration:
                render_collaboration(st, base_url, assessment, level=3)
            with workspace:
                render_workspace(st, base_url, level=3)
            with portfolio:
                render_assessment_portfolio(st, base_url, assessment, level=3)
        with st.expander(
            t("ui.streamlit_app.preview_legacy", "Stateless rules-only preview (legacy Phase 1)")
        ):
            raw = st.text_area(
                LABEL_PREVIEW_JSON, value=json.dumps(DEMO_PAYLOAD, indent=2), height=220
            )
            if st.button(LABEL_RUN_PREVIEW):
                try:
                    result = preview_assessment(
                        base_url,
                        _json_object(raw, t("ui.streamlit_app.preview_input", "Preview input")),
                    )
                except (HTTPError, URLError, OSError, ValueError, json.JSONDecodeError) as exc:
                    st.error(_api_error(exc))
                else:
                    _show_recommendations(st, result)
    except (HTTPError, URLError, OSError, ValueError, json.JSONDecodeError) as exc:
        st.error(
            t(
                "ui.streamlit_app.api_failed",
                "API request failed. Start FastAPI and apply migrations first. {error}",
                error=_api_error(exc),
            )
        )


if __name__ == "__main__":
    main()
