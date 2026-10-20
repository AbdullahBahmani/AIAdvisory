"""The Streamlit view of the localization layer.

The module owns three things and nothing else:

* the **locale selector**, which lives in the sidebar and is labelled, so a
  screen reader announces it;
* the **activation** of a locale for one script run: the translator becomes
  the active one and the ``LABEL_*`` constants of the UI modules are
  replaced with the localized text;
* the **catalogue report**, which states how many strings the active locale
  does not translate.  A fallback to English is never silent.

The catalogue itself lives in :mod:`app.localization` so that the API and
the exported report can use the same ids and the same formatting rules.
"""

from __future__ import annotations

from types import ModuleType

from app.localization import (
    DEFAULT_LOCALE,
    LOCALES,
    Translator,
    install_labels,
    set_current,
    t,
)

#: The label of the locale selector.  It is a module constant because the
#: accessibility audit reads widget labels from the source.
LABEL_LANGUAGE = "Language"


def ui_modules() -> tuple[ModuleType, ...]:
    """The UI modules whose ``LABEL_*`` constants are localized."""
    from ui import collaboration, i18n, portfolio, reporting, streamlit_app

    return (streamlit_app, collaboration, portfolio, reporting, i18n)


def locale_selector(st, key: str = "locale") -> str:
    """Render the labelled locale selector in the sidebar and return the code."""
    choice = st.sidebar.selectbox(
        LABEL_LANGUAGE,
        options=list(LOCALES),
        format_func=lambda code: LOCALES[code].endonym,
        key=key,
    )
    return choice if choice in LOCALES else DEFAULT_LOCALE


def activate(st, key: str = "locale") -> Translator:
    """Activate the locale chosen in the sidebar for this script run.

    The labels are installed *before* the selector is rendered, so the label
    of the selector itself is written in the active language.  The selection
    lives in the session state and survives the rerun.
    """
    selected = st.session_state.get(key)
    code = selected if selected in LOCALES else DEFAULT_LOCALE
    translator = Translator(code)
    set_current(translator)
    install_labels(translator, ui_modules())
    locale_selector(st, key=key)
    return translator


def report_catalogue(st, translator: Translator) -> None:
    """State in the sidebar how complete the active catalogue is.

    A string that falls back to English is reported, not swallowed: the
    count is shown even when it is zero, so a shrinking catalogue is visible.
    """
    missing = translator.missing_count()
    st.sidebar.caption(
        t(
            "ui.locale.catalogue_report",
            "Catalogue: {total} strings · {missing} untranslated in this locale",
            total=len(translator.catalogue.ids(DEFAULT_LOCALE)),
            missing=missing,
        )
    )
    if missing:
        st.sidebar.warning(
            t(
                "ui.locale.missing_warning",
                "{missing} strings fall back to English in this locale.",
                missing=missing,
            )
        )


__all__ = [
    "DEFAULT_LOCALE",
    "LOCALES",
    "LABEL_LANGUAGE",
    "Translator",
    "activate",
    "locale_selector",
    "report_catalogue",
    "t",
]
