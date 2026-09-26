from fastapi.testclient import TestClient

from visionrig.api import create_app
from visionrig.pipeline import PerceptionPipeline
from visionrig.sensor_events import SensorChangeJournal
from visionrig.sensor_registry import SensorMetadataPatch, SensorRegistry


def test_sensor_change_journal_reports_cursor_gap() -> None:
    journal = SensorChangeJournal(capacity=2, stream_id="stream-a")
    for index in range(4):
        journal.append(
            kind="metadata_changed",
            source_id=f"camera-{index}",
            payload={"index": index},
            occurred_utc=f"2026-09-26T10:00:0{index}+00:00",
        )

    batch = journal.read(after_cursor=1, limit=10)
    assert batch.schema_id == "visionrig/sensor-change-batch/v2"
    assert batch.stream_id == "stream-a"
    assert batch.stream_reset is False
    assert batch.gap is True
    assert batch.oldest_available_cursor == 3
    assert batch.newest_available_cursor == 4
    assert [entry.cursor for entry in batch.entries] == [3, 4]
    assert batch.next_cursor == 4


def test_sensor_change_feed_emits_semantic_changes_without_heartbeat_spam() -> None:
    registry = SensorRegistry()
    journal = SensorChangeJournal(stream_id="stream-live")
    client = TestClient(
        create_app(
            PerceptionPipeline(),
            sensor_registry=registry,
            sensor_change_journal=journal,
        )
    )

    heartbeat = {
        "schema_id": "visionrig/sensor-heartbeat/v2",
        "source_id": "camera-a",
        "source_type": "camera",
        "device": "usb-camera",
        "capabilities": ["rgb"],
        "capture_active": True,
        "applied_revision": 0,
    }
    assert client.post("/api/v1/sensors/heartbeat", json=heartbeat).status_code == 200

    first = client.get("/api/v1/sensors/changes").json()
    assert [entry["event"]["kind"] for entry in first["entries"]] == [
        "registered",
        "runtime_changed",
    ]
    cursor = first["next_cursor"]

    # Liveness refresh only: no semantic producer/discovery change.
    assert client.post("/api/v1/sensors/heartbeat", json=heartbeat).status_code == 200
    quiet = client.get(
        "/api/v1/sensors/changes",
        params={"after_cursor": cursor},
    ).json()
    assert quiet["entries"] == []
    assert quiet["next_cursor"] == cursor

    changed_discovery = dict(heartbeat)
    changed_discovery["capabilities"] = ["rgb", "depth"]
    assert (
        client.post("/api/v1/sensors/heartbeat", json=changed_discovery).status_code
        == 200
    )
    discovery_batch = client.get(
        "/api/v1/sensors/changes",
        params={"after_cursor": cursor},
    ).json()
    assert [entry["event"]["kind"] for entry in discovery_batch["entries"]] == [
        "discovery_changed"
    ]
    cursor = discovery_batch["next_cursor"]

    patch = client.patch(
        "/api/v1/sensors/camera-a/metadata",
        json={
            "display_name": "Desk camera",
            "enabled": False,
        },
    )
    assert patch.status_code == 200
    patch_batch = client.get(
        "/api/v1/sensors/changes",
        params={"after_cursor": cursor},
    ).json()
    assert [entry["event"]["kind"] for entry in patch_batch["entries"]] == [
        "metadata_changed",
        "control_changed",
    ]
    assert patch_batch["entries"][1]["event"]["payload"]["revision"] == 1
    cursor = patch_batch["next_cursor"]

    applied = dict(changed_discovery)
    applied["capture_active"] = False
    applied["applied_revision"] = 1
    assert client.post("/api/v1/sensors/heartbeat", json=applied).status_code == 200
    runtime_batch = client.get(
        "/api/v1/sensors/changes",
        params={"after_cursor": cursor},
    ).json()
    assert [entry["event"]["kind"] for entry in runtime_batch["entries"]] == [
        "runtime_changed"
    ]
    assert runtime_batch["entries"][0]["event"]["payload"] == {
        "capture_active": False,
        "applied_revision": 1,
        "presence": "online",
    }


def test_sensor_change_feed_tracks_lifecycle_and_forget() -> None:
    registry = SensorRegistry()
    journal = SensorChangeJournal()
    registry.patch(
        "old-camera",
        SensorMetadataPatch(
            display_name="Old camera",
            enabled=False,
        ),
    )
    client = TestClient(
        create_app(
            PerceptionPipeline(),
            sensor_registry=registry,
            sensor_change_journal=journal,
        )
    )

    retired = client.post("/api/v1/sensors/old-camera/retire")
    assert retired.status_code == 200
    restored = client.post("/api/v1/sensors/old-camera/restore")
    assert restored.status_code == 200
    retired_again = client.post("/api/v1/sensors/old-camera/retire")
    assert retired_again.status_code == 200
    forgotten = client.delete("/api/v1/sensors/old-camera")
    assert forgotten.status_code == 200

    batch = client.get("/api/v1/sensors/changes").json()
    assert [entry["event"]["kind"] for entry in batch["entries"]] == [
        "retired",
        "restored",
        "retired",
        "forgotten",
    ]
    assert batch["entries"][-1]["event"]["source_id"] == "old-camera"


