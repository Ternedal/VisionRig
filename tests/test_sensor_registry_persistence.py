import json
from datetime import datetime, timezone

import pytest

from visionrig.sensor_registry import (
    SensorMetadataPatch,
    SensorRegistry,
    SensorRegistryError,
)


def test_persistent_registry_survives_restart(tmp_path) -> None:
    path = tmp_path / "sensor-registry.json"

    first = SensorRegistry(path)
    first.patch(
        "kinect-living-room",
        SensorMetadataPatch(
            display_name="Living room Kinect",
            location="Living room",
            role="tracking",
            enabled=False,
        ),
    )

    second = SensorRegistry(path)
    restored = second.get("kinect-living-room")
    assert restored.display_name == "Living room Kinect"
    assert restored.location == "Living room"
    assert restored.role == "tracking"
    assert restored.enabled is False

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_id"] == "visionrig/sensor-registry-file/v1"
    assert payload["entries"][0]["source_id"] == "kinect-living-room"


def test_persistent_registry_writes_sorted_entries(tmp_path) -> None:
    path = tmp_path / "sensor-registry.json"
    registry = SensorRegistry(path)
    registry.patch("z-camera", SensorMetadataPatch(display_name="Z"))
    registry.patch("a-camera", SensorMetadataPatch(display_name="A"))

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert [entry["source_id"] for entry in payload["entries"]] == [
        "a-camera",
        "z-camera",
    ]


def test_corrupt_registry_fails_closed(tmp_path) -> None:
    path = tmp_path / "sensor-registry.json"
    path.write_text('{"schema_id":"wrong","entries":[]}', encoding="utf-8")

    with pytest.raises(SensorRegistryError, match="invalid sensor registry file"):
        SensorRegistry(path)


def test_failed_persist_rolls_back_in_memory_state(tmp_path, monkeypatch) -> None:
    path = tmp_path / "sensor-registry.json"
    registry = SensorRegistry(path)
    registry.patch("cam-a", SensorMetadataPatch(display_name="Camera A"))

    def fail_replace(_source, _target):
        raise OSError("disk failure")

    monkeypatch.setattr("visionrig.sensor_registry.os.replace", fail_replace)

    with pytest.raises(SensorRegistryError, match="unable to persist"):
        registry.patch("cam-a", SensorMetadataPatch(display_name="Broken update"))

    assert registry.get("cam-a").display_name == "Camera A"


def test_first_seen_registration_is_persistent_and_idempotent(tmp_path) -> None:
    path = tmp_path / "sensor-registry.json"
    registry = SensorRegistry(path)

    created = registry.ensure_registered("quest-living-room")
    assert created.source_id == "quest-living-room"
    assert created.enabled is True

    registry.patch(
        "quest-living-room",
        SensorMetadataPatch(
            display_name="Kaliv Quest",
            location="Living room",
            role="vr",
            enabled=False,
        ),
    )

    same = registry.ensure_registered("quest-living-room")
    assert same.display_name == "Kaliv Quest"
    assert same.location == "Living room"
    assert same.role == "vr"
    assert same.enabled is False

    restarted = SensorRegistry(path)
    restored = restarted.get("quest-living-room")
    assert restored.display_name == "Kaliv Quest"
    assert restored.enabled is False


def test_discovery_survives_restart_and_preserves_capabilities(tmp_path) -> None:
    path = tmp_path / "sensor-registry.json"
    registry = SensorRegistry(path)

    registry.observe(
        "kinect-living-room",
        source_type="camera",
        device="kinect-v2",
        capabilities=("RGB", "depth", "infrared", "depth"),
    )

    restarted = SensorRegistry(path)
    discovery = restarted.get_discovery("kinect-living-room")
    assert discovery is not None
    assert discovery.source_type == "camera"
    assert discovery.device == "kinect-v2"
    assert discovery.capabilities == ("depth", "infrared", "rgb")


def test_discovery_rejects_source_type_reuse(tmp_path) -> None:
    from visionrig.sensor_registry import SensorIdentityConflict

    registry = SensorRegistry(tmp_path / "sensor-registry.json")
    registry.observe("shared-id", source_type="camera")

    with pytest.raises(SensorIdentityConflict, match="already registered"):
        registry.observe("shared-id", source_type="vr")


def test_discovery_first_last_seen_and_count_survive_restart(tmp_path) -> None:
    path = tmp_path / "sensor-registry.json"
    now = [datetime(2026, 9, 26, 4, 30, tzinfo=timezone.utc)]
    registry = SensorRegistry(path, clock=lambda: now[0])

    first = registry.observe(
        "camera-a",
        source_type="camera",
        device="usb-camera",
        capabilities=("rgb",),
    )
    assert first.first_seen_utc == "2026-09-26T04:30:00+00:00"
    assert first.last_seen_utc == "2026-09-26T04:30:00+00:00"
    assert first.observation_count == 1

    now[0] = datetime(2026, 9, 26, 4, 35, tzinfo=timezone.utc)
    second = registry.observe(
        "camera-a",
        source_type="camera",
        capabilities=("rgb", "depth"),
    )
    assert second.first_seen_utc == first.first_seen_utc
    assert second.last_seen_utc == "2026-09-26T04:35:00+00:00"
    assert second.observation_count == 2
    assert second.device == "usb-camera"
    assert second.capabilities == ("depth", "rgb")

    restarted = SensorRegistry(path)
    restored = restarted.get_discovery("camera-a")
    assert restored is not None
    assert restored.first_seen_utc == "2026-09-26T04:30:00+00:00"
    assert restored.last_seen_utc == "2026-09-26T04:35:00+00:00"
    assert restored.observation_count == 2


def test_legacy_discovery_without_timestamps_still_loads(tmp_path) -> None:
    path = tmp_path / "sensor-registry.json"
    path.write_text(
        json.dumps(
            {
                "schema_id": "visionrig/sensor-registry-file/v1",
                "entries": [{"source_id": "legacy-camera"}],
                "discovery": [
                    {
                        "source_id": "legacy-camera",
                        "source_type": "camera",
                        "device": "legacy-usb",
                        "capabilities": ["rgb"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    registry = SensorRegistry(path)
    discovery = registry.get_discovery("legacy-camera")
    assert discovery is not None
    assert discovery.first_seen_utc is None
    assert discovery.last_seen_utc is None
    assert discovery.observation_count == 0


def test_control_revision_and_change_time_survive_restart(tmp_path) -> None:
    path = tmp_path / "sensor-registry.json"
    now = [datetime(2026, 9, 26, 8, 0, tzinfo=timezone.utc)]
    registry = SensorRegistry(path, clock=lambda: now[0])

    registry.patch("camera-a", SensorMetadataPatch(enabled=False))
    now[0] = datetime(2026, 9, 26, 8, 5, tzinfo=timezone.utc)
    registry.patch("camera-a", SensorMetadataPatch(enabled=True))
    assert registry.control_revision("camera-a") == 2
    assert registry.control_state("camera-a").changed_utc == "2026-09-26T08:05:00+00:00"

    restarted = SensorRegistry(path, clock=lambda: datetime(2026, 9, 26, 8, 6, tzinfo=timezone.utc))
    assert restarted.control_revision("camera-a") == 2
    assert restarted.control_state("camera-a").changed_utc == "2026-09-26T08:05:00+00:00"
    assert restarted.control_pending_seconds("camera-a") == 60.0
    desired = restarted.desired_state("camera-a")
    assert desired.enabled is True
    assert desired.revision == 2
