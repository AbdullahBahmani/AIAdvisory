"""P8-10 enterprise-safe evidence attachments.

These checks are deliberately adversarial: traversal, absolute paths, null
bytes, Unicode look-alikes, oversized bodies, disallowed types, cross-tenant
reads, stale revisions, disk purging and inline rendering of active content.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from app.services import attachments
from tests.test_security import secure_client  # noqa: F401 - reuse the isolated auth fixture

TEXT = b"case_id,minutes\nA-1,12\n"


@pytest.fixture
def store(secure_client, monkeypatch, tmp_path: Path):
    """Authenticated client with an isolated attachment storage root."""
    client, provision, _ = secure_client
    root = tmp_path / "attachments"
    monkeypatch.setenv("ADVISORY_ATTACHMENT_ROOT", str(root))
    return client, provision, root, tmp_path


def _assessment(client, headers, title="Evidence attachments"):
    created = client.post("/api/v1/assessments", json={"title": title}, headers=headers)
    assert created.status_code == 201, created.text
    return created.json()["id"]


def _upload(client, assessment_id, headers, name, media_type, payload, evidence_id=None):
    from urllib.parse import quote

    url = f"/api/v1/assessments/{assessment_id}/evidence-attachments"
    if evidence_id:
        url += f"?evidence_id={evidence_id}"
    return client.post(
        url,
        content=payload,
        headers={
            **headers,
            "Content-Type": media_type,
            "X-Attachment-Filename": quote(name, safe=""),
            "X-Attachment-Content-Type": media_type,
        },
    )


def test_upload_and_download_round_trip_for_allowed_type(store):
    client, provision, root, _ = store
    owner, _, _ = provision("Acme", "analyst-a", "analyst")
    aid = _assessment(client, owner)
    created = _upload(client, aid, owner, "baseline.csv", "text/csv", TEXT)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["display_name"] == "baseline.csv"
    assert body["content_type"] == "text/csv"
    assert body["size_bytes"] == len(TEXT)
    assert body["sha256"] == hashlib.sha256(TEXT).hexdigest()
    assert body["uploaded_by"] == "analyst-a"
    assert body["assessment_revision"] == 1
    assert body["stale"] is False
    assert body["evidence_id"] is None

    listing = client.get(f"/api/v1/assessments/{aid}/evidence-attachments", headers=owner)
    assert listing.status_code == 200, listing.text
    assert [item["id"] for item in listing.json()] == [body["id"]]

    download = client.get(
        f"/api/v1/assessments/{aid}/evidence-attachments/{body['id']}/download", headers=owner
    )
    assert download.status_code == 200, download.text
    assert download.content == TEXT
    assert download.headers["content-type"] == "application/octet-stream"
    assert download.headers["content-disposition"].startswith("attachment;")
    assert download.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in download.headers["content-security-policy"]
    assert download.headers["x-attachment-sha256"] == body["sha256"]

    # The blob lives under the storage root with an opaque, server-generated name.
    blobs = sorted((root / "blobs").iterdir())
    assert len(blobs) == 1
    assert blobs[0].name == body["id"]
    assert root.resolve() in blobs[0].resolve().parents


HOSTILE_NAMES = [
    "../../etc/passwd",
    "/etc/passwd",
    "..\\..\\Windows\\System32\\config\\SAM",
    "subdir/../../escape.txt",
    "null\x00byte.txt",
    "bidi\u202ereveal.txt",
    "dots\u2024\u2024/etc/passwd",
    "....//....//escape.txt",
    "spaces   .txt",
    "x" * 500 + ".txt",
    "日本語のファイル.txt",
]


@pytest.mark.parametrize("hostile", HOSTILE_NAMES)
def test_hostile_filenames_never_escape_the_storage_root(store, hostile):
    client, provision, root, tmp_path = store
    owner, _, _ = provision("Acme", "analyst-a", "analyst")
    aid = _assessment(client, owner)
    sentinel = tmp_path / "sentinel.txt"
    sentinel.write_text("untouched", encoding="utf-8")

    created = _upload(client, aid, owner, hostile, "text/plain", b"plain evidence text")
    assert created.status_code == 201, created.text
    body = created.json()
    name = body["display_name"]
    assert "/" not in name
    assert "\\" not in name
    assert "\x00" not in name
    assert ".." not in name
    assert len(name) <= attachments.MAX_DISPLAY_NAME

    # Nothing was written outside the storage root.
    assert sentinel.read_text(encoding="utf-8") == "untouched"
    outside = {
        item.name
        for item in tmp_path.iterdir()
        if not item.name.startswith("isolation.sqlite3")
        and item.name not in {"attachments", "sentinel.txt"}
    }
    assert outside == set()

    blobs = list((root / "blobs").iterdir())
    assert len(blobs) == 1
    assert root.resolve() in blobs[0].resolve().parents
    assert blobs[0].read_bytes() == b"plain evidence text"

    download = client.get(
        f"/api/v1/assessments/{aid}/evidence-attachments/{body['id']}/download", headers=owner
    )
    assert download.status_code == 200, download.text
    assert download.content == b"plain evidence text"


def test_oversized_attachment_is_rejected(store, monkeypatch):
    client, provision, root, _ = store
    monkeypatch.setenv("ADVISORY_ATTACHMENT_MAX_BYTES", "1024")
    owner, _, _ = provision("Acme", "analyst-a", "analyst")
    aid = _assessment(client, owner)
    created = _upload(client, aid, owner, "big.txt", "text/plain", b"x" * 4096)
    assert created.status_code == 413, created.text
    assert not (root / "blobs").exists() or not list((root / "blobs").iterdir())
    listing = client.get(f"/api/v1/assessments/{aid}/evidence-attachments", headers=owner)
    assert listing.json() == []


@pytest.mark.parametrize(
    "media_type", ["application/x-msdownload", "application/zip", "application/x-sh", ""]
)
def test_disallowed_content_type_is_rejected(store, media_type):
    client, provision, _, _ = store
    owner, _, _ = provision("Acme", "analyst-a", "analyst")
    aid = _assessment(client, owner)
    created = _upload(client, aid, owner, "payload.bin", media_type, b"binary payload")
    assert created.status_code == 415, created.text


def test_dangerous_file_name_is_rejected_even_for_an_allowed_type(store):
    client, provision, _, _ = store
    owner, _, _ = provision("Acme", "analyst-a", "analyst")
    aid = _assessment(client, owner)
    created = _upload(client, aid, owner, "invoice.exe", "text/plain", b"not really an executable")
    assert created.status_code == 415, created.text


def test_declared_type_must_match_the_stored_content(store):
    client, provision, _, _ = store
    owner, _, _ = provision("Acme", "analyst-a", "analyst")
    aid = _assessment(client, owner)
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 64
    created = _upload(client, aid, owner, "image.csv", "text/csv", png)
    assert created.status_code == 415, created.text


def test_empty_attachment_is_rejected(store):
    client, provision, _, _ = store
    owner, _, _ = provision("Acme", "analyst-a", "analyst")
    aid = _assessment(client, owner)
    created = _upload(client, aid, owner, "empty.txt", "text/plain", b"")
    assert created.status_code == 422, created.text


def test_cross_organization_access_is_rejected(store):
    client, provision, root, _ = store
    owner, _, _ = provision("Acme", "analyst-a", "analyst")
    outsider, _, _ = provision("Globex", "admin-b", "admin")
    aid = _assessment(client, owner)
    created = _upload(client, aid, owner, "baseline.csv", "text/csv", TEXT)
    assert created.status_code == 201, created.text
    attachment_id = created.json()["id"]

    assert (
        client.get(f"/api/v1/assessments/{aid}/evidence-attachments", headers=outsider).status_code
        == 404
    )
    assert (
        client.get(
            f"/api/v1/assessments/{aid}/evidence-attachments/{attachment_id}/download",
            headers=outsider,
        ).status_code
        == 404
    )
    assert (
        client.delete(
            f"/api/v1/assessments/{aid}/evidence-attachments/{attachment_id}", headers=outsider
        ).status_code
        == 404
    )
    assert _upload(client, aid, outsider, "intrusion.txt", "text/plain", b"nope").status_code == 404
    # The outsider's attempt did not touch the stored file.
    assert len(list((root / "blobs").iterdir())) == 1


def test_attachment_bound_to_an_old_revision_is_marked_stale(store):
    client, provision, _, _ = store
    owner, _, _ = provision("Acme", "analyst-a", "analyst")
    aid = _assessment(client, owner)
    created = _upload(client, aid, owner, "baseline.csv", "text/csv", TEXT)
    assert created.status_code == 201, created.text
    attachment_id = created.json()["id"]
    assert (
        client.patch(
            f"/api/v1/assessments/{aid}", json={"title": "Revised scope"}, headers=owner
        ).status_code
        == 200
    )
    listing = client.get(f"/api/v1/assessments/{aid}/evidence-attachments", headers=owner)
    assert listing.status_code == 200, listing.text
    item = listing.json()[0]
    assert item["stale"] is True
    assert item["assessment_revision"] == 1
    download = client.get(
        f"/api/v1/assessments/{aid}/evidence-attachments/{attachment_id}/download", headers=owner
    )
    assert download.status_code == 200, download.text
    assert download.headers["x-attachment-stale"] == "true"
    assert download.content == TEXT


def test_delete_removes_the_file_from_disk(store):
    client, provision, root, _ = store
    owner, _, _ = provision("Acme", "analyst-a", "analyst")
    aid = _assessment(client, owner)
    created = _upload(client, aid, owner, "baseline.csv", "text/csv", TEXT)
    assert created.status_code == 201, created.text
    attachment_id = created.json()["id"]
    blob = root / "blobs" / attachment_id
    record = root / "records" / aid / f"{attachment_id}.json"
    assert blob.exists() and record.exists()

    deleted = client.delete(
        f"/api/v1/assessments/{aid}/evidence-attachments/{attachment_id}", headers=owner
    )
    assert deleted.status_code == 204, deleted.text
    assert not blob.exists()
    assert not record.exists()
    assert not list((root / "blobs").iterdir())
    assert (
        client.get(
            f"/api/v1/assessments/{aid}/evidence-attachments/{attachment_id}/download",
            headers=owner,
        ).status_code
        == 404
    )
    listing = client.get(f"/api/v1/assessments/{aid}/evidence-attachments", headers=owner)
    assert listing.json() == []


def test_html_and_svg_are_never_served_inline(store):
    client, provision, _, _ = store
    owner, _, _ = provision("Acme", "analyst-a", "analyst")
    aid = _assessment(client, owner)
    html = b"<html><body><script>alert('xss')</script></body></html>"
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    for name, media_type, payload in (
        ("report.html", "text/html", html),
        ("logo.svg", "image/svg+xml", svg),
    ):
        created = _upload(client, aid, owner, name, media_type, payload)
        assert created.status_code == 201, created.text
        attachment_id = created.json()["id"]
        download = client.get(
            f"/api/v1/assessments/{aid}/evidence-attachments/{attachment_id}/download",
            headers=owner,
        )
        assert download.status_code == 200, download.text
        assert download.headers["content-type"] == "application/octet-stream"
        assert "text/html" not in download.headers["content-type"]
        assert "image/svg+xml" not in download.headers["content-type"]
        assert download.headers["content-disposition"].startswith("attachment;")
        assert download.headers["x-content-type-options"] == "nosniff"
        assert "sandbox" in download.headers["content-security-policy"]
        assert download.content == payload


def test_offered_download_name_cannot_rebuild_a_hostile_extension(store):
    client, provision, _, _ = store
    owner, _, _ = provision("Acme", "analyst-a", "analyst")
    aid = _assessment(client, owner)
    created = _upload(client, aid, owner, "report.pdf.html", "text/html", b"<p>notes</p>")
    assert created.status_code == 201, created.text
    download = client.get(
        f"/api/v1/assessments/{aid}/evidence-attachments/{created.json()['id']}/download",
        headers=owner,
    )
    assert download.status_code == 200, download.text
    disposition = download.headers["content-disposition"]
    assert 'filename="report.html"' in disposition
    assert "pdf" not in disposition


def test_upload_requires_the_analyst_role_and_reads_need_only_a_viewer(store):
    client, provision, _, _ = store
    owner, _, _ = provision("Acme", "analyst-a", "analyst")
    viewer, _, _ = provision("Acme", "viewer-a", "viewer")
    aid = _assessment(client, owner)
    assert _upload(client, aid, viewer, "viewer.txt", "text/plain", b"nope").status_code == 403
    created = _upload(client, aid, owner, "baseline.csv", "text/csv", TEXT)
    assert created.status_code == 201, created.text
    attachment_id = created.json()["id"]
    assert (
        client.get(
            f"/api/v1/assessments/{aid}/evidence-attachments/{attachment_id}/download",
            headers=viewer,
        ).status_code
        == 200
    )


def test_attachment_can_be_linked_to_an_evidence_record(store):
    client, provision, _, _ = store
    owner, _, _ = provision("Acme", "analyst-a", "analyst")
    other, _, _ = provision("Globex", "analyst-b", "analyst")
    aid = _assessment(client, owner)
    foreign = _assessment(client, other, title="Foreign")
    evidence = client.post(
        f"/api/v1/assessments/{aid}/evidence",
        headers=owner,
        json={
            "kind": "baseline",
            "description": "Synthetic baseline observation",
            "source_reference": "Pilot worksheet 2026-01",
        },
    )
    assert evidence.status_code == 201, evidence.text
    evidence_id = evidence.json()["id"]

    linked = _upload(client, aid, owner, "baseline.csv", "text/csv", TEXT, evidence_id=evidence_id)
    assert linked.status_code == 201, linked.text
    assert linked.json()["evidence_id"] == evidence_id
    filtered = client.get(
        f"/api/v1/assessments/{aid}/evidence-attachments?evidence_id={evidence_id}",
        headers=owner,
    )
    assert [item["id"] for item in filtered.json()] == [linked.json()["id"]]

    # Evidence from another assessment cannot be linked.
    foreign_evidence = client.post(
        f"/api/v1/assessments/{foreign}/evidence",
        headers=other,
        json={
            "kind": "baseline",
            "description": "Synthetic baseline observation",
            "source_reference": "Pilot worksheet 2026-01",
        },
    )
    assert foreign_evidence.status_code == 201, foreign_evidence.text
    rejected = _upload(
        client,
        aid,
        owner,
        "baseline.csv",
        "text/csv",
        TEXT,
        evidence_id=foreign_evidence.json()["id"],
    )
    assert rejected.status_code == 404, rejected.text


def test_store_is_not_reachable_as_static_content(store):
    from starlette.routing import Mount
    from starlette.staticfiles import StaticFiles

    from app.main import app

    client, provision, root, _ = store
    owner, _, _ = provision("Acme", "analyst-a", "analyst")
    aid = _assessment(client, owner)
    created = _upload(client, aid, owner, "baseline.csv", "text/csv", TEXT)
    attachment_id = created.json()["id"]

    for route in app.routes:
        assert not isinstance(route, Mount) or not isinstance(
            getattr(route, "app", None), StaticFiles
        )
    assert client.get(f"/blobs/{attachment_id}").status_code == 404
    assert client.get(f"/{root.name}/{attachment_id}").status_code == 404
    assert client.get(f"/records/{aid}/{attachment_id}.json").status_code == 404


def test_default_storage_root_is_outside_the_application_packages(monkeypatch):
    import ui
    from app import api

    monkeypatch.delenv("ADVISORY_ATTACHMENT_ROOT", raising=False)
    default = attachments.storage_root()
    app_dir = Path(api.__file__).resolve().parent
    ui_dir = Path(ui.__file__).resolve().parent
    assert app_dir not in default.parents
    assert ui_dir not in default.parents
    assert default.name == "attachments"


class _StubStreamlit:
    def __init__(self) -> None:
        self.errors: list[str] = []

    def error(self, message: str) -> None:
        self.errors.append(message)


class _StubUpload:
    def __init__(self, name: str, media_type: str, payload: bytes) -> None:
        self.name = name
        self.type = media_type
        self._payload = payload

    def getvalue(self) -> bytes:
        return self._payload


def test_ui_upload_adapter_goes_through_the_api_and_encodes_the_name(monkeypatch):
    from urllib.parse import quote

    from ui import portfolio as ui_portfolio

    captured: dict[str, object] = {}

    class _FakeResponse:
        status = 201

        def read(self) -> bytes:
            return b"{}"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def _fake_urlopen(request, timeout=None):
        captured["request"] = request
        return _FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    st = _StubStreamlit()
    ok = ui_portfolio._upload_attachment(
        st,
        "http://127.0.0.1:8000",
        "assessment-1",
        _StubUpload("måle fil.txt", "text/plain", b"evidence"),
    )
    assert ok is True
    assert st.errors == []
    request = captured["request"]
    assert request.get_method() == "POST"
    assert request.full_url.endswith("/api/v1/assessments/assessment-1/evidence-attachments")
    assert request.data == b"evidence"
    assert request.get_header("X-attachment-filename") == quote("måle fil.txt", safe="")
    assert request.get_header("X-attachment-content-type") == "text/plain"


def test_ui_download_adapter_reports_api_errors(monkeypatch):
    from urllib.error import HTTPError

    from ui import portfolio as ui_portfolio

    def _fake_urlopen(request, timeout=None):
        raise HTTPError(request.full_url, 404, "Not Found", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    st = _StubStreamlit()
    assert ui_portfolio._download_attachment(st, "http://127.0.0.1:8000", "a1", "att1") is None
    assert st.errors and "404" in st.errors[0]
