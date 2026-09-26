from datetime import datetime, timezone

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
            "schema_id": "visionrig/sensor-heartbeat/v2",
            "source_id": "kinect-living-room",
            "source_type": "camera",
            "device": "kinect-v2",
            "capabilities": ["rgb", "depth", "infrared"],
            "capture_active": True,
            "applied_revision": 0,
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
    assert patch.json()["schema"] == "visionrig/sensor-metadata/v2"
    assert patch.json()["state_revision"] == 2

    catalog = client.get("/api/v1/sensors/catalog")
    assert catalog.status_code == 200
    body = catalog.json()
    assert body["schema"] == "visionrig/sensor-catalog/v7"
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
        "retired_utc": None,
    }
    assert source["discovery"]["source_id"] == "kinect-living-room"
    assert source["discovery"]["source_type"] == "camera"
    assert source["discovery"]["device"] == "kinect-v2"
    assert source["discovery"]["capabilities"] == ["depth", "infrared", "rgb"]
    assert source["discovery"]["first_seen_utc"] is not None
    assert source["discovery"]["last_seen_utc"] is not None
    assert source["discovery"]["observation_count"] == 1
    assert source["lifecycle"] == {
        "status": "active",
        "retired_utc": None,
    }
    assert source["control"] == {
        "desired_enabled": True,
        "desired_revision": 0,
        "desired_changed_utc": None,
        "effective_capture_active": True,
        "applied_revision": 0,
        "pending_seconds": None,
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
            "schema_id": "visionrig/sensor-heartbeat/v2",
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
    assert health["schema"] == "visionrig/health/v24"
    assert health["sensor_registry"] == {
        "schema": "visionrig/sensor-registry/v7",
        "state_revision": 1,
        "entries": 1,
        "discovered": 0,
        "desired_state_schema": "visionrig/sensor-desired-state/v2",
    }


def test_heartbeat_auto_registers_unknown_sensor() -> None:
    registry = SensorRegistry()
    client = TestClient(create_app(PerceptionPipeline(), sensor_registry=registry))

    response = client.post(
        "/api/v1/sensors/heartbeat",
        json={
            "schema_id": "visionrig/sensor-heartbeat/v2",
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
            "schema_id": "visionrig/sensor-heartbeat/v2",
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


def test_sensor_type_reuse_is_rejected_without_overwriting_discovery() -> None:
    registry = SensorRegistry()
    client = TestClient(create_app(PerceptionPipeline(), sensor_registry=registry))

    first = client.post(
        "/api/v1/sensors/heartbeat",
        json={
            "schema_id": "visionrig/sensor-heartbeat/v2",
            "source_id": "sensor-a",
            "source_type": "camera",
            "device": "usb-camera",
            "capabilities": ["rgb"],
        },
    )
    assert first.status_code == 200

    conflict = client.post(
        "/api/v1/sensors/heartbeat",
        json={
            "schema_id": "visionrig/sensor-heartbeat/v2",
            "source_id": "sensor-a",
            "source_type": "vr",
            "device": "quest",
            "capabilities": ["passthrough"],
        },
    )
    assert conflict.status_code == 409

    discovery = registry.get_discovery("sensor-a")
    assert discovery is not None
    assert discovery.source_type == "camera"
    assert discovery.device == "usb-camera"
    assert discovery.capabilities == ("rgb",)


def test_catalog_requires_current_revision_before_converged() -> None:
    now = [datetime(2026, 9, 26, 8, 0, tzinfo=timezone.utc)]
    registry = SensorRegistry(clock=lambda: now[0])
    client = TestClient(create_app(PerceptionPipeline(), sensor_registry=registry))

    initial = client.post(
        "/api/v1/sensors/heartbeat",
        json={
            "schema_id": "visionrig/sensor-heartbeat/v2",
            "source_id": "cam-revision",
            "source_type": "camera",
            "capabilities": ["rgb"],
            "capture_active": True,
            "applied_revision": 0,
        },
    )
    assert initial.status_code == 200
    assert client.get("/api/v1/sensors/catalog").json()["sources"][0]["control"]["status"] == "converged"

    client.patch(
        "/api/v1/sensors/cam-revision/metadata",
        json={"enabled": False},
    )
    changed_utc = registry.control_state("cam-revision").changed_utc
    assert changed_utc == "2026-09-26T08:00:00+00:00"
    now[0] = datetime(2026, 9, 26, 8, 0, 12, 500000, tzinfo=timezone.utc)
    pending = client.get("/api/v1/sensors/catalog").json()["sources"][0]["control"]
    assert pending["desired_revision"] == 1
    assert pending["desired_changed_utc"] == changed_utc
    assert pending["applied_revision"] == 0
    assert pending["pending_seconds"] == 12.5
    assert pending["status"] == "pending"

    ack = client.post(
        "/api/v1/sensors/heartbeat",
        json={
            "schema_id": "visionrig/sensor-heartbeat/v2",
            "source_id": "cam-revision",
            "source_type": "camera",
            "capabilities": ["rgb"],
            "capture_active": False,
            "applied_revision": 1,
        },
    )
    assert ack.status_code == 200
    converged = client.get("/api/v1/sensors/catalog").json()["sources"][0]["control"]
    assert converged["status"] == "converged"
    assert converged["pending_seconds"] is None
    assert converged["desired_changed_utc"] == changed_utc


def test_retire_disables_sensor_with_revision_and_restore_stays_disabled() -> None:
    now = [datetime(2026, 9, 26, 9, 0, tzinfo=timezone.utc)]
    registry = SensorRegistry(clock=lambda: now[0])
    client = TestClient(create_app(PerceptionPipeline(), sensor_registry=registry))

    heartbeat = client.post(
        "/api/v1/sensors/heartbeat",
        json={
            "schema_id": "visionrig/sensor-heartbeat/v2",
            "source_id": "camera-retire",
            "source_type": "camera",
            "capture_active": True,
            "applied_revision": 0,
        },
    )
    assert heartbeat.status_code == 200

    retired = client.post("/api/v1/sensors/camera-retire/retire")
    assert retired.status_code == 200
    retired_body = retired.json()
    assert retired_body["schema"] == "visionrig/sensor-lifecycle/v2"
    assert retired_body["state_revision"] == 2
    assert retired_body["status"] == "retired"
    assert retired_body["metadata"]["enabled"] is False
    assert retired_body["metadata"]["retired_utc"] == "2026-09-26T09:00:00+00:00"
    assert retired_body["desired_state"]["revision"] == 1
    assert retired_body["desired_state"]["enabled"] is False

    catalog = client.get("/api/v1/sensors/catalog").json()["sources"][0]
    assert catalog["lifecycle"] == {
        "status": "retired",
        "retired_utc": "2026-09-26T09:00:00+00:00",
    }
    assert catalog["control"]["status"] == "pending"

    duplicate = client.post("/api/v1/sensors/camera-retire/retire")
    assert duplicate.status_code == 200
    assert duplicate.json()["desired_state"]["revision"] == 1

    enable_while_retired = client.patch(
        "/api/v1/sensors/camera-retire/metadata",
        json={"enabled": True},
    )
    assert enable_while_retired.status_code == 422

    restored = client.post("/api/v1/sensors/camera-retire/restore")
    assert restored.status_code == 200
    assert restored.json()["state_revision"] == 3
    assert restored.json()["status"] == "active"
    assert restored.json()["metadata"]["retired_utc"] is None
    assert restored.json()["metadata"]["enabled"] is False
    assert restored.json()["desired_state"]["revision"] == 1

    enabled = client.patch(
        "/api/v1/sensors/camera-retire/metadata",
        json={"enabled": True},
    )
    assert enabled.status_code == 200
    assert registry.desired_state("camera-retire").revision == 2


def test_forget_requires_retired_and_offline_then_allows_fresh_registration() -> None:
    registry = SensorRegistry()
    registry.observe(
        "forgotten-sensor",
        source_type="camera",
        device="old-camera",
        capabilities=("rgb",),
    )
    client = TestClient(create_app(PerceptionPipeline(), sensor_registry=registry))

    not_retired = client.delete("/api/v1/sensors/forgotten-sensor")
    assert not_retired.status_code == 409

    retired = client.post("/api/v1/sensors/forgotten-sensor/retire")
    assert retired.status_code == 200

    forgotten = client.delete("/api/v1/sensors/forgotten-sensor")
    assert forgotten.status_code == 200
    assert forgotten.json() == {
        "schema": "visionrig/sensor-forget/v2",
        "state_revision": 3,
        "status": "forgotten",
        "source_id": "forgotten-sensor",
    }
    assert registry.contains("forgotten-sensor") is False
    assert client.get("/api/v1/sensors/catalog").json()["sources"] == []

    fresh = client.post(
        "/api/v1/sensors/heartbeat",
        json={
            "schema_id": "visionrig/sensor-heartbeat/v2",
            "source_id": "forgotten-sensor",
            "source_type": "vr",
            "device": "new-quest",
            "capabilities": ["passthrough"],
            "capture_active": True,
            "applied_revision": 0,
        },
    )
    assert fresh.status_code == 200
    assert registry.get_discovery("forgotten-sensor").source_type == "vr"
    assert registry.control_revision("forgotten-sensor") == 0
    assert registry.get("forgotten-sensor").retired_utc is None


def test_forget_rejects_retired_sensor_that_is_still_online() -> None:
    registry = SensorRegistry()
    client = TestClient(create_app(PerceptionPipeline(), sensor_registry=registry))

    heartbeat = client.post(
        "/api/v1/sensors/heartbeat",
        json={
            "schema_id": "visionrig/sensor-heartbeat/v2",
            "source_id": "online-camera",
            "source_type": "camera",
            "capture_active": True,
            "applied_revision": 0,
        },
    )
    assert heartbeat.status_code == 200
    assert client.post("/api/v1/sensors/online-camera/retire").status_code == 200

    forgotten = client.delete("/api/v1/sensors/online-camera")
    assert forgotten.status_code == 409
    assert "offline" in forgotten.json()["detail"]
    assert registry.contains("online-camera") is True


def test_sensor_fleet_summary_counts_runtime_lifecycle_and_control() -> None:
    registry = SensorRegistry()
    client = TestClient(create_app(PerceptionPipeline(), sensor_registry=registry))

    online = client.post(
        "/api/v1/sensors/heartbeat",
        json={
            "schema_id": "visionrig/sensor-heartbeat/v2",
            "source_id": "online-ok",
            "source_type": "camera",
            "capture_active": True,
            "applied_revision": 0,
        },
    )
    assert online.status_code == 200

    preconfigured = client.patch(
        "/api/v1/sensors/preconfigured/metadata",
        json={"display_name": "Future camera"},
    )
    assert preconfigured.status_code == 200

    retired = client.post("/api/v1/sensors/retired-offline/retire")
    assert retired.status_code == 200

    pending_hb = client.post(
        "/api/v1/sensors/heartbeat",
        json={
            "schema_id": "visionrig/sensor-heartbeat/v2",
            "source_id": "pending-camera",
            "source_type": "camera",
            "capture_active": True,
            "applied_revision": 0,
        },
    )
    assert pending_hb.status_code == 200
    disable = client.patch(
        "/api/v1/sensors/pending-camera/metadata",
        json={"enabled": False},
    )
    assert disable.status_code == 200

    response = client.get("/api/v1/sensors/fleet")
    assert response.status_code == 200
    fleet = response.json()
    assert fleet["schema"] == "visionrig/sensor-fleet-summary/v2"
    assert fleet["state_revision"] == 5
    assert fleet["total"] == 4
    assert fleet["lifecycle"] == {"active": 3, "retired": 1}
    assert fleet["presence"] == {
        "online": 2,
        "stale": 0,
        "offline": 0,
        "unknown": 2,
    }
    assert fleet["control"] == {
        "converged": 1,
        "pending": 1,
        "unknown": 2,
    }
    assert fleet["attention_total"] == 2
    assert fleet["attention_truncated"] is False
    assert [item["source_id"] for item in fleet["attention"]] == [
        "pending-camera",
        "preconfigured",
    ]
    pending_item = fleet["attention"][0]
    assert pending_item["presence"] == "online"
    assert pending_item["control_status"] == "pending"
    assert pending_item["pending_seconds"] is not None
    unknown_item = fleet["attention"][1]
    assert unknown_item["presence"] == "unknown"
    assert unknown_item["control_status"] == "unknown"
    assert unknown_item["pending_seconds"] is None

    health = client.get("/health").json()
    assert health["schema"] == "visionrig/health/v24"
    health_fleet = health["sensor_fleet"]
    assert health_fleet["schema"] == fleet["schema"]
    assert health_fleet["total"] == fleet["total"]
    assert health_fleet["lifecycle"] == fleet["lifecycle"]
    assert health_fleet["presence"] == fleet["presence"]
    assert health_fleet["control"] == fleet["control"]
    assert health_fleet["attention_total"] == fleet["attention_total"]
    assert [
        item["source_id"] for item in health_fleet["attention"]
    ] == [
        item["source_id"] for item in fleet["attention"]
    ]


def test_sensor_fleet_attention_is_bounded() -> None:
    registry = SensorRegistry()
    for index in range(40):
        registry.patch(
            f"offline-{index:02d}",
            SensorMetadataPatch(display_name=f"Camera {index:02d}"),
        )
    client = TestClient(create_app(PerceptionPipeline(), sensor_registry=registry))

    fleet = client.get("/api/v1/sensors/fleet").json()
    assert fleet["state_revision"] == 40
    assert fleet["total"] == 40
    assert fleet["attention_total"] == 40
    assert len(fleet["attention"]) == 32
    assert fleet["attention_truncated"] is True
    assert fleet["attention"][0]["source_id"] == "offline-00"
    assert fleet["attention"][-1]["source_id"] == "offline-31"


def test_metadata_write_rejects_stale_state_revision_without_mutation() -> None:
    registry = SensorRegistry()
    registry.patch(
        "camera-concurrent",
        SensorMetadataPatch(display_name="Camera"),
    )
    assert registry.state_revision == 1
    client = TestClient(create_app(PerceptionPipeline(), sensor_registry=registry))

    accepted = client.patch(
        "/api/v1/sensors/camera-concurrent/metadata",
        params={"expected_state_revision": 1},
        json={"location": "Office"},
    )
    assert accepted.status_code == 200
    assert accepted.json()["schema"] == "visionrig/sensor-metadata/v2"
    assert accepted.json()["state_revision"] == 2
    assert registry.get("camera-concurrent").location == "Office"

    stale = client.patch(
        "/api/v1/sensors/camera-concurrent/metadata",
        params={"expected_state_revision": 1},
        json={"location": "Bedroom"},
    )
    assert stale.status_code == 409
    assert stale.json()["detail"] == {
        "code": "sensor_state_revision_conflict",
        "expected": 1,
        "current": 2,
    }
    assert registry.state_revision == 2
    assert registry.get("camera-concurrent").location == "Office"


def test_lifecycle_writes_enforce_expected_state_revision() -> None:
    registry = SensorRegistry()
    registry.patch(
        "camera-lifecycle",
        SensorMetadataPatch(display_name="Camera", enabled=False),
    )
    assert registry.state_revision == 1
    client = TestClient(create_app(PerceptionPipeline(), sensor_registry=registry))

    retired = client.post(
        "/api/v1/sensors/camera-lifecycle/retire",
        params={"expected_state_revision": 1},
    )
    assert retired.status_code == 200
    assert retired.json()["state_revision"] == 2

    stale_restore = client.post(
        "/api/v1/sensors/camera-lifecycle/restore",
        params={"expected_state_revision": 1},
    )
    assert stale_restore.status_code == 409
    assert stale_restore.json()["detail"]["current"] == 2
    assert registry.get("camera-lifecycle").retired_utc is not None

    restored = client.post(
        "/api/v1/sensors/camera-lifecycle/restore",
        params={"expected_state_revision": 2},
    )
    assert restored.status_code == 200
    assert restored.json()["state_revision"] == 3
    assert registry.get("camera-lifecycle").retired_utc is None

    retired_again = client.post(
        "/api/v1/sensors/camera-lifecycle/retire",
        params={"expected_state_revision": 3},
    )
    assert retired_again.status_code == 200
    assert retired_again.json()["state_revision"] == 4

    stale_forget = client.delete(
        "/api/v1/sensors/camera-lifecycle",
        params={"expected_state_revision": 3},
    )
    assert stale_forget.status_code == 409
    assert stale_forget.json()["detail"] == {
        "code": "sensor_state_revision_conflict",
        "expected": 3,
        "current": 4,
    }
    assert registry.contains("camera-lifecycle") is True

    forgotten = client.delete(
        "/api/v1/sensors/camera-lifecycle",
        params={"expected_state_revision": 4},
    )
    assert forgotten.status_code == 200
    assert forgotten.json()["state_revision"] == 5
    assert registry.contains("camera-lifecycle") is False
