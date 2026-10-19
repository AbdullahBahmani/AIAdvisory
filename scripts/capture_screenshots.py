"""Capture documentation screenshots from a running local instance.

Documentation-only helper. It is NOT part of the application dependency set:
Playwright and a local Chrome/Chromium build must be provided by the operator.

Prerequisites (all local, synthetic data only):

    uv sync --extra dev --extra postgres
    uv pip install playwright                     # documentation capture only
    DATABASE_URL="sqlite:///$(pwd)/demo_capture.sqlite3" \
    ADVISORY_DEMO_MODE=1 ADVISORY_REVIEW_TOKEN="local-demo-review-token-CHANGE-ME" \
      uv run --no-sync alembic upgrade head
    DATABASE_URL="sqlite:///$(pwd)/demo_capture.sqlite3" \
    ADVISORY_DEMO_MODE=1 ADVISORY_REVIEW_TOKEN="local-demo-review-token-CHANGE-ME" \
      uv run --no-sync uvicorn app.main:app --port 8000
    API_BASE_URL="http://127.0.0.1:8000" \
      uv run --no-sync streamlit run ui/streamlit_app.py --server.port 8501 --server.headless=true
    ADVISORY_REVIEW_TOKEN="local-demo-review-token-CHANGE-ME" \
      uv run --no-sync python -m scripts.seed_demo_data

Usage:

    uv run --no-sync python -m scripts.capture_screenshots --output-dir docs/images

Every PNG is produced by a real browser session against the running app. No
image is synthesised, and no status or result is asserted by this script.
"""

import argparse
import os
import re
import sys
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

CHROME = os.environ.get(
    "CAPTURE_CHROME",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)
UI_URL = os.environ.get("CAPTURE_UI_URL", "http://127.0.0.1:8501")
API_URL = os.environ.get("CAPTURE_API_URL", "http://127.0.0.1:8000")
VIEWPORT = {"width": 1600, "height": 1100}


def wait_idle(page: Page, settle: int = 900) -> None:
    """Wait until Streamlit finished the current rerun."""
    page.wait_for_timeout(350)
    try:
        page.wait_for_function(
            """() => {
                const widget = document.querySelector('[data-testid="stStatusWidget"]');
                return !widget || !/running/i.test(widget.textContent || '');
            }""",
            timeout=45000,
        )
    except Exception:  # noqa: BLE001 - the timeout is handled by the settle delay
        pass
    page.wait_for_timeout(settle)


def _selectbox_value(page: Page) -> str:
    box = page.locator('[data-testid="stSelectbox"]:visible').filter(
        has=page.get_by_text("Open assessment", exact=True)
    )
    return box.get_by_role("combobox").input_value()


def select_assessment(page: Page, title_fragment: str) -> None:
    """Pick an assessment in the dashboard 'Open assessment' selectbox.

    Streamlit occasionally drops a widget update while a previous rerun is still
    in flight, so the resulting value is verified and the selection retried.
    """
    for attempt in range(6):
        wait_idle(page)
        box = page.locator('[data-testid="stSelectbox"]:visible').filter(
            has=page.get_by_text("Open assessment", exact=True)
        )
        combobox = box.get_by_role("combobox")
        combobox.scroll_into_view_if_needed()
        page.wait_for_timeout(300)
        combobox.click()
        page.wait_for_timeout(600)
        if page.get_by_role("option").count():
            page.get_by_role("option", name=re.compile(re.escape(title_fragment))).first.click()
        else:
            page.keyboard.press("Escape")
            page.wait_for_timeout(400)
            continue
        for _ in range(12):
            page.wait_for_timeout(500)
            if title_fragment in _selectbox_value(page):
                wait_idle(page)
                return
        print(f"  selection of {title_fragment!r} did not stick (attempt {attempt + 1}); retrying")
    raise SystemExit(f"Could not select assessment {title_fragment!r}")


def open_tab(page: Page, label: str) -> None:
    """Activate a Streamlit tab and wait for the rerun."""
    page.get_by_role("tab", name=re.compile(re.escape(label))).first.click()
    wait_idle(page)


def expand(page: Page, label: str) -> None:
    """Expand a visible Streamlit expander whose summary contains the label."""
    summary = page.locator("summary:visible", has_text=re.compile(re.escape(label))).first
    summary.scroll_into_view_if_needed()
    page.wait_for_timeout(200)
    if not summary.evaluate("el => el.parentElement.hasAttribute('open')"):
        summary.click()
        page.wait_for_timeout(2500)


def expand_json_key(page: Page, key: str, expect: str) -> None:
    """Expand a react-json-view node so that the expected content becomes visible."""
    if page.get_by_text(re.compile(re.escape(expect))).count():
        return
    node = page.locator(f'span.object-key:text-is("{key}")').first
    node.scroll_into_view_if_needed()
    page.wait_for_timeout(200)
    node.click()
    page.wait_for_timeout(1200)


SCROLL_JS = """
({text, top, key, bottom, offset}) => {
    const main = document.querySelector('[data-testid="stMain"]');
    if (!main) return null;
    if (bottom) { main.scrollTop = main.scrollHeight; return 0; }
    if (top !== null) { main.scrollTop = top; return 0; }
    let el = null;
    if (key) {
        el = Array.from(document.querySelectorAll('span.object-key'))
            .find(n => n.offsetParent !== null && n.textContent.trim().replace(/"/g, '') === key);
    } else {
        const nodes = Array.from(document.querySelectorAll('main *'));
        el = nodes.find(n => n.offsetParent !== null && n.children.length === 0
            && (n.textContent || '').includes(text));
    }
    if (!el) return null;
    const rect = el.getBoundingClientRect();
    main.scrollTop += rect.top - offset;
    return rect.top;
}
"""


