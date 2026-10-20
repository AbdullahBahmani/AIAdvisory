# ADR-0015: a server-side PDF export pipeline

**Date:** 2026-10-21
**Status:** Accepted
**Supersedes:** ADR-0013 (PDF export is not MVP-critical) — only the *deferral*; the five
conditions ADR-0013 set for building a PDF export are carried over and are met.
**Answers:** `main-planner.md` §2 (next release: "PDF export and more polished reports"),
§10 Phase 7 P7-10, Phase 8 P8-10.

## Context

ADR-0013 deferred PDF export and set five conditions that a PDF pipeline would have to satisfy
before it could be called a PDF export. The owner has now asked for the missing export formats, so
the deferral is lifted and this record states how the conditions are met.

The dependency question ADR-0013 raised is real and has to be answered here. A PDF pipeline
normally means either a headless browser — a large, network-capable, frequently-CVE'd dependency
that would also make rendering a network-capable operation — or a PDF library. The headless
browser route is rejected: rendering must stay offline and must not add a browser to the
dependency and attack surface of a product that already refuses to boot in production mode.

The library route is taken: **ReportLab** (`reportlab>=4.2,<5`), a pure-Python PDF generator with
no system libraries, no browser, no network access and no native extension. It is the smallest
dependency that can produce a real PDF with wrapped tables, embedded fonts and drawn vector
graphics.

A second constraint comes from the product's own rule: **unknown stays unknown, and text is never
silently lost**. A PDF embeds a font, and a font has a fixed set of glyphs. The export must not
quietly drop a character that the font cannot draw, because a report that silently loses content
is exactly the failure mode the whole product exists to prevent.

## Decision

**The product ships a server-side PDF export, rendered with ReportLab.**

1. **One view model, three renderers.** The PDF is built from `build_sections()`, the *same*
   internal section builder the Markdown and the HTML exports use. The three artefacts cannot
   drift in meaning, because they are the same statements in three presentations. This satisfies
   the first condition of ADR-0013 directly.

2. **No network, no browser, no external resource.** The PDF is produced by the API process. The
   architecture diagram is *drawn* with PDF primitives from the same specification the SVG
   renderer uses, so the PDF embeds no image and references no external file.

3. **Untrusted text is escaped, never rendered as active content.** Every user and model string
   is XML-escaped before it reaches a ReportLab paragraph. The PDF carries no script, no
   `OpenAction`, no embedded file and no launch action; the export tests assert this.

4. **The staleness refusal is unchanged.** A PDF can only be rendered from a current analysis.
   A missing or stale analysis is refused with HTTP 409, exactly as for the other formats.

5. **A character the font cannot draw is replaced by a visible placeholder and *counted*.** The
   export uses the Bitstream Vera font that ReportLab ships, which covers Latin, Greek and
   Cyrillic. A character outside that range becomes `?`, the count is reported in the
   `X-PDF-Unrenderable-Characters` response header, and the PDF itself carries a visible note
   saying how many characters could not be drawn and that the Markdown export holds the complete
   text. The loss is never silent. The Markdown export remains the complete record.

6. **The advisory framing is carried over unchanged.** The "advisory only · not rollout
   authorization" notice is on every page, the fact/estimate/assumption labelling is identical to
   the other exports, and the diagram keeps its "starting pattern, not an implementation" wording.

7. **The dependency is justified and is inside the scanning gate.** ReportLab is a new
   runtime dependency. It is locked in `uv.lock`, it is installed by the Docker build through the
   same `uv sync --locked` path as every other dependency, and it is therefore covered by the
   local dependency-vulnerability scanner and by the lock-drift check.

## Consequences

- The export formats are Markdown, HTML and PDF. The HTML-to-PDF browser print is no longer the
  only way to obtain a PDF, and the caveat in ADR-0013 about that path no longer applies to the
  supported export.
- The report's truthfulness guarantees are *not* diluted: the PDF is a third rendering of one
  view model, not a third source of statements.
- The dependency surface grows by one pure-Python package. That is the price of the format, and
  it is paid knowingly rather than by accident.
- A character outside Latin/Greek/Cyrillic is not drawable in the PDF. This is a **known
  limitation**, stated in the export itself, and it does not affect the Markdown export.
- The PDF font is a subset, so a reader needs no external font — the glyphs are embedded in the
  file.

## References

- `main-planner.md` §2, §10 (P7-10, P8-10), §13.
- `app/reporting/pdf.py`, `app/reporting/render.py`, `app/reporting/diagrams.py`,
  `app/api/reports.py`, `tests/test_pdf_export.py`.
- ADR-0004 (read-only reports and offline evaluations), ADR-0013 (the deferral this lifts).
