"""Automated accessibility checks for the Streamlit UI.

The checks in this file do not need a browser: they inspect the application
source and the colour arithmetic in :mod:`ui.accessibility`, so the default
``pytest -q`` run enforces the properties the application code is
responsible for.

A browser-based DOM/axe scan is included as an opt-in test
(``A11Y_UI_URL=http://127.0.0.1:8501``); it skips cleanly when no running
instance, browser or axe build is available.

None of these checks is a WCAG conformance claim; see ``docs/accessibility.md``.
"""

from __future__ import annotations

import ast
import json
import os
import re
from pathlib import Path

import pytest

from ui.accessibility import (
    CODE_BLOCK_TOKENS,
    DARK_BACKGROUND,
    DARK_CODE_BACKGROUND,
    DARK_SUCCESS_BACKGROUND,
    DARK_SUCCESS_TEXT,
    JSON_TOKENS,
    LIGHT_BACKGROUND,
    LIGHT_CODE_BACKGROUND,
    LIGHT_SIDEBAR_BACKGROUND,
    LIGHT_SUCCESS_BACKGROUND,
    LIGHT_SUCCESS_TEXT,
    LIGHT_TEXT,
    MIN_CONTRAST,
    STREAMLIT_PRIMARY,
    accessible_variant,
    blend,
    build_stylesheet,
    contrast_ratio,
    escape_markdown,
    heading,
    hex_to_rgb,
    minimum_opacity,
    mix,
    relative_luminance,
)

UI_DIR = Path(__file__).resolve().parents[1] / "ui"
UI_MODULES = sorted(p for p in UI_DIR.glob("*.py"))

# Widgets that Streamlit renders with a visible label; the first positional
# argument is that label.
LABELLED_WIDGETS = {
    "button",
    "checkbox",
    "color_picker",
    "date_input",
    "download_button",
    "file_uploader",
    "form_submit_button",
    "multiselect",
    "number_input",
    "radio",
    "selectbox",
    "slider",
    "text_area",
    "text_input",
    "time_input",
}