def scroll_main(
    page: Page,
    text: str | None = None,
    top: int | None = None,
    key: str | None = None,
    bottom: bool = False,
    offset: int = 90,
) -> None:
    """Scroll the Streamlit main container to a text anchor, JSON key or offset."""
    for _ in range(3):
        position = page.evaluate(
            SCROLL_JS, {"text": text, "top": top, "key": key, "bottom": bottom, "offset": offset}
        )
        page.wait_for_timeout(350)
        if position is None:
            continue
        if top is not None or bottom or abs(position - offset) < 40:
            break
    page.wait_for_timeout(250)


def shot(
    page: Page,
    path: Path,
    anchor: str | None = None,
    height: int | None = None,
    key: str | None = None,
    bottom: bool = False,
) -> None:
    """Save one viewport screenshot, optionally scrolled to an anchor element."""
    if height:
        page.set_viewport_size({"width": VIEWPORT["width"], "height": height})
        page.wait_for_timeout(400)
    if anchor or key or bottom:
        scroll_main(page, text=anchor, key=key, bottom=bottom)
    path.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(path), full_page=False)
    print(f"captured {path.name}")


def capture_ui(page: Page, out: Path) -> None:
    page.goto(UI_URL, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(4000)
    wait_idle(page, settle=1500)

    # 1. Dashboard with the assessment list and the phase tabs.
    scroll_main(page, top=0)
    shot(page, out / "01-dashboard-assessment-list.png")

    # 2. Guided discovery: next question, findings and the saved answer tracker.
    select_assessment(page, "Knowledge-base support triage")
    open_tab(page, "2 · Discovery")
    expand(page, "Saved source answers")
    shot(page, out / "02-guided-discovery.png", anchor="Missing applicable evidence", height=1350)

    # 3. Workflow builder with steps and atomic tasks.
    open_tab(page, "3 · Workflow")
    expand(page, "Step 1")
    shot(
        page,
        out / "03-workflow-builder.png",
        anchor="Task: Extract intent and route ticket",
        height=1350,
    )

    # 4. Per-task analysis: advisory score, hard-rule hits and controls.
    select_assessment(page, "Refund email workflow")
    open_tab(page, "4 · Analysis")
    expand(page, "Rule hits, factors and risks")
    expand_json_key(page, "rules", "ACT-001")
    shot(page, out / "04-analysis-scores-hard-rules.png", anchor="AI suitability", height=1700)

    # 5. Risk register of the same task (impact, likelihood, score, band).
    expand_json_key(page, "risks", "likelihood")
    shot(page, out / "05-analysis-risk-register.png", key="risks", height=1250)

    # 6. AI / non-AI boundary for a deterministic task.
    select_assessment(page, "Exact pricing calculation")
    open_tab(page, "4 · Analysis")
    shot(
        page, out / "06-ai-non-ai-boundary.png", anchor="Code / deterministic boundary", height=1250
    )

    # 7. Evidence, ROI scenario and the PoC go/no-go gate.
    select_assessment(page, "Knowledge-base support triage")
    open_tab(page, "10 · Portfolio")
    shot(page, out / "07-recommendation-poc-gates.png", anchor="Evidence registry", height=1700)

    # 8. FDE review with a recorded override rationale.
    select_assessment(page, "Returns policy exception review")
    open_tab(page, "6 · FDE review")
    shot(page, out / "08-fde-review-override-rationale.png", anchor="override_reason", height=1250)

    # 9. Final decision and audit trail of the finalized demo assessment.
    expand(page, "Assessment audit history")
    shot(page, out / "09-final-decision-audit-trail.png", bottom=True, height=1350)

    # 10. Exported Markdown report.
    select_assessment(page, "Knowledge-base support triage")
    open_tab(page, "7 · Reports")
    expand(page, "Preview report (Markdown)")
    shot(
        page,
        out / "10-exported-markdown-report.png",
        anchor="Preview report (Markdown)",
        height=1500,
    )

    # 11. Portfolio overview (Phase 8).
    page.locator('[data-testid="stSidebar"]').get_by_text("Portfolio", exact=True).click()
    wait_idle(page, settle=1500)
    scroll_main(page, top=0)
    shot(page, out / "11-portfolio-opportunities.png", height=1250)


def capture_api(page: Page, out: Path) -> None:
    page.goto(f"{API_URL}/docs", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(4000)
    shot(page, out / "12-api-openapi-docs.png")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("docs/images"))
    parser.add_argument("--skip-api", action="store_true")
    args = parser.parse_args()
    if not Path(CHROME).exists():
        print(f"Chrome not found at {CHROME}; set CAPTURE_CHROME.", file=sys.stderr)
        return 2
    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=CHROME,
            headless=True,
            args=["--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=1"],
        )
        context = browser.new_context(viewport=VIEWPORT, device_scale_factor=2)
        page = context.new_page()
        try:
            capture_ui(page, args.output_dir)
            if not args.skip_api:
                capture_api(page, args.output_dir)
        finally:
            context.close()
            browser.close()
    print("Screenshots captured from the running local instance. Synthetic data only.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
