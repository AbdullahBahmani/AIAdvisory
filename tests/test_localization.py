"""Tests for the localization layer: catalogue, locale selector, formatting and export.

The checks in this file are browser-free.  They prove that

* every string id the application references exists in the catalogue, and
  every catalogue entry is reachable (no dead translations);
* the English catalogue mirrors the English text that stays in the source;
* the locale selector is labelled and the fallback to English works and is
  *reported* instead of being swallowed;
* money is formatted exactly, with the precision of the currency and never
  through a float;
* a Norwegian export has Norwegian headings while rule ids, category values
  and timestamps are unchanged;
* the English export is byte-identical to the artefact the product shipped
  before the localization work;
* the accessibility checks still pass.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from decimal import Decimal
from pathlib import Path

import pytest

from app.localization import (
    DEFAULT_LOCALE,
    LOCALES,
    Catalogue,
    Locale,
    Translator,
    current,
    default_catalogue,
    format_currency,
    format_datetime,
    format_number,
    format_percent,
    install_labels,
    set_current,
    t,
)
from app.reporting.render import build_sections, render_html, render_markdown
from tests.test_persistence import client  # noqa: F401 - the database fixture

ROOT = Path(__file__).resolve().parents[1]
UI_DIR = ROOT / "ui"
APP_DIR = ROOT / "app"

#: The modules that own translatable strings.
SOURCES = (
    APP_DIR / "reporting" / "render.py",
    APP_DIR / "reporting" / "diagrams.py",
    UI_DIR / "streamlit_app.py",
    UI_DIR / "collaboration.py",
    UI_DIR / "portfolio.py",
    UI_DIR / "reporting.py",
    UI_DIR / "i18n.py",
)

CATALOGUE = default_catalogue()
NBSP = " "


# --- helpers ------------------------------------------------------------------


def _module_name(path: Path) -> str:
    return ".".join(path.relative_to(ROOT).with_suffix("").parts)


def _literal(node: ast.AST) -> str | None:
    """The text of a string constant or of implicitly concatenated constants."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return None
    parts = [
        element.value
        for element in ast.walk(node)
        if isinstance(element, ast.Constant) and isinstance(element.value, str)
    ]
    return "".join(parts) if parts else None


