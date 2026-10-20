# Evidence attachments (P8-10) — local pilot subset

**Status:** implemented as a *local pilot subset*, not an enterprise release. This
document states exactly what the attachment capability does, what it deliberately
does not do, and which decisions are still open.

Phase 9 lists "evidence file attachments" as out of scope because they need a
storage, DLP and residency decision that has not been made
(`docs/phase9.md`, "Out of scope for Phase 9"). This implementation takes the
smallest safe subset of P8-10 that does **not** pre-empt that decision: local
filesystem storage, outside every web-served directory, with hard validation,
tenant isolation, inert downloads and auditable provenance. It does not claim
DLP, malware scanning, data residency, object storage or enterprise approval.

## Design

### Storage

- A dedicated **storage root**, configurable with `ADVISORY_ATTACHMENT_ROOT`.
  The default is `<repo>/.advisory/attachments` (git-ignored). The root is
  created `0700`; blobs are `0600`.
- The root is **never** mounted as static content. The application registers no
  `StaticFiles` route, so an upload is reachable only through the authenticated
  download endpoint. A test asserts that no route serves the store.
- Layout:

  ```text
  <root>/blobs/<attachment id>              # opaque bytes, no extension
  <root>/records/<assessment id>/<id>.json  # provenance metadata
  <root>/tmp/<id>.part                      # staging file for atomic writes
  ```

- The **on-disk name is always a server-generated opaque token**
  (`secrets.token_hex(16)`, 128 bits). The client file name is never used to
  build a path. The blob carries no extension, so even a stray mount could not
  serve it as an executable type.
- Writes are atomic: the blob is written to `tmp/` and `os.replace`d into
  place; the metadata record is written the same way. A failed metadata write
  removes the blob, so an orphan blob is never listed.

### Validation

- **Size**: at most `ADVISORY_ATTACHMENT_MAX_BYTES` (default 10 MiB, hard
  capped at 64 MiB). The body is read with a running ceiling, so an oversized
  or chunked upload is rejected while it streams — it is never buffered in
  full. `Content-Length` is checked first when present. Rejection: `413`.
- **Type allowlist** (`X-Attachment-Content-Type` or the request content type):
  `application/pdf`, `text/plain`, `text/markdown`, `text/csv`,
  `application/json`, `image/png`, `image/jpeg`, `text/html`, `image/svg+xml`.
  Anything else is rejected with `415`.
- **Content sniffing**: the bytes must agree with the declared type (PDF
  header, PNG/JPEG magic, valid JSON, valid UTF-8 text without NUL bytes).
  A mismatch is `415`. This stops a client from claiming `text/csv` for a
  binary payload.
- **File names**: the stored display name is NFKC-normalised, reduced to its
  last path segment, stripped of control/format characters (including bidi
  overrides and zero-width joiners), stripped of leading/trailing dots and
  truncated to 200 characters. Names that are empty after sanitising are
  rejected (`422`). Executable/scriptable extensions (`.exe`, `.js`, `.sh`,
  `.ps1`, `.jar`, …) are rejected even for an allowlisted type (`415`).
- **Empty files** are rejected (`422`).

### Tenant isolation

- Every route resolves the owning assessment through the shared
  `get_or_404` helper, which is organization-scoped: a principal from another
  organization gets `404`, never `403` with an existence hint.
- The stored record also carries the owning `organization_id`; the download and
  delete routes re-check it against the authenticated principal and fail
  closed with `404` if it does not match.
- Attachment ids are unguessable, so the id space is not enumerable.
- Uploads require the `analyst` role; reads require only `viewer`.

### Download safety

Downloads are always **inert**:

- `Content-Type: application/octet-stream` — never the stored type;
- `Content-Disposition: attachment; filename="…"; filename*=UTF-8''…` — the
  offered name is rebuilt from the sanitised *first segment* of the client name
  plus the *allowlisted* extension, so `report.pdf.html` or `payload.exe`
  cannot be reconstructed;
- `X-Content-Type-Options: nosniff`;
- `Content-Security-Policy: default-src 'none'; sandbox; frame-ancestors 'none'`;
- `Cache-Control: no-store` (also set globally by the request telemetry).

An uploaded `.html` or `.svg` is therefore never rendered inline in the
application origin. It is downloaded as an opaque byte stream. The bytes are
returned unchanged, so the SHA-256 digest in the metadata always matches the
served content.

### Provenance and integrity

Each attachment records: id, owning assessment, optional linked evidence id,
the **assessment revision it was bound to**, the sanitised display name, the
media type, the size, the SHA-256 digest, the uploading subject and the
timestamp. The download response echoes the digest and the bound revision.

Staleness follows the existing portfolio convention: an attachment is `stale`
when the assessment revision it was bound to is no longer the current
revision. Editing the assessment therefore marks its attachments stale
exactly as it marks evidence, scenarios and PoC plans stale.

### Retention

- `DELETE /api/v1/assessments/{id}/evidence-attachments/{attachment_id}`
  removes the metadata record **and** the file from disk, and emits a
  `portfolio.attachment_deleted` audit event. A test asserts the file is gone.
- `attachments.purge_assessment(root, assessment_id)` removes every attachment
  of one assessment; `attachments.purge_orphans(root)` removes blobs without a
  record and leftover staging files. Both are operator/maintenance helpers, not
  public endpoints.
- A finalized assessment must be explicitly reopened before attachments can be
  removed, consistent with the rest of the application (finalized is
  read-only).

## API surface

