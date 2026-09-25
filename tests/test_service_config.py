import pytest

from visionrig.service_config import ServiceConfig, ServiceConfigError


def test_service_config_reads_optional_pipeline_switches(monkeypatch) -> None:
    monkeypatch.setenv("VISIONRIG_OCR", "1")
    monkeypatch.setenv("VISIONRIG_LANDMARKS", "true")
    monkeypatch.setenv("VISIONRIG_SPATIAL_RELATIONS", "0")
    monkeypatch.setenv("VISIONRIG_FORCE_CPU", "yes")
    monkeypatch.setenv("VISIONRIG_MAX_SENSOR_FRAME_BYTES", "4096")

    config = ServiceConfig.from_env()
    assert config.ocr is True
    assert config.landmarks is True
    assert config.spatial_relations is False
    assert config.prefer_cuda is False
    assert config.max_sensor_frame_bytes == 4096


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