def _referenced_ids(path: Path) -> dict[str, str]:
    """Every string id the module references, with its English text."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: dict[str, str] = {}

    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id.startswith("LABEL_"):
                text = _literal(node.value)
                assert text is not None, f"{path.name}: {target.id} is not a literal"
                found[f"{_module_name(path)}.{target.id}"] = text

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name != "t":
            continue
        string_id = _literal(node.args[0])
        if string_id is None:
            continue
        default = _literal(node.args[1]) if len(node.args) > 1 else None
        assert default is not None, f"{path.name}: t({string_id!r}) has no literal default"
        found[string_id] = default
    return found


def _source_catalogue() -> dict[str, str]:
    catalogue: dict[str, str] = {}
    for source in SOURCES:
        for key, value in _referenced_ids(source).items():
            assert catalogue.get(key, value) == value, f"conflicting text for {key!r}"
            catalogue[key] = value
    return catalogue


def _write_catalogue(directory: Path, locale: str, entries: dict[str, str]) -> None:
    (directory / f"{locale}.json").write_text(
        json.dumps(entries, ensure_ascii=False), encoding="utf-8"
    )


# --- the catalogue ------------------------------------------------------------


def test_every_referenced_id_exists_in_the_english_catalogue() -> None:
    """A string id that is used but not defined is a failure."""
    referenced = set(_source_catalogue())
    defined = CATALOGUE.ids(DEFAULT_LOCALE)
    missing = sorted(referenced - defined)
    assert not missing, f"ids used in the source but missing from en.json: {missing}"


def test_every_catalogue_entry_is_reachable() -> None:
    """A key that is defined but never referenced is a failure."""
    referenced = set(_source_catalogue())
    unreachable = sorted(CATALOGUE.ids(DEFAULT_LOCALE) - referenced)
    assert not unreachable, f"ids in en.json that no module references: {unreachable}"


def test_the_english_catalogue_mirrors_the_source() -> None:
    """The English text in the catalogue is the English text in the source."""
    source = _source_catalogue()
    differences = {
        key: (value, CATALOGUE.text(DEFAULT_LOCALE, key))
        for key, value in source.items()
        if CATALOGUE.text(DEFAULT_LOCALE, key) != value
    }
    assert not differences, f"the English catalogue drifted from the source: {differences}"


def test_every_locale_translates_the_same_ids() -> None:
    """A locale that is missing an id would fall back to English silently."""
    english = CATALOGUE.ids(DEFAULT_LOCALE)
    for code in LOCALES:
        missing = sorted(english - CATALOGUE.ids(code))
        assert not missing, f"locale {code!r} does not translate: {missing}"


def test_the_placeholders_of_a_translation_match_the_english_text() -> None:
    """A translation must interpolate the same values as the English text."""
    pattern = re.compile(r"\{([a-z_]+)\}")
    english = CATALOGUE.ids(DEFAULT_LOCALE)
    for code in LOCALES:
        for key in sorted(english):
            source = CATALOGUE.text(DEFAULT_LOCALE, key) or ""
            translation = CATALOGUE.text(code, key) or ""
            assert set(pattern.findall(source)) == set(pattern.findall(translation)), (
                f"locale {code!r} has different placeholders for {key!r}: "
                f"{source!r} vs {translation!r}"
            )


# --- the translator -----------------------------------------------------------


def test_a_missing_translation_falls_back_to_english_and_is_reported(tmp_path) -> None:
    """The fallback works and the gap is counted, not swallowed."""
    directory = tmp_path / "locales"
    directory.mkdir()
    _write_catalogue(
        directory,
        "en",
        {
            "report.section.executive_summary": "Executive summary",
            "report.section.workflow": "Workflow",
        },
    )
    _write_catalogue(
        directory,
        "nb",
        {"report.section.executive_summary": "Sammendrag for ledelsen"},
    )
    catalogue = Catalogue(directory)
    translator = Translator("nb", catalogue)
    assert translator.text("report.section.executive_summary") == "Sammendrag for ledelsen"
    # the id the locale does not translate falls back to English and is reported
    assert translator.text("report.section.workflow", "Workflow") == "Workflow"
    assert translator.missing == {"report.section.workflow"}
    assert translator.missing_count() == 1
    assert translator.missing_translations() == ["report.section.workflow"]


def test_an_unknown_id_is_visible_instead_of_empty(tmp_path) -> None:
    """An id that exists nowhere renders as the id, never as an empty label."""
    directory = tmp_path / "locales"
    directory.mkdir()
    _write_catalogue(directory, "en", {"a": "A"})
    _write_catalogue(directory, "nb", {"a": "A"})
    translator = Translator("nb", Catalogue(directory))
    assert translator.text("ui.does.not.exist", "fallback") == "fallback"
    assert "ui.does.not.exist" in translator.unknown
    assert translator.text("ui.does.not.exist") == "ui.does.not.exist"


def test_the_fallback_count_is_visible_for_the_shipped_catalogue() -> None:
    """The shipped Norwegian catalogue is complete, so nothing falls back."""
    translator = Translator("nb")
    assert translator.missing_count() == 0
    assert translator.unknown == set()


def test_the_active_translator_is_used_by_the_module_level_helper() -> None:
    """``t`` resolves in the active locale."""
    try:
        set_current(Translator("nb"))
        assert t("report.section.executive_summary") == "Sammendrag for ledelsen"
        set_current(Translator("en"))
        assert t("report.section.executive_summary") == "Executive summary"
    finally:
        set_current(Translator(DEFAULT_LOCALE))
    assert current().locale_code == DEFAULT_LOCALE


# --- the locale selector -----------------------------------------------------


class _FakeSidebar:
    def __init__(self, recorder: list) -> None:
        self.recorder = recorder

    def selectbox(self, label, options=None, format_func=None, key=None, **_: object):
        self.recorder.append(("selectbox", label, options, key))
        assert format_func is not None, "the locale selector must show the endonym"
        return options[0]

    def caption(self, text: str) -> None:
        self.recorder.append(("caption", text))

    def warning(self, text: str) -> None:
        self.recorder.append(("warning", text))


class _FakeStreamlit:
    """The smallest sidebar that the locale selector needs."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.sidebar = _FakeSidebar(self.calls)
        self.session_state: dict[str, object] = {}

    def caption(self, text: str) -> None:
        self.calls.append(("caption", text))

    def warning(self, text: str) -> None:
        self.calls.append(("warning", text))


