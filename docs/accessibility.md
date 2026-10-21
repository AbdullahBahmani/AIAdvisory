# Accessibility evaluation of the Streamlit UI

**This is an evaluation, not a conformance audit.** It reports what was
measured, what was changed and what is still unknown. It does **not** claim
that the application conforms to WCAG, in whole or in part, and no assistive
technology was used to test it. The reader must judge the result.

Scope: the Streamlit UI in `ui/` (the `Assessments`, `Workspace` and
`Portfolio` views and the ten assessment tabs). It covers the accessibility
half of planner item P7-10; the attachment and notification halves are out of
scope here, and localization was not evaluated. The evaluation predates the
Phase 10 work (the PDF download button, the measurement-ingestion form and
the net-present-value display), so **those additions are not covered by the
axe scan** and must be re-evaluated before they are claimed to be accessible.

---

## 1. Method and tooling

| Tool | Version | Use |
| --- | --- | --- |
| Streamlit | 1.66.0 | The application under test |
| FastAPI | 0.115+ | Local synthetic API the UI was driven against |
| Playwright | 1.64.0 | Headless Chromium 156 driver for the DOM |
| axe-core | 4.4.3 | Automated rule engine injected into the rendered page |
| Google Chrome | `--headless=new` | First-pass DOM capture (see below) |

The evaluation was performed against a **running** application, not against
the source:

1. `alembic upgrade head` on a local SQLite database, then
   `uvicorn app.main:app` with `ADVISORY_DEMO_MODE=1` on `127.0.0.1:8123`
   and `streamlit run ui/streamlit_app.py` on `127.0.0.1:8124`.
2. The local API was seeded with synthetic data (an assessment with two
   workflow steps, two tasks, a deterministic analysis, discussion comments,
   an evidence record, a value scenario and a started PoC with one
   observation) so that every panel, table, code block and status badge
   rendered.
3. A Playwright script opened the UI, waited for hydration, dumped the
   rendered DOM for each screen and injected axe-core to collect rule
   violations.
4. Every tab (`1 · Intake` … `10 · Portfolio`) and both sidebar views
   (`Workspace`, `Portfolio`) were opened in turn and scanned separately,
   because Streamlit only renders the selected tab panel into an active
   `role="tabpanel"`.
5. The whole sequence was repeated with Streamlit's dark theme
   (`streamlit run --theme.base=dark`) so that theme-dependent rules could
   be checked in both palettes.

`--dump-dom` with Chrome's `--headless=new` was tried first and produced
only the pre-hydration shell (Streamlit is a client-rendered React
application and the websocket never completed under a virtual time budget),
so Playwright was used for the real measurements.

### Result summary

| Run | axe violations | affected nodes |
| --- | --- | --- |
| Before, light theme | `color-contrast` (serious), `heading-order` (moderate), `scrollable-region-focusable` (moderate) | 108 |
| Before, dark theme | `color-contrast` (serious, 2 nodes), `heading-order` (moderate), `scrollable-region-focusable` (moderate) | 16 |
| After, light theme | none | 0 |
| After, dark theme | none | 0 |

---

## 2. Defects found

Severity is axe's own impact rating unless stated otherwise.

### 2.1 Heading structure — moderate, fixed

* `st.title` renders an `h1`, `st.header` an `h2` and `st.subheader` an
  `h3`. The application used `st.subheader` for its **top-level** sections
  ("Assessments", "Organization workspace", "AI opportunity portfolio"), so
  the document outline jumped from `h1` straight to `h3` — flagged as
  `heading-order` on every screen (13 nodes).
* Six of the ten tab panels (`Intake`, `Discovery`, `Workflow`, `Analysis`,
  `AI advisor`, `FDE review`) rendered **no heading at all**, so a user
  navigating by heading could not tell which tab was open.
* The workflow name was written as `st.markdown("### …")` (an `h3`) inside a
  tab that had no heading of its own, and the analysis sub-section used
  `st.subheader` (an `h3`) at a sub-section level.

**Fix.** A single helper, `ui.accessibility.heading(st, text, level)`, is now
the only way the UI emits a heading. The outline is `h1` (page) → `h2`
(top-level view) → `h3` (tab panel) → `h4` (detail). Every tab panel starts
with exactly one `h3`; the ten tab labels and their order are unchanged.
Text that reaches a heading is markdown-escaped, so a workflow name can no
longer inject markup into the outline.

