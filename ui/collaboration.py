"""Phase 7 Streamlit panels; backend remains the sole source of permissions/data."""

from __future__ import annotations

from urllib.error import HTTPError, URLError

from ui.accessibility import heading


def _call(st, base_url, method, path, payload=None):
    from ui.streamlit_app import _api_error, api_request

    try:
        return api_request(base_url, method, path, payload)
    except (HTTPError, URLError, ValueError, OSError) as exc:
        st.error(_api_error(exc))
        return None


def render_collaboration(st, base_url: str, assessment: dict, level: int = 3) -> None:
    aid = assessment["id"]
    heading(st, "Team collaboration", level)
    st.caption(
        "Comments and approvals are attributed by the API. "
        "Old revisions remain historical evidence."
    )

    reviewer = _call(st, base_url, "GET", f"/api/v1/assessments/{aid}/reviewer")
    if reviewer:
        st.write(f"**Assigned reviewer:** {reviewer['reviewer_subject']}")
    else:
        st.caption("No reviewer assigned.")

    with st.expander("Assign reviewer (organization administrator)"):
        from ui.streamlit_app import api_request

        try:
            members = api_request(base_url, "GET", "/api/v1/workspace/members")
        except (HTTPError, URLError, ValueError, OSError):
            members = []
            st.caption("Reviewer assignment is available only to organization administrators.")
        reviewers = [m["subject"] for m in members if m["role"] in ("reviewer", "admin")]
        if reviewers:
            chosen = st.selectbox("Active reviewer", reviewers, key=f"reviewer_{aid}")
            if st.button("Assign", key=f"assign_{aid}"):
                result = _call(
                    st,
                    base_url,
                    "PUT",
                    f"/api/v1/assessments/{aid}/reviewer",
                    {"reviewer_subject": chosen},
                )
                if result:
                    st.rerun()

    heading(st, "Discussion", level + 1)
    comments = _call(st, base_url, "GET", f"/api/v1/assessments/{aid}/comments")
    if comments is not None:
        if not comments:
            st.caption("No discussion yet.")
        for comment in comments:
            with st.container(border=True):
                st.caption(
                    f"{comment['author']} · assessment revision {comment['assessment_revision']}"
                )
                st.write(comment["body"])
    with st.form(f"comment_{aid}", clear_on_submit=True):
        note = st.text_area("Add a comment", max_chars=2000, key=f"note_{aid}")
        if st.form_submit_button("Post comment"):
            if len(note.strip()) < 3:
                st.error("Comment must contain at least 3 characters.")
            elif _call(
                st, base_url, "POST", f"/api/v1/assessments/{aid}/comments", {"body": note.strip()}
            ):
                st.rerun()

    heading(st, "Staged review", level + 1)
    current = _call(st, base_url, "GET", f"/api/v1/assessments/{aid}/approvals")
    if current:
        st.caption(
            f"Applies only to revision {current['revision']}; AI recommendations are advisory."
        )
        stages = ("technical", "security", "business")
        columns = st.columns(len(stages))
        # `columns` is built from `stages`, so both iterables always have the same length.
        for column, stage in zip(columns, stages, strict=True):
            column.metric(
                stage.capitalize(),
                current["stages"].get(stage, "pending").replace("_", " ").title(),
            )
        for vote in current["history"]:
            st.caption(
                f"{vote['stage']} · {vote['decision']} · {vote['actor']} · {vote['rationale']}"
            )
    with st.form(f"approval_{aid}"):
        stage = st.selectbox("Review stage", ["technical", "security", "business"])
        decision = st.selectbox(
            "Decision",
            ["approved", "changes_requested"],
            format_func=lambda v: v.replace("_", " ").title(),
        )
        rationale = st.text_area("Evidence and rationale", max_chars=2000)
        if st.form_submit_button("Record signed-in reviewer decision"):
            if len(rationale.strip()) < 5:
                st.error("Provide a reason of at least 5 characters.")
            elif _call(
                st,
                base_url,
                "POST",
                f"/api/v1/assessments/{aid}/approvals",
                {"stage": stage, "decision": decision, "rationale": rationale.strip()},
            ):
                st.rerun()
    st.caption(
        "Enterprise review requires authenticated distinct people; "
        "this screen does not authorize rollout."
    )


def render_workspace(st, base_url: str, level: int = 2) -> None:
    heading(st, "Organization workspace", level)
    summary = _call(st, base_url, "GET", "/api/v1/workspace/summary")
    if summary:
        a, b, c, d = st.columns(4)
        a.metric("Assessments", summary["total"])
        b.metric("In review", summary["pending_review"])
        c.metric("Assigned to me", summary["assigned_to_me"])
        d.metric("Unread notices", summary["unread_notifications"])
        heading(st, "Portfolio by status", level + 1)
        st.caption("Table: number of assessments in the organization by status.")
        st.dataframe(
            [
                {"Status": name.replace("_", " ").title(), "Count": n}
                for name, n in summary["by_status"].items()
            ],
            width="stretch",
            hide_index=True,
        )
    inbox = _call(st, base_url, "GET", "/api/v1/workspace/inbox")
    if not inbox:
        return
    heading(st, "My assigned assessments", level + 1)
    for assignment in inbox["assigned_assessments"]:
        st.write(
            f"**{assignment['title']}** — {assignment['status']} "
            f"(revision {assignment['revision']})"
        )
    if not inbox["assigned_assessments"]:
        st.caption("No assigned assessments.")
    heading(st, "Notifications", level + 1)
    for notice in inbox["notifications"]:
        if notice["read_at"]:
            continue
        cols = st.columns([5, 1])
        cols[0].write(notice["message"])
        if cols[1].button("Read", key=f"notice_{notice['id']}"):
            if _call(st, base_url, "POST", f"/api/v1/workspace/notifications/{notice['id']}/read"):
                st.rerun()