def test_the_locale_selector_is_labelled() -> None:
    """The selector has a visible label, so a screen reader announces it."""
    from ui.i18n import LABEL_LANGUAGE, locale_selector

    fake = _FakeStreamlit()
    assert locale_selector(fake) == DEFAULT_LOCALE
    call = fake.calls[0]
    assert call[0] == "selectbox"
    assert call[1] == LABEL_LANGUAGE
    assert LABEL_LANGUAGE.strip()
    assert list(LOCALES) == ["en", "nb"]
    assert LOCALES["nb"].endonym == "Norsk bokmål"


def test_the_locale_selector_installs_the_labels_of_the_ui_modules() -> None:
    """Choosing a locale swaps the label constants of the UI modules."""
    from ui import collaboration, portfolio, reporting, streamlit_app
    from ui.i18n import activate

    fake = _FakeStreamlit()
    modules = (streamlit_app, collaboration, portfolio, reporting)
    try:
        activate(fake)
        assert streamlit_app.LABEL_ASSESSMENT_TITLE == "Assessment title"
        translator = Translator("nb")
        set_current(translator)
        install_labels(translator, modules)
        assert streamlit_app.LABEL_ASSESSMENT_TITLE == "Tittel på vurdering"
        assert reporting.LABEL_DOWNLOAD_MARKDOWN == "Last ned Markdown-rapport"
    finally:
        set_current(Translator(DEFAULT_LOCALE))
        install_labels(Translator(DEFAULT_LOCALE), modules)
    assert streamlit_app.LABEL_ASSESSMENT_TITLE == "Assessment title"


def test_the_download_adapter_carries_the_locale() -> None:
    """The export is requested in the locale of the user interface."""
    from io import BytesIO
    from unittest.mock import patch

    from ui.reporting import fetch_report

    class _Response(BytesIO):
        status = 200

    with patch("ui.reporting.urlopen", return_value=_Response(b"# report")) as called:
        fetch_report("http://127.0.0.1:8000", "abc123", "markdown", locale="nb")
    assert called.call_args.args[0].full_url == (
        "http://127.0.0.1:8000/api/v1/assessments/abc123/report?format=markdown&locale=nb"
    )
    with patch("ui.reporting.urlopen", return_value=_Response(b"# report")) as called:
        fetch_report("http://127.0.0.1:8000", "abc123", "markdown")
    assert called.call_args.args[0].full_url == (
        "http://127.0.0.1:8000/api/v1/assessments/abc123/report?format=markdown"
    )


def test_the_catalogue_report_states_the_number_of_missing_translations() -> None:
    """The gap is reported in the sidebar, even when it is zero."""
    from ui.i18n import report_catalogue

    fake = _FakeStreamlit()
    translator = Translator("nb")
    report_catalogue(fake, translator)
    captions = [call for call in fake.calls if call[0] == "caption"]
    assert captions
    assert "0 untranslated" in captions[0][1]
    assert not [call for call in fake.calls if call[0] == "warning"]