### 2.2 Text contrast — serious, fixed

All of these are Streamlit's own default palette; the application chooses
none of these colours, which is why the fix is a theme overlay rather than a
change of individual widgets.

| Element | Measured | Nodes | Fixed value | Measured after |
| --- | --- | --- | --- | --- |
| `st.caption` (60 % opacity) | `#83858c` on `#ffffff` = 3.69:1 | 33 | opacity 0.67 | 4.52:1 |
| `st.success` body text | `#158237` on `#e9f9ee` = 4.49:1 | 10 | `#158137` | 4.55:1 |
| Markdown/code highlight | `#ff4b4b` on `#f8f9fb` = 3.13:1 | 23 | `#d03d3d` | 4.51:1 |
| JSON viewer tokens | 3.15–3.87:1 | 10 | per-token corrections | 4.50–4.55:1 |
| Primary button | `#ffffff` on `#ff4b4b` = 3.30:1 | 1 | `#d63f3f` | 4.52:1 |
| Multiselect value chips | `#ffffff` on `#ff4b4b` = 3.30:1 | 2 | `#d63f3f` | 4.52:1 |
| Slider value label | `#ff4b4b` on `#ffffff` = 3.30:1 | 1 | `#d63f3f` | 4.52:1 |

The corrected values are the smallest change that reaches 4.5:1, so the
original palette is preserved as far as the ratio allows. In the dark theme
the corresponding values were already above the ratio (for example the
slider label `#ff4b4b` on `#0e1117` = 5.72:1) and are left unchanged.

**Fix.** `ui/accessibility.py` computes corrected colours with WCAG
arithmetic (`contrast_ratio`, `accessible_variant`, `minimum_opacity`) and
emits a stylesheet that `ui/streamlit_app.py` injects at start-up. The
stylesheet uses `light-dark()`, because Streamlit sets `color-scheme` on its
app container; the same sheet is therefore correct in the light and in the
dark theme, and a browser without `light-dark()` falls back to the first
(light) declaration.

The JSON viewer is the awkward case: `react-json-view` paints its tokens
with **inline** styles, so a stylesheet rule cannot win without
`!important`, and the inline colour strings differ between the light and the
dark theme. The rules therefore match the exact inline declaration of each
token, so the dark theme's own (already passing) palette is left untouched.

### 2.3 Keyboard access to the report preview — moderate, fixed

`st.code` renders a scrollable `<pre>` with `overflow: auto` and no tab
stop, so the report preview could not be scrolled with the keyboard
(`scrollable-region-focusable`).

**Fix.** The inner `<pre>` no longer scrolls; the surrounding `st.code`
container — which Streamlit already gives `tabindex="0"` — becomes the
scroll region, so the content is reachable and scrollable with the arrow
keys. A caption now tells the user to focus the block and use the arrow
keys.

### 2.4 Data tables have no accessible name — serious, partly fixed

`st.dataframe` renders a `role="grid"` with column headers, but the grid
itself has no accessible name and no row headers (the first column is not
marked as a row header).

