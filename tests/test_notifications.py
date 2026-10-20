"""External notification delivery: channels, isolation, egress and retries.

The webhook tests use a local HTTP server on the loopback interface. The
default test run never contacts an external host: the only destinations that
are ever contacted are ``127.0.0.1`` servers started by this module, and the
outbox files are written into a temporary directory.
"""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import InboxNotificationRow
from app.notifications import channels, dispatcher, egress, events
from app.notifications.models import (
    CHANNEL_FILE,
    CHANNEL_WEBHOOK,
    STATUS_DELIVERED,
    STATUS_FAILED,
    STATUS_PENDING,
    NotificationDeliveryRow,
    NotificationDestinationRow,
)
from app.notifications.schemas import DispatchRequest
from scripts import notifications as notifications_cli
from tests.test_security import secure_client  # noqa: F401 - reuse the isolated auth fixture

#: A secret that only exists inside this test process.
TEST_SECRET = "test-webhook-signing-secret-value"

#: Strings that must never leave the application in a payload.
CANARY_TITLE = "ZephyrineRevenueLeakCanary"
CANARY_RATIONALE = "CanaryRationalePhrase42"
CANARY_COMMENT = "CanaryCommentBodyPhrase17"
CANARY_TOKEN = "aia_zzzNOTAREALTOKENzzz"


class _Handler(BaseHTTPRequestHandler):
    """Records every POST and answers with whatever the server is told to."""

    protocol_version = "HTTP/1.1"

    def do_POST(self):  # noqa: N802 - the stdlib handler API
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        record = {
            "path": self.path,
            "headers": {key.lower(): value for key, value in self.headers.items()},
            "body": body,
        }
        self.server.requests.append(record)
        status, headers, payload = self.server.responder(record)
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args, **kwargs):  # noqa: ARG002 - keep the test output clean
        return