# --- formatting ---------------------------------------------------------------


def test_numbers_use_the_separators_of_the_locale() -> None:
    assert format_number(1234567, None, LOCALES["en"]) == "1,234,567"
    assert format_number(1234567, None, LOCALES["nb"]) == f"1{NBSP}234{NBSP}567"
    assert format_number(1234.5, None, LOCALES["en"]) == "1,234.5"
    assert format_number(1234.5, None, LOCALES["nb"]) == f"1{NBSP}234,5"
    assert format_number(-1234.5, None, LOCALES["nb"]) == f"-1{NBSP}234,5"
    assert format_number(Decimal("0"), None, LOCALES["nb"]) == "0"


def test_money_is_formatted_exactly_and_never_through_a_float() -> None:
    """NOK has two decimals; the amount is quantized with Decimal, not float."""
    amount = Decimal("1200.5")
    assert format_currency(amount, "NOK", LOCALES["en"]) == "NOK 1,200.50"
    assert format_currency(amount, "NOK", LOCALES["nb"]) == f"1{NBSP}200,50{NBSP}kr"
    # the same value quantized by hand: the formatter never rounds twice
    assert "1,200.50" in format_currency(amount, "NOK", LOCALES["en"])
    assert str(amount.quantize(Decimal("0.01"))) == "1200.50"
    # a currency without minor units gets none
    assert format_currency(Decimal("1200.5"), "JPY", LOCALES["en"]) == "JPY 1,201"
    assert format_currency(Decimal("1200.4"), "JPY", LOCALES["en"]) == "JPY 1,200"
    # a large amount keeps every digit: a float would lose them
    big = Decimal("12345678901234567890.12")
    rendered = format_currency(big, "NOK", LOCALES["en"])
    assert rendered.replace(",", "").endswith("12345678901234567890.12")


def test_the_money_rounding_matches_the_decimals_of_the_currency() -> None:
    """The rounding is the commercial one and it happens on the Decimal."""
    from decimal import ROUND_HALF_UP

    translator = Translator("nb")
    assert translator.currency(Decimal("99.999"), "NOK") == f"100,00{NBSP}kr"
    assert translator.currency(Decimal("0.005"), "NOK") == f"0,01{NBSP}kr"
    assert translator.currency(Decimal("0"), "NOK") == f"0,00{NBSP}kr"
    quantized = Decimal("0.005").quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    assert translator.currency(Decimal("0.005"), "NOK").startswith(str(quantized).replace(".", ","))


def test_percentages_and_scores_use_the_locale() -> None:
    assert format_percent(0.21, None, LOCALES["en"]) == "21%"
    assert format_percent(0.21, None, LOCALES["nb"]) == "21 %"
    assert format_percent(0, None, LOCALES["nb"]) == "0 %"
    assert Translator("nb").percent(1) == "100 %"


def test_dates_are_localized_but_the_stored_value_is_not() -> None:
    """The ISO-8601 timestamp is stored once; only the display is localized."""
    stored = "2026-10-20T14:05:00Z"
    assert format_datetime(stored, LOCALES["en"]) == "2026-10-20 14:05"
    assert format_datetime(stored, LOCALES["nb"]) == "20.10.2026 14:05"
    assert stored == "2026-10-20T14:05:00Z"


def test_an_unparsable_timestamp_is_left_alone() -> None:
    assert format_datetime("not a timestamp", LOCALES["nb"]) == "not a timestamp"


# --- the report ---------------------------------------------------------------