def test_preconfigured_sensor_emits_registered_then_metadata_change() -> None:
    journal = SensorChangeJournal()
    client = TestClient(
        create_app(
            PerceptionPipeline(),
            sensor_change_journal=journal,
        )
    )

    response = client.patch(
        "/api/v1/sensors/future-camera/metadata",
        json={"display_name": "Future camera"},
    )
    assert response.status_code == 200

    batch = client.get("/api/v1/sensors/changes").json()
    assert [entry["event"]["kind"] for entry in batch["entries"]] == [
        "registered",
        "metadata_changed",
    ]


def test_sensor_bootstrap_snapshot_returns_state_and_change_cursor() -> None:
    registry = SensorRegistry()
    journal = SensorChangeJournal()
    client = TestClient(
        create_app(
            PerceptionPipeline(),
            sensor_registry=registry,
            sensor_change_journal=journal,
        )
    )

    first = client.patch(
        "/api/v1/sensors/camera-bootstrap/metadata",
        json={"display_name": "Bootstrap camera"},
    )
    assert first.status_code == 200

    before = client.get("/api/v1/sensors/changes").json()
    assert before["next_cursor"] == 2

    snapshot = client.get("/api/v1/sensors/bootstrap")
    assert snapshot.status_code == 200
    body = snapshot.json()
    assert body["schema"] == "visionrig/sensor-bootstrap-snapshot/v2"
    assert body["change_stream_id"] == "stream-live"
    assert body["change_cursor"] == 2
    assert body["catalog"]["schema"] == "visionrig/sensor-catalog/v7"
    assert body["catalog"]["sources"][0]["source_id"] == "camera-bootstrap"
    assert body["fleet"]["schema"] == "visionrig/sensor-fleet-summary/v1"
    assert body["fleet"]["total"] == 1

    changed = client.patch(
        "/api/v1/sensors/camera-bootstrap/metadata",
        json={"enabled": False},
    )
    assert changed.status_code == 200

    after = client.get(
        "/api/v1/sensors/changes",
        params={
            "after_cursor": body["change_cursor"],
            "stream_id": body["change_stream_id"],
        },
    ).json()
    assert [entry["event"]["kind"] for entry in after["entries"]] == [
        "control_changed"
    ]
    assert after["entries"][0]["event"]["payload"]["revision"] == 1


def test_empty_sensor_bootstrap_uses_zero_cursor() -> None:
    client = TestClient(create_app(PerceptionPipeline()))

    snapshot = client.get("/api/v1/sensors/bootstrap")
    assert snapshot.status_code == 200
    body = snapshot.json()
    assert body["schema"] == "visionrig/sensor-bootstrap-snapshot/v2"
    assert body["change_cursor"] == 0
    assert isinstance(body["change_stream_id"], str)
    assert body["change_stream_id"]
    assert body["catalog"] == {
            "schema": "visionrig/sensor-catalog/v7",
            "sources": [],
        }
    assert body["fleet"] == {
            "schema": "visionrig/sensor-fleet-summary/v1",
            "total": 0,
            "lifecycle": {"active": 0, "retired": 0},
            "presence": {
                "online": 0,
                "stale": 0,
                "offline": 0,
                "unknown": 0,
            },
            "control": {
                "converged": 0,
                "pending": 0,
                "unknown": 0,
            },
            "attention": [],
            "attention_total": 0,
            "attention_truncated": False,
        }
        "catalog": {
            "schema": "visionrig/sensor-catalog/v7",
            "sources": [],
        },
        "fleet": {
            "schema": "visionrig/sensor-fleet-summary/v1",
            "total": 0,
            "lifecycle": {"active": 0, "retired": 0},
            "presence": {
                "online": 0,
                "stale": 0,
                "offline": 0,
                "unknown": 0,
            },
            "control": {
                "converged": 0,
                "pending": 0,
                "unknown": 0,
            },
            "attention": [],
            "attention_total": 0,
            "attention_truncated": False,
        },
    }


def test_sensor_change_feed_detects_stream_restart() -> None:
    old = SensorChangeJournal(stream_id="old-stream")
    old.append(kind="metadata_changed", source_id="camera-a")
    old_batch = old.read(after_cursor=0)
    assert old_batch.stream_id == "old-stream"
    assert old_batch.next_cursor == 1

    restarted = SensorChangeJournal(stream_id="new-stream")
    restarted.append(kind="registered", source_id="camera-b")
    reset = restarted.read(
        after_cursor=old_batch.next_cursor,
        expected_stream_id=old_batch.stream_id,
    )
    assert reset.stream_id == "new-stream"
    assert reset.stream_reset is True
    assert reset.entries == ()
    assert reset.next_cursor == 0
    assert reset.newest_available_cursor == 1
