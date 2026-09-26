import pytest

from visionrig.service_config import ServiceConfig, ServiceConfigError


def test_service_config_reads_optional_pipeline_switches(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VISIONRIG_OCR", "1")
    monkeypatch.setenv("VISIONRIG_LANDMARKS", "true")
    monkeypatch.setenv("VISIONRIG_SPATIAL_RELATIONS", "0")
    monkeypatch.setenv("VISIONRIG_FORCE_CPU", "yes")
    monkeypatch.setenv("VISIONRIG_MAX_SENSOR_FRAME_BYTES", "4096")
    monkeypatch.setenv("VISIONRIG_MODELRIG_BRIDGE", "1")
    monkeypatch.setenv(
        "VISIONRIG_MODELRIG_WORKER_URL",
        "http://127.0.0.1:8099",
    )
    registry_path = tmp_path / "sensor-registry.json"
    change_path = tmp_path / "sensor-changes.json"
    monkeypatch.setenv("VISIONRIG_SENSOR_REGISTRY_FILE", str(registry_path))
    monkeypatch.setenv("VISIONRIG_SENSOR_CHANGE_JOURNAL_FILE", str(change_path))

    config = ServiceConfig.from_env()
    assert config.ocr is True
    assert config.landmarks is True
    assert config.spatial_relations is False
    assert config.prefer_cuda is False
    assert config.max_sensor_frame_bytes == 4096
    assert config.modelrig_bridge is True
    assert config.modelrig_worker_url == "http://127.0.0.1:8099"
    assert config.sensor_registry_file == str(registry_path)
    assert config.sensor_change_journal_file == str(change_path)

    publisher = config.build_modelrig_publisher()
    assert publisher is not None
    publisher.close()

    registry = config.build_sensor_registry()
    assert registry.path == registry_path
    assert registry.persistent is True

    journal = config.build_sensor_change_journal()
    assert journal.path == change_path
    assert journal.persistent is True


def test_sensor_registry_path_defaults_under_home(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("VISIONRIG_SENSOR_REGISTRY_FILE", raising=False)
    monkeypatch.setattr("visionrig.service_config.Path.home", lambda: tmp_path)

    config = ServiceConfig.from_env()
    assert config.sensor_registry_file == str(
        tmp_path / ".visionrig" / "sensor-registry.json"
    )


def test_sensor_registry_can_be_explicitly_ephemeral(monkeypatch) -> None:
    monkeypatch.setenv("VISIONRIG_SENSOR_REGISTRY_FILE", "   ")
    config = ServiceConfig.from_env()
    assert config.sensor_registry_file is None
    assert config.build_sensor_registry().persistent is False


def test_spatial_relations_default_on(monkeypatch) -> None:
    monkeypatch.delenv("VISIONRIG_SPATIAL_RELATIONS", raising=False)
    assert ServiceConfig.from_env().spatial_relations is True


def test_service_config_rejects_ambiguous_boolean(monkeypatch) -> None:
    monkeypatch.setenv("VISIONRIG_OCR", "sometimes")
    with pytest.raises(ServiceConfigError):
        ServiceConfig.from_env()


def test_service_config_rejects_unbounded_frame_limit(monkeypatch) -> None:
    monkeypatch.setenv("VISIONRIG_MAX_SENSOR_FRAME_BYTES", str(128 * 1024 * 1024))
    with pytest.raises(ServiceConfigError):
        ServiceConfig.from_env()


def test_modelrig_bridge_defaults_off(monkeypatch) -> None:
    monkeypatch.delenv("VISIONRIG_MODELRIG_BRIDGE", raising=False)
    config = ServiceConfig.from_env()
    assert config.modelrig_bridge is False
    assert config.build_modelrig_publisher() is None


def test_sensor_change_journal_path_defaults_under_home(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("VISIONRIG_SENSOR_CHANGE_JOURNAL_FILE", raising=False)
    monkeypatch.setattr("visionrig.service_config.Path.home", lambda: tmp_path)

    config = ServiceConfig.from_env()
    assert config.sensor_change_journal_file == str(
        tmp_path / ".visionrig" / "sensor-changes.json"
    )


def test_sensor_change_journal_can_be_explicitly_ephemeral(monkeypatch) -> None:
    monkeypatch.setenv("VISIONRIG_SENSOR_CHANGE_JOURNAL_FILE", "   ")
    config = ServiceConfig.from_env()
    assert config.sensor_change_journal_file is None
    assert config.build_sensor_change_journal().persistent is False