def _stable_detail(detail):
    """Replace the volatile identifiers so the report is byte-stable."""
    from app.domain.persistence import AssessmentDetail

    payload = json.loads(detail.model_dump_json())
    seen: dict[str, str] = {}

    def walk(node):
        if isinstance(node, dict):
            return {key: walk(value) for key, value in node.items()}
        if isinstance(node, list):
            return [walk(value) for value in node]
        if isinstance(node, str):

            def replace(match):
                raw = match.group(0)
                if raw not in seen:
                    seen[raw] = f"id-{len(seen) + 1:04d}"
                return seen[raw]

            text = re.sub(
                r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", replace, node
            )
            return re.sub(
                r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})?",
                "2026-01-01T00:00:00Z",
                text,
            )
        return node

    return AssessmentDetail.model_validate(walk(payload))


@pytest.fixture
def analyzed(client):
    """One analyzed assessment with deterministic identifiers."""
    from app.domain.persistence import AssessmentDetail

    assessment = client.post(
        "/api/v1/assessments",
        json={
            "title": "Golden report fixture",
            "organization": "Example Co",
            "owner": "FDE",
            "business_problem": "Reduce manual work",
        },
    )
    assert assessment.status_code == 201
    aid = assessment.json()["id"]
    workflow = client.post(f"/api/v1/assessments/{aid}/workflows", json={"name": "Refund handling"})
    step = client.post(
        f"/api/v1/workflows/{workflow.json()['id']}/steps",
        json={"name": "Handle case", "order": 0},
    )
    task = client.post(
        f"/api/v1/workflow-steps/{step.json()['id']}/tasks",
        json={
            "title": "Determine refund amount",
            "signals": {
                "deterministic_rules": True,
                "exact_math": True,
                "data_available": True,
                "output_verifiable": True,
            },
        },
    )
    assert task.status_code == 201
    assert client.post(f"/api/v1/assessments/{aid}/analyze").status_code == 200
    detail = AssessmentDetail.model_validate(client.get(f"/api/v1/assessments/{aid}").json())
    return aid, _stable_detail(detail)


#: The SHA-256 of the English report as the product ships it.  Any change to
#: the English artefact breaks this test.  Re-baselined on 2026-10-20 when the
#: deterministic architecture-pattern diagrams were added to the export; the
#: intent is unchanged — the English export must stay byte-stable, and the
#: locale must never be what changes it.
ENGLISH_MARKDOWN_SHA256 = "b7205b6c710f31ca7b6ed4745ff2e2c6e4489365e2d57f3044ac092b098f6243"
ENGLISH_HTML_SHA256 = "39f6a76ad28548aac0b3eb81905f9206b7868f68b2e7392889feb3f753e7a65d"


def test_the_english_report_is_byte_identical_to_the_previous_artefact(analyzed) -> None:
    """Localization must not change the English export by a single byte."""
    _aid, detail = analyzed
    markdown = render_markdown(detail)
    html = render_html(detail)
    assert hashlib.sha256(markdown.encode()).hexdigest() == ENGLISH_MARKDOWN_SHA256
    assert hashlib.sha256(html.encode()).hexdigest() == ENGLISH_HTML_SHA256
    assert render_markdown(detail, locale=DEFAULT_LOCALE) == markdown


def test_the_norwegian_report_has_norwegian_headings(analyzed) -> None:
    """The headings are translated; the contract values are not."""
    _aid, detail = analyzed
    english = render_markdown(detail)
    norwegian = render_markdown(detail, locale="nb")
    assert "## Executive summary" in english
    assert "## Sammendrag for ledelsen" in norwegian
    assert "## Sammendrag for ledelsen" not in english
    for value in (
        "traditional\\_software",
        "traditional software",
        "rules\\-v2",
        "DET\\-001",
        "MATH\\-001",
        "analyzed",
    ):
        assert value in english
        assert value in norwegian


def test_the_norwegian_report_keeps_the_contract_values(analyzed) -> None:
    """Rule ids, category values and revisions are never translated."""
    _aid, detail = analyzed
    norwegian = render_markdown(detail, locale="nb")
    assert "traditional\\_software" in norwegian
    assert "DET\\-001 \\(preference\\)" in norwegian
    assert "Regel DET\\-001 \\(preference\\)" in norwegian
    assert "Assessment revision | 4" not in norwegian
    assert "Vurderingsrevisjon | 4" in norwegian
    assert "Policy version | rules\\-v2" not in norwegian
    assert "Policyversjon | rules\\-v2" in norwegian


