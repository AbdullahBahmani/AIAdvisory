"""Ingested measurements must be stored faithfully and change nothing.

A measurement that arrives from a production system is evidence about a
quantity.  The tests therefore check three things: that it is stored exactly
as it was reported, that a repeated delivery cannot inflate the evidence, and
that ingesting one never changes a score, a rule hit or a recommendation.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.domain.portfolio import ObservedMetricIn, ValueScenarioIn
from tests.test_phase8_portfolio import _scenario
from tests.test_security import secure_client  # noqa: F401 - the tenant fixture

OBSERVED = {
    "source_system": "ticket-system",
    "metric_key": "cases_per_period",
    "value": "420",
    "unit": "cases",
    "observed_at": "2026-10-01T00:00:00+00:00",
    "period": "month",
    "source_reference": "Monthly export from the ticket system",
    "sample_size": 420,
}


def _ingest(client, owner, root, **overrides):
    payload = dict(OBSERVED)
    payload.update(overrides)
    return client.post(root + "/observed-metrics", headers=owner, json={"metrics": [payload]})


def test_a_measurement_is_stored_exactly_as_it_was_reported(secure_client):
    """The value, the unit and the moment must survive the round trip."""
    client, provision, _ = secure_client
    owner, _, _ = provision("Ops", "owner", "analyst")
    aid = client.post("/api/v1/assessments", headers=owner, json={"title": "Ops"}).json()["id"]
    root = f"/api/v1/assessments/{aid}"
    created = _ingest(client, owner, root)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["already_recorded"] == 0
    assert len(body["stored"]) == 1
    stored = body["stored"][0]
    assert str(stored["value"]) == "420"
    assert stored["unit"] == "cases"
    assert stored["source_system"] == "ticket-system"
    assert stored["assessment_revision"] >= 1
    assert stored["advisory_only"] is True

    listed = client.get(root + "/observed-metrics", headers=owner)
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    assert listed.json()[0]["metric_key"] == "cases_per_period"


def test_a_repeated_delivery_is_not_counted_twice(secure_client):
    """A retried push must not be able to inflate the evidence."""
    client, provision, _ = secure_client
    owner, _, _ = provision("Ops", "owner", "analyst")
    aid = client.post("/api/v1/assessments", headers=owner, json={"title": "Ops"}).json()["id"]
    root = f"/api/v1/assessments/{aid}"
    first = _ingest(client, owner, root)
    assert first.status_code == 201
    assert len(first.json()["stored"]) == 1
    second = _ingest(client, owner, root)
    assert second.status_code == 201
    assert second.json()["stored"] == []
    assert second.json()["already_recorded"] == 1
    assert len(client.get(root + "/observed-metrics", headers=owner).json()) == 1


def test_a_measurement_without_a_timezone_is_refused(secure_client):
    """A naive timestamp cannot be placed in time, so it is refused."""
    client, provision, _ = secure_client
    owner, _, _ = provision("Ops", "owner", "analyst")
    aid = client.post("/api/v1/assessments", headers=owner, json={"title": "Ops"}).json()["id"]
    root = f"/api/v1/assessments/{aid}"
    refused = _ingest(client, owner, root, observed_at="2026-10-01T00:00:00")
    assert refused.status_code == 422
    assert "timezone" in refused.text.lower()


def test_ingesting_a_measurement_changes_no_recommendation(secure_client):
    """A number arriving from a machine must not decide anything."""
    client, provision, _ = secure_client
    owner, _, _ = provision("Ops", "owner", "analyst")
    aid = client.post("/api/v1/assessments", headers=owner, json={"title": "Ops"}).json()["id"]
    root = f"/api/v1/assessments/{aid}"
    before = client.get(root, headers=owner).json()
    _ingest(client, owner, root)
    after = client.get(root, headers=owner).json()
    assert before.get("analysis") is None
    assert after.get("analysis") is None
    assert after["revision"] == before["revision"]


def test_a_measurement_is_tenant_scoped(secure_client):
    """Another organization must not see another organization's measurements."""
    client, provision, _ = secure_client
    owner, _, _ = provision("Ops", "owner", "analyst")
    outsider, _, _ = provision("Other", "visitor", "admin")
    aid = client.post("/api/v1/assessments", headers=owner, json={"title": "Ops"}).json()["id"]
    root = f"/api/v1/assessments/{aid}"
    assert _ingest(client, owner, root).status_code == 201
    assert client.get(root + "/observed-metrics", headers=outsider).status_code == 404
    assert (
        client.post(
            root + "/observed-metrics", headers=outsider, json={"metrics": [OBSERVED]}
        ).status_code
        == 404
    )


def test_ingesting_requires_the_analyst_role(secure_client):
    """A measurement may not be written by a role that may not write evidence."""
    client, provision, _ = secure_client
    owner, _, _ = provision("Ops", "owner", "analyst")
    viewer, _, _ = provision("Ops", "viewer", "viewer")
    aid = client.post("/api/v1/assessments", headers=owner, json={"title": "Ops"}).json()["id"]
    root = f"/api/v1/assessments/{aid}"
    assert _ingest(client, viewer, root).status_code == 403