| Method | Path | Notes |
| --- | --- | --- |
| `POST` | `/api/v1/assessments/{assessment_id}/evidence-attachments` | Raw body upload. Returns `201` with the attachment view. |
| `GET` | `/api/v1/assessments/{assessment_id}/evidence-attachments` | List; optional `?evidence_id=` filter. |
| `GET` | `/api/v1/assessments/{assessment_id}/evidence-attachments/{attachment_id}/download` | Inert download. |
| `DELETE` | `/api/v1/assessments/{assessment_id}/evidence-attachments/{attachment_id}` | Removes file and record; `204`. |

Upload request shape: the **body is the raw file**; the original name and the
declared media type travel in headers, because both are untrusted metadata.

```text
POST /api/v1/assessments/<id>/evidence-attachments
Content-Type: text/csv
X-Attachment-Filename: baseline.csv        # percent-encoded UTF-8
X-Attachment-Content-Type: text/csv
If-Match: "3"                              # in pilot mode, as for the siblings

<raw file bytes>
```

The name and media type are deliberately *not* multipart form fields: a
hand-rolled multipart parser would add parsing risk, and a declared name is
never trusted for storage in any case. `X-Attachment-Filename` is
percent-encoded so that non-ASCII names survive HTTP header encoding.

Mutating routes follow the sibling conventions exactly: `require_role`,
`check_mutable`, `_ensure_current` (so `If-Match` preconditions apply in
pilot mode), an audit event, and a single commit.

The upload handler is `async` so the body can be capped while it streams; the
surrounding database calls are short local transactions. The response view also
carries `download_name`, the exact file name the server will offer, so clients
never have to reconstruct it.

## Threats considered

| Threat | Mitigation |
| --- | --- |
| Path traversal via file name (`../../etc/passwd`, absolute paths, Windows separators, null bytes, bidi overrides, Unicode look-alike dots) | Name is metadata only; the on-disk name is a server-generated token; path components are regex-validated; every resolved path is asserted to be inside the storage root; directories that are symlinks are refused. Tests assert nothing is written outside the root. |
| Arbitrary file read / disclosure of server files | The store only ever returns a blob it wrote; a blob path is derived from a validated opaque id; there is no route that accepts a client path. |
| Unbounded upload (disk or memory exhaustion) | Running byte ceiling while streaming, `Content-Length` pre-check, hard operator cap, `413`. |
| Uploaded active content executing in the app origin (stored XSS) | Every download is `application/octet-stream` + `attachment` + `nosniff` + CSP `sandbox`; the offered name is rebuilt with an allowlisted extension. |
| Executable attachment delivered to a user | Executable/scriptable extensions are rejected; the download extension is always the allowlisted one. |
| Type confusion (claiming `text/csv` for a binary) | Content sniffing must agree with the declared type. |
| Cross-tenant leak | Organization-scoped assessment lookup (`404`), plus an independent organization check on the stored record (`404`), plus unguessable ids. |
| Enumeration of other tenants' attachments | Ids are 128-bit random; cross-tenant requests return `404` without revealing existence. |
| Tampering with stored content | SHA-256 digest recorded at upload and echoed on download; the digest is verifiable by the client. |
| Unbounded metadata growth | Display name is length-limited; the record schema is validated on read and any malformed record is ignored (fail closed). |
| Log/audit leakage of customer data | The file name, the bytes and the content are never logged. Audit events carry ids, size, media type and digest only. The request telemetry logs the route template, not the URL or body. |
| Store reachable as static content | No `StaticFiles` mount exists; a test asserts the routes and that the store paths return `404`. |
| Symlinked storage directory used to redirect writes | The store refuses symlinked directories and re-checks path containment. |

## What is deliberately NOT built

These are the open decisions from `docs/phase9.md` and P8-10. Building them
without the decisions would be unsafe or would pre-empt the decision:

- **No database-backed attachment registry.** A new table would need an Alembic
  migration, and migration numbering is contended. The metadata is therefore a
  JSON record beside the blob. Consequence: attachment metadata is not
  transactional with the assessment transaction, listing is a directory scan,
  and database-level referential integrity, retention jobs and reporting do not
  apply. When the schema decision is taken, migrate to a table.
- **No DLP, classification, malware scanning or content rewriting.** Phase 9
  requires a DLP decision first. Uploads are not scanned; a file that is
  allowlisted by type may still contain sensitive data, so the operator must
  keep the deployment inside a synthetic-data boundary.
- **No data residency or regional storage guarantees.** The store inherits the
  host's filesystem. `ADVISORY_ATTACHMENT_ROOT` lets an operator place the
  store on a volume in the required region, but no policy is asserted here.
- **No encryption at rest beyond filesystem permissions.** The store relies on
  host filesystem permissions (`0700`/`0600`). There is no key management, no
  envelope encryption and no backup/erasure guarantee.
- **No object storage, no signed URLs, no replication, no versioning.** The
  local filesystem is a pilot store only.
- **No antivirus scanning, no content disarm, no OCR, no thumbnailer.**
- **No automatic retention schedule.** Deletion is explicit. Nothing purges
  attachments of a deleted assessment automatically; `purge_assessment` exists
  for an operator to run as part of a future retention job.
- **No metrics ingestion, no NPV/sensitivity work and no cross-currency
  logic** — the rest of P8-10, and explicitly out of scope for Phase 9.
- **No production approval.** This does not close any Phase 5, 6 or 7 gate and
  does not satisfy `docs/operations.md`.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `ADVISORY_ATTACHMENT_ROOT` | `<repo>/.advisory/attachments` | Storage root. Never a web-served directory. |
| `ADVISORY_ATTACHMENT_MAX_BYTES` | `10485760` (10 MiB) | Maximum attachment size; hard-capped at 64 MiB. |

The default root sits inside the working tree and is git-ignored. Point it at a
dedicated volume for real deployments.
