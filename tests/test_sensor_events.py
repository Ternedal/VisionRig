import threading
import time

import pytest
from fastapi.testclient import TestClient

from visionrig.api import create_app
from visionrig.pipeline import PerceptionPipeline
from visionrig.sensor_events import SensorChangeJournal, SensorChangeJournalError
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
    assert [entry["event"]["state_revision"] for entry in first["entries"]] == [1, 1]
    assert registry.state_revision == 1
    cursor = first["next_cursor"]

    # Liveness refresh only: no semantic producer/discovery change.
    assert client.post("/api/v1/sensors/heartbeat", json=heartbeat).status_code == 200
    quiet = client.get(
        "/api/v1/sensors/changes",
        params={"after_cursor": cursor},
    ).json()
    assert quiet["entries"] == []
    assert quiet["next_cursor"] == cursor
    assert registry.state_revision == 1

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
    assert discovery_batch["entries"][0]["event"]["state_revision"] == 2
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
    assert [entry["event"]["state_revision"] for entry in patch_batch["entries"]] == [3, 3]
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
    assert runtime_batch["entries"][0]["event"]["state_revision"] == 3


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
    journal = SensorChangeJournal(stream_id="stream-live")
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
    assert body["schema"] == "visionrig/sensor-bootstrap-snapshot/v3"
    assert body["sensor_state_revision"] == 1
    assert body["change_stream_id"] == "stream-live"
    assert body["change_cursor"] == 2
    assert body["catalog"]["schema"] == "visionrig/sensor-catalog/v7"
    assert body["catalog"]["sources"][0]["source_id"] == "camera-bootstrap"
    assert body["fleet"]["schema"] == "visionrig/sensor-fleet-summary/v2"
    assert body["fleet"]["state_revision"] == 1
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
    assert after["entries"][0]["event"]["state_revision"] == 2


