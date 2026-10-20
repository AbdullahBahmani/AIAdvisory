"""Enterprise-safe evidence attachment storage (P8-10).

Attachments are deliberately kept OUT of the relational schema and OUT of every
web-served directory:

* blobs live in a dedicated storage root that the application never mounts as
  static content; the only way to read a file is the authenticated download
  route, which always forces a non-executable download response;
* the on-disk name is a server-generated opaque token, never the client file
  name, so a hostile name can never influence a path;
* every upload is size-bounded, type-allowlisted and content-sniffed, and the
  stored display name is sanitised and length-limited;
* provenance (uploader, timestamp, size, media type, SHA-256 digest, owning
  assessment/evidence and the bound assessment revision) is stored beside the
  blob as a JSON metadata record.

This is a local-filesystem pilot implementation. Object storage, DLP and data
residency decisions are still open (see ``docs/attachments.md``), so this
module never claims enterprise compliance or regulated-data readiness.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

POLICY_VERSION = "attachments-v1"

DEFAULT_MAX_BYTES = 10 * 1024 * 1024
HARD_MAX_BYTES = 64 * 1024 * 1024
MAX_DISPLAY_NAME = 200
MAX_FILENAME_STEM = 120

_ATTACHMENT_ID = re.compile(r"^[0-9a-f]{32}$")
_ASSESSMENT_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# Allowlisted media types. The value is the canonical extension used when the
# file is offered for download; the client's own extension is never trusted.
ALLOWED_CONTENT_TYPES: dict[str, str] = {
    "application/pdf": ".pdf",
    "text/plain": ".txt",
    "text/markdown": ".md",
    "text/csv": ".csv",
    "application/json": ".json",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "text/html": ".html",
    "image/svg+xml": ".svg",
}

# Executable/scriptable extensions are rejected even when the declared media
# type is allowlisted, so an attachment can never be saved as a runnable file.
DANGEROUS_EXTENSIONS = frozenset(
    {
        ".ade",
        ".adp",
        ".apk",
        ".app",
        ".appref-ms",
        ".bat",
        ".cmd",
        ".com",
        ".cpl",
        ".csh",
        ".dde",
        ".deb",
        ".dll",
        ".dmg",
        ".exe",
        ".hta",
        ".img",
        ".iso",
        ".jar",
        ".js",
        ".jse",
        ".lnk",
        ".mjs",
        ".cjs",
        ".msi",
        ".msp",
        ".pif",
        ".pl",
        ".ps1",
        ".py",
        ".pyc",
        ".pyw",
        ".reg",
        ".rpm",
        ".scr",
        ".sh",
        ".so",
        ".dylib",
        ".vbe",
        ".vbs",
        ".wsf",
        ".wsh",
        ".zsh",
    }
)

_TEXT_TYPES = frozenset({"text/plain", "text/markdown", "text/csv", "text/html", "image/svg+xml"})


class AttachmentError(Exception):
    """Base class for rejected uploads; carries the HTTP status to surface."""

    status_code = 400

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class AttachmentInvalid(AttachmentError):
    status_code = 422


class AttachmentTooLarge(AttachmentError):
    status_code = 413


class AttachmentTypeRejected(AttachmentError):
    status_code = 415


@dataclass(frozen=True)
class AttachmentRecord:
    """Provenance record for one stored attachment."""

    id: str
    assessment_id: str
    assessment_revision: int
    evidence_id: str | None
    organization_id: str | None
    display_name: str
    content_type: str
    size_bytes: int
    sha256: str
    uploaded_by: str
    uploaded_at: datetime
    policy_version: str = POLICY_VERSION


def storage_root() -> Path:
    """Resolve the attachment storage root; it is never a web-served directory."""
    configured = os.getenv("ADVISORY_ATTACHMENT_ROOT", "").strip()
    if configured:
        root = Path(configured).expanduser()
    else:
        root = Path(__file__).resolve().parents[2] / ".advisory" / "attachments"
    return root.resolve()


def max_bytes() -> int:
    """Maximum accepted attachment size; operator-configurable but hard-capped."""
    raw = os.getenv("ADVISORY_ATTACHMENT_MAX_BYTES", "").strip()
    if not raw:
        return DEFAULT_MAX_BYTES
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MAX_BYTES
    if value <= 0:
        return DEFAULT_MAX_BYTES
    return min(value, HARD_MAX_BYTES)


def sanitize_display_name(raw: str | None) -> str:
    """Reduce a client-supplied name to a harmless display label.

    The result is metadata only; it is never used to build a filesystem path.
    Directory components, control characters, bidirectional overrides and
    Unicode look-alike separators are removed.
    """
    if not raw:
        return ""
    name = unicodedata.normalize("NFKC", raw)
    name = name.replace("\\", "/")
    name = name.rsplit("/", 1)[-1]
    name = "".join(ch for ch in name if not unicodedata.category(ch).startswith("C"))
    name = name.strip().strip(".").strip()
    if not name or name in {".", ".."}:
        return ""
    return name[:MAX_DISPLAY_NAME]


def extension_of(name: str) -> str:
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    if "." not in base:
        return ""
    return "." + base.rsplit(".", 1)[-1].lower()


def download_filename(display_name: str, content_type: str) -> str:
    """Build the offered file name from a sanitised stem and a trusted extension.

    Only the first segment of the client name is kept and the extension always
    comes from the allowlist, so `report.pdf.html` or `payload.exe` can never
    be reconstructed as the offered file name.
    """
    stem = sanitize_display_name(display_name)
    stem = stem.split(".")[0]
    stem = re.sub(r"[^\w.\- ]", "_", stem, flags=re.UNICODE).strip(" ._-")
    stem = stem[:MAX_FILENAME_STEM].strip(" ._-")
    if not stem:
        stem = "attachment"
    return stem + ALLOWED_CONTENT_TYPES.get(content_type, ".bin")


def content_disposition(filename: str) -> str:
    """Force a download disposition with an ASCII fallback and a UTF-8 name."""
    from urllib.parse import quote

    ascii_name = re.sub(r"[^A-Za-z0-9._-]", "_", filename).strip("._-") or "attachment"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename, safe='')}"


def _assert_inside(root: Path, candidate: Path) -> None:
    try:
        root_path = root.resolve()
        target = candidate.resolve()
    except OSError as exc:  # pragma: no cover - defensive
        raise AttachmentInvalid("Attachment path could not be resolved") from exc
    if target != root_path and root_path not in target.parents:
        raise AttachmentInvalid("Attachment path escapes the storage root")


def _prepare(root: Path) -> None:
    """Create the private store layout; refuse to use a hijacked directory."""
    for path in (root, root / "blobs", root / "records", root / "tmp"):
        if path.is_symlink():
            raise AttachmentInvalid("Attachment storage directory must not be a symbolic link")
        path.mkdir(parents=True, exist_ok=True)
        if not path.is_dir():
            raise AttachmentInvalid("Attachment storage directory is not a directory")
        os.chmod(path, 0o700)


def _blob_path(root: Path, attachment_id: str) -> Path:
    if not _ATTACHMENT_ID.match(attachment_id):
        raise AttachmentInvalid("Invalid attachment id")
    path = root / "blobs" / attachment_id
    _assert_inside(root, path)
    return path


def _record_path(root: Path, assessment_id: str, attachment_id: str) -> Path:
    if not _ASSESSMENT_ID.match(assessment_id):
        raise AttachmentInvalid("Invalid assessment id")
    if not _ATTACHMENT_ID.match(attachment_id):
        raise AttachmentInvalid("Invalid attachment id")
    path = root / "records" / assessment_id / f"{attachment_id}.json"
    _assert_inside(root, path)
    return path


def _sniff_matches(content_type: str, data: bytes) -> bool:
    """Check that the bytes agree with the declared media type."""
    if content_type == "application/pdf":
        return b"%PDF-" in data[:4096]
    if content_type == "image/png":
        return data[:8] == b"\x89PNG\r\n\x1a\n"
    if content_type == "image/jpeg":
        return data[:3] == b"\xff\xd8\xff"
    if content_type == "application/json":
        try:
            json.loads(data.decode("utf-8-sig"))
        except (ValueError, UnicodeDecodeError):
            return False
        return True
    if content_type in _TEXT_TYPES:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            return False
        return "\x00" not in text
    return False


def _record_payload(record: AttachmentRecord) -> dict[str, object]:
    return {
        "attachment_id": record.id,
        "assessment_id": record.assessment_id,
        "assessment_revision": record.assessment_revision,
        "evidence_id": record.evidence_id,
        "organization_id": record.organization_id,
        "display_name": record.display_name,
        "content_type": record.content_type,
        "size_bytes": record.size_bytes,
        "sha256": record.sha256,
        "uploaded_by": record.uploaded_by,
        "uploaded_at": record.uploaded_at.isoformat(),
        "policy_version": record.policy_version,
    }


def _record_from_payload(payload: dict[str, object]) -> AttachmentRecord:
    attachment_id = payload.get("attachment_id")
    assessment_id = payload.get("assessment_id")
    revision = payload.get("assessment_revision")
    display_name = payload.get("display_name")
    content_type = payload.get("content_type")
    size_bytes = payload.get("size_bytes")
    digest = payload.get("sha256")
    uploaded_by = payload.get("uploaded_by")
    uploaded_at = payload.get("uploaded_at")
    evidence_id = payload.get("evidence_id")
    organization_id = payload.get("organization_id")
    if not isinstance(attachment_id, str) or not _ATTACHMENT_ID.match(attachment_id):
        raise ValueError("invalid attachment id")
    if not isinstance(assessment_id, str) or not _ASSESSMENT_ID.match(assessment_id):
        raise ValueError("invalid assessment id")
    if not isinstance(revision, int):
        raise ValueError("invalid revision")
    if not isinstance(display_name, str) or not display_name:
        raise ValueError("invalid display name")
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise ValueError("invalid content type")
    if not isinstance(size_bytes, int) or size_bytes < 0:
        raise ValueError("invalid size")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("invalid digest")
    if not isinstance(uploaded_by, str) or not uploaded_by:
        raise ValueError("invalid uploader")
    if not isinstance(uploaded_at, str):
        raise ValueError("invalid timestamp")
    if evidence_id is not None and not isinstance(evidence_id, str):
        raise ValueError("invalid evidence id")
    if organization_id is not None and not isinstance(organization_id, str):
        raise ValueError("invalid organization id")
    stamp = datetime.fromisoformat(uploaded_at)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return AttachmentRecord(
        id=attachment_id,
        assessment_id=assessment_id,
        assessment_revision=revision,
        evidence_id=evidence_id,
        organization_id=organization_id,
        display_name=display_name,
        content_type=content_type,
        size_bytes=size_bytes,
        sha256=digest,
        uploaded_by=uploaded_by,
        uploaded_at=stamp,
    )


def _write_record(root: Path, record: AttachmentRecord) -> None:
    path = _record_path(root, record.assessment_id, record.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    payload = json.dumps(_record_payload(record), ensure_ascii=False, sort_keys=True).encode(
        "utf-8"
    )
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def store_attachment(
    *,
    root: Path,
    assessment_id: str,
    assessment_revision: int,
    uploaded_by: str,
    filename: str | None,
    declared_content_type: str | None,
    data: bytes,
    evidence_id: str | None = None,
    organization_id: str | None = None,
) -> AttachmentRecord:
    """Validate and store one attachment; returns its provenance record."""
    limit = max_bytes()
    if not data:
        raise AttachmentInvalid("Attachment file is empty")
    if len(data) > limit:
        raise AttachmentTooLarge(f"Attachment exceeds the {limit} byte limit")
    media_type = (declared_content_type or "").split(";")[0].strip().lower()
    if media_type not in ALLOWED_CONTENT_TYPES:
        raise AttachmentTypeRejected("Attachment content type is not allowed")
    display_name = sanitize_display_name(filename)
    if not display_name:
        raise AttachmentInvalid("Attachment file name is missing or unusable")
    if extension_of(display_name) in DANGEROUS_EXTENSIONS:
        raise AttachmentTypeRejected("Attachment file name is not allowed")
    if not _sniff_matches(media_type, data):
        raise AttachmentTypeRejected("Attachment content does not match its declared type")
    attachment_id = secrets.token_hex(16)
    record = AttachmentRecord(
        id=attachment_id,
        assessment_id=assessment_id,
        assessment_revision=assessment_revision,
        evidence_id=evidence_id,
        organization_id=organization_id,
        display_name=display_name,
        content_type=media_type,
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        uploaded_by=uploaded_by,
        uploaded_at=datetime.now(UTC),
    )
    _prepare(root)
    blob = _blob_path(root, attachment_id)
    tmp = root / "tmp" / f"{attachment_id}.part"
    try:
        with open(tmp, "xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, blob)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise
    try:
        _write_record(root, record)
    except OSError:
        blob.unlink(missing_ok=True)
        raise
    return record


def load_record(root: Path, assessment_id: str, attachment_id: str) -> AttachmentRecord | None:
    """Load one metadata record; returns None on any inconsistency (fail closed)."""
    try:
        path = _record_path(root, assessment_id, attachment_id)
    except AttachmentError:
        return None
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    try:
        payload = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    try:
        record = _record_from_payload(payload)
    except (TypeError, ValueError):
        return None
    if record.id != attachment_id or record.assessment_id != assessment_id:
        return None
    return record


def list_records(root: Path, assessment_id: str) -> list[AttachmentRecord]:
    """List the attachment records that belong to one assessment."""
    if not _ASSESSMENT_ID.match(assessment_id):
        return []
    directory = root / "records" / assessment_id
    try:
        entries = sorted(directory.iterdir())
    except OSError:
        return []
    records: list[AttachmentRecord] = []
    for entry in entries:
        if entry.suffix != ".json" or not _ATTACHMENT_ID.match(entry.stem):
            continue
        record = load_record(root, assessment_id, entry.stem)
        if record is not None:
            records.append(record)
    records.sort(key=lambda record: (record.uploaded_at, record.id))
    return records


def read_blob(root: Path, attachment_id: str) -> bytes | None:
    try:
        path = _blob_path(root, attachment_id)
    except AttachmentError:
        return None
    try:
        return path.read_bytes()
    except OSError:
        return None


def delete_attachment(root: Path, assessment_id: str, attachment_id: str) -> bool:
    """Remove both the stored file and its metadata record."""
    record = load_record(root, assessment_id, attachment_id)
    if record is None:
        return False
    blob = _blob_path(root, record.id)
    try:
        blob.unlink(missing_ok=True)
    except OSError:
        return False
    path = _record_path(root, assessment_id, attachment_id)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        return False
    try:
        path.parent.rmdir()
    except OSError:
        pass
    return True


def purge_assessment(root: Path, assessment_id: str) -> int:
    """Retention helper: delete every stored attachment of one assessment."""
    removed = 0
    for record in list_records(root, assessment_id):
        if delete_attachment(root, assessment_id, record.id):
            removed += 1
    return removed


def purge_orphans(root: Path) -> int:
    """Maintenance helper: delete blobs without a record and leftover temp files."""
    removed = 0
    known: set[str] = set()
    try:
        for path in (root / "records").rglob("*.json"):
            known.add(path.stem)
    except OSError:
        pass
    for directory in (root / "blobs", root / "tmp"):
        try:
            entries = list(directory.iterdir())
        except OSError:
            continue
        for entry in entries:
            if not entry.is_file():
                continue
            if directory.name == "blobs" and entry.stem in known:
                continue
            try:
                entry.unlink()
                removed += 1
            except OSError:
                continue
    return removed
