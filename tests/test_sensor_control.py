import httpx
import pytest
from fastapi.testclient import TestClient

from visionrig.api import create_app
from visionrig.pipeline import PerceptionPipeline
from visionrig.producer import GatewayFrameProducer, ProducerProtocolError
from visionrig.sensor_registry import SensorMetadataPatch, SensorRegistry


TOKEN = "c" * 48


def test_core_desired_state_defaults_enabled_and_tracks_registry() -> None:
    registry = SensorRegistry()
    client = TestClient(create_app(PerceptionPipeline(), sensor_registry=registry))

    initial = client.get("/api/v1/sensors/cam-a/desired-state")
    assert initial.status_code == 200
    assert initial.json() == {
        "schema_id": "visionrig/sensor-desired-state/v2",
        "source_id": "cam-a",
        "enabled": True,
        "revision": 0,
        "production_authority": False,
    }

    registry.patch("cam-a", SensorMetadataPatch(enabled=False))
    changed = client.get("/api/v1/sensors/cam-a/desired-state")
    assert changed.status_code == 200
    assert changed.json()["enabled"] is False
    assert changed.json()["revision"] == 1


def test_producer_fetches_its_own_desired_state() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/api/v1/sensors/quest/desired-state"
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        return httpx.Response(
            200,
            json={
                "schema_id": "visionrig/sensor-desired-state/v2",
                "source_id": "quest",
                "enabled": False,
                "revision": 3,
                "production_authority": False,
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        producer = GatewayFrameProducer(
            gateway_url="http://100.64.0.2:8111",
            token=TOKEN,
            source_id="quest",
            source_type="vr",
            client=client,
        )
        state = producer.fetch_desired_state()

    assert state.source_id == "quest"
    assert state.enabled is False
    assert state.revision == 3
    assert state.production_authority is False


def test_producer_rejects_desired_state_for_another_source() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "schema_id": "visionrig/sensor-desired-state/v2",
                "source_id": "other-camera",
                "enabled": True,
                "revision": 1,
                "production_authority": False,
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        producer = GatewayFrameProducer(
            gateway_url="http://127.0.0.1:8111",
            token=TOKEN,
            source_id="quest",
            source_type="vr",
            client=client,
        )
        with pytest.raises(ProducerProtocolError, match="does not match"):
            producer.fetch_desired_state()


def test_enabled_revision_changes_only_when_enabled_value_changes() -> None:
    registry = SensorRegistry()

    assert registry.desired_state("cam").revision == 0
    registry.patch("cam", SensorMetadataPatch(display_name="Camera"))
    assert registry.desired_state("cam").revision == 0

    registry.patch("cam", SensorMetadataPatch(enabled=True))
    assert registry.desired_state("cam").revision == 0

    registry.patch("cam", SensorMetadataPatch(enabled=False))
    assert registry.desired_state("cam").revision == 1

    registry.patch("cam", SensorMetadataPatch(enabled=False))
    assert registry.desired_state("cam").revision == 1

    registry.patch("cam", SensorMetadataPatch(enabled=True))
    assert registry.desired_state("cam").revision == 2