def test_empty_sensor_bootstrap_uses_zero_cursor() -> None:
    journal = SensorChangeJournal(stream_id="empty-stream")
    client = TestClient(
        create_app(
            PerceptionPipeline(),
            sensor_change_journal=journal,
        )
    )

    snapshot = client.get("/api/v1/sensors/bootstrap")
    assert snapshot.status_code == 200
    body = snapshot.json()
    assert body["schema"] == "visionrig/sensor-bootstrap-snapshot/v3"
    assert body["sensor_state_revision"] == 0
    assert body["change_stream_id"] == "empty-stream"
    assert body["change_cursor"] == 0
    assert body["catalog"] == {
        "schema": "visionrig/sensor-catalog/v7",
        "sources": [],
    }
    assert body["fleet"] == {
        "schema": "visionrig/sensor-fleet-summary/v2",
        "state_revision": 0,
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


def test_sensor_change_endpoint_reports_stream_reset() -> None:
    journal = SensorChangeJournal(stream_id="current-stream")
    journal.append(kind="registered", source_id="camera-a")
    client = TestClient(
        create_app(
            PerceptionPipeline(),
            sensor_change_journal=journal,
        )
    )

    response = client.get(
        "/api/v1/sensors/changes",
        params={
            "after_cursor": 99,
            "stream_id": "previous-process-stream",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["schema_id"] == "visionrig/sensor-change-batch/v2"
    assert body["stream_id"] == "current-stream"
    assert body["stream_reset"] is True
    assert body["entries"] == []
    assert body["next_cursor"] == 0
    assert body["newest_available_cursor"] == 1


def test_sensor_change_long_poll_wakes_on_new_event() -> None:
    journal = SensorChangeJournal(stream_id="long-poll")
    result = {}

    def reader() -> None:
        result["batch"] = journal.wait_for_changes(
            after_cursor=0,
            expected_stream_id="long-poll",
            wait_seconds=1.0,
        )

    thread = threading.Thread(target=reader)
    thread.start()
    time.sleep(0.05)
    journal.append(kind="registered", source_id="camera-long-poll")
    thread.join(timeout=2.0)

    assert thread.is_alive() is False
    batch = result["batch"]
    assert [entry.event.kind for entry in batch.entries] == ["registered"]
    assert batch.next_cursor == 1
    assert batch.stream_reset is False


def test_sensor_change_long_poll_times_out_cleanly() -> None:
    journal = SensorChangeJournal(stream_id="timeout-stream")
    started = time.monotonic()
    batch = journal.wait_for_changes(
        after_cursor=0,
        expected_stream_id="timeout-stream",
        wait_seconds=0.05,
    )
    elapsed = time.monotonic() - started

    assert elapsed >= 0.04
    assert batch.entries == ()
    assert batch.next_cursor == 0
    assert batch.stream_reset is False


def test_sensor_change_endpoint_long_poll_returns_immediately_on_stream_reset() -> None:
    journal = SensorChangeJournal(stream_id="current-stream")
    client = TestClient(
        create_app(
            PerceptionPipeline(),
            sensor_change_journal=journal,
        )
    )

    started = time.monotonic()
    response = client.get(
        "/api/v1/sensors/changes",
        params={
            "after_cursor": 50,
            "stream_id": "old-stream",
            "wait_seconds": 1.0,
        },
    )
    elapsed = time.monotonic() - started

    assert response.status_code == 200
    assert elapsed < 0.5
    assert response.json()["stream_reset"] is True


def test_persistent_sensor_change_journal_survives_restart(tmp_path) -> None:
    path = tmp_path / "sensor-changes.json"
    first = SensorChangeJournal(
        capacity=3,
        stream_id="persistent-stream",
        path=path,
    )
    for index in range(5):
        first.append(
            kind="metadata_changed",
            source_id=f"camera-{index}",
            payload={"index": index},
            occurred_utc=f"2026-09-26T12:40:0{index}+00:00",
        )

    assert first.persistent is True
    assert path.exists()

    restarted = SensorChangeJournal(capacity=3, path=path)
    assert restarted.stream_id == "persistent-stream"
    batch = restarted.read(after_cursor=1, limit=10)
    assert batch.gap is True
    assert [entry.cursor for entry in batch.entries] == [3, 4, 5]
    assert [entry.event.payload["index"] for entry in batch.entries] == [2, 3, 4]

    appended = restarted.append(
        kind="registered",
        source_id="camera-5",
        occurred_utc="2026-09-26T12:40:05+00:00",
    )
    assert appended.cursor == 6

    third = SensorChangeJournal(capacity=3, path=path)
    latest = third.read(after_cursor=3, limit=10)
    assert [entry.cursor for entry in latest.entries] == [4, 5, 6]
    assert latest.stream_id == "persistent-stream"


def test_corrupt_sensor_change_journal_fails_closed(tmp_path) -> None:
    path = tmp_path / "sensor-changes.json"
    path.write_text(
        '{"schema_id":"wrong","stream_id":"x","next_cursor":1,"entries":[]}',
        encoding="utf-8",
    )

    with pytest.raises(
        SensorChangeJournalError,
        match="invalid sensor change journal file",
    ):
        SensorChangeJournal(path=path)


def test_failed_sensor_change_persist_rolls_back_cursor_and_entries(
    tmp_path,
    monkeypatch,
) -> None:
    path = tmp_path / "sensor-changes.json"
    journal = SensorChangeJournal(
        stream_id="rollback-stream",
        path=path,
    )
    first = journal.append(kind="registered", source_id="camera-a")
    assert first.cursor == 1

    def fail_replace(_source, _target):
        raise OSError("disk failure")

    monkeypatch.setattr("visionrig.sensor_events.os.replace", fail_replace)

    with pytest.raises(
        SensorChangeJournalError,
        match="unable to persist sensor change journal",
    ):
        journal.append(kind="metadata_changed", source_id="camera-a")

    batch = journal.read(after_cursor=0)
    assert [entry.cursor for entry in batch.entries] == [1]
    assert batch.newest_available_cursor == 1

    monkeypatch.undo()
    second = journal.append(kind="metadata_changed", source_id="camera-a")
    assert second.cursor == 2


def test_persisted_stream_id_mismatch_fails_closed(tmp_path) -> None:
    path = tmp_path / "sensor-changes.json"
    SensorChangeJournal(
        stream_id="stored-stream",
        path=path,
    ).append(kind="registered", source_id="camera-a")

    with pytest.raises(
        SensorChangeJournalError,
        match="does not match persisted journal",
    ):
        SensorChangeJournal(
            stream_id="different-stream",
            path=path,
        )


def test_api_uses_restored_persistent_change_stream(tmp_path) -> None:
    path = tmp_path / "sensor-changes.json"
    initial = SensorChangeJournal(
        stream_id="restored-stream",
        path=path,
    )
    initial.append(kind="registered", source_id="camera-a")

    restored = SensorChangeJournal(path=path)
    client = TestClient(
        create_app(
            PerceptionPipeline(),
            sensor_change_journal=restored,
        )
    )

    health = client.get("/health").json()
    assert health["schema"] == "visionrig/health/v24"
    assert health["sensor_changes"]["durability"] == "persistent"
    assert health["sensor_changes"]["stream_id"] == "restored-stream"

    bootstrap = client.get("/api/v1/sensors/bootstrap").json()
    assert bootstrap["change_stream_id"] == "restored-stream"
    assert bootstrap["change_cursor"] == 1

    batch = client.get(
        "/api/v1/sensors/changes",
        params={
            "after_cursor": 0,
            "stream_id": "restored-stream",
        },
    ).json()
    assert batch["stream_reset"] is False
    assert [entry["cursor"] for entry in batch["entries"]] == [1]


def test_fleet_revision_exposes_change_feed_drift() -> None:
    registry = SensorRegistry()
    journal = SensorChangeJournal(stream_id="drift-stream")
    client = TestClient(
        create_app(
            PerceptionPipeline(),
            sensor_registry=registry,
            sensor_change_journal=journal,
        )
    )

    initial = client.patch(
        "/api/v1/sensors/camera-drift/metadata",
        json={"display_name": "Camera"},
    )
    assert initial.status_code == 200

    first_changes = client.get("/api/v1/sensors/changes").json()
    assert max(
        entry["event"]["state_revision"]
        for entry in first_changes["entries"]
    ) == 1
    assert client.get("/api/v1/sensors/fleet").json()["state_revision"] == 1

    # Simulate a crash window where registry state persisted but the matching
    # semantic journal append never happened.
    registry.patch(
        "camera-drift",
        SensorMetadataPatch(location="Office"),
    )
    assert registry.state_revision == 2

    unchanged_feed = client.get(
        "/api/v1/sensors/changes",
        params={
            "after_cursor": first_changes["next_cursor"],
            "stream_id": first_changes["stream_id"],
        },
    ).json()
    assert unchanged_feed["entries"] == []

    fleet = client.get("/api/v1/sensors/fleet").json()
    assert fleet["state_revision"] == 2
    assert fleet["state_revision"] > 1

    bootstrap = client.get("/api/v1/sensors/bootstrap").json()
    assert bootstrap["sensor_state_revision"] == 2
    assert bootstrap["fleet"]["state_revision"] == 2


def test_stale_operator_write_emits_no_sensor_change_event() -> None:
    registry = SensorRegistry()
    registry.patch(
        "camera-stale-event",
        SensorMetadataPatch(display_name="Camera"),
    )
    journal = SensorChangeJournal(stream_id="concurrency-stream")
    client = TestClient(
        create_app(
            PerceptionPipeline(),
            sensor_registry=registry,
            sensor_change_journal=journal,
        )
    )

    accepted = client.patch(
        "/api/v1/sensors/camera-stale-event/metadata",
        params={"expected_state_revision": 1},
        json={"location": "Office"},
    )
    assert accepted.status_code == 200
    first = client.get("/api/v1/sensors/changes").json()
    assert [entry["event"]["kind"] for entry in first["entries"]] == [
        "metadata_changed"
    ]
    assert first["entries"][0]["event"]["state_revision"] == 2

    stale = client.patch(
        "/api/v1/sensors/camera-stale-event/metadata",
        params={"expected_state_revision": 1},
        json={"location": "Bedroom"},
    )
    assert stale.status_code == 409

    after = client.get(
        "/api/v1/sensors/changes",
        params={
            "after_cursor": first["next_cursor"],
            "stream_id": first["stream_id"],
        },
    ).json()
    assert after["entries"] == []
    assert registry.state_revision == 2
