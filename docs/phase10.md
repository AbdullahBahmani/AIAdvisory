# Phase 10 — the product gaps that were code, not procurement

Phase 10 closes the four items of the planner that turned out to be
**buildable in this repository** rather than dependent on an IdP, a managed
cluster, a vendor or a field pilot:

| Item | What it is | Where it lives |
| --- | --- | --- |
| Architecture-pattern diagrams | A deterministic picture of the simplest adequate starting pattern for each task | `app/reporting/diagrams.py` |
| Server-side PDF export | The report as a real PDF, rendered from the same view model as the other exports | `app/reporting/pdf.py` |
| Advisory net present value | NPV, IRR and discounted payback for a value scenario | `app/services/npv.py` |
| Measurement ingestion | Measurements taken by a production system, stored faithfully and cited by scenarios | `app/services/metrics.py` |

None of this adds an approval, an external infrastructure dependency, or a
claim that a modelled number is a measurement. **Phase 10 closes no Phase
5–9 release gate**, and `ADVISORY_DEPLOYMENT_MODE=production` still refuses
to boot.

---

## 1. Architecture-pattern diagrams

### 1.1 What a diagram is

A diagram is drawn from the recorded analysis: the recommendation category
decides the shape of the flow, and the triggered rules decide which control
gates are drawn. Nothing in a diagram is inferred from an image, and nothing
is invented.

The product rule stays visible in the picture:

- a **deterministic** path is drawn *without* a model, and carries an explicit
  "no model in this path" annotation;
- a **human approval** is drawn as a human step, never as a box the model
  writes into;
- a **more-discovery** outcome is drawn as an unresolved evidence gap — no
  architecture is committed at all;
- the **required controls** are drawn as a *gate bar* over the whole flow, so
  a reader sees at once that the control gates the pattern rather than one
  step of it.

### 1.2 One specification, three renderers

`app/reporting/diagrams.py` owns one specification (`Diagram`: nodes, edges,
caption, notes) and three renderers:

| Export | Renderer | Output |
| --- | --- | --- |
| Markdown | `render_mermaid` | a `flowchart LR` fence plus a plain-text description |
| HTML | `render_svg` | an inline SVG fragment with no external resource |
| PDF | `_draw_diagram` in `app/reporting/pdf.py` | PDF vectors drawn with the same spec |

Because all three render the same specification, the three artefacts cannot
disagree about the picture. The HTML SVG is inline and the PDF draws its
vectors, so neither export references an external file and the report's
`default-src 'none'` Content-Security-Policy still holds.

### 1.3 Determinism and safety

- The same analysis always produces the same picture. The control set is
  de-duplicated and sorted, so the order never depends on a dictionary.
- Untrusted text is flattened to a single line before it is drawn. A task id
  containing a newline could otherwise break out of the Markdown block quote
  in the export; it is now impossible.
- A Mermaid label cannot break out of the node syntax, and an SVG label is
  HTML-escaped.

---

## 2. The PDF export

### 2.1 Why the deferral was lifted

ADR-0013 deferred PDF export and set five conditions for building it. All
five are met:

1. It renders from the **same internal section builder** (`build_sections`) as
   the Markdown and HTML exports, so the three formats cannot drift in
   meaning.
2. It is covered by the report regression tests (`tests/test_pdf_export.py`),
   so a content change in any format fails the suite.
3. It carries the same staleness refusal (HTTP 409) and the same
   fact/estimate/assumption labelling.
4. The new dependency is justified in **ADR-0015** and sits inside the
   dependency-scanning gate: it is locked in `uv.lock` and installed by the
   Docker build through the same `uv sync --locked` path as every other
   dependency.
5. It is generated server-side with **no network access** during rendering,
   and untrusted text is escaped rather than rendered as active content.

### 2.2 Why ReportLab and not a headless browser

A PDF pipeline normally means either a headless browser — a large,
network-capable, frequently-CVE'd dependency that would also make rendering a
network-capable operation — or a PDF library. The browser route is rejected.
ReportLab is a pure-Python generator with no system libraries, no browser, no
native extension and no network access.

### 2.3 The character-coverage rule

The PDF embeds the Bitstream Vera font that ReportLab ships, which covers
Latin, Greek and Cyrillic. A character outside that range is replaced by a
visible placeholder (`?`) and **counted**:

- the count is reported in the `X-PDF-Unrenderable-Characters` response
  header, and
- the PDF itself carries a visible note saying how many characters could not
  be drawn and that the Markdown export holds the complete text.

The loss is never silent. The Markdown export remains the complete record.

### 2.4 What the PDF is not

It is not a rollout authorization, not a substitute for the human decision and
not a different report. Every page carries the "advisory only · not rollout
authorization" footer, and the architecture diagram is *drawn* rather than
embedded, so the file references nothing external.

---

## 3. The advisory net present value model

### 3.1 The three rules

| Rule | Why |
| --- | --- |
| A discount rate **without a stated source is refused** | A rate that does not say where it came from looks authoritative and is not. |
| A missing input produces **unavailable with a reason**, never a zero | A missing discount rate is not a discount rate of zero. |
| The arithmetic is `Decimal` throughout | A float never touches a monetary value. |

### 3.2 What is and is not modelled

