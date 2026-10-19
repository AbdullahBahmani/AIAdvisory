# ADR-0013: PDF export is not MVP-critical

**Date:** 2026-10-20
**Status:** Accepted
**Answers:** `main-planner.md` §13 open decision "whether PDF is MVP-critical".

## Context

The blueprint listed PDF export as optional after the report *content* was correct, and made the
same distinction for Markdown versus HTML: "Full Markdown or HTML assessment report export with
evidence and caveats" was the requirement; PDF was explicitly not in it.

Both Markdown and HTML exports are implemented, share one internal section builder, escape
user and model text, label facts/estimates/assumptions, mark unverified or stale AI proposals,
and refuse to export a stale analysis (HTTP 409). The HTML attachment ships with a restrictive
Content-Security-Policy.

PDF is a *presentation* concern layered on top of that content. Nothing about the report's
truthfulness, its labelling, or its staleness rules depends on the output format. The open
question is whether adding a PDF pipeline is worth the dependency, the rendering surface and the
risk of producing a document that no longer matches the Markdown/HTML export byte-for-byte in
meaning.

A second consideration is the security surface. PDF generation normally means either a headless
browser (a large, network-capable, frequently-CVE'd dependency) or a PDF library (a new
dependency that must then be covered by the dependency-scanning gate). Neither is a small
addition, and the repository has an explicit rule that a report must never be a stale or
misleading artefact.

## Decision

**PDF export is not MVP-critical and is deferred.** The supported export formats remain Markdown
and HTML.

1. The product does not ship a PDF pipeline, and no PDF dependency is added.
2. The HTML export remains the printable format: an operator may print it to PDF from a browser.
   This is stated plainly as *an operator convenience, not a validated PDF pipeline* — it is not
   byte-reproducible, not guaranteed to preserve layout, and not covered by the report
   regression tests.
3. If PDF export is later built, it must satisfy these conditions **before** it is called a PDF
   export:
   - it renders from the **same internal section builder** as the Markdown and HTML exports, so
     the three formats cannot drift in meaning;
   - it is covered by the existing snapshot/golden report tests, so a content change in any
     format fails the suite;
   - it carries the same staleness refusal and the same fact/estimate/assumption labelling;
   - it introduces no new dependency without the dependency-scanning gate being satisfied, and
     any new dependency is justified in an ADR;
   - it is generated server-side with no network access during rendering, and untrusted text is
     escaped rather than rendered as active content.

## Consequences

- The dependency surface and attack surface of the product stay small.
- The report's truthfulness guarantees are not diluted by a third rendering path.
- Anyone needing a PDF today uses the HTML export and prints it, with the caveat above clearly
  stated. That is an honest limitation rather than a hidden gap.

## References

- `main-planner.md` §2 (next release: "PDF export and more polished reports"), §10 Phase 4
  (P4-01, P4-02), §13 (open decisions).
- `app/reporting/render.py`, `docs/demo.md`, `docs/evaluation.md`.
- ADR-0004 (read-only reports and offline evaluations).
