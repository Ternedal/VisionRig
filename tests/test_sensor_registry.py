from fastapi.testclient import TestClient

from visionrig.api import create_app
from visionrig.pipeline import PerceptionPipeline
from visionrig.sensor_registry import SensorMetadataPatch, SensorRegistry


def test_registry_patch_is_operator_owned_and_defaults_enabled() -> None:
    registry = SensorRegistry()

    untouched = registry.get("cam-a")
    assert untouched.source_id == "cam-a"
    assert untouched.enabled is True
    assert registry.list() == ()

    updated = registry.patch(
        "cam-a",
        SensorMetadataPatch(
            display_name=" Living room Kinect ",
            location="Living room",
            role="tracking",
            enabled=False,
        ),
    )

    assert updated.display_name == "Living room Kinect"
    assert updated.location == "Living room"
    assert updated.role == "tracking"
    assert updated.enabled is False
    assert registry.list() == (updated,)


def test_sensor_catalog_joins_runtime_and_operator_metadata() -> None:
    registry = SensorRegistry()
    client = TestClient(
        create_app(
            PerceptionPipeline(),
            sensor_registry=registry,
        )
    )

    heartbeat = client.post(
        "/api/v1/sensors/heartbeat",
        json={
            "schema_id": "visionrig/sensor-heartbeat/v1",
            "source_id": "kinect-living-room",
            "source_type": "camera",
            "device": "kinect-v2",
            "capabilities": ["rgb", "depth", "infrared"],
            "capture_active": True,
        },
    )
    assert heartbeat.status_code == 200

    patch = client.patch(
        "/api/v1/sensors/kinect-living-room/metadata",
        json={
            "display_name": "Living room Kinect",
            "location": "Living room",
            "role": "tracking",
            "enabled": True,
        },
    )
    assert patch.status_code == 200
    assert patch.json()["schema"] == "visionrig/sensor-metadata/v1"

    catalog = client.get("/api/v1/sensors/catalog")
    assert catalog.status_code == 200
    body = catalog.json()
    assert body["schema"] == "visionrig/sensor-catalog/v2"
    assert len(body["sources"]) == 1

    source = body["sources"][0]
    assert source["source_id"] == "kinect-living-room"
    assert source["runtime"]["presence"] == "online"
    assert source["runtime"]["capabilities"] == ["depth", "infrared", "rgb"]
    assert source["metadata"] == {
        "source_id": "kinect-living-room",
        "display_name": "Living room Kinect",
        "location": "Living room",
        "role": "tracking",
        "enabled": True,
    }
    assert source["control"] == {
        "desired_enabled": True,
        "effective_capture_active": True,
        "status": "converged",
    }


def test_catalog_can_preconfigure_sensor_before_it_is_online() -> None:
    client = TestClient(create_app(PerceptionPipeline()))

    response = client.patch(
        "/api/v1/sensors/future-quest/metadata",
        json={
            "display_name": "Kaliv Quest",
            "location": "Portable",
            "role": "vr",
            "enabled": False,
        },
    )
    assert response.status_code == 200

    catalog = client.get("/api/v1/sensors/catalog").json()
    assert catalog["sources"][0]["runtime"] is None
    assert catalog["sources"][0]["metadata"]["enabled"] is False


def test_metadata_patch_does_not_disable_ingress() -> None:
    """enabled is desired operator state, not hidden enforcement authority."""
    client = TestClient(create_app(PerceptionPipeline()))

    response = client.patch(
        "/api/v1/sensors/cam-a/metadata",
        json={"enabled": False},
    )
    assert response.status_code == 200

    heartbeat = client.post(
        "/api/v1/sensors/heartbeat",
        json={
            "schema_id": "visionrig/sensor-heartbeat/v1",
            "source_id": "cam-a",
            "source_type": "camera",
        },
    )
    assert heartbeat.status_code == 200
    catalog = client.get("/api/v1/sensors/catalog").json()
    assert catalog["sources"][0]["runtime"]["presence"] == "online"
    assert catalog["sources"][0]["metadata"]["enabled"] is False


def test_health_reports_registry_surface() -> None:
    registry = SensorRegistry()
    registry.patch("cam-a", SensorMetadataPatch(display_name="Camera A"))
    client = TestClient(create_app(PerceptionPipeline(), sensor_registry=registry))

    health = client.get("/health").json()
    assert health["schema"] == "visionrig/health/v10"
    assert health["sensor_registry"] == {
        "schema": "visionrig/sensor-registry/v1",
        "entries": 1,
        "desired_state_schema": "visionrig/sensor-desired-state/v1",
    }


def test_heartbeat_auto_registers_unknown_sensor() -> None:
    registry = SensorRegistry()
    client = TestClient(create_app(PerceptionPipeline(), sensor_registry=registry))

    response = client.post(
        "/api/v1/sensors/heartbeat",
        json={
            "schema_id": "visionrig/sensor-heartbeat/v1",
            "source_id": "new-kinect",
            "source_type": "camera",
            "device": "kinect-v2",
            "capabilities": ["rgb", "depth", "infrared"],
            "capture_active": False,
        },
    )
    assert response.status_code == 200

    registered = registry.list()
    assert len(registered) == 1
    assert registered[0].source_id == "new-kinect"
    assert registered[0].display_name is None
    assert registered[0].enabled is True


def test_auto_registration_never_overwrites_operator_metadata() -> None:
    registry = SensorRegistry()
    registry.patch(
        "camera-a",
        SensorMetadataPatch(
            display_name="Desk camera",
            location="Office",
            role="primary",
            enabled=False,
        ),
    )
    client = TestClient(create_app(PerceptionPipeline(), sensor_registry=registry))

    response = client.post(
        "/api/v1/sensors/heartbeat",
        json={
            "schema_id": "visionrig/sensor-heartbeat/v1",
            "source_id": "camera-a",
            "source_type": "camera",
            "device": "usb-camera",
            "capabilities": ["rgb"],
            "capture_active": True,
        },
    )
    assert response.status_code == 200

    metadata = registry.get("camera-a")
    assert metadata.display_name == "Desk camera"
    assert metadata.location == "Office"
    assert metadata.role == "primary"
    assert metadata.enabled is False
