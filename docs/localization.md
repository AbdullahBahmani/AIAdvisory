# Localization of the AI Opportunity Advisor

This document describes the string layer of the product: how the catalogue
works, how a locale is added, what is translated and what is deliberately
**not** translated, how the formatting rules differ per locale, and what the
fallback does when a translation is missing.

It covers the localization half of planner item P7-10 ("Accessibility and
localization evaluation, …").  The accessibility half is documented in
`docs/accessibility.md`.

**Nothing in this document is a claim of professional translation.**  See
[Honesty](#honesty) at the end: the Norwegian catalogue was written by the
author of the code, not by a native translator, and no machine translation
was used.

---

## 1. The shape of the layer

```
app/localization/            the shared string layer
├── __init__.py              the public API
├── engine.py                the catalogue, the translator and the formatters
└── locales/
    ├── en.json              the English catalogue (the default locale)
    └── nb.json              the Norwegian (Bokmål) catalogue
ui/i18n.py                   the Streamlit view: locale selector, activation,
                             catalogue report
```

The layer lives under `app/` because both the API and the exported report
need it, and because the container image copies `app/` and `ui/` only (a
top-level package would not ship).  The UI never imports the database or the
scoring code through it; it is a pure string and formatting library.

### The catalogue

A catalogue is a JSON file per locale that maps a **stable string id** to the
translated text:

```json
{
  "report.section.executive_summary": "Sammendrag for ledelsen",
  "ui.streamlit_app.LABEL_ASSESSMENT_TITLE": "Tittel på vurdering"
}
```

Ids are stable: they never change when the English wording changes.  They
are namespaced by the module that owns the string:

| Id shape | Owner |
| --- | --- |
| `report.<area>.<name>` | `app/reporting/render.py` |
| `ui.<module>.<name>` | a message of `ui/<module>.py` |
| `ui.<module>.LABEL_<NAME>` | a widget label of `ui/<module>.py` |

### Where the English text lives

The English text stays **in the source code**:

* a message is a call with the English text as its default:

  ```python
  st.info(t("ui.streamlit_app.dashboard_empty", "Create an assessment to begin guided discovery."))
  ```

* a widget label is a module constant:

  ```python
  LABEL_ASSESSMENT_TITLE = "Assessment title"
  ```

  The labels are constants because `tests/test_ui_accessibility.py` reads the
  label of every control **from the source**; a call would hide it from the
  audit.  The catalogue id of a label is its qualified name,
  `ui.streamlit_app.LABEL_ASSESSMENT_TITLE`, so the id and the source cannot
  drift apart.

`locales/en.json` mirrors that English text so that a translator sees one
file per locale.  `tests/test_localization.py` fails when the mirror and the
source disagree, when an id is used but not defined, and when a catalogue
entry is not reachable.

### The translator

```python
from app.localization import Translator, t

translator = Translator("nb")      # a translator for one locale
translator.t("report.section.executive_summary")   # "Sammendrag for ledelsen"
t("report.section.executive_summary")              # the active translator
```

`app/localization/engine.py` resolves an id in this order:

1. the active locale's catalogue,
2. the English catalogue (**the fallback**),
3. the English text given at the call site,
4. the id itself, so a missing string is visible instead of blank.

Every fallback is counted (see [§5](#5-the-fallback-rule)).

---

## 2. Using a locale in the interface

The locale selector is the first control of the sidebar.  It is a labelled
`st.selectbox` (a screen reader announces it) that lists the locales by their
endonym — "English", "Norsk bokmål" — because a language names itself in its
own language.

Choosing a locale

* activates a `Translator` for the run (`app.localization.set_current`),
* replaces the `LABEL_*` constants of the UI modules with the translated
  text (`app.localization.install_labels`),
* makes the report tab request the report in the same locale, and
* shows the catalogue report in the sidebar (see [§5](#5-the-fallback-rule)).

The locale is kept in the Streamlit session state under the key `locale`.

---

## 3. Adding a locale

1. **Copy the English catalogue** to a new file:

   ```bash
   cp app/localization/locales/en.json app/localization/locales/nb.json
   ```

2. **Translate the values.**  Keep the ids, and keep the placeholders
   (`{name}`, `{count}`, …) exactly as they are; the test suite checks that a
   translation interpolates the same values as the English text.

3. **Register the locale** in `app/localization/engine.py`:

   ```python
   "nb": Locale(
       code="nb",
       endonym="Norsk bokmål",
       decimal_separator=",",
       thousands_separator="\u00a0",
       currency_token="symbol",
       currency_position="after",
       currency_separator="\u00a0",
       percent_separator=" ",
       percent_decimals=0,
       date_format="%d.%m.%Y",
       time_format="%H:%M",
   ),
   ```

4. **Run the tests**: `uv run python -m pytest -q`.  The suite fails if the
   new locale leaves an id untranslated, if a placeholder differs, or if the
   English mirror drifted from the source.

No dependency is added: the catalogue is JSON, the locale rules are a frozen
dataclass, and the formatting is written by hand.  There is no gettext and no
i18n library.

---

## 4. What is translated and what is not

### Translated (display only)

* the headings and captions of the interface,
* the labels of the form controls,
* the section headings, table headers and prose of the exported report,
* the *labels* of machine-readable values, for example the display of the
  period `month` as "måned" — the value that is sent to the API is still
  `month`,
* the numbers, amounts, percentages and dates that are **rendered**.

### Deliberately **not** translated

These are the contract of the system.  They are reproduced exactly as
recorded, in every locale:

| Not translated | Why |
| --- | --- |
| rule ids (`DET-001`, `MATH-001`) | the rule catalogue is addressed by id |
| rule severities (`preference`, `block_autonomous_ai`) | enum values |
| recommendation categories (`traditional_software`, `hybrid`, …) | enum values |
| decision types, risk categories and bands, statuses | enum values |
| action types (`issue_refund`, `legal_commitment`, …) | enum values |
| policy and report versions (`rules-v2`, `4.0`) | versions of the contract |
| assessment, analysis and report revisions, digests | identifiers of the record |
| ISO-8601 timestamps | stored once, in ISO-8601 |
| machine-readable field names and JSON keys | the API contract |
| the policy texts of the rules engine and of the starting patterns | policy content, not interface text |

A rule id is therefore rendered as `DET-001` in English and in Norwegian, and
the recommendation category is rendered as `traditional_software` in both.
Only the label *around* the value is translated.

The report is an audit artefact: its values are reproduced as recorded, and
only its headings and labels are localized.

---

## 5. The fallback rule

> A string that the active locale does not translate falls back to English.
> The fallback is **reported**, never swallowed.

* `Translator.missing_translations()` lists the ids of the English catalogue
  that the active locale does not translate; `Translator.missing_count()`
  counts them.
* The interface shows the count in the sidebar, under the locale selector:

  ```
  Catalogue: 405 strings · 0 untranslated in this locale
  ```

  When the count is above zero, a warning names the number of strings that
  fall back to English.
* The export endpoint repeats the number in a header
  (`X-Report-Missing-Translations: 0`) next to `X-Report-Locale: nb`, so a
  downloaded artefact states its own locale and the state of its catalogue.
* `Translator.unknown` collects the ids that exist in **no** catalogue.  Such
  an id is rendered as the id itself, never as an empty label, so a broken
  reference is visible on the screen.

The Norwegian catalogue that ships with the product is complete: the count
is zero.  The test suite proves the machinery with a deliberately incomplete
catalogue.

---

## 6. Formatting rules per locale

| | English (`en`) | Norwegian Bokmål (`nb`) |
| --- | --- | --- |
| decimal separator | `.` | `,` |
| thousands separator | `,` | non-breaking space (U+00A0) |
| grouping | 3 digits | 3 digits |
| currency token | ISO code (`NOK`) | symbol (`kr`) |
| currency placement | before the amount (`NOK 1 200,50`) | after the amount (`1 200,50 kr`) |
| decimals of a currency | ISO 4217 minor units (NOK 2, JPY 0) | the same |
| percentage | `21%` | `21 %` |
| date | `2026-10-20` | `20.10.2026` |
| time | `14:05` | `14:05` |

### Money is never a float

The repository stores money as an exact `Decimal`, and the formatter keeps it
that way:

```python
def format_currency(amount, currency, locale):
    exponent = CURRENCY_EXPONENTS.get(currency, 2)      # NOK: 2, JPY: 0
    number = to_decimal(amount).quantize(Decimal(1).scaleb(-exponent), rounding=ROUND_HALF_UP)
    ...
```

* the amount is quantized with `Decimal.quantize`, never with a float, so
  `Decimal("1200.5")` is displayed as `1200.50` and a value with twenty
  digits keeps all of them;
* the number of decimals is a property of the **currency**, not of the
  locale: NOK has two minor units, JPY has none;
* the rounding is the commercial one (`ROUND_HALF_UP`), applied exactly
  once, on the `Decimal`;
* a float that reaches the formatter is converted through its shortest
  round-trip representation (`Decimal(str(value))`), so no binary noise is
  introduced.

### Timestamps

Timestamps are stored in ISO-8601 and are never rewritten.  The formatter
parses the stored value and renders it with the patterns of the locale; the
value in the database is untouched.  The interface shows the recorded time of
an evidence record in the active language (`20.10.2026 14:05` in Norwegian).
The report keeps the stored timestamp verbatim, because the report is an
audit artefact.

### Percentages and scores

A ratio is rendered as a percentage with the separator of the locale
(`21%` / `21 %`).  The rounding is half-even, the same rule Python's
`"{:.0%}"` format uses, so a localized percentage and a formatted one never
disagree.

The suitability score and the evidence coverage of a task are rendered with
the same rules in the interface, so a score never changes shape when the
locale changes.

---

## 7. The exported report

The report endpoint takes the locale of the export:

```
GET /api/v1/assessments/{id}/report?format=markdown&locale=nb
```

* an unknown locale is refused with `422`;
* the default is `en`, and **the English export is byte-identical** to the
  artefact the product shipped before the localization work.  The test
  `test_the_english_report_is_byte_identical_to_the_previous_artefact`
  pins the SHA-256 of both the Markdown and the HTML export;
* the Norwegian export has Norwegian headings, table headers and prose, while
  rule ids, category values, revisions and timestamps are unchanged;
* the HTML export declares its language (`<html lang="nb">`).

The report renderer takes the locale as an optional argument
(`render_markdown(detail, proposals, provenance, locale="en")`), so the
existing calls keep their behaviour.

---

## 8. Accessibility

Localization must not weaken the accessibility work of `docs/accessibility.md`:

* **the heading structure is unchanged.**  The headings keep their levels;
  only their text is translated.  A longer translation can make a heading
  wrap onto two lines; that is a layout effect, not a hidden one, and the
  heading levels are untouched.
* **no control lost its label.**  Every label is a module constant with a
  visible text, and the locale selector itself is a labelled `selectbox`.
* **the contrast overlay is untouched.**  `ui/accessibility.py` was not
  modified; the stylesheet is byte-for-byte the one that was measured.
* the browser-free accessibility checks of `tests/test_ui_accessibility.py`
  still pass, and `tests/test_localization.py` runs them again.

### A known limitation: the tab strip

The ten tabs of the assessment view (`1 · Intake` … `10 · Portfolio`) are
**not** localized.  `tests/test_ui_accessibility.py::test_tab_labels_are_unique_and_ordered`
pins the literal English tab labels in the source of `ui/streamlit_app.py`,
because that audit reads the labels from the source.  Localizing the tab
strip therefore requires extending that audit to resolve the tab labels
through the catalogue first; until then the tab strip stays English while
every panel it opens is localized.

The same applies to the label of the tab strip itself: the navigation radio
in the sidebar *is* localized, because its options are not pinned by the
audit.

### Another known limitation: the label constants are process-global

`install_labels` replaces the `LABEL_*` constants of the UI modules in
place.  In a single-session local demo that is exactly right; two browser
sessions in different locales at the same time would race on the constants.
A multi-session deployment should resolve the labels per render instead.

---

## 9. Honesty

* **No machine translation was used.**  Every Norwegian string was written by
  hand by the author of the code, with the English text in front of them.
* **The Norwegian catalogue is a starting point, not a reviewed
  translation.**  It has not been read by a native speaker, and it has not
  been reviewed by a Norwegian domain expert.  Before the product is used in
  Norwegian, the catalogue should be reviewed by a native speaker, and the
  wording of the risk and decision language in particular should be checked
  against how Norwegian organizations actually speak.
* The English catalogue is the source of truth for the wording, and it is
  the one that is byte-identical to the artefact the product shipped before.
* The localization work covers the user interface and the exported report.
  The API's JSON responses keep their machine-readable English field names
  and are not localized.
