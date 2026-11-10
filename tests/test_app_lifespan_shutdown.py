"""Shutdown hooks run from the app lifespan: the registry flushes pending
heartbeat bookkeeping and the ModelRig publisher is closed. (Starlette 1.x
removed add_event_handler, which the publisher hook used to rely on.)"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from visionrig.api import create_app
from visionrig.sensor_registry import SensorRegistry


class _Publisher:
    def __init__(self) -> None:
        self.closed = 0

    def close(self) -> None:
        self.closed += 1


def test_app_builds_with_a_publisher_and_closes_it_on_shutdown(tmp_path) -> None:
    now = [datetime(2026, 11, 7, 5, 0, tzinfo=timezone.utc)]
    registry = SensorRegistry(
        tmp_path / "registry.json", clock=lambda: now[0], heartbeat_persist_interval_seconds=3600.0
    )
    registry.observe("camera-a", source_type="camera", device="usb-camera", capabilities=("rgb",))
    now[0] = now[0].replace(second=1)
    registry.observe("camera-a", source_type="camera", device="usb-camera", capabilities=("rgb",))
    assert registry.heartbeat_dirty

    publisher = _Publisher()
    app = create_app(modelrig_publisher=publisher, sensor_registry=registry)  # type: ignore[arg-type]
    with TestClient(app):
        pass

    assert publisher.closed == 1
    assert not registry.heartbeat_dirty
    assert SensorRegistry(tmp_path / "registry.json").get_discovery("camera-a").observation_count == 2