**Partly fixed.** A visible caption line now precedes each table
("Table: number of assessments in the organization by status.", "Table:
planning priority, evidence coverage and PoC outcomes per opportunity."),
so the table is introduced in reading order.

**Not fixed.** Streamlit's `st.dataframe` accepts no `aria-label` and emits
no row headers. Naming the grid would mean replacing it with `st.table`
(a static HTML table with `<th scope="col">` but no sorting, no column
controls and a much worse experience for the wide portfolio table). That
trade-off is left to the product owner; the evaluation does not make it.

### 2.5 The tab list has no accessible name — moderate, not fixed

The `role="tablist"` element has no accessible name, and only the
**selected** tab carries `aria-controls`; the nine inactive tabs have none.
Both are inside Streamlit's react-aria tab implementation, and `st.tabs`
exposes no hook for either. Fixing it would mean abandoning `st.tabs` for a
hand-built tab widget.

### 2.6 Inactive tab panels depend on `inert` — informational, not fixed

Streamlit renders the nine inactive tab panels into the DOM but marks them
`inert="true"`, which removes them from the accessibility tree and from the
tab order. That is the behaviour the ARIA tabs pattern expects (the content
of an unselected tab is not exposed), and it is what the audit confirmed: no
inactive panel was reachable or announced. It does mean the application
depends on `inert` support (Chrome 102+, Safari 15.5+, Firefox 112+). In
older browsers the hidden panels would be focusable and readable, which
would be a real problem; nothing in the application can change that.

### 2.7 Repeated accessible names inside Streamlit widgets — minor, not fixed

Every expander toggle is named "Open" and every password field's reveal
button is named "Show password". These names come from Streamlit's own
components, not from the application, and the application cannot override
them without abandoning those widgets.

### 2.8 Things that were checked and found to be in order

* Every input, select, slider, checkbox, button and download control has a
  non-empty text label, and the label is exposed to assistive technology
  (Streamlit duplicates the visible label into `aria-label`).
* No control was left without an accessible name; no duplicate names were
  introduced by the application itself.
* The document language is `lang="en"` and the page title is set.
* Streamlit supplies a "Skip to main content" link and exposes the page
  header and the main region as landmarks.
* **The sidebar is a labelled region, not a navigation landmark.** It is
  rendered as `<section aria-label="Sidebar">`, and the only way to move
  between the three views is the "Navigation" radio group inside it. There
  is therefore no landmark or link list a keyboard user can jump to in
  order to change view. This is Streamlit's own markup (`st.sidebar`); the
  application cannot change an element's ARIA role from Python, so it is
  reported rather than fixed.
* Streamlit's `st.info` / `st.success` / `st.warning` alerts carry
  `role="status"` / `role="alert"`, so they are announced when they change.
* **No state is signalled by colour alone.** Evidence state is the words
  "Verified" / "Unverified" / "Stale"; approval stages are words; PoC
  status and "target met" / "target not met" are words. The application
  contains no colour literal of its own at all, and a test now enforces
  that.
* The application renders no images, so there is no missing alt text; the
  only graphic is the page icon.
* Focus is visible: Streamlit draws a focus ring (`box-shadow`) on the
  focused control, and axe's focus rules found nothing.
* Keyboard order follows the visual order because the DOM order matches it.

---

## 3. What changed in the application

| File | Change |
| --- | --- |
| `ui/accessibility.py` (new) | Heading helper, WCAG colour arithmetic, theme-adaptive stylesheet, markdown escaping |
| `ui/streamlit_app.py` | Injects the stylesheet; one `h2` for the view, one `h3` per tab panel, `h4` for details |
| `ui/collaboration.py` | Heading levels parameterised so the same panel works as a view (`h2`/`h3`) and as a tab (`h3`/`h4`); caption for the status table |
| `ui/portfolio.py` | Same heading parameterisation; caption for the portfolio table |
| `ui/reporting.py` | Same heading parameterisation; caption explaining keyboard scrolling of the preview |
| `tests/test_ui_accessibility.py` (new) | 50 checks that run by default, plus one opt-in browser scan (see below) |

No Streamlit feature was removed and the framework was not abandoned.

---

## 4. Automated checks

`tests/test_ui_accessibility.py` runs in the default `pytest -q` run and
needs **no browser**. It enforces the properties the application code is
responsible for:

* **Labels** — every `st.*` input widget in `ui/` must have a non-empty text
  label. Literal labels, f-strings and labels held in variables (including
  loop and comprehension variables) are all resolved and checked.
* **Heading structure** — headings may only be emitted through
  `accessibility.heading()`; the page must contain exactly one `st.title`
  (the `h1`); heading levels may never jump by more than one inside a
  function; the tab strip must keep its ten labels, their order and their
  uniqueness; every tab panel must start with a level-3 heading.
* **No colour-only signalling** — the UI modules must not contain a colour
  literal, and the evidence, PoC and approval states must be rendered as
  words.
* **No unlabelled visuals** — the UI must not render images or unaudited
  raw HTML.
* **Contrast** — the WCAG arithmetic is checked against known values, and
  every colour the generated stylesheet installs is re-checked against the
  background it sits on (light and dark), including every JSON token.
* **Coverage of the measured failures** — the stylesheet must contain a
  rule for each Streamlit element that was measured as failing, and it must
  be theme-adaptive.

A **browser-based** check is included but **opt-in**; it skips cleanly when
no instance, browser or axe build is present, so the default test run never
needs one. It walks the ten tabs and both sidebar views, exactly as the
evaluation did, and fails if any axe rule is violated on any of them:

```bash
# terminal 1: API and UI
ADVISORY_DEMO_MODE=1 uvicorn app.main:app --host 127.0.0.1 --port 8000
streamlit run ui/streamlit_app.py --server.address=127.0.0.1 --server.port=8501

# terminal 2: opt-in DOM scan
A11Y_UI_URL=http://127.0.0.1:8501/ \
  uv run --no-project --with playwright --with axe-core-python \
  python -m pytest -q tests/test_ui_accessibility.py -k axe
```

`uv run --with` keeps the tools out of the project dependencies. The scan
was verified to fail when the stylesheet injection is removed, so it is a
real regression test and not a tautology.

Result of the full suite on this branch: **198 passed, 17 skipped** (the 16
pre-existing skips plus the opt-in browser test).

---

## 5. What is **not** verified

This is the most important section of this document.

* **No assistive technology was used.** Nothing was tested with NVDA,
  JAWS, VoiceOver, Narrator, TalkBack or Orca. A screen-reader user may
  still find the tab strip, the code preview or the data grids unusable.
* **No screen-reader pass** was made over any screen, in either theme.
* **No WCAG conformance claim.** axe-core implements a subset of the
  criteria, its rules are not the criteria, and passing axe is neither
  necessary nor sufficient for conformance. No conformance level (A, AA or
  AAA) is claimed for any part of this application.
* **No formal audit** by an independent accessibility specialist, and no
  statement of conformance was issued.
* **Only Chromium was tested.** Safari and Firefox were not run. The
  `light-dark()` fallback for older engines is by design (the first
  declaration wins) but was not verified on those browsers, and the
  `inert` behaviour of the hidden tab panels (§2.6) was not verified
  outside Chromium.
* **Only Streamlit's default light and dark themes were tested.** A custom
  operator theme (`.streamlit/config.toml`) would change the colours the
  overlay corrects, and the overlay's rules are matched against
  `data-testid` attributes and, for the JSON viewer, against exact inline
  style strings. A Streamlit upgrade could therefore invalidate them. The
  automated checks assert that every rule is still present, and the opt-in
  browser test re-checks the rendered result, but neither proves the
  overlay matches a future Streamlit release.
* **No keyboard-only walkthrough** of every form was performed. The audit
  confirmed that the code preview became keyboard-scrollable, but the
  complete tab order through the ten forms, the behaviour of the disabled
  submit buttons and the focus behaviour of the tab strip were not
  exercised by hand.
* **Reflow and zoom were not tested** (200 % / 400 % zoom, 320 CSS pixel
  viewport, text spacing overrides).
* **Not all states were exercised.** The evaluation used a seeded,
  populated assessment. Empty, loading, error and long-content states were
  only partially covered, and long values were not stress-tested for
  truncation, overlap or focus order.
* **Localization was not evaluated.** All strings are hard-coded English;
  the planner's localization half of P7-10 is untouched and remains
  unverified.
* **Performance and assistive-technology cost of the overlay** (an extra
  `<style>` element with roughly a dozen rules) was not measured.

---

## 6. Reproducing the evaluation

```bash
uv sync --extra dev --extra postgres
uv run --no-sync python -m alembic upgrade head

ADVISORY_DEMO_MODE=1 uv run --no-sync uvicorn app.main:app --host 127.0.0.1 --port 8000 &
uv run --no-sync streamlit run ui/streamlit_app.py \
  --server.address=127.0.0.1 --server.port=8501 --server.headless=true &

# scan every tab and both sidebar views with axe-core
A11Y_UI_URL=http://127.0.0.1:8501/ \
  uv run --no-project --with playwright --with axe-core-python \
  python -m pytest -q tests/test_ui_accessibility.py -k axe
```

The opt-in pytest check performs the same per-screen axe scan that produced
the measurements in this document, so the evaluation is reproducible from the
repository. The ad-hoc Playwright script used to capture the raw DOM dumps
and the computed colours quoted above was a research harness and is not
part of the repository; the permanent, reproducible part is the opt-in
pytest check and the measurements recorded here.