Modelled: the initial cost, the recurring net saving per period, a horizon,
and a discount rate. **Not** modelled: tax, inflation, an adoption curve,
financing cost, a residual value, or an observed ROI. The disclaimer travels
with every result and states this.

The periodic discount rate needs a twelfth root, which is irrational in
general, so it is computed with a high-precision transcendental approximation
and the result is reported as a number with a stated precision — never as an
exact value.

### 3.3 Per-measure state

The internal rate of return does **not** need a discount rate — it is the rate
the project earns — so it is reported even when the net present value is
unavailable. Each measure carries its own reason:

| Measure | Needs | State when it is missing |
| --- | --- | --- |
| Net present value | horizon + discount rate | `unavailable` with a reason |
| Internal rate of return | horizon only | `null` with its own reason |
| Discounted payback | horizon + discount rate | `null` with its own reason |

A cash flow that never changes sign, or that changes sign more than once, has
no single internal rate of return, and the model says so instead of returning
a number.

---

## 4. Measurement ingestion

### 4.1 What is delivered, and what is not

Phase 10 delivers the **receiving end**: a tenant-scoped, role-gated endpoint
that stores what a production system reported. It does not deliver the
connectors, the automated pipeline, the cross-currency logic, the DLP or the
encryption at rest. Those are not code the product can decide on its own.

### 4.2 The four properties

| Property | How it is enforced |
| --- | --- |
| It never decides anything | Nothing in the ingestion path feeds the rules engine, the scoring or the recommendation precedence. A measurement is evidence about a quantity, not a conclusion. |
| It is never rewritten | The value is stored exactly as reported, as an exact decimal string, bound to the assessment revision that was current when it arrived. |
| It is never counted twice | A unique key on (assessment, source system, metric key, observed moment) makes a repeated delivery a no-op, and the response *reports* the count. |
| A moment is in one reference frame | A moment without a timezone is **refused** rather than silently read as UTC. A stored moment is normalised to UTC, so the same instant in two timezones is one measurement. |

### 4.3 Citations

A value scenario may cite a measurement. The citation is a *claim*, and it is
checked:

- a citation to a measurement that does not exist, or that belongs to another
  assessment, is **refused** with HTTP 422 — a scenario must never carry a
  claim that cannot be checked;
- when the cited measurement names a quantity the scenario also states, and
  the two disagree, the **disagreement is reported** with the scenario.

The citation is never resolved silently. A scenario that says `100` cases a
month while the measurement says `420` shows the contradiction to whoever
reads it.

### 4.4 Staleness

A measurement is bound to the revision that was current when it arrived. When
the assessment is revised, the measurement becomes **stale** and says so,
using the same convention as the evidence records. The measurement is not
deleted and not rewritten; the revision it belongs to is simply no longer
current.

---

## 5. The accessibility re-measurement

The Mermaid fence in the Markdown report meant the report preview in the UI
contained **syntax-highlighted tokens** for the first time. The axe scan was
repeated against the Phase 10 build, in the light and the dark theme, and the
previous "0 violations" result did **not** reproduce.

The scan was also re-run against the code *before* Phase 10, in the same
environment, and it found the same 14 findings. They were therefore
**pre-existing gaps that the previous result had not actually detected**, not
a regression introduced by Phase 10 — but the Mermaid fence made them visible,
and they are now fixed:

- the code block's `keyword`, `string`, `operator` and `punctuation` token
  colours;
- the caption, which was measured against the page but is also rendered on
  the darker sidebar, where it fell to 4.27:1;
- the file-uploader hint;
- the label of a disabled widget.

The result is **0 application violations in the light theme and 0 in the dark
theme**. One framework-owned finding remains — Streamlit renders the
file-uploader dropzone as a focusable element with `role="presentation"` —
and the scan now reports it and allow-lists it by name, with a check that the
allow-list cannot go stale. See [accessibility.md](accessibility.md).

---

## 6. What is still open

Phase 10 closed four items and left everything else exactly where it was.

| Still open | Why it is not code |
| --- | --- |
| Configurable scoring weights | ADR-0012 keeps the weights a versioned constant. Recalibration needs field evidence and an ADR, not a settings screen. |
| Cross-currency logic | There is no sourced rate and no rate-date policy, so mixed currency stays refused. |
| DLP and encryption at rest | A prerequisite decision that has not been made. A consent-verification claim cannot be manufactured by an ingestion endpoint. |
| Richer collaboration UX | A product-design increment, not a gap in the model. |
| Excel export | Deliberately deferred with PDF (ADR-0013); it was not requested. |
| Every Phase 5–9 release gate | An IdP, a managed cluster, a vendor, a field pilot and a named approver. |

---

## 7. References

- `app/reporting/diagrams.py`, `app/reporting/pdf.py`, `app/services/npv.py`,
  `app/services/metrics.py`, `migrations/versions/0015_observed_metrics.py`.
- `tests/test_diagrams.py`, `tests/test_pdf_export.py`, `tests/test_npv.py`,
  `tests/test_metrics_ingestion.py`.
- ADR-0012 (fixed value and score thresholds), ADR-0013 (the deferral that
  ADR-0015 lifts), ADR-0015 (the server-side PDF export).
- `main-planner.md` §2 and the Phase 10 section.