# The tab strip of the assessment view, in the order the API returns it.
EXPECTED_TAB_LABELS = [
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

TAB_PANEL_NAMES = {
    "intake",
    "discovery",
    "workflow",
    "analysis",
    "advisor",
    "review",
    "report",
    "collaboration",
    "workspace",
    "portfolio",
}


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _calls(tree: ast.AST) -> list[ast.Call]:
    return [node for node in ast.walk(tree) if isinstance(node, ast.Call)]


def _dotted_name(node: ast.AST) -> str:
    if isinstance(node, ast.Attribute):
        return f"{_dotted_name(node.value)}.{node.attr}"
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _constant(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _literal_text(node: ast.AST) -> str | None:
    """The literal text of a string constant or of an f-string's fixed parts."""
    literal = _constant(node)
    if literal is not None:
        return literal
    if isinstance(node, ast.JoinedStr):
        parts = [
            value.value
            for value in node.values
            if isinstance(value, ast.Constant) and isinstance(value.value, str)
        ]
        return "".join(parts)
    return None


def _streamlit_widgets(module: ast.Module) -> list[tuple[str, ast.Call]]:
    """Return ``(widget, call)`` for every ``st.<widget>(...)`` call."""
    found = []
    for call in _calls(module):
        name = _dotted_name(call.func)
        if not name.startswith("st."):
            continue
        widget = name.split(".", 1)[1]
        if widget in LABELLED_WIDGETS:
            found.append((widget, call))
    return found


def _label_bindings(module: ast.Module) -> dict[str, list[ast.AST]]:
    """Map a variable name to the expressions that are bound to it."""
    bindings: dict[str, list[ast.AST]] = {}

    def bind(target: ast.AST, value: ast.AST) -> None:
        if isinstance(target, ast.Name):
            bindings.setdefault(target.id, []).append(value)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for element in target.elts:
                bind(element, value)

    for node in ast.walk(module):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            bind(node.targets[0], node.value)
        elif isinstance(node, ast.For):
            bind(node.target, node.iter)
        elif isinstance(node, ast.comprehension):
            bind(node.target, node.iter)
    return bindings


def _resolve_labels(name: str, bindings: dict[str, list[ast.AST]], depth: int = 0) -> list[str]:
    """Best-effort resolution of a variable that holds one or more labels."""
    if depth > 4:
        return []
    resolved: list[str] = []
    for expression in bindings.get(name, []):
        if isinstance(expression, ast.Constant) and isinstance(expression.value, str):
            resolved.append(expression.value)
        elif isinstance(expression, (ast.Tuple, ast.List)):
            for element in expression.elts:
                if isinstance(element, (ast.Tuple, ast.List)) and len(element.elts) > 1:
                    nested = _constant(element.elts[1])
                    if nested is not None:
                        resolved.append(nested)
                else:
                    nested = _constant(element)
                    if nested is not None:
                        resolved.append(nested)
        elif isinstance(expression, ast.Name):
            resolved.extend(_resolve_labels(expression.id, bindings, depth + 1))
    return resolved


def _level_value(node: ast.AST) -> int | None:
    """Evaluate the integer level argument of a ``heading(...)`` call."""
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _level_value(node.left)
        right = _level_value(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def _function_named(name: str) -> ast.FunctionDef | None:
    for path in UI_MODULES:
        for node in ast.walk(_parse(path)):
            if isinstance(node, ast.FunctionDef) and node.name == name:
                return node
    return None


# --- labels -------------------------------------------------------------------


@pytest.mark.parametrize("path", UI_MODULES, ids=lambda p: p.name)
def test_every_input_widget_has_a_visible_label(path: Path) -> None:
    """Each form control must carry a non-empty text label."""
    module = _parse(path)
    bindings = _label_bindings(module)
    problems = []
    for widget, call in _streamlit_widgets(module):
        label = None
        if call.args:
            label = call.args[0]
        if label is None:
            for keyword in call.keywords:
                if keyword.arg == "label":
                    label = keyword.value
        if label is None:
            problems.append((widget, call.lineno, "no label argument"))
            continue
        literal = _literal_text(label)
        if literal is not None:
            if not literal.strip():
                problems.append((widget, call.lineno, "empty label"))
            continue
        if isinstance(label, ast.Name):
            candidates = _resolve_labels(label.id, bindings)
            if not candidates:
                problems.append((widget, call.lineno, f"unresolved label {label.id!r}"))
            elif not all(candidate.strip() for candidate in candidates):
                problems.append((widget, call.lineno, f"empty label {label.id!r}"))
            continue
        problems.append((widget, call.lineno, "label is not a literal"))
    assert not problems, f"widgets without a usable text label: {problems}"


@pytest.mark.parametrize("path", UI_MODULES, ids=lambda p: p.name)
def test_no_images_or_unaudited_raw_html(path: Path) -> None:
    """The app must not introduce images or raw HTML without a description."""
    module = _parse(path)
    for call in _calls(module):
        name = _dotted_name(call.func)
        if name in {"st.image", "st.image_url"}:
            pytest.fail(f"{path.name}: images need alternative text ({name} at line {call.lineno})")
        if name == "st.html":
            pytest.fail(f"{path.name}: raw st.html is not audited (line {call.lineno})")


# --- heading structure --------------------------------------------------------


@pytest.mark.parametrize("path", UI_MODULES, ids=lambda p: p.name)
def test_headings_go_through_the_shared_helper(path: Path) -> None:
    """Only :func:`ui.accessibility.heading` may emit a section heading."""
    if path.name == "accessibility.py":
        return
    module = _parse(path)
    for call in _calls(module):
        name = _dotted_name(call.func)
        if name in {"st.header", "st.subheader"}:
            pytest.fail(f"{path.name}: use accessibility.heading() (line {call.lineno})")
        if name == "st.markdown" and call.args:
            literal = _constant(call.args[0])
            if literal and re.match(r"^#{1,6}\s", literal):
                pytest.fail(f"{path.name}: use accessibility.heading() (line {call.lineno})")


def test_page_has_a_single_h1() -> None:
    """Streamlit maps st.title to h1; the page must declare exactly one."""
    titles = []
    for path in UI_MODULES:
        if path.name == "accessibility.py":
            continue
        for call in _calls(_parse(path)):
            if _dotted_name(call.func) == "st.title":
                titles.append((path.name, call.lineno))
    assert len(titles) == 1, f"expected exactly one st.title, found {titles}"


@pytest.mark.parametrize("path", UI_MODULES, ids=lambda p: p.name)
def test_heading_levels_never_jump(path: Path) -> None:
    """Within a function the heading level may only increase by one."""
    if path.name == "accessibility.py":
        return
    module = _parse(path)
    for function in [node for node in ast.walk(module) if isinstance(node, ast.FunctionDef)]:
        levels = []
        for call in _calls(function):
            if _dotted_name(call.func) == "heading" and len(call.args) >= 3:
                level = _level_value(call.args[2])
                if level is not None:
                    levels.append((call.lineno, level))
        for previous, current in zip(levels, levels[1:], strict=False):
            assert current[1] <= previous[1] + 1, (
                f"{path.name}:{current[0]} heading level jumps from {previous[1]} to "
                f"{current[1]} in {function.name}()"
            )


def test_tab_labels_are_unique_and_ordered() -> None:
    """The assessment tab strip has stable, unique, ordered labels."""
    module = _parse(UI_DIR / "streamlit_app.py")
    labels = None
    for call in _calls(module):
        if _dotted_name(call.func) == "st.tabs" and call.args:
            first = call.args[0]
            if isinstance(first, ast.List):
                labels = [_constant(element) for element in first.elts]
            break
    assert labels == EXPECTED_TAB_LABELS
    assert len(set(labels)) == len(labels)


def test_every_tab_panel_starts_with_a_heading() -> None:
    """Each tab panel renders a level-3 heading as its first element."""
    module = _parse(UI_DIR / "streamlit_app.py")
    main = next(
        node
        for node in ast.walk(module)
        if isinstance(node, ast.FunctionDef) and node.name == "main"
    )
    panels = []
    for node in ast.walk(main):
        if not isinstance(node, ast.With) or not node.items:
            continue
        context = node.items[0].context_expr
        name = getattr(context, "id", None)
        if name not in TAB_PANEL_NAMES:
            continue
        statement = node.body[0]
        call = statement.value if isinstance(statement, ast.Expr) else statement
        level = 3
        for keyword in call.keywords:
            if keyword.arg == "level" and isinstance(keyword.value, ast.Constant):
                level = keyword.value.value
        panels.append((name, _dotted_name(call.func), level))
    assert len(panels) == 10, f"expected ten tab panels, found {len(panels)}"
    for _name, function, level in panels:
        target = _function_named(function.split(".")[-1])
        assert target is not None, f"could not find {function}"
        levels = []
        for call in _calls(target):
            if _dotted_name(call.func) == "heading" and len(call.args) >= 3:
                resolved = _level_value(call.args[2])
                if resolved is None:
                    # The level is a parameter of the panel function.
                    resolved = level
                levels.append(resolved)
        assert levels, f"{function}() does not render a heading"
        assert levels[0] == 3, f"{function}() starts at level {levels[0]}, expected 3"


# --- status is never colour-only ----------------------------------------------


@pytest.mark.parametrize("path", UI_MODULES, ids=lambda p: p.name)
def test_app_never_chooses_its_own_colours(path: Path) -> None:
    """The app must not signal state with a colour of its own."""
    if path.name == "accessibility.py":
        # The accessibility helpers are the only place that knows about colours.
        return
    source = path.read_text(encoding="utf-8")
    body = "\n".join(line for line in source.splitlines() if not line.strip().startswith("#"))
    assert not re.search(r"#[0-9a-fA-F]{6}\b", body), f"{path.name} contains a colour literal"


def test_statuses_are_rendered_as_words() -> None:
    """Evidence, PoC and approval state must be spelled out, not just coloured."""
    portfolio = (UI_DIR / "portfolio.py").read_text(encoding="utf-8")
    for state in ("Verified", "Unverified", "Stale"):
        assert state in portfolio, f"evidence state {state!r} is not rendered as text"
    collaboration = (UI_DIR / "collaboration.py").read_text(encoding="utf-8")
    assert "pending" in collaboration, "approval stage state is not rendered as text"


# --- contrast arithmetic ------------------------------------------------------


def test_contrast_helpers_agree_with_the_wcag_formula() -> None:
    assert relative_luminance("#ffffff") == pytest.approx(1.0)
    assert relative_luminance("#000000") == pytest.approx(0.0)
    assert contrast_ratio("#000000", "#ffffff") == pytest.approx(21.0, rel=1e-3)
    assert contrast_ratio("#ffffff", "#ffffff") == pytest.approx(1.0)
    with pytest.raises(ValueError):
        hex_to_rgb("nope")


def test_mix_moves_towards_the_other_colour() -> None:
    assert mix("#000000", "#ffffff", 0.0) == "#000000"
    assert mix("#000000", "#ffffff", 1.0) == "#ffffff"
    assert mix("#000000", "#ffffff", 0.5) == "#808080"


def test_blend_composites_foreground_over_background() -> None:
    assert blend("#000000", "#ffffff", 1.0) == "#000000"
    assert blend("#000000", "#ffffff", 0.0) == "#ffffff"
    assert blend("#000000", "#ffffff", 0.5) == "#808080"


def test_accessible_variant_keeps_the_hue_family_and_reaches_the_ratio() -> None:
    corrected = accessible_variant(STREAMLIT_PRIMARY, "#ffffff")
    assert contrast_ratio(corrected, "#ffffff") >= MIN_CONTRAST
    red, green, blue = hex_to_rgb(corrected)
    assert red > green and red > blue
    assert accessible_variant("#000000", "#ffffff") == "#000000"


def test_minimum_opacity_is_the_smallest_value_that_passes() -> None:
    opacity = minimum_opacity("#31333f", "#ffffff")
    assert opacity > 0.6
    assert contrast_ratio(blend("#31333f", "#ffffff", opacity), "#ffffff") >= MIN_CONTRAST
    assert contrast_ratio(blend("#31333f", "#ffffff", round(opacity - 0.01, 2)), "#ffffff") < (
        MIN_CONTRAST
    )


@pytest.mark.parametrize(
    ("colour", "background"),
    [
        (
            accessible_variant(LIGHT_SUCCESS_TEXT, LIGHT_SUCCESS_BACKGROUND),
            LIGHT_SUCCESS_BACKGROUND,
        ),
        (
            accessible_variant(DARK_SUCCESS_TEXT, DARK_SUCCESS_BACKGROUND),
            DARK_SUCCESS_BACKGROUND,
        ),
        (accessible_variant(STREAMLIT_PRIMARY, LIGHT_CODE_BACKGROUND), LIGHT_CODE_BACKGROUND),
        (accessible_variant(STREAMLIT_PRIMARY, DARK_CODE_BACKGROUND), DARK_CODE_BACKGROUND),
        (accessible_variant(STREAMLIT_PRIMARY, LIGHT_BACKGROUND), LIGHT_BACKGROUND),
        (accessible_variant(STREAMLIT_PRIMARY, DARK_BACKGROUND), DARK_BACKGROUND),
    ],
)
def test_generated_colours_meet_the_contrast_ratio(colour: str, background: str) -> None:
    assert contrast_ratio(colour, background) >= MIN_CONTRAST


def test_json_token_colours_are_corrected_to_the_ratio() -> None:
    """Every failing JSON token colour is replaced by a passing one."""
    failing = [
        (declaration, colour, background)
        for declaration, colour, background in JSON_TOKENS
        if contrast_ratio(colour, background) < MIN_CONTRAST
    ]
    assert failing, "expected at least one failing JSON token colour to correct"
    for _declaration, colour, background in failing:
        corrected = accessible_variant(colour, background)
        assert contrast_ratio(corrected, background) >= MIN_CONTRAST


def test_stylesheet_covers_every_measured_failure() -> None:
    """The overlay must correct each Streamlit element measured as failing."""
    stylesheet = build_stylesheet()
    for selector in (
        '[data-testid="stCaptionContainer"]',
        '[data-testid="stAlertContentSuccess"] p',
        '[data-testid="stCode"] .token.title.important',
        '[data-testid="stSliderThumbValue"]',
        '[data-testid="stMultiSelectTagsContainer"] span[data-tag]',
        '[data-testid="stBaseButton-primary"]',
        '[data-testid="stCode"] pre { overflow: visible; }',
        '[data-testid="stFileUploaderDropzoneInstructions"] span',
        '[data-testid="stWidgetLabel"][disabled] p',
    ):
        assert selector in stylesheet, f"missing rule for {selector}"
    assert '[data-testid="stJson"]' in stylesheet


def test_every_code_block_token_colour_reaches_the_contrast_ratio() -> None:
    """The syntax highlighter's own colours must be lifted to 4.5:1.

    The report preview shows a Mermaid fence, so these colours are read by
    people.  A colour that already passes must be left exactly as it is.
    """
    stylesheet = build_stylesheet()
    for short_class, colour in CODE_BLOCK_TOKENS:
        rule = f'[data-testid="stCode"] span.token.{short_class}'
        assert rule in stylesheet, f"missing rule for the {short_class} token"
        light = accessible_variant(colour, LIGHT_CODE_BACKGROUND)
        dark = accessible_variant(colour, DARK_CODE_BACKGROUND)
        assert contrast_ratio(light, LIGHT_CODE_BACKGROUND) >= MIN_CONTRAST
        assert contrast_ratio(dark, DARK_CODE_BACKGROUND) >= MIN_CONTRAST
        if light == colour.lower() and dark == colour.lower():
            # The colour already passes in both themes, so no rule is needed.
            continue
        assert light in stylesheet
        assert dark in stylesheet


def test_the_caption_is_measured_against_the_darker_surface() -> None:
    """A caption is rendered on the page *and* on the sidebar.

    The sidebar background is slightly darker, so a caption that is measured
    against the page alone can fall just below the ratio.  The overlay must
    use the darker of the two.
    """
    opacity = minimum_opacity(LIGHT_TEXT, LIGHT_SIDEBAR_BACKGROUND)
    blended = blend(LIGHT_TEXT, LIGHT_SIDEBAR_BACKGROUND, opacity)
    assert contrast_ratio(blended, LIGHT_SIDEBAR_BACKGROUND) >= MIN_CONTRAST
    assert f"opacity: {opacity}" in build_stylesheet()


def test_stylesheet_is_theme_adaptive() -> None:
    """The overlay must work in the dark theme as well as the light one."""
    stylesheet = build_stylesheet()
    assert "light-dark(" in stylesheet
    pairs = (
        (LIGHT_CODE_BACKGROUND, DARK_CODE_BACKGROUND),
        (
            accessible_variant(LIGHT_SUCCESS_TEXT, LIGHT_SUCCESS_BACKGROUND),
            accessible_variant(DARK_SUCCESS_TEXT, DARK_SUCCESS_BACKGROUND),
        ),
    )
    for light, dark in pairs:
        assert light in stylesheet, f"{light} missing from the stylesheet"
        assert dark in stylesheet, f"{dark} missing from the stylesheet"


# --- heading rendering --------------------------------------------------------


class _FakeStreamlit:
    """Minimal stand-in that records what the helper renders."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def title(self, text: str) -> None:
        self.calls.append(("title", text))

    def header(self, text: str) -> None:
        self.calls.append(("header", text))

    def subheader(self, text: str) -> None:
        self.calls.append(("subheader", text))

    def markdown(self, text: str, **_: object) -> None:
        self.calls.append(("markdown", text))


def test_heading_helper_maps_levels_to_streamlit_headings() -> None:
    fake = _FakeStreamlit()
    heading(fake, "Assessments", 2)
    heading(fake, "Intake", 3)
    heading(fake, "Details", 4)
    assert fake.calls == [
        ("header", "Assessments"),
        ("subheader", "Intake"),
        ("markdown", "#### Details"),
    ]


def test_heading_helper_escapes_untrusted_text() -> None:
    fake = _FakeStreamlit()
    heading(fake, "Step #1 *now*", 4)
    rendered = fake.calls[0][1]
    assert rendered.startswith("#### ")
    assert "#1" not in rendered.replace("\\#1", "")
    assert "*" not in rendered.replace("\\*", "")
    assert escape_markdown("a_b") == "a\\_b"


# --- opt-in browser scan ------------------------------------------------------

#: Findings the application cannot correct, because the element is Streamlit's
#: own markup rather than something the app writes.
#:
#: Each entry is (rule id, a fragment of the element that must match, the
#: reason it cannot be fixed here).  They are **reported**, not hidden: the
#: test fails on any violation that is not on this list, so a new defect
#: cannot slip in behind an allow-list.
KNOWN_FRAMEWORK_FINDINGS = (
    (
        "presentation-role-conflict",
        "stFileUploaderDropzone",
        "Streamlit renders the file-uploader dropzone as a focusable element with "
        "role=presentation. The markup is the framework's, so the application "
        "cannot correct it, and the dropzone carries an aria-label, so it is "
        "still announced.",
    ),
)


def _is_framework_finding(rule: str, nodes: list) -> bool:
    """Is this finding one of the documented, framework-owned ones?

    The match is made on the element's markup rather than on the selector
    axe reported, because Streamlit's selectors are hashed class names that
    change between builds.
    """
    for known_rule, fragment, _reason in KNOWN_FRAMEWORK_FINDINGS:
        if rule != known_rule:
            continue
        if any(fragment in str(node.get("html", "")) for node in nodes):
            return True
    return False


@pytest.mark.skipif(
    not os.environ.get("A11Y_UI_URL"),
    reason="set A11Y_UI_URL to a running Streamlit instance to run the browser scan",
)
def test_rendered_dom_has_no_axe_violations() -> None:
    """Scan every screen of the rendered app with axe-core (opt-in, needs a browser).

    The scan walks the ten assessment tabs and both sidebar views, because
    Streamlit renders only the selected tab panel into an active
    ``role="tabpanel"``.
    """
    pytest.importorskip("playwright.sync_api", reason="playwright is not installed")
    axe_core = pytest.importorskip("axe_core_python", reason="axe-core is not installed")
    from playwright.sync_api import sync_playwright

    url = os.environ["A11Y_UI_URL"]
    axe_path = os.path.join(os.path.dirname(axe_core.__file__), "axe.min.js")
    axe_source = Path(axe_path).read_text(encoding="utf-8")

    def scan(page) -> dict:
        page.add_script_tag(content=axe_source)
        result = page.evaluate("async () => await axe.run(document, {resultTypes:['violations']})")
        return {
            item["id"]: [
                {"target": node["target"], "html": node["html"][:300]} for node in item["nodes"]
            ]
            for item in result["violations"]
        }

    findings: dict[str, dict] = {}
    with sync_playwright() as driver:
        browser = driver.chromium.launch()
        page = browser.new_page(viewport={"width": 1400, "height": 1000})
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_selector("text=AI Opportunity Advisor", timeout=60000)
        page.wait_for_timeout(3000)
        findings["initial view"] = scan(page)
        for label in EXPECTED_TAB_LABELS:
            page.get_by_role("tab", name=label).click()
            page.wait_for_timeout(1500)
            findings[f"tab {label}"] = scan(page)
        for view in ("Workspace", "Portfolio"):
            page.locator('[data-testid="stSidebar"]').get_by_text(view, exact=True).click()
            page.wait_for_timeout(1500)
            findings[f"view {view}"] = scan(page)
        browser.close()

    # The framework-owned findings are reported, not hidden.  They are
    # separated from the application's own violations so that a new defect
    # still fails the test.
    violations: dict[str, dict] = {}
    framework: dict[str, dict] = {}
    for screen, items in findings.items():
        for rule, targets in items.items():
            if _is_framework_finding(rule, targets):
                framework.setdefault(screen, {})[rule] = targets
            else:
                violations.setdefault(screen, {})[rule] = targets
    assert not violations, json.dumps(violations, indent=2)
    # Every allow-listed finding must still be the one that was documented:
    # a finding that disappears is not a pass, it is a stale allow-list.
    for known_rule, fragment, _reason in KNOWN_FRAMEWORK_FINDINGS:
        seen = [
            screen
            for screen, items in framework.items()
            if known_rule in items
            and any(fragment in str(node.get("html", "")) for node in items[known_rule])
        ]
        assert seen or not findings, (
            f"The documented framework finding {known_rule!r} on {fragment!r} was not "
            "observed. The allow-list is stale and must be reviewed."
        )
