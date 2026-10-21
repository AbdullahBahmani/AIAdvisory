"""Phase 8 Streamlit portfolio and outcome screens, always through authorized API.

Every user-facing string is a catalogue entry and every amount is formatted
with the conventions of the active locale.  Amounts arrive as the exact
``str(Decimal)`` of the API and are formatted as :class:`~decimal.Decimal`,
never as a float.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from urllib.error import HTTPError, URLError

from app.localization import current, t
from ui.accessibility import heading

# --- widget labels ------------------------------------------------------------

LABEL_PRIORITY_PROFILE = "Prioritization factors"
LABEL_STRATEGIC_ALIGNMENT = "Strategic alignment"
LABEL_BUSINESS_URGENCY = "Business urgency"
LABEL_DELIVERY_EFFORT = "Delivery effort (higher = harder)"
LABEL_SAVE_PLANNING_PROFILE = "Save planning profile"
LABEL_EVIDENCE_KIND = "Kind"
LABEL_EVIDENCE_DESCRIPTION = "What does the evidence establish?"
LABEL_SOURCE_REFERENCE = "Source reference (no sensitive data)"
LABEL_SAVE_EVIDENCE = "Save evidence"
LABEL_VERIFY_EVIDENCE = "Verify evidence as reviewer"
LABEL_ATTACHMENT = "Attachment"
LABEL_FETCH_ATTACHMENT = "Fetch attachment"
LABEL_SAVE_ATTACHMENT = "Save attachment"
LABEL_DELETE_ATTACHMENT = "Delete attachment"
LABEL_EVIDENCE_FILE = "Evidence file"
LABEL_UPLOAD_ATTACHMENT = "Upload attachment"
LABEL_SCENARIO_NAME = "Scenario name"
LABEL_CURRENCY_CODE = "Currency ISO code (e.g. NOK)"
LABEL_PERIOD = "Period"
LABEL_CASES_PER_PERIOD = "Cases per period"
LABEL_BASELINE_MINUTES = "Baseline minutes/case"
LABEL_TARGET_MINUTES = "Target minutes/case"
LABEL_HOURLY_COST = "Loaded hourly cost"
LABEL_RECURRING_COST = "Recurring costs per period"
LABEL_INITIAL_COST = "One-time implementation cost"
LABEL_ASSUMPTION_SOURCE = "Basis for these assumptions"
LABEL_HORIZON_PERIODS = "Horizon for the financial model (periods)"
LABEL_DISCOUNT_RATE = "Annual discount rate (e.g. 0.05)"
LABEL_DISCOUNT_RATE_SOURCE = "Where the discount rate comes from"
LABEL_SOURCE_SYSTEM = "Source system"
LABEL_METRIC_KEY = "Metric"
LABEL_METRIC_VALUE = "Value"
LABEL_METRIC_UNIT = "Unit"
LABEL_METRIC_OBSERVED_AT = "Observed at (ISO-8601, with timezone)"
LABEL_METRIC_REFERENCE = "Where the measurement came from"
LABEL_INGEST_METRICS = "Ingest measurement"
LABEL_SAVE_SCENARIO = "Save scenario"
LABEL_KPI_NAME = "KPI name"
LABEL_UNIT = "Unit"
LABEL_BETTER_RESULT = "Better result"
LABEL_OBSERVED_BASELINE = "Observed baseline"
LABEL_SUCCESS_THRESHOLD = "Success threshold"
LABEL_EVALUATION_CRITERIA = "Evaluation criteria"
LABEL_SAVE_POC_PLAN = "Save PoC plan"
LABEL_MEASURED_ACTUAL = "Measured actual"
LABEL_MEASUREMENT_SOURCE = "Measurement source"
LABEL_RECORD_OBSERVATION = "Record observed outcome (reviewer)"
LABEL_START_TRACKING = "Start tracking (reviewer)"
LABEL_COMPARE_SCENARIOS = "Compare scenarios (one currency and period)"
LABEL_SCENARIOS_TO_COMPARE = "Scenarios to compare"
LABEL_COMPARE_SELECTED = "Compare selected scenarios"
LABEL_OPEN_OPPORTUNITY = "Open an opportunity"
LABEL_OPEN_ASSESSMENT = "Open assessment"

#: The label of every scenario field, used in the validation messages.
SCENARIO_FIELD_LABELS = {
    "cases_per_period": LABEL_CASES_PER_PERIOD,
    "baseline_minutes": LABEL_BASELINE_MINUTES,
    "target_minutes": LABEL_TARGET_MINUTES,
    "hourly_cost": LABEL_HOURLY_COST,
    "recurring_cost": LABEL_RECURRING_COST,
    "initial_cost": LABEL_INITIAL_COST,
}


def _render_observed_metrics(st, base: str, root: str, aid: str, assessment: dict) -> None:
    """Show the ingested measurements and offer to ingest one more."""
    from app.localization import t

    observed = _request(st, base, "GET", root + "/observed-metrics")
    if observed is not None:
        if not observed:
            st.caption(
                t(
                    "ui.portfolio.observed_metrics_empty",
                    "No measurement has been ingested for this assessment.",
                )
            )
        for metric in observed:
            st.write(f"**{metric['metric_key']}** · {_number(metric['value'])} {metric['unit']}")
            st.caption(
                t(
                    "ui.portfolio.observed_metric_meta",
                    "{system} · observed at {observed_at} · {reference} · bound to revision "
                    "{revision}",
                    system=metric["source_system"],
                    observed_at=metric["observed_at"],
                    reference=metric["source_reference"],
                    revision=metric["assessment_revision"],
                )
                + (" · STALE" if metric["stale"] else "")
            )
    with st.expander(t("ui.portfolio.ingest_metric", "Ingest a measurement")):
        with st.form(f"observed_metric_{aid}", clear_on_submit=True):
            source_system = st.text_input(LABEL_SOURCE_SYSTEM, max_chars=120)
            metric_key = st.text_input(LABEL_METRIC_KEY, max_chars=120)
            value = st.text_input(LABEL_METRIC_VALUE, max_chars=40)
            unit = st.text_input(LABEL_METRIC_UNIT, max_chars=60)
            observed_at = st.text_input(LABEL_METRIC_OBSERVED_AT, max_chars=40)
            reference = st.text_input(LABEL_METRIC_REFERENCE, max_chars=300)
            if st.form_submit_button(
                LABEL_INGEST_METRICS, disabled=assessment["status"] == "finalized"
            ):
                try:
                    amount = _decimal(value.strip(), LABEL_METRIC_VALUE)
                    body = {
                        "metrics": [
                            {
                                "source_system": source_system.strip(),
                                "metric_key": metric_key.strip(),
                                "value": amount,
                                "unit": unit.strip(),
                                "observed_at": observed_at.strip(),
                                "source_reference": reference.strip(),
                            }
                        ]
                    }
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    if _request(st, base, "POST", root + "/observed-metrics", body):
                        st.rerun()


def _show_financial_model(st, scenario: dict) -> None:
    """Show the advisory financial model of a value scenario.

    An unavailable model is shown with its reason.  It is never shown as
    zero, and a number that could not be computed is never hidden.
    """
    from app.localization import t

    model = scenario.get("financial_model")
    if not model:
        return
    if model.get("status") != "available":
        st.caption(
            t(
                "ui.portfolio.financial_model_unavailable",
                "Net present value: not available · {reason}",
                reason=model.get("reason") or t("ui.portfolio.not_established", "Not established"),
            )
        )
        return
    currency = scenario.get("currency", "")
    parts = [
        t(
            "ui.portfolio.financial_model_npv",
            "Net present value: {npv}",
            npv=_money(model.get("net_present_value"), currency),
        )
    ]
    if model.get("internal_rate_of_return") is not None:
        parts.append(
            t(
                "ui.portfolio.financial_model_irr",
                "Internal rate of return: {irr}",
                irr=_percent(model.get("internal_rate_of_return")),
            )
        )
    elif model.get("internal_rate_of_return_reason"):
        parts.append(
            t(
                "ui.portfolio.financial_model_irr_unavailable",
                "Internal rate of return: not available · {reason}",
                reason=model["internal_rate_of_return_reason"],
            )
        )
    if model.get("discounted_payback_periods") is not None:
        parts.append(
            t(
                "ui.portfolio.financial_model_payback",
                "Discounted payback: {payback} periods",
                payback=_number(model.get("discounted_payback_periods")),
            )
        )
    elif model.get("discounted_payback_reason"):
        parts.append(
            t(
                "ui.portfolio.financial_model_payback_unavailable",
                "Discounted payback: not available · {reason}",
                reason=model["discounted_payback_reason"],
            )
        )
    st.caption(" · ".join(parts))
    # The disclaimer is a contract statement produced by the service, so it is
    # reproduced exactly as recorded rather than translated.
    disclaimer = model.get("disclaimer")
    if disclaimer:
        st.caption(disclaimer)


def _request(st, base: str, method: str, path: str, body=None):
    from ui.streamlit_app import _api_error, api_request

    try:
        return api_request(base, method, path, body)
    except (HTTPError, URLError, OSError, ValueError) as exc:
        st.error(_api_error(exc))
        return None


def _raw_request(
    base: str,
    method: str,
    path: str,
    *,
    data: bytes | None = None,
    extra_headers: dict[str, str] | None = None,
    timeout: float = 30.0,
) -> bytes:
    """Binary API call. The UI never reads the attachment store directly."""
    from urllib.request import Request, urlopen

    from ui.streamlit_app import _API_BEARER, _API_ORGANIZATION, _ASSESSMENT_REVISION

    headers = {"Accept": "*/*"}
    bearer = _API_BEARER.get()
    if bearer:
        headers["Authorization"] = "Bearer " + bearer
    tenant = _API_ORGANIZATION.get()
    if tenant:
        headers["X-Organization-ID"] = tenant
    revision = _ASSESSMENT_REVISION.get()
    if revision is not None and method in {"POST", "PUT", "PATCH", "DELETE"}:
        headers["If-Match"] = f'"{revision}"'
    headers.update(extra_headers or {})
    request = Request(f"{base.rstrip('/')}{path}", data=data, headers=headers, method=method)
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - configured local API
        return response.read()


MEDIA_TYPES_BY_SUFFIX = {
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".json": "application/json",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".html": "text/html",
    ".htm": "text/html",
    ".svg": "image/svg+xml",
}


def _upload_attachment(st, base: str, assessment_id: str, uploaded) -> bool:
    """Upload one file through the API; the API owns storage and validation."""
    from urllib.parse import quote

    from ui.streamlit_app import _api_error

    media_type = getattr(uploaded, "type", None) or ""
    if not media_type or media_type == "application/octet-stream":
        suffix = "." + uploaded.name.rsplit(".", 1)[-1].lower() if "." in uploaded.name else ""
        media_type = MEDIA_TYPES_BY_SUFFIX.get(suffix, "application/octet-stream")
    try:
        payload = uploaded.getvalue() if hasattr(uploaded, "getvalue") else uploaded.read()
    except (OSError, ValueError) as exc:
        st.error(_api_error(exc))
        return False
    headers = {
        "Content-Type": media_type,
        "X-Attachment-Filename": quote(uploaded.name, safe=""),
        "X-Attachment-Content-Type": media_type,
    }
    try:
        _raw_request(
            base,
            "POST",
            f"/api/v1/assessments/{assessment_id}/evidence-attachments",
            data=payload,
            extra_headers=headers,
        )
    except (HTTPError, URLError, OSError, ValueError) as exc:
        st.error(_api_error(exc))
        return False
    return True


def _download_attachment(st, base: str, assessment_id: str, attachment_id: str) -> bytes | None:
    from ui.streamlit_app import _api_error

    try:
        return _raw_request(
            base,
            "GET",
            f"/api/v1/assessments/{assessment_id}/evidence-attachments/{attachment_id}/download",
        )
    except (HTTPError, URLError, OSError, ValueError) as exc:
        st.error(_api_error(exc))
        return None


def _delete_attachment(st, base: str, assessment_id: str, attachment_id: str) -> bool:
    from ui.streamlit_app import _api_error

    try:
        _raw_request(
            base,
            "DELETE",
            f"/api/v1/assessments/{assessment_id}/evidence-attachments/{attachment_id}",
        )
    except (HTTPError, URLError, OSError, ValueError) as exc:
        st.error(_api_error(exc))
        return False
    return True


def _decimal(text: str, label: str) -> str:
    try:
        value = Decimal(text.strip())
    except (InvalidOperation, ValueError):
        raise ValueError(
            t(
                "ui.portfolio.decimal_required",
                "{label} requires an observed or explicitly assumed number",
                label=label,
            )
        ) from None
    if not value.is_finite() or value < 0:
        raise ValueError(
            t(
                "ui.portfolio.decimal_nonnegative",
                "{label} must be a finite, non-negative value",
                label=label,
            )
        )
    return str(value)


def _money(value: object, currency: str) -> str:
    """Format an exact amount with the conventions of the active locale."""
    return current().currency(Decimal(str(value)), currency)


def _number(value: object) -> str:
    """Format a plain number with the conventions of the active locale."""
    return current().number(value)


def _percent(value: object) -> str:
    """Format a ratio (0.05) as a percentage of the active locale."""
    return current().percent(value)


def render_portfolio_dashboard(st, base: str, level: int = 2) -> None:
    heading(st, t("ui.portfolio.dashboard", "AI opportunity portfolio"), level)
    st.caption(
        t(
            "ui.portfolio.dashboard_caption",
            "Planning scores are provisional, not validated business value or AI rollout approval.",
        )
    )
    result = _request(st, base, "GET", "/api/v1/portfolio/opportunities")
    if result is None:
        return
    rows = result["items"]
    first, second, third = st.columns(3)
    first.metric(t("ui.portfolio.metric_opportunities", "Opportunities"), len(rows))
    second.metric(
        t("ui.portfolio.metric_provisional", "Provisionally ranked"),
        sum(1 for r in rows if r["priority"]["status"] == "provisional"),
    )
    third.metric(
        t("ui.portfolio.metric_need_evidence", "Need evidence"),
        sum(1 for r in rows if r["priority"]["status"] == "unranked"),
    )
    if not rows:
        st.info(
            t(
                "ui.portfolio.no_assessments",
                "No assessments in the current organization. Create one first.",
            )
        )
        return
    st.caption(
        t(
            "ui.portfolio.priority_policy",
            "Prioritization policy: {policy} · No cross-currency aggregation",
            policy=result["policy_version"],
        )
    )
    data = []
    for entry in rows:
        forecast = entry.get("latest_forecast")
        data.append(
            {
                t("ui.portfolio.column_assessment", "Assessment"): entry["title"],
                t("ui.portfolio.column_status", "Status"): entry["status"],
                t("ui.portfolio.column_priority", "Planning priority"): _number(
                    entry["priority"]["priority_score"]
                ),
                t("ui.portfolio.column_evidence", "Evidence verified"): _number(
                    entry["verified_evidence_count"]
                ),
                t("ui.portfolio.column_pocs", "PoCs measured"): _number(
                    entry["poc_outcomes"]["measured"]
                ),
                t("ui.portfolio.column_targets", "KPI targets met*"): _number(
                    entry["poc_outcomes"]["targets_met"]
                ),
                t("ui.portfolio.column_net", "Illustrative net per period"): (
                    f"{_money(forecast['net_savings'], forecast['currency'])}/"
                    f"{_period_label(forecast['period'])}"
                    if forecast
                    else t("ui.portfolio.unknown", "Unknown")
                ),
                t("ui.portfolio.column_missing", "Missing"): ", ".join(entry["priority"]["missing"])
                or "—",
            }
        )
    st.caption(
        t(
            "ui.portfolio.table_caption",
            "Table: planning priority, evidence coverage and PoC outcomes per opportunity. "
            "Planning priority is a provisional heuristic, not a validated ranking.",
        )
    )
    st.dataframe(data, hide_index=True, width="stretch")
    st.caption(
        t(
            "ui.portfolio.table_footnote",
            "* Targets reflect self-reported observations and are not validated causal impacts.",
        )
    )
    by_id = {r["assessment_id"]: r for r in rows}
    chosen = st.selectbox(
        LABEL_OPEN_OPPORTUNITY, options=list(by_id), format_func=lambda k: by_id[k]["title"]
    )
    if st.button(LABEL_OPEN_ASSESSMENT, key="phase8_portfolio_open"):
        st.session_state["assessment_id"] = chosen
        st.session_state["next_view"] = "Assessments"
        st.rerun()


#: The period *values* are contract values; only their display label is
#: translated, and the label of an unknown period is the value itself.
LABEL_PERIOD_MONTH = "month"
LABEL_PERIOD_QUARTER = "quarter"
LABEL_PERIOD_YEAR = "year"

PERIOD_LABELS = {
    "month": LABEL_PERIOD_MONTH,
    "quarter": LABEL_PERIOD_QUARTER,
    "year": LABEL_PERIOD_YEAR,
}


def _period_label(period: object) -> str:
    """Translate a period *label*; the period value itself is never changed."""
    label = PERIOD_LABELS.get(str(period))
    return label if label is not None else str(period)


def render_assessment_portfolio(st, base: str, assessment: dict, level: int = 3) -> None:
    aid = assessment["id"]
    root = f"/api/v1/assessments/{aid}"
    heading(st, t("ui.portfolio.evidence_intelligence", "Evidence and value intelligence"), level)
    st.caption(
        t(
            "ui.portfolio.evidence_caption",
            "Assessment revision {revision} · Human-owned planning evidence only",
            revision=assessment["revision"],
        )
    )
    profile = _request(st, base, "GET", root + "/portfolio-profile")
    if profile is not None:
        st.write(
            t("ui.portfolio.planning_profile", "**Planning profile**"),
            {k: profile.get(k) for k in ("strategic_alignment", "urgency", "delivery_effort")},
        )
    with st.expander(LABEL_PRIORITY_PROFILE):
        with st.form(f"priority_{aid}"):
            choices = [None, 1, 2, 3, 4, 5]
            rating = {
                "strategic_alignment": st.selectbox(
                    LABEL_STRATEGIC_ALIGNMENT,
                    options=choices,
                    index=(
                        choices.index(profile.get("strategic_alignment"))
                        if profile and profile.get("strategic_alignment") in choices
                        else 0
                    ),
                    format_func=lambda v: (
                        t("ui.portfolio.unknown", "Unknown") if v is None else _number(v)
                    ),
                    key=f"strategic_alignment_{aid}",
                ),
                "urgency": st.selectbox(
                    LABEL_BUSINESS_URGENCY,
                    options=choices,
                    index=(
                        choices.index(profile.get("urgency"))
                        if profile and profile.get("urgency") in choices
                        else 0
                    ),
                    format_func=lambda v: (
                        t("ui.portfolio.unknown", "Unknown") if v is None else _number(v)
                    ),
                    key=f"urgency_{aid}",
                ),
                "delivery_effort": st.selectbox(
                    LABEL_DELIVERY_EFFORT,
                    options=choices,
                    index=(
                        choices.index(profile.get("delivery_effort"))
                        if profile and profile.get("delivery_effort") in choices
                        else 0
                    ),
                    format_func=lambda v: (
                        t("ui.portfolio.unknown", "Unknown") if v is None else _number(v)
                    ),
                    key=f"delivery_effort_{aid}",
                ),
            }
            if st.form_submit_button(
                LABEL_SAVE_PLANNING_PROFILE, disabled=assessment["status"] == "finalized"
            ):
                if _request(st, base, "PUT", root + "/portfolio-profile", rating):
                    st.rerun()

    heading(st, t("ui.portfolio.evidence_registry", "Evidence registry"), level + 1)
    evidence = _request(st, base, "GET", root + "/evidence")
    if evidence is not None:
        for item in evidence:
            with st.container(border=True):
                if item["stale"]:
                    state = t("ui.portfolio.evidence_stale", "Stale")
                elif item["verified_by"]:
                    state = t("ui.portfolio.evidence_verified", "Verified")
                else:
                    state = t("ui.portfolio.evidence_unverified", "Unverified")
                st.write(f"**{item['kind'].capitalize()}** — {state}")
                st.write(item["description"])
                st.caption(
                    t(
                        "ui.portfolio.evidence_source",
                        "Source: {source} · recorded by {author} · {recorded}",
                        source=item["source_reference"],
                        author=item["created_by"],
                        recorded=current().datetime(item["created_at"]),
                    )
                )
                if (
                    not item["stale"]
                    and not item["verified_by"]
                    and assessment["status"] != "finalized"
                ):
                    if st.button(LABEL_VERIFY_EVIDENCE, key=f"verify_{item['id']}"):
                        if _request(st, base, "POST", root + f"/evidence/{item['id']}/verify"):
                            st.rerun()
    with st.expander(t("ui.portfolio.register_evidence", "Register evidence")):
        with st.form(f"evidence_{aid}", clear_on_submit=True):
            kind = st.selectbox(
                LABEL_EVIDENCE_KIND, ["baseline", "technical", "business", "risk", "other"]
            )
            description = st.text_area(LABEL_EVIDENCE_DESCRIPTION, max_chars=1000)
            reference = st.text_input(LABEL_SOURCE_REFERENCE, max_chars=300)
            if st.form_submit_button(
                LABEL_SAVE_EVIDENCE, disabled=assessment["status"] == "finalized"
            ):
                if _request(
                    st,
                    base,
                    "POST",
                    root + "/evidence",
                    {
                        "kind": kind,
                        "description": description,
                        "source_reference": reference,
                    },
                ):
                    st.rerun()

    heading(st, t("ui.portfolio.attachments", "Evidence attachments"), level + 1)
    st.caption(
        t(
            "ui.portfolio.attachments_caption",
            "Files are stored outside the application, never served inline, and are "
            "readable only inside the owning organization. Allowed: PDF, TXT, MD, "
            "CSV, JSON, PNG, JPEG, HTML and SVG.",
        )
    )
    listing = _request(st, base, "GET", root + "/evidence-attachments")
    if listing:
        by_id = {item["id"]: item for item in listing}
        chosen = st.selectbox(
            LABEL_ATTACHMENT,
            options=list(by_id),
            format_func=lambda key: by_id[key]["display_name"],
            key=f"attachment_pick_{aid}",
        )
        item = by_id[chosen]
        st.caption(
            t(
                "ui.portfolio.attachment_meta",
                "{content_type} · {size} bytes · bound to revision {revision} · "
                "uploaded by {uploader}",
                content_type=item["content_type"],
                size=_number(item["size_bytes"]),
                revision=item["assessment_revision"],
                uploader=item["uploaded_by"],
            )
            + (" · STALE" if item["stale"] else "")
        )
        st.caption(f"SHA-256 {item['sha256']}")
        if st.button(LABEL_FETCH_ATTACHMENT, key=f"attachment_fetch_{aid}"):
            payload = _download_attachment(st, base, aid, chosen)
            if payload is not None:
                st.session_state[f"attachment_bytes_{aid}"] = payload
        payload = st.session_state.get(f"attachment_bytes_{aid}")
        if payload is not None:
            st.download_button(
                LABEL_SAVE_ATTACHMENT,
                data=payload,
                file_name=item.get("download_name") or item["display_name"],
                key=f"attachment_save_{aid}",
            )
        if st.button(LABEL_DELETE_ATTACHMENT, key=f"attachment_delete_{aid}"):
            if _delete_attachment(st, base, aid, chosen):
                st.session_state.pop(f"attachment_bytes_{aid}", None)
                st.rerun()
    with st.expander(t("ui.portfolio.attach_file", "Attach a file")):
        uploaded = st.file_uploader(
            LABEL_EVIDENCE_FILE,
            type=["pdf", "txt", "md", "csv", "json", "png", "jpg", "jpeg", "html", "svg"],
            key=f"attachment_upload_{aid}",
        )
        if uploaded is not None and st.button(
            LABEL_UPLOAD_ATTACHMENT, key=f"attachment_send_{aid}"
        ):
            if _upload_attachment(st, base, aid, uploaded):
                st.rerun()

    heading(
        st,
        t(
            "ui.portfolio.observed_metrics",
            "Observed metrics from production systems",
        ),
        level + 1,
    )
    st.caption(
        t(
            "ui.portfolio.observed_metrics_caption",
            "A measurement is evidence about a quantity, not a conclusion. Ingesting one "
            "changes no score, no rule hit and no recommendation; a scenario cites a "
            "measurement, and the citation is checked against it.",
        )
    )
    _render_observed_metrics(st, base, root, aid, assessment)

    heading(st, t("ui.portfolio.roi_simulator", "ROI scenario simulator"), level + 1)
    st.caption(
        t(
            "ui.portfolio.roi_caption",
            "Forecasts use only entered assumptions. "
            "All amounts share one currency and period; no FX conversion.",
        )
    )
    scenarios = _request(st, base, "GET", root + "/value-scenarios")
    if scenarios is not None:
        for scenario in scenarios:
            with st.container(border=True):
                st.write(
                    f"**{scenario['name']}**"
                    + (t("ui.portfolio.stale_suffix", " (stale)") if scenario["stale"] else "")
                )
                st.write(
                    t(
                        "ui.portfolio.scenario_amounts",
                        "Gross: {gross} / {period} · Net: {net} / {period}",
                        gross=_money(scenario["gross_savings"], scenario["currency"]),
                        period=_period_label(scenario["period"]),
                        net=_money(scenario["net_savings"], scenario["currency"]),
                    )
                )
                st.caption(
                    t(
                        "ui.portfolio.scenario_payback",
                        "Simple payback: {payback} periods · Source: {source} · illustrative only",
                        payback=(
                            _number(scenario["payback_periods"])
                            if scenario["payback_periods"]
                            else t("ui.portfolio.not_established", "Not established")
                        ),
                        source=scenario["assumption_source"],
                    )
                )
                _show_financial_model(st, scenario)
                for warning in scenario.get("citation_warnings") or []:
                    st.warning(warning)
    with st.expander(t("ui.portfolio.sensitivity", "Assumption sensitivity (advisory)")):
        st.caption(
            t(
                "ui.portfolio.sensitivity_caption",
                "How wrong the entered assumptions would have to be before the modelled net "
                "savings turn negative. Default ranges are a documented modelling convention, "
                "not market data. This is a forecast, not measured ROI.",
            )
        )
        for scenario in scenarios or []:
            envelope = _request(
                st, base, "GET", f"{root}/value-scenarios/{scenario['id']}/sensitivity"
            )
            if envelope is None:
                continue
            with st.container(border=True):
                st.write(
                    f"**{scenario['name']}** · {envelope['currency']} / "
                    f"{_period_label(envelope['period'])}"
                )
                rows = []
                for dimension in envelope["dimensions"]:
                    band = dimension.get("band")
                    net_range = dimension.get("net_savings_range")
                    breakeven = dimension["breakeven"]
                    if breakeven["status"] == "available":
                        breakeven_text = (
                            f"{_money(breakeven['value'], envelope['currency'])} "
                            f"({'exact' if breakeven['value_is_exact'] else 'rounded rendering'})"
                        )
                    else:
                        breakeven_text = (
                            f"{t('ui.portfolio.unavailable', 'Unavailable')} "
                            f"({breakeven.get('reason')})"
                        )
                    rows.append(
                        {
                            t("ui.portfolio.column_assumption", "Assumption"): dimension[
                                "assumption"
                            ],
                            t("ui.portfolio.column_entered", "Entered value"): (
                                _number(dimension["base_value"])
                                if dimension["base_value"] is not None
                                else t("ui.portfolio.unknown", "Unknown")
                            ),
                            t("ui.portfolio.column_range", "Analysed range"): (
                                f"{_money(band['low'], envelope['currency'])} – "
                                f"{_money(band['high'], envelope['currency'])}"
                                if band
                                else t("ui.portfolio.unavailable", "Unavailable")
                            ),
                            t("ui.portfolio.column_net_range", "Net savings range"): (
                                f"{_money(net_range['low'], envelope['currency'])} – "
                                f"{_money(net_range['high'], envelope['currency'])}"
                                if net_range
                                else t("ui.portfolio.unavailable", "Unavailable")
                            ),
                            t(
                                "ui.portfolio.column_breakeven", "Breakeven (net = 0)"
                            ): breakeven_text,
                        }
                    )
                st.dataframe(rows, hide_index=True, width="stretch")
                st.caption(envelope["disclaimer"])
    with st.expander(LABEL_COMPARE_SCENARIOS):
        st.caption(
            t(
                "ui.portfolio.compare_caption",
                "Scenarios are compared side by side only when they share a currency and a "
                "period. A mixed currency or period is refused; nothing is converted.",
            )
        )
        by_id = {scenario["id"]: scenario for scenario in scenarios or []}
        chosen = st.multiselect(
            LABEL_SCENARIOS_TO_COMPARE,
            options=list(by_id),
            format_func=lambda key: by_id[key]["name"],
            key=f"compare_{aid}",
        )
        if len(chosen) >= 2 and st.button(LABEL_COMPARE_SELECTED, key=f"compare_go_{aid}"):
            query = "&".join(f"scenario_ids={scenario_id}" for scenario_id in chosen)
            comparison = _request(st, base, "GET", f"{root}/value-scenarios/comparison?{query}")
            if comparison is not None:
                st.dataframe(
                    [
                        {
                            t("ui.portfolio.column_rank", "Rank"): _number(row["rank"]),
                            t("ui.portfolio.column_scenario", "Scenario"): row["name"],
                            t("ui.portfolio.column_net_savings", "Net savings"): _money(
                                row["net_savings"], row["currency"]
                            ),
                            t("ui.portfolio.column_gross_savings", "Gross savings"): _money(
                                row["gross_savings"], row["currency"]
                            ),
                            t("ui.portfolio.column_payback", "Payback (periods)"): (
                                _number(row["payback_periods"])
                                if row["payback_periods"] is not None
                                else t("ui.portfolio.not_established", "Not established")
                            ),
                            t("ui.portfolio.column_stale", "Stale"): row["stale"],
                        }
                        for row in comparison["rows"]
                    ],
                    hide_index=True,
                    width="stretch",
                )
                st.caption(comparison["disclaimer"])
    with st.expander(t("ui.portfolio.create_scenario", "Create an illustrative scenario")):
        with st.form(f"scenario_{aid}", clear_on_submit=True):
            name = st.text_input(LABEL_SCENARIO_NAME, max_chars=120)
            currency = st.text_input(LABEL_CURRENCY_CODE, max_chars=3)
            period = st.selectbox(
                LABEL_PERIOD,
                ["month", "quarter", "year"],
                format_func=_period_label,
            )
            entered = {
                "cases_per_period": st.text_input(
                    LABEL_CASES_PER_PERIOD, key=f"scenario_{aid}_cases_per_period"
                ),
                "baseline_minutes": st.text_input(
                    LABEL_BASELINE_MINUTES, key=f"scenario_{aid}_baseline_minutes"
                ),
                "target_minutes": st.text_input(
                    LABEL_TARGET_MINUTES, key=f"scenario_{aid}_target_minutes"
                ),
                "hourly_cost": st.text_input(LABEL_HOURLY_COST, key=f"scenario_{aid}_hourly_cost"),
                "recurring_cost": st.text_input(
                    LABEL_RECURRING_COST, key=f"scenario_{aid}_recurring_cost"
                ),
                "initial_cost": st.text_input(
                    LABEL_INITIAL_COST, key=f"scenario_{aid}_initial_cost"
                ),
            }
            source = st.text_input(LABEL_ASSUMPTION_SOURCE, max_chars=300)
            horizon = st.number_input(
                LABEL_HORIZON_PERIODS,
                min_value=0,
                max_value=120,
                value=0,
                step=1,
                key=f"scenario_{aid}_horizon",
                help=(
                    "Optional. Leave at zero for no horizon: without one there is no net "
                    "present value and the financial model reports that it is unavailable."
                ),
            )
            discount_rate = st.text_input(
                LABEL_DISCOUNT_RATE,
                max_chars=16,
                key=f"scenario_{aid}_discount_rate",
            )
            discount_source = st.text_input(
                LABEL_DISCOUNT_RATE_SOURCE,
                max_chars=300,
                key=f"scenario_{aid}_discount_source",
            )
            if st.form_submit_button(
                LABEL_SAVE_SCENARIO, disabled=assessment["status"] == "finalized"
            ):
                try:
                    body = {
                        key: _decimal(entered[key], SCENARIO_FIELD_LABELS[key]) for key in entered
                    }
                    body.update(
                        {
                            "name": name,
                            "currency": currency.strip().upper(),
                            "period": period,
                            "assumption_source": source,
                        }
                    )
                    if horizon:
                        body["horizon_periods"] = int(horizon)
                    if discount_rate.strip():
                        rate = _decimal(discount_rate.strip(), LABEL_DISCOUNT_RATE)
                        if rate < 0 or rate > 1:
                            raise ValueError(
                                f"{LABEL_DISCOUNT_RATE} must be between 0 and 1, for example 0.05"
                            )
                        body["discount_rate_annual"] = rate
                        if not discount_source.strip():
                            raise ValueError(
                                f"{LABEL_DISCOUNT_RATE_SOURCE} is required when a rate is given"
                            )
                        body["discount_rate_source"] = discount_source.strip()
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    if _request(st, base, "POST", root + "/value-scenarios", body):
                        st.rerun()

    heading(st, t("ui.portfolio.poc_outcomes", "PoC outcomes"), level + 1)
    st.caption(
        t(
            "ui.portfolio.poc_caption",
            "Tracking marks progress in this app only; it never starts external workflows.",
        )
    )
    pocs = _request(st, base, "GET", root + "/pocs")
    if pocs is not None:
        for plan in pocs:
            with st.container(border=True):
                st.write(
                    f"**{plan['kpi_name']}** — {plan['status']}"
                    + (t("ui.portfolio.stale_suffix", " (stale)") if plan["stale"] else "")
                )
                st.caption(
                    t(
                        "ui.portfolio.poc_baseline",
                        "Baseline {baseline} → target {target} {unit} ({direction} is better)",
                        baseline=_number(plan["baseline"]),
                        target=_number(plan["target"]),
                        unit=plan["unit"],
                        direction=plan["direction"],
                    )
                )
                st.write(plan["success_criteria"])
                for ob in plan["observations"]:
                    st.write(
                        t(
                            "ui.portfolio.poc_observation",
                            "Measured {actual} {unit} — {outcome}",
                            actual=_number(ob["actual"]),
                            unit=plan["unit"],
                            outcome=(
                                t("ui.portfolio.target_met", "target met")
                                if ob["target_met"]
                                else t("ui.portfolio.target_not_met", "target not met")
                            ),
                        )
                    )
                    st.caption(
                        t(
                            "ui.portfolio.poc_observation_source",
                            "Source: {source} · Recorded by {author}",
                            source=ob["source_reference"],
                            author=ob["created_by"],
                        )
                    )
                if plan["stale"]:
                    st.warning(
                        t(
                            "ui.portfolio.poc_stale",
                            "Assessment inputs changed; create a new current-revision PoC.",
                        )
                    )
                elif plan["status"] == "planned":
                    if st.button(LABEL_START_TRACKING, key=f"poc_start_{plan['id']}"):
                        if _request(st, base, "POST", root + f"/pocs/{plan['id']}/start"):
                            st.rerun()
                else:
                    with st.form(f"poc_measure_{plan['id']}", clear_on_submit=True):
                        actual = st.text_input(
                            LABEL_MEASURED_ACTUAL, key=f"poc_actual_{plan['id']}"
                        )
                        reference = st.text_input(
                            LABEL_MEASUREMENT_SOURCE, key=f"poc_source_{plan['id']}"
                        )
                        if st.form_submit_button(LABEL_RECORD_OBSERVATION):
                            try:
                                body = {
                                    "actual": _decimal(actual, LABEL_MEASURED_ACTUAL),
                                    "source_reference": reference,
                                }
                            except ValueError as exc:
                                st.error(str(exc))
                            else:
                                if _request(
                                    st,
                                    base,
                                    "POST",
                                    root + f"/pocs/{plan['id']}/observations",
                                    body,
                                ):
                                    st.rerun()
    with st.expander(t("ui.portfolio.plan_poc", "Plan a PoC metric")):
        with st.form(f"poc_plan_{aid}", clear_on_submit=True):
            metric = st.text_input(LABEL_KPI_NAME, max_chars=160)
            unit = st.text_input(LABEL_UNIT, max_chars=60)
            direction = st.selectbox(LABEL_BETTER_RESULT, ["lower", "higher"])
            baseline = st.text_input(LABEL_OBSERVED_BASELINE)
            target = st.text_input(LABEL_SUCCESS_THRESHOLD)
            criteria = st.text_area(LABEL_EVALUATION_CRITERIA, max_chars=1000)
            if st.form_submit_button(
                LABEL_SAVE_POC_PLAN, disabled=assessment["status"] == "finalized"
            ):
                try:
                    body = {
                        "kpi_name": metric,
                        "unit": unit,
                        "direction": direction,
                        "baseline": _decimal(baseline, LABEL_OBSERVED_BASELINE),
                        "target": _decimal(target, LABEL_SUCCESS_THRESHOLD),
                        "success_criteria": criteria,
                    }
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    if _request(st, base, "POST", root + "/pocs", body):
                        st.rerun()
