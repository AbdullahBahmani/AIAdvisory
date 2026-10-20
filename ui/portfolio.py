"""Phase 8 Streamlit portfolio and outcome screens, always through authorized API."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from urllib.error import HTTPError, URLError


def _request(st, base: str, method: str, path: str, body=None):
    from ui.streamlit_app import _api_error, api_request

    try:
        return api_request(base, method, path, body)
    except (HTTPError, URLError, OSError, ValueError) as exc:
        st.error(_api_error(exc))
        return None


def _decimal(text: str, label: str) -> str:
    try:
        value = Decimal(text.strip())
    except (InvalidOperation, ValueError):
        raise ValueError(f"{label} requires an observed or explicitly assumed number") from None
    if not value.is_finite() or value < 0:
        raise ValueError(f"{label} must be a finite, non-negative value")
    return str(value)


def render_portfolio_dashboard(st, base: str) -> None:
    st.subheader("AI opportunity portfolio")
    st.caption(
        "Planning scores are provisional, not validated business value or AI rollout approval."
    )
    result = _request(st, base, "GET", "/api/v1/portfolio/opportunities")
    if result is None:
        return
    rows = result["items"]
    first, second, third = st.columns(3)
    first.metric("Opportunities", len(rows))
    second.metric(
        "Provisionally ranked", sum(1 for r in rows if r["priority"]["status"] == "provisional")
    )
    third.metric("Need evidence", sum(1 for r in rows if r["priority"]["status"] == "unranked"))
    if not rows:
        st.info("No assessments in the current organization. Create one first.")
        return
    st.caption(f"Prioritization policy: {result['policy_version']} · No cross-currency aggregation")
    data = []
    for entry in rows:
        forecast = entry.get("latest_forecast")
        data.append(
            {
                "Assessment": entry["title"],
                "Status": entry["status"],
                "Planning priority": entry["priority"]["priority_score"],
                "Evidence verified": entry["verified_evidence_count"],
                "PoCs measured": entry["poc_outcomes"]["measured"],
                "KPI targets met*": entry["poc_outcomes"]["targets_met"],
                "Illustrative net per period": (
                    f"{forecast['net_savings']} {forecast['currency']}/{forecast['period']}"
                    if forecast
                    else "Unknown"
                ),
                "Missing": ", ".join(entry["priority"]["missing"]) or "—",
            }
        )
    st.dataframe(data, hide_index=True, use_container_width=True)
    st.caption("* Targets reflect self-reported observations and are not validated causal impacts.")
    by_id = {r["assessment_id"]: r for r in rows}
    chosen = st.selectbox(
        "Open an opportunity", options=list(by_id), format_func=lambda k: by_id[k]["title"]
    )
    if st.button("Open assessment", key="phase8_portfolio_open"):
        st.session_state["assessment_id"] = chosen
        st.session_state["next_view"] = "Assessments"
        st.rerun()


def render_assessment_portfolio(st, base: str, assessment: dict) -> None:
    aid = assessment["id"]
    root = f"/api/v1/assessments/{aid}"
    st.subheader("Evidence and value intelligence")
    st.caption(f"Assessment revision {assessment['revision']} · Human-owned planning evidence only")
    profile = _request(st, base, "GET", root + "/portfolio-profile")
    if profile is not None:
        st.write(
            "**Planning profile**",
            {k: profile.get(k) for k in ("strategic_alignment", "urgency", "delivery_effort")},
        )
    with st.expander("Prioritization factors"):
        with st.form(f"priority_{aid}"):
            choices = [None, 1, 2, 3, 4, 5]
            names = [
                ("strategic_alignment", "Strategic alignment"),
                ("urgency", "Business urgency"),
                ("delivery_effort", "Delivery effort (higher = harder)"),
            ]
            rating = {}
            for key, label in names:
                current = profile.get(key) if profile else None
                rating[key] = st.selectbox(
                    label,
                    options=choices,
                    index=choices.index(current) if current in choices else 0,
                    format_func=lambda v: "Unknown" if v is None else str(v),
                    key=f"{key}_{aid}",
                )
            if st.form_submit_button(
                "Save planning profile", disabled=assessment["status"] == "finalized"
            ):
                if _request(st, base, "PUT", root + "/portfolio-profile", rating):
                    st.rerun()

    st.markdown("#### Evidence registry")
    evidence = _request(st, base, "GET", root + "/evidence")
    if evidence is not None:
        for item in evidence:
            with st.container(border=True):
                state = (
                    "Stale"
                    if item["stale"]
                    else "Verified"
                    if item["verified_by"]
                    else "Unverified"
                )
                st.write(f"**{item['kind'].capitalize()}** — {state}")
                st.write(item["description"])
                st.caption(f"Source: {item['source_reference']} · recorded by {item['created_by']}")
                if (
                    not item["stale"]
                    and not item["verified_by"]
                    and assessment["status"] != "finalized"
                ):
                    if st.button("Verify evidence as reviewer", key=f"verify_{item['id']}"):
                        if _request(st, base, "POST", root + f"/evidence/{item['id']}/verify"):
                            st.rerun()
    with st.expander("Register evidence"):
        with st.form(f"evidence_{aid}", clear_on_submit=True):
            kind = st.selectbox("Kind", ["baseline", "technical", "business", "risk", "other"])
            description = st.text_area("What does the evidence establish?", max_chars=1000)
            reference = st.text_input("Source reference (no sensitive data)", max_chars=300)
            if st.form_submit_button("Save evidence", disabled=assessment["status"] == "finalized"):
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

    st.markdown("#### ROI scenario simulator")
    st.caption(
        "Forecasts use only entered assumptions. "
        "All amounts share one currency and period; no FX conversion."
    )
    scenarios = _request(st, base, "GET", root + "/value-scenarios")
    if scenarios is not None:
        for scenario in scenarios:
            with st.container(border=True):
                st.write(f"**{scenario['name']}** {'(stale)' if scenario['stale'] else ''}")
                st.write(
                    f"Gross: {scenario['gross_savings']} {scenario['currency']} / "
                    f"{scenario['period']} · Net: {scenario['net_savings']} "
                    f"{scenario['currency']} / {scenario['period']}"
                )
                st.caption(
                    f"Simple payback: {scenario['payback_periods'] or 'Not established'} periods · "
                    f"Source: {scenario['assumption_source']} · illustrative only"
                )
    with st.expander("Assumption sensitivity (advisory)"):
        st.caption(
            "How wrong the entered assumptions would have to be before the modelled net "
            "savings turn negative. Default ranges are a documented modelling convention, "
            "not market data. This is a forecast, not measured ROI."
        )
        for scenario in scenarios or []:
            envelope = _request(
                st, base, "GET", f"{root}/value-scenarios/{scenario['id']}/sensitivity"
            )
            if envelope is None:
                continue
            with st.container(border=True):
                st.write(f"**{scenario['name']}** · {envelope['currency']} / {envelope['period']}")
                rows = []
                for dimension in envelope["dimensions"]:
                    band = dimension.get("band")
                    net_range = dimension.get("net_savings_range")
                    breakeven = dimension["breakeven"]
                    if breakeven["status"] == "available":
                        breakeven_text = (
                            f"{breakeven['value']} "
                            f"({'exact' if breakeven['value_is_exact'] else 'rounded rendering'})"
                        )
                    else:
                        breakeven_text = f"Unavailable ({breakeven.get('reason')})"
                    rows.append(
                        {
                            "Assumption": dimension["assumption"],
                            "Entered value": (
                                dimension["base_value"]
                                if dimension["base_value"] is not None
                                else "Unknown"
                            ),
                            "Analysed range": (
                                f"{band['low']} – {band['high']}" if band else "Unavailable"
                            ),
                            "Net savings range": (
                                f"{net_range['low']} – {net_range['high']}"
                                if net_range
                                else "Unavailable"
                            ),
                            "Breakeven (net = 0)": breakeven_text,
                        }
                    )
                st.dataframe(rows, hide_index=True, use_container_width=True)
                st.caption(envelope["disclaimer"])
    with st.expander("Compare scenarios (one currency and period)"):
        st.caption(
            "Scenarios are compared side by side only when they share a currency and a "
            "period. A mixed currency or period is refused; nothing is converted."
        )
        by_id = {scenario["id"]: scenario for scenario in scenarios or []}
        chosen = st.multiselect(
            "Scenarios to compare",
            options=list(by_id),
            format_func=lambda key: by_id[key]["name"],
            key=f"compare_{aid}",
        )
        if len(chosen) >= 2 and st.button("Compare selected scenarios", key=f"compare_go_{aid}"):
            query = "&".join(f"scenario_ids={scenario_id}" for scenario_id in chosen)
            comparison = _request(st, base, "GET", f"{root}/value-scenarios/comparison?{query}")
            if comparison is not None:
                st.dataframe(
                    [
                        {
                            "Rank": row["rank"],
                            "Scenario": row["name"],
                            "Net savings": f"{row['net_savings']} {row['currency']}",
                            "Gross savings": f"{row['gross_savings']} {row['currency']}",
                            "Payback (periods)": (
                                row["payback_periods"]
                                if row["payback_periods"] is not None
                                else "Not established"
                            ),
                            "Stale": row["stale"],
                        }
                        for row in comparison["rows"]
                    ],
                    hide_index=True,
                    use_container_width=True,
                )
                st.caption(comparison["disclaimer"])
    with st.expander("Create an illustrative scenario"):
        with st.form(f"scenario_{aid}", clear_on_submit=True):
            name = st.text_input("Scenario name", max_chars=120)
            currency = st.text_input("Currency ISO code (e.g. NOK)", max_chars=3)
            period = st.selectbox("Period", ["month", "quarter", "year"])
            fields = (
                ("cases_per_period", "Cases per period"),
                ("baseline_minutes", "Baseline minutes/case"),
                ("target_minutes", "Target minutes/case"),
                ("hourly_cost", "Loaded hourly cost"),
                ("recurring_cost", "Recurring costs per period"),
                ("initial_cost", "One-time implementation cost"),
            )
            entered = {
                key: st.text_input(label, key=f"scenario_{aid}_{key}") for key, label in fields
            }
            source = st.text_input("Basis for these assumptions", max_chars=300)
            if st.form_submit_button("Save scenario", disabled=assessment["status"] == "finalized"):
                try:
                    body = {key: _decimal(entered[key], label) for key, label in fields}
                    body.update(
                        {
                            "name": name,
                            "currency": currency.strip().upper(),
                            "period": period,
                            "assumption_source": source,
                        }
                    )
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    if _request(st, base, "POST", root + "/value-scenarios", body):
                        st.rerun()

    st.markdown("#### PoC outcomes")
    st.caption("Tracking marks progress in this app only; it never starts external workflows.")
    pocs = _request(st, base, "GET", root + "/pocs")
    if pocs is not None:
        for plan in pocs:
            with st.container(border=True):
                st.write(
                    f"**{plan['kpi_name']}** — {plan['status']}"
                    + (" (stale)" if plan["stale"] else "")
                )
                st.caption(
                    f"Baseline {plan['baseline']} → target {plan['target']} {plan['unit']} "
                    f"({plan['direction']} is better)"
                )
                st.write(plan["success_criteria"])
                for ob in plan["observations"]:
                    st.write(
                        f"Measured {ob['actual']} {plan['unit']} — "
                        f"{'target met' if ob['target_met'] else 'target not met'}"
                    )
                    st.caption(f"Source: {ob['source_reference']} · Recorded by {ob['created_by']}")
                if plan["stale"]:
                    st.warning("Assessment inputs changed; create a new current-revision PoC.")
                elif plan["status"] == "planned":
                    if st.button("Start tracking (reviewer)", key=f"poc_start_{plan['id']}"):
                        if _request(st, base, "POST", root + f"/pocs/{plan['id']}/start"):
                            st.rerun()
                else:
                    with st.form(f"poc_measure_{plan['id']}", clear_on_submit=True):
                        actual = st.text_input("Measured actual", key=f"poc_actual_{plan['id']}")
                        reference = st.text_input(
                            "Measurement source", key=f"poc_source_{plan['id']}"
                        )
                        if st.form_submit_button("Record observed outcome (reviewer)"):
                            try:
                                body = {
                                    "actual": _decimal(actual, "Measured actual"),
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
    with st.expander("Plan a PoC metric"):
        with st.form(f"poc_plan_{aid}", clear_on_submit=True):
            metric = st.text_input("KPI name", max_chars=160)
            unit = st.text_input("Unit", max_chars=60)
            direction = st.selectbox("Better result", ["lower", "higher"])
            baseline = st.text_input("Observed baseline")
            target = st.text_input("Success threshold")
            criteria = st.text_area("Evaluation criteria", max_chars=1000)
            if st.form_submit_button("Save PoC plan", disabled=assessment["status"] == "finalized"):
                try:
                    body = {
                        "kpi_name": metric,
                        "unit": unit,
                        "direction": direction,
                        "baseline": _decimal(baseline, "Baseline"),
                        "target": _decimal(target, "Target"),
                        "success_criteria": criteria,
                    }
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    if _request(st, base, "POST", root + "/pocs", body):
                        st.rerun()
