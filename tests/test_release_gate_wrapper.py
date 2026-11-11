"""The release-gate wrapper must point the physical qualifier at the core service."""

import re
from dataclasses import fields
from pathlib import Path
from urllib.parse import urlsplit

from visionrig.gateway import GatewayConfig
from visionrig.physical_qualification import DEFAULT_VISIONRIG_URL

WRAPPER = Path(__file__).resolve().parents[1] / "run-v1-release-gate.ps1"


def _wrapper_default_url() -> str:
    text = WRAPPER.read_text(encoding="utf-8-sig")
    match = re.search(r'\[string\]\$VisionRigUrl\s*=\s*"([^"]+)"', text)
    assert match, "run-v1-release-gate.ps1 no longer declares a -VisionRigUrl default"
    return match.group(1)


def test_wrapper_default_is_the_qualifier_default():
    assert _wrapper_default_url() == DEFAULT_VISIONRIG_URL


def test_wrapper_default_is_not_the_sensor_gateway():
    # The gateway serves only its own /health and the sensor routes. The
    # qualifier reads /health with a perception schema, /api/v1/sensors/status
    # and /api/v1/perception/events, which only the core service has.
    gateway_port = next(f.default for f in fields(GatewayConfig) if f.name == "bind_port")
    assert urlsplit(_wrapper_default_url()).port != gateway_port


def test_wrapper_passes_the_url_to_both_device_qualifications():
    text = WRAPPER.read_text(encoding="utf-8-sig")
    assert text.count('"--visionrig-url", $VisionRigUrl') == 2