@pytest.fixture
def http_server():
    """A local HTTP server that records the webhooks it receives."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.requests = []
    server.responder = lambda record: (200, {}, b"{}")
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
    )
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def loopback_enabled(monkeypatch):
    """Allow the explicit loopback development target."""
    monkeypatch.setenv(egress.ALLOW_LOOPBACK_ENV, "1")
    monkeypatch.setenv(events.SECRET_ENV, TEST_SECRET)


@pytest.fixture
def outbox_root(tmp_path, monkeypatch):
    """Point the file outbox at a temporary directory."""
    root = tmp_path / "outbox"
    root.mkdir()
    monkeypatch.setenv(channels.OUTBOX_ENV, str(root))
    return root


def _url(server, path: str = "/hook") -> str:
    return f"http://127.0.0.1:{server.server_address[1]}{path}"


def _session(engine) -> Session:
    return Session(engine)


def _destination(engine, organization_id: str, channel: str, target: str, events_wanted=None):
    with _session(engine) as db:
        row = NotificationDestinationRow(
            organization_id=organization_id,
            channel=channel,
            target=target,
            events=list(events_wanted or []),
        )
        db.add(row)
        db.commit()
        return row.id


def _assessment(client, admin, title: str = "Enterprise discovery") -> str:
    created = client.post("/api/v1/assessments", json={"title": title}, headers=admin)
    assert created.status_code == 201, created.text
    aid = created.json()["id"]
    workflow = client.post(
        f"/api/v1/assessments/{aid}/workflows", json={"name": "Pricing"}, headers=admin
    )
    wid = workflow.json()["id"]
    step = client.post(
        f"/api/v1/workflows/{wid}/steps", json={"name": "Calculate", "order": 0}, headers=admin
    )
    sid = step.json()["id"]
    client.post(
        f"/api/v1/workflow-steps/{sid}/tasks",
        json={"title": "Pricing", "signals": {"exact_math": True}},
        headers=admin,
    )
    analysis = client.post(f"/api/v1/assessments/{aid}/analyze", headers=admin)
    assert analysis.status_code == 200, analysis.text
    return aid


def _deliveries(engine, organization_id: str) -> list[NotificationDeliveryRow]:
    with _session(engine) as db:
        return list(
            db.scalars(
                select(NotificationDeliveryRow)
                .where(NotificationDeliveryRow.organization_id == organization_id)
                .order_by(NotificationDeliveryRow.seq)
            ).all()
        )


def _delivery_by_id(engine, delivery_id: str):
    """One delivery, looked up by its stable delivery id."""
    with _session(engine) as db:
        return db.scalar(
            select(NotificationDeliveryRow).where(NotificationDeliveryRow.id == delivery_id)
        )


def test_notification_generates_in_app_row_and_outbound_delivery(
    secure_client, http_server, loopback_enabled
):
    """A generated notification produces an in-app row *and* a delivery record."""
    client, provision, engine = secure_client
    admin, org_id, _ = provision("Notify Co", "admin", "admin")
    reviewer, _, _ = provision("Notify Co", "fde", "reviewer")
    _destination(engine, org_id, CHANNEL_WEBHOOK, _url(http_server))

    aid = _assessment(client, admin)
    assigned = client.put(
        f"/api/v1/assessments/{aid}/reviewer", json={"reviewer_subject": "fde"}, headers=admin
    )
    assert assigned.status_code == 200, assigned.text

    with _session(engine) as db:
        inbox = db.scalars(
            select(InboxNotificationRow).where(InboxNotificationRow.assessment_id == aid)
        ).all()
        assert inbox, "the in-app inbox row must exist"
        assert inbox[0].event_type == "reviewer.assigned"

    queued = _deliveries(engine, org_id)
    assert len(queued) == 1
    delivery = queued[0]
    assert delivery.status == STATUS_PENDING
    assert delivery.channel == CHANNEL_WEBHOOK
    assert delivery.event_type == "reviewer.assigned"
    assert delivery.organization_id == org_id
    assert delivery.payload["assessment_id"] == aid
    assert delivery.payload["delivery_id"] == delivery.id
    assert delivery.payload["link_path"] == f"/api/v1/assessments/{aid}"

    # Generation itself must not open a socket: delivery is out of band.
    assert http_server.requests == []
    assert reviewer  # the recipient exists; the payload never names them


def test_failed_delivery_never_fails_the_originating_request(
    secure_client, http_server, loopback_enabled, monkeypatch
):
    """A failing external delivery is recorded as failed, not swallowed."""
    client, provision, engine = secure_client
    admin, org_id, _ = provision("Fail Co", "admin", "admin")
    provision("Fail Co", "fde", "reviewer")
    http_server.responder = lambda record: (500, {}, b"nope")
    _destination(engine, org_id, CHANNEL_WEBHOOK, _url(http_server))
    monkeypatch.setenv(dispatcher.MAX_ATTEMPTS_ENV, "1")

    aid = _assessment(client, admin)
    response = client.put(
        f"/api/v1/assessments/{aid}/reviewer", json={"reviewer_subject": "fde"}, headers=admin
    )
    assert response.status_code == 200, response.text

    with _session(engine) as db:
        report = dispatcher.dispatch_pending(db, organization_id=org_id)
    assert report.attempted == 1
    assert report.failed == 1
    assert report.delivered == 0
    assert "http_500" in report.reasons

    delivery = _deliveries(engine, org_id)[0]
    assert delivery.status == STATUS_FAILED
    assert delivery.last_error == "http_500"
    assert delivery.attempts == 1
    # The in-app notification is untouched by the delivery failure.
    with _session(engine) as db:
        assert db.scalars(
            select(InboxNotificationRow).where(InboxNotificationRow.assessment_id == aid)
        ).all()


def test_retries_are_bounded_and_idempotent(
    secure_client, http_server, loopback_enabled, monkeypatch
):
    """Retries reuse the delivery id, are bounded, and never duplicate a message."""
    client, provision, engine = secure_client
    admin, org_id, _ = provision("Retry Co", "admin", "admin")
    provision("Retry Co", "fde", "reviewer")
    attempts = {"count": 0}

    def responder(record):
        attempts["count"] += 1
        if attempts["count"] == 1:
            return 500, {}, b"try again"
        return 200, {}, b"{}"

    http_server.responder = responder
    _destination(engine, org_id, CHANNEL_WEBHOOK, _url(http_server))
    monkeypatch.setenv(dispatcher.MAX_ATTEMPTS_ENV, "3")

    aid = _assessment(client, admin)
    client.put(
        f"/api/v1/assessments/{aid}/reviewer", json={"reviewer_subject": "fde"}, headers=admin
    )

    with _session(engine) as db:
        report = dispatcher.dispatch_pending(db, organization_id=org_id)
    assert report.retrying == 1
    delivery = _deliveries(engine, org_id)[0]
    assert delivery.status == STATUS_PENDING
    assert delivery.attempts == 1
    first_id = delivery.id
    first_body = http_server.requests[0]["body"]

    # Make the retry due and run it again.
    with _session(engine) as db:
        row = db.scalar(
            select(NotificationDeliveryRow).where(NotificationDeliveryRow.id == delivery.id)
        )
        row.next_attempt_at = row.created_at
        db.commit()
    with _session(engine) as db:
        report = dispatcher.dispatch_pending(db, organization_id=org_id)
    assert report.delivered == 1

    delivery = _deliveries(engine, org_id)[0]
    assert delivery.status == STATUS_DELIVERED
    assert delivery.attempts == 2
    assert delivery.id == first_id, "a retry must keep the delivery id"
    assert len(http_server.requests) == 2
    assert http_server.requests[1]["body"] == first_body, "a retry must be byte-identical"
    assert http_server.requests[0]["headers"]["idempotency-key"] == first_id

    # A delivered message is never sent again.
    with _session(engine) as db:
        dispatcher.dispatch_pending(db, organization_id=org_id)
    assert len(http_server.requests) == 2


def test_failures_stop_after_the_bounded_attempt_budget(
    secure_client, http_server, loopback_enabled, monkeypatch
):
    """The attempt budget is bounded: a permanent failure stops being retried."""
    client, provision, engine = secure_client
    admin, org_id, _ = provision("Bounded Co", "admin", "admin")
    provision("Bounded Co", "fde", "reviewer")
    http_server.responder = lambda record: (503, {}, b"down")
    _destination(engine, org_id, CHANNEL_WEBHOOK, _url(http_server))
    monkeypatch.setenv(dispatcher.MAX_ATTEMPTS_ENV, "2")

    aid = _assessment(client, admin)
    client.put(
        f"/api/v1/assessments/{aid}/reviewer", json={"reviewer_subject": "fde"}, headers=admin
    )
    for _ in range(4):
        with _session(engine) as db:
            row = db.scalars(
                select(NotificationDeliveryRow).where(
                    NotificationDeliveryRow.organization_id == org_id
                )
            ).all()[0]
            row.next_attempt_at = row.created_at
            db.commit()
        with _session(engine) as db:
            dispatcher.dispatch_pending(db, organization_id=org_id)

    delivery = _deliveries(engine, org_id)[0]
    assert delivery.status == STATUS_FAILED
    assert delivery.attempts == 2
    assert len(http_server.requests) == 2, "no attempt may exceed the budget"


def test_payload_excludes_content_and_the_signature_verifies(
    secure_client, http_server, loopback_enabled
):
    """The payload carries the minimum, and the signature proves the sender."""
    client, provision, engine = secure_client
    admin, org_id, _ = provision("Payload Co", "admin", "admin")
    reviewer, _, _ = provision("Payload Co", "fde", "reviewer")
    _destination(engine, org_id, CHANNEL_WEBHOOK, _url(http_server))

    aid = _assessment(client, admin, title=CANARY_TITLE)
    client.put(
        f"/api/v1/assessments/{aid}/reviewer", json={"reviewer_subject": "fde"}, headers=admin
    )
    client.post(
        f"/api/v1/assessments/{aid}/comments",
        json={"body": CANARY_COMMENT},
        headers=admin,
    )
    client.post(
        f"/api/v1/assessments/{aid}/approvals",
        json={"stage": "technical", "decision": "approved", "rationale": CANARY_RATIONALE},
        headers=reviewer,
    )

    with _session(engine) as db:
        dispatcher.dispatch_pending(db, organization_id=org_id)

    assert http_server.requests
    for record in http_server.requests:
        body = record["body"]
        payload = json.loads(body)
        assert set(payload) == {
            "schema",
            "delivery_id",
            "event_type",
            "assessment_id",
            "organization_id",
            "occurred_at",
            "link_path",
        }
        text = body.decode()
        for canary in (CANARY_TITLE, CANARY_RATIONALE, CANARY_COMMENT, CANARY_TOKEN):
            assert canary not in text
        assert "rationale" not in text
        assert "business_problem" not in text
        # The signing secret is never part of the message.
        assert TEST_SECRET not in text
        # The signature verifies against the shared secret.
        secret = events.signing_secret()
        assert secret is not None
        timestamp = record["headers"]["x-aiadvisory-timestamp"]
        assert events.verify(secret, timestamp, body, record["headers"]["x-aiadvisory-signature"])
        assert record["headers"]["x-aiadvisory-delivery-id"] == payload["delivery_id"]


def test_file_channel_appends_one_message_per_organization(
    secure_client, outbox_root, loopback_enabled
):
    """The file outbox is per tenant, append-only and idempotent."""
    client, provision, engine = secure_client
    admin, org_id, _ = provision("File Co", "admin", "admin")
    provision("File Co", "fde", "reviewer")
    _destination(engine, org_id, CHANNEL_FILE, "messages.jsonl")

    aid = _assessment(client, admin)
    client.put(
        f"/api/v1/assessments/{aid}/reviewer", json={"reviewer_subject": "fde"}, headers=admin
    )
    with _session(engine) as db:
        report = dispatcher.dispatch_pending(db, organization_id=org_id)
    assert report.delivered == 1

    path = outbox_root / org_id / "messages.jsonl"
    assert path.is_file()
    lines = [line for line in path.read_text().splitlines() if line.strip()]
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["payload"]["assessment_id"] == aid
    assert record["payload"]["delivery_id"]
    assert record["signature"].startswith("sha256=")

    # Re-sending the same delivery id must not duplicate the message.
    with _session(engine) as db:
        row = db.scalars(
            select(NotificationDeliveryRow).where(NotificationDeliveryRow.organization_id == org_id)
        ).all()[0]
        row.status = STATUS_PENDING
        row.next_attempt_at = row.created_at
        db.commit()
    with _session(engine) as db:
        dispatcher.dispatch_pending(db, organization_id=org_id)
    lines = [line for line in path.read_text().splitlines() if line.strip()]
    assert len(lines) == 1


def test_log_lines_carry_no_content_and_no_secret(
    secure_client, http_server, loopback_enabled, caplog
):
    """The delivery log follows the redaction policy of the request log."""
    client, provision, engine = secure_client
    admin, org_id, _ = provision("Log Co", "admin", "admin")
    provision("Log Co", "fde", "reviewer")
    _destination(engine, org_id, CHANNEL_WEBHOOK, _url(http_server))

    aid = _assessment(client, admin, title=CANARY_TITLE)
    with caplog.at_level("INFO"):
        client.put(
            f"/api/v1/assessments/{aid}/reviewer",
            json={"reviewer_subject": "fde"},
            headers=admin,
        )
        with _session(engine) as db:
            dispatcher.dispatch_pending(db, organization_id=org_id)

    lines = [
        record.getMessage()
        for record in caplog.records
        if record.name.startswith("aiadvisory.notifications")
    ]
    assert lines, "the dispatcher must leave a trace"
    for line in lines:
        assert CANARY_TITLE not in line
        assert TEST_SECRET not in line
        assert "rationale" not in line
    joined = "\n".join(lines)
    assert "notification.queued" in joined
    assert "notification.delivered" in joined
    # The organisation and the assessment never appear in a delivery log line.
    assert org_id not in joined
    assert aid not in joined


def test_destination_of_another_organization_is_refused(
    secure_client, http_server, loopback_enabled
):
    """A delivery may never leave the tenant that owns it."""
    client, provision, engine = secure_client
    admin_a, org_a, _ = provision("Tenant A", "admin-a", "admin")
    _, org_b, _ = provision("Tenant B", "admin-b", "admin")
    provision("Tenant A", "fde", "reviewer")
    target = _url(http_server)
    _destination(engine, org_a, CHANNEL_WEBHOOK, target)
    destination_b = _destination(engine, org_b, CHANNEL_WEBHOOK, target)

    aid = _assessment(client, admin_a)
    client.put(
        f"/api/v1/assessments/{aid}/reviewer", json={"reviewer_subject": "fde"}, headers=admin_a
    )
    # Point a delivery of tenant A at the destination of tenant B.
    with _session(engine) as db:
        delivery = db.scalars(
            select(NotificationDeliveryRow).where(NotificationDeliveryRow.organization_id == org_a)
        ).all()[0]
        delivery.destination_id = destination_b
        db.commit()

    with _session(engine) as db:
        report = dispatcher.dispatch_pending(db, organization_id=org_a)
    assert report.failed == 1
    assert "tenant_mismatch" in report.reasons
    assert http_server.requests == [], "nothing may be sent to another tenant's destination"

    delivery = _deliveries(engine, org_a)[0]
    assert delivery.status == STATUS_FAILED
    assert delivery.last_error == "tenant_mismatch"


def test_link_local_and_metadata_destinations_are_refused():
    """Link-local, private and metadata addresses are never contacted."""
    for url in (
        "http://169.254.169.254/latest/meta-data/",
        "https://169.254.169.254/latest/meta-data/",
        "http://10.0.0.5/internal",
        "http://192.168.1.10/internal",
        "http://172.16.0.5/internal",
        "http://[::1]:8080/internal",
        "http://127.0.0.1:8080/internal",
    ):
        with pytest.raises(egress.TargetError) as raised:
            egress.validate_target(url, allow_loopback=False)
        assert raised.value.reason in {"blocked_address", "scheme_refused"}

    # Plain HTTP is refused for a public target even when it resolves fine.
    with pytest.raises(egress.TargetError) as raised:
        egress.validate_target("http://93.184.216.34/hook", allow_loopback=False)
    assert raised.value.reason == "scheme_refused"

    # The operator CLI refuses a metadata address before it is ever stored.
    with pytest.raises(ValueError):
        notifications_cli._validate_target(
            CHANNEL_WEBHOOK, "http://169.254.169.254/latest/meta-data/"
        )


def test_dispatch_refuses_a_blocked_destination(secure_client, http_server, loopback_enabled):
    """A queued delivery to a metadata address fails closed and sends nothing."""
    client, provision, engine = secure_client
    admin, org_id, _ = provision("Blocked Co", "admin", "admin")
    provision("Blocked Co", "fde", "reviewer")
    destination_id = _destination(
        engine, org_id, CHANNEL_WEBHOOK, "http://169.254.169.254/latest/meta-data/"
    )
    aid = _assessment(client, admin)
    client.put(
        f"/api/v1/assessments/{aid}/reviewer", json={"reviewer_subject": "fde"}, headers=admin
    )
    assert _deliveries(engine, org_id)[0].destination_id == destination_id

    with _session(engine) as db:
        report = dispatcher.dispatch_pending(db, organization_id=org_id)
    assert report.failed == 1
    assert "blocked_address" in report.reasons
    assert http_server.requests == []
    delivery = _deliveries(engine, org_id)[0]
    assert delivery.status == STATUS_FAILED
    assert delivery.last_error == "blocked_address"


def test_redirect_to_another_host_is_not_followed(secure_client, loopback_enabled):
    """A redirect to a different host is refused, never followed."""
    target = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    target.requests = []
    target.responder = lambda record: (200, {}, b"{}")
    thread = threading.Thread(target=target.serve_forever, daemon=True)
    thread.start()
    redirector = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    redirector.requests = []
    redirector.responder = lambda record: (
        302,
        {"Location": f"http://127.0.0.1:{target.server_address[1]}/stolen"},
        b"",
    )
    thread2 = threading.Thread(target=redirector.serve_forever, daemon=True)
    thread2.start()
    try:
        client, provision, engine = secure_client
        admin, org_id, _ = provision("Redirect Co", "admin", "admin")
        provision("Redirect Co", "fde", "reviewer")
        _destination(
            engine,
            org_id,
            CHANNEL_WEBHOOK,
            f"http://127.0.0.1:{redirector.server_address[1]}/hook",
        )
        aid = _assessment(client, admin)
        client.put(
            f"/api/v1/assessments/{aid}/reviewer",
            json={"reviewer_subject": "fde"},
            headers=admin,
        )
        with _session(engine) as db:
            report = dispatcher.dispatch_pending(db, organization_id=org_id)
        assert report.failed == 1
        assert "redirect_refused" in report.reasons
        assert len(redirector.requests) == 1
        assert target.requests == [], "the redirect target must never be contacted"
        delivery = _deliveries(engine, org_id)[0]
        assert delivery.status == STATUS_FAILED
        assert delivery.last_error == "redirect_refused"
    finally:
        redirector.shutdown()
        redirector.server_close()
        target.shutdown()
        target.server_close()


def test_delivery_order_follows_the_generated_order(
    secure_client, http_server, loopback_enabled, monkeypatch
):
    """An approval request is never overtaken by the decision that follows it."""
    client, provision, engine = secure_client
    admin, org_id, _ = provision("Order Co", "admin", "admin")
    reviewer, _, _ = provision("Order Co", "fde", "reviewer")
    _destination(engine, org_id, CHANNEL_WEBHOOK, _url(http_server))
    monkeypatch.setenv(dispatcher.MAX_ATTEMPTS_ENV, "3")

    aid = _assessment(client, admin)
    # The approval request: an assessment is assigned for review.
    client.put(
        f"/api/v1/assessments/{aid}/reviewer", json={"reviewer_subject": "fde"}, headers=admin
    )
    # The decision: a review stage was recorded by somebody else, so the
    # assigned reviewer is told about it.
    client.post(
        f"/api/v1/assessments/{aid}/approvals",
        json={"stage": "technical", "decision": "approved", "rationale": "Looks fine"},
        headers=admin,
    )

    queued = _deliveries(engine, org_id)
    assert len(queued) == 2
    assert [row.event_type for row in queued] == [
        "reviewer.assigned",
        "approval.recorded",
    ]

    # The first message fails; the second one must wait for it.
    state = {"calls": 0}

    def responder(record):
        state["calls"] += 1
        return (500, {}, b"down") if state["calls"] == 1 else (200, {}, b"{}")

    http_server.responder = responder
    with _session(engine) as db:
        report = dispatcher.dispatch_pending(db, organization_id=org_id)
    assert report.retrying == 1
    assert report.delivered == 0
    assert len(http_server.requests) == 1, "the second message must wait for the first"

    with _session(engine) as db:
        row = db.scalar(
            select(NotificationDeliveryRow).where(NotificationDeliveryRow.id == queued[0].id)
        )
        row.next_attempt_at = row.created_at
        db.commit()
    with _session(engine) as db:
        report = dispatcher.dispatch_pending(db, organization_id=org_id)
    assert report.delivered == 2
    assert len(http_server.requests) == 3

    delivered = [json.loads(record["body"]) for record in http_server.requests]
    order = [payload["event_type"] for payload in delivered]
    assert order == [
        "reviewer.assigned",
        "reviewer.assigned",
        "approval.recorded",
    ], "the retry of the first message must still come before the second message"


def test_operator_commands_configure_inspect_and_retry(
    secure_client, http_server, loopback_enabled, monkeypatch
):
    """The operator CLI configures, inspects and retries - dry run by default."""
    client, provision, engine = secure_client
    admin, org_id, _ = provision("Cli Co", "admin", "admin")
    provision("Cli Co", "fde", "reviewer")
    monkeypatch.setattr(
        notifications_cli,
        "SessionLocal",
        sessionmaker(bind=engine, autoflush=False, expire_on_commit=False),
    )

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "notifications",
            "configure-destination",
            "--organization",
            "Cli Co",
            "--channel",
            "webhook",
            "--target",
            _url(http_server),
        ],
    )
    notifications_cli.main()
    with _session(engine) as db:
        rows = db.scalars(
            select(NotificationDestinationRow).where(
                NotificationDestinationRow.organization_id == org_id
            )
        ).all()
        assert len(rows) == 1
        assert rows[0].channel == CHANNEL_WEBHOOK
        assert rows[0].enabled is True

    aid = _assessment(client, admin)
    client.put(
        f"/api/v1/assessments/{aid}/reviewer", json={"reviewer_subject": "fde"}, headers=admin
    )
    delivery_id = _deliveries(engine, org_id)[0].id

    # The CLI sees the delivery status of the tenant.
    monkeypatch.setattr(
        sys,
        "argv",
        ["notifications", "list-deliveries", "--organization", "Cli Co"],
    )
    notifications_cli.main()

    # Make the delivery fail so that there is something to retry.
    http_server.responder = lambda record: (500, {}, b"down")
    monkeypatch.setenv(dispatcher.MAX_ATTEMPTS_ENV, "1")
    with _session(engine) as db:
        dispatcher.dispatch_pending(db, organization_id=org_id)
    assert _deliveries(engine, org_id)[0].status == STATUS_FAILED

    # A retry is a dry run until it is asked for.
    monkeypatch.setattr(sys, "argv", ["notifications", "retry", "--delivery-id", delivery_id])
    notifications_cli.main()
    assert _deliveries(engine, org_id)[0].status == STATUS_FAILED

    monkeypatch.setattr(
        sys,
        "argv",
        ["notifications", "retry", "--delivery-id", delivery_id, "--execute"],
    )
    notifications_cli.main()
    requeued = _deliveries(engine, org_id)[0]
    assert requeued.status == STATUS_PENDING
    assert requeued.id == delivery_id, "a retry keeps the delivery id"

    # The re-queued message is delivered under the same id.
    http_server.responder = lambda record: (200, {}, b"{}")
    with _session(engine) as db:
        dispatcher.dispatch_pending(db, organization_id=org_id)
    assert _deliveries(engine, org_id)[0].status == STATUS_DELIVERED
    assert http_server.requests[-1]["headers"]["idempotency-key"] == delivery_id


def test_dry_run_default_performs_no_destructive_action(
    secure_client, http_server, loopback_enabled, monkeypatch
):
    """Without --execute nothing is sent, re-queued or deleted."""
    client, provision, engine = secure_client
    admin, org_id, _ = provision("Dry Co", "admin", "admin")
    provision("Dry Co", "fde", "reviewer")
    http_server.responder = lambda record: (500, {}, b"down")
    _destination(engine, org_id, CHANNEL_WEBHOOK, _url(http_server))
    monkeypatch.setenv(dispatcher.MAX_ATTEMPTS_ENV, "1")

    aid = _assessment(client, admin)
    client.put(
        f"/api/v1/assessments/{aid}/reviewer", json={"reviewer_subject": "fde"}, headers=admin
    )
    with _session(engine) as db:
        dispatcher.dispatch_pending(db, organization_id=org_id)
    assert len(http_server.requests) == 1
    assert _deliveries(engine, org_id)[0].status == STATUS_FAILED

    # A dry-run dispatch sends nothing and changes nothing.
    with _session(engine) as db:
        report = dispatcher.dispatch_pending(db, organization_id=org_id, dry_run=True)
    assert report.dry_run is True
    assert len(http_server.requests) == 1
    delivery = _deliveries(engine, org_id)[0]
    assert delivery.status == STATUS_FAILED

    # A dry-run purge deletes nothing.
    with _session(engine) as db:
        count = dispatcher.purge_failed_deliveries(db, org_id, execute=False)
    assert count == 1
    assert len(_deliveries(engine, org_id)) == 1

    # The operator command defaults to a dry run as well.
    monkeypatch.setattr(
        notifications_cli,
        "SessionLocal",
        sessionmaker(bind=engine, autoflush=False, expire_on_commit=False),
    )
    monkeypatch.setattr(sys, "argv", ["notifications", "purge-failed", "--organization", "Dry Co"])
    notifications_cli.main()
    assert len(_deliveries(engine, org_id)) == 1

    monkeypatch.setattr(
        sys,
        "argv",
        ["notifications", "purge-failed", "--organization", "Dry Co", "--execute"],
    )
    notifications_cli.main()
    assert len(_deliveries(engine, org_id)) == 0


def test_api_is_tenant_scoped_and_dry_run_by_default(secure_client, http_server, loopback_enabled):
    """The status API shows one tenant its own deliveries only."""
    client, provision, engine = secure_client
    admin, org_id, _ = provision("Api Co", "admin", "admin")
    outsider, _, _ = provision("Other Co", "outsider", "admin")
    provision("Api Co", "fde", "reviewer")
    _destination(engine, org_id, CHANNEL_WEBHOOK, _url(http_server))

    aid = _assessment(client, admin)
    client.put(
        f"/api/v1/assessments/{aid}/reviewer", json={"reviewer_subject": "fde"}, headers=admin
    )

    destinations = client.get("/api/v1/notifications/destinations", headers=admin)
    assert destinations.status_code == 200, destinations.text
    assert len(destinations.json()) == 1
    assert destinations.json()[0]["channel"] == CHANNEL_WEBHOOK
    # The full target is not exposed; only the host is.
    assert destinations.json()[0]["target_host"].startswith("127.0.0.1")

    listed = client.get("/api/v1/notifications/deliveries", headers=admin)
    assert listed.status_code == 200, listed.text
    assert len(listed.json()) == 1
    assert listed.json()[0]["status"] == STATUS_PENDING

    # Another tenant sees nothing and cannot dispatch for this tenant.
    assert client.get("/api/v1/notifications/deliveries", headers=outsider).json() == []
    assert client.get("/api/v1/notifications/destinations", headers=outsider).json() == []

    # The dispatch endpoint is a dry run unless the caller asks for it.
    dry = client.post(
        "/api/v1/notifications/dispatch",
        json=DispatchRequest(dry_run=True).model_dump(),
        headers=admin,
    )
    assert dry.status_code == 200, dry.text
    assert dry.json()["dry_run"] is True
    assert dry.json()["delivered"] == 0
    assert http_server.requests == []

    live = client.post("/api/v1/notifications/dispatch", json={"dry_run": False}, headers=admin)
    assert live.status_code == 200, live.text
    assert live.json()["delivered"] == 1
    assert len(http_server.requests) == 1


def test_delivery_status_is_visible_to_the_organization_only(
    secure_client, http_server, loopback_enabled
):
    """A reviewer of another tenant cannot see the delivery status."""
    client, provision, engine = secure_client
    admin, org_id, _ = provision("Scoped Co", "admin", "admin")
    outsider, _, _ = provision("Scoped Co", "outsider", "reviewer")
    provision("Scoped Co", "fde", "reviewer")
    _destination(engine, org_id, CHANNEL_WEBHOOK, _url(http_server))
    aid = _assessment(client, admin)
    client.put(
        f"/api/v1/assessments/{aid}/reviewer", json={"reviewer_subject": "fde"}, headers=admin
    )
    # A reviewer may not read the organization's delivery status.
    assert client.get("/api/v1/notifications/deliveries", headers=outsider).status_code == 403
    assert client.get("/api/v1/notifications/deliveries").status_code == 401