def test_the_report_sections_are_localized(analyzed) -> None:
    """The section builder takes the locale too."""
    _aid, detail = analyzed
    english = [section.title for section in build_sections(detail)]
    norwegian = [section.title for section in build_sections(detail, locale="nb")]
    assert english[0] == "Executive summary"
    assert norwegian[0] == "Sammendrag for ledelsen"
    assert len(english) == len(norwegian)


def test_the_html_report_declares_its_locale(analyzed) -> None:
    """The exported document states the language it is written in."""
    _aid, detail = analyzed
    assert '<html lang="en">' in render_html(detail)
    assert '<html lang="nb">' in render_html(detail, locale="nb")


def test_the_export_endpoint_carries_the_locale(client, analyzed) -> None:
    """The API exports the locale that is asked for and refuses unknown ones."""
    aid, _detail = analyzed
    english = client.get(f"/api/v1/assessments/{aid}/report?format=markdown")
    norwegian = client.get(f"/api/v1/assessments/{aid}/report?format=markdown&locale=nb")
    assert english.status_code == norwegian.status_code == 200
    assert english.headers["x-report-locale"] == "en"
    assert norwegian.headers["x-report-locale"] == "nb"
    assert english.headers["x-report-missing-translations"] == "0"
    assert "Executive summary" in english.text
    assert "Sammendrag for ledelsen" in norwegian.text
    assert client.get(f"/api/v1/assessments/{aid}/report?locale=de").status_code == 422


# --- accessibility ------------------------------------------------------------


def test_the_accessibility_checks_still_pass() -> None:
    """The static accessibility audit of the UI still holds after the refactor."""
    from tests import test_ui_accessibility as audit

    for path in audit.UI_MODULES:
        audit.test_every_input_widget_has_a_visible_label(path)
        audit.test_no_images_or_unaudited_raw_html(path)
        audit.test_headings_go_through_the_shared_helper(path)
        audit.test_heading_levels_never_jump(path)
        audit.test_app_never_chooses_its_own_colours(path)
    audit.test_page_has_a_single_h1()
    audit.test_tab_labels_are_unique_and_ordered()
    audit.test_every_tab_panel_starts_with_a_heading()
    audit.test_statuses_are_rendered_as_words()


def test_the_contrast_stylesheet_is_untouched() -> None:
    """The localization work must not touch the contrast overlay."""
    from ui.accessibility import build_stylesheet

    stylesheet = build_stylesheet()
    assert '[data-testid="stCaptionContainer"]' in stylesheet
    assert "light-dark(" in stylesheet


def test_the_heading_helper_is_unchanged() -> None:
    """The headings keep their levels; only their text is localized."""
    from ui.accessibility import heading

    class _Fake:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        def title(self, text):
            self.calls.append(("title", text))

        def header(self, text):
            self.calls.append(("header", text))

        def subheader(self, text):
            self.calls.append(("subheader", text))

        def markdown(self, text, **_):
            self.calls.append(("markdown", text))

    fake = _Fake()
    heading(fake, "Sammendrag", 2)
    heading(fake, "Detaljer", 4)
    assert fake.calls == [("header", "Sammendrag"), ("markdown", "#### Detaljer")]


def test_the_locale_dataclass_describes_display_only() -> None:
    """A locale changes the display, never the stored data."""
    locale = LOCALES["nb"]
    assert isinstance(locale, Locale)
    assert locale.decimal_separator == ","
    assert locale.thousands_separator == NBSP
    assert locale.date_format == "%d.%m.%Y"
    assert LOCALES["en"].date_format == "%Y-%m-%d"