def test_a_scenario_may_cite_a_measurement(secure_client):
    """The citation is recorded and travels with the scenario."""
    client, provision, _ = secure_client
    owner, _, _ = provision("Ops", "owner", "analyst")
    aid = client.post("/api/v1/assessments", headers=owner, json={"title": "Ops"}).json()["id"]
    root = f"/api/v1/assessments/{aid}"
    # The measurement agrees with the scenario, so nothing is reported.
    stored = _ingest(client, owner, root, value="100").json()["stored"][0]
    scenario = client.post(
        root + "/value-scenarios",
        headers=owner,
        json=_scenario(observed_metric_ids=[stored["id"]]),
    )
    assert scenario.status_code == 201, scenario.text
    assert scenario.json()["observed_metric_ids"] == [stored["id"]]
    assert scenario.json()["citation_warnings"] == []


def test_the_same_instant_in_two_timezones_is_one_measurement(secure_client):
    """The moment is stored in one reference frame, so a retried push matches."""
    client, provision, _ = secure_client
    owner, _, _ = provision("Ops", "owner", "analyst")
    aid = client.post("/api/v1/assessments", headers=owner, json={"title": "Ops"}).json()["id"]
    root = f"/api/v1/assessments/{aid}"
    assert _ingest(client, owner, root, observed_at="2026-10-01T00:00:00+00:00").status_code == 201
    # The same instant, written in another timezone.
    again = _ingest(client, owner, root, observed_at="2026-10-01T02:00:00+02:00")
    assert again.status_code == 201
    assert again.json()["stored"] == []
    assert again.json()["already_recorded"] == 1
    assert len(client.get(root + "/observed-metrics", headers=owner).json()) == 1


def test_a_citation_to_a_measurement_of_another_assessment_is_refused(secure_client):
    """A scenario must not carry a claim that cannot be checked."""
    client, provision, _ = secure_client
    owner, _, _ = provision("Ops", "owner", "analyst")
    first = client.post("/api/v1/assessments", headers=owner, json={"title": "One"}).json()["id"]
    second = client.post("/api/v1/assessments", headers=owner, json={"title": "Two"}).json()["id"]
    stored = _ingest(client, owner, f"/api/v1/assessments/{first}").json()["stored"][0]
    refused = client.post(
        f"/api/v1/assessments/{second}/value-scenarios",
        headers=owner,
        json=_scenario(observed_metric_ids=[stored["id"]]),
    )
    assert refused.status_code == 422
    assert "do not belong" in refused.text


def test_a_citation_that_contradicts_the_scenario_is_reported(secure_client):
    """A measurement that does not support the scenario must say so."""
    client, provision, _ = secure_client
    owner, _, _ = provision("Ops", "owner", "analyst")
    aid = client.post("/api/v1/assessments", headers=owner, json={"title": "Ops"}).json()["id"]
    root = f"/api/v1/assessments/{aid}"
    # The measurement says 420 cases; the scenario states 100.
    stored = _ingest(client, owner, root).json()["stored"][0]
    scenario = client.post(
        root + "/value-scenarios",
        headers=owner,
        json=_scenario(observed_metric_ids=[stored["id"]]),
    )
    assert scenario.status_code == 201, scenario.text
    warnings = scenario.json()["citation_warnings"]
    assert warnings
    assert "cases_per_period" in warnings[0]


def test_the_same_measurement_cannot_be_cited_twice():
    """A duplicate citation is refused at the contract level."""
    try:
        ValueScenarioIn.model_validate(_scenario(observed_metric_ids=["a", "a"]))
    except Exception as exc:  # noqa: BLE001 - the type is not what matters here
        assert "twice" in str(exc)
    else:  # pragma: no cover - the validation must fail
        raise AssertionError("a duplicate citation must be refused")


def test_a_measurement_is_bound_to_the_revision_it_arrived_in(secure_client):
    """A measurement belongs to the revision that was current when it arrived."""
    client, provision, _ = secure_client
    owner, _, _ = provision("Ops", "owner", "analyst")
    aid = client.post("/api/v1/assessments", headers=owner, json={"title": "Ops"}).json()["id"]
    root = f"/api/v1/assessments/{aid}"
    stored = _ingest(client, owner, root).json()["stored"][0]
    assert stored["assessment_revision"] == 1
    assert stored["stale"] is False
    # A later revision makes the measurement stale, and says so.
    client.patch(root, headers=owner, json={"title": "Ops, revised"})
    listed = client.get(root + "/observed-metrics", headers=owner).json()
    assert listed[0]["stale"] is True


def test_a_measurement_is_validated_by_the_contract():
    """The contract itself refuses a blank field and a negative value."""
    import pytest

    with pytest.raises(ValueError):
        ObservedMetricIn(
            source_system="ticket-system",
            metric_key="",
            value="1",
            unit="cases",
            observed_at=datetime.now(UTC),
            source_reference="Monthly export from the ticket system",
        )
    with pytest.raises(ValueError):
        ObservedMetricIn(
            source_system="ticket-system",
            metric_key="cases",
            value="-1",
            unit="cases",
            observed_at=datetime.now(UTC),
            source_reference="Monthly export from the ticket system",
        )
