"""Bounded binary multimodal sensor packet contract.

SensorPacket/v1 transports one encoded RGB frame plus optional color-aligned
uint16 metric depth and uint16 infrared planes without base64 expansion.
"""
from __future__ import annotations

from dataclasses import dataclass
import struct
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError


SENSOR_PACKET_MEDIA_TYPE = "application/vnd.visionrig.sensor-packet"
_MAGIC = b"VRSP1\x00"
_HEADER_LENGTH = struct.Struct(">I")


class SensorPacketError(ValueError):
    pass


class SensorPacketPlane(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    width: int = Field(ge=1, le=8192)
    height: int = Field(ge=1, le=8192)
    dtype: str = Field(pattern="^uint16$")
    byte_length: int = Field(ge=2)
    alignment: str | None = None


class SensorPacketHeader(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: str = Field(pattern="^visionrig/sensor-packet/v1$")
    rgb_content_type: str
    rgb_byte_length: int = Field(ge=1)
    depth: SensorPacketPlane | None = None
    infrared: SensorPacketPlane | None = None


@dataclass(frozen=True, slots=True)
class DecodedSensorPacket:
    rgb_content_type: str
    rgb_payload: bytes
    depth_mm: Any | None
    infrared: Any | None


class ArrayMetricDepthSampler:
    """Sample a color-aligned millimeter depth plane at normalized RGB coords."""

    def __init__(self, depth_mm: Any) -> None:
        self._depth_mm = depth_mm
        self._height = int(depth_mm.shape[0])
        self._width = int(depth_mm.shape[1])

    def distance_m(self, x: float, y: float) -> float | None:
        if not 0.0 <= x <= 1.0 or not 0.0 <= y <= 1.0:
            return None
        px = min(self._width - 1, max(0, int(round(x * (self._width - 1)))))
        py = min(self._height - 1, max(0, int(round(y * (self._height - 1)))))
        value = int(self._depth_mm[py, px])
        if value <= 0:
            return None
        return value / 1000.0


def _validate_plane_bytes(plane: SensorPacketPlane, payload: bytes, *, label: str) -> None:
    expected = plane.width * plane.height * 2
    if plane.byte_length != expected:
        raise SensorPacketError(
            f"{label} byte_length must equal width*height*2 for uint16"
        )
    if len(payload) != expected:
        raise SensorPacketError(f"{label} payload length does not match header")


def decode_sensor_packet(payload: bytes) -> DecodedSensorPacket:
    if len(payload) < len(_MAGIC) + _HEADER_LENGTH.size:
        raise SensorPacketError("sensor packet is truncated")
    if payload[: len(_MAGIC)] != _MAGIC:
        raise SensorPacketError("sensor packet magic/version is invalid")

    offset = len(_MAGIC)
    (header_length,) = _HEADER_LENGTH.unpack(
        payload[offset : offset + _HEADER_LENGTH.size]
    )
    offset += _HEADER_LENGTH.size
    if header_length < 2 or header_length > 64 * 1024:
        raise SensorPacketError("sensor packet header length is invalid")
    if offset + header_length > len(payload):
        raise SensorPacketError("sensor packet header is truncated")

    header_bytes = payload[offset : offset + header_length]
    offset += header_length
    try:
        header = SensorPacketHeader.model_validate_json(header_bytes)
    except (ValidationError, ValueError) as exc:
        raise SensorPacketError("sensor packet header is invalid") from exc

    rgb_type = header.rgb_content_type.split(";", 1)[0].strip().lower()
    if rgb_type not in {"image/jpeg", "image/png", "image/webp"}:
        raise SensorPacketError("sensor packet RGB media type is unsupported")

    end_rgb = offset + header.rgb_byte_length
    if end_rgb > len(payload):
        raise SensorPacketError("sensor packet RGB payload is truncated")
    rgb_payload = payload[offset:end_rgb]
    offset = end_rgb

    depth_bytes: bytes | None = None
    if header.depth is not None:
        if header.depth.alignment != "color":
            raise SensorPacketError("depth plane must be color-aligned")
        end_depth = offset + header.depth.byte_length
        if end_depth > len(payload):
            raise SensorPacketError("sensor packet depth payload is truncated")
        depth_bytes = payload[offset:end_depth]
        _validate_plane_bytes(header.depth, depth_bytes, label="depth")
        offset = end_depth

    infrared_bytes: bytes | None = None
    if header.infrared is not None:
        end_ir = offset + header.infrared.byte_length
        if end_ir > len(payload):
            raise SensorPacketError("sensor packet infrared payload is truncated")
        infrared_bytes = payload[offset:end_ir]
        _validate_plane_bytes(header.infrared, infrared_bytes, label="infrared")
        offset = end_ir

    if offset != len(payload):
        raise SensorPacketError("sensor packet contains trailing bytes")

    try:
        import numpy as np  # type: ignore[import-not-found]
    except ImportError as exc:
        if depth_bytes is not None or infrared_bytes is not None:
            raise SensorPacketError(
                'multimodal sensor packets require numpy; install VisionRig ".[inference]"'
            ) from exc
        np = None  # type: ignore[assignment]

    depth_mm = None
    if depth_bytes is not None and header.depth is not None:
        depth_mm = np.frombuffer(depth_bytes, dtype="<u2").reshape(
            header.depth.height,
            header.depth.width,
        ).copy()

    infrared = None
    if infrared_bytes is not None and header.infrared is not None:
        infrared = np.frombuffer(infrared_bytes, dtype="<u2").reshape(
            header.infrared.height,
            header.infrared.width,
        ).copy()

    return DecodedSensorPacket(
        rgb_content_type=rgb_type,
        rgb_payload=rgb_payload,
        depth_mm=depth_mm,
        infrared=infrared,
    )


def encode_sensor_packet(
    *,
    rgb_payload: bytes,
    rgb_content_type: str,
    depth_mm: Any | None = None,
    infrared: Any | None = None,
) -> bytes:
    if not rgb_payload:
        raise SensorPacketError("RGB payload is empty")

    normalized_rgb_type = rgb_content_type.split(";", 1)[0].strip().lower()
    if normalized_rgb_type not in {"image/jpeg", "image/png", "image/webp"}:
        raise SensorPacketError("RGB media type is unsupported")

    try:
        import numpy as np  # type: ignore[import-not-found]
    except ImportError as exc:
        if depth_mm is not None or infrared is not None:
            raise SensorPacketError("encoding depth/infrared requires numpy") from exc
        np = None  # type: ignore[assignment]

    planes: list[bytes] = []
    depth_header = None
    if depth_mm is not None:
        depth = np.asarray(depth_mm, dtype="<u2")
        if depth.ndim != 2:
            raise SensorPacketError("depth plane must be 2D")
        depth_bytes = depth.tobytes(order="C")
        depth_header = SensorPacketPlane(
            width=int(depth.shape[1]),
            height=int(depth.shape[0]),
            dtype="uint16",
            byte_length=len(depth_bytes),
            alignment="color",
        )
        planes.append(depth_bytes)

    ir_header = None
    if infrared is not None:
        ir = np.asarray(infrared, dtype="<u2")
        if ir.ndim != 2:
            raise SensorPacketError("infrared plane must be 2D")
        ir_bytes = ir.tobytes(order="C")
        ir_header = SensorPacketPlane(
            width=int(ir.shape[1]),
            height=int(ir.shape[0]),
            dtype="uint16",
            byte_length=len(ir_bytes),
        )
        planes.append(ir_bytes)

    header = SensorPacketHeader(
        schema_id="visionrig/sensor-packet/v1",
        rgb_content_type=normalized_rgb_type,
        rgb_byte_length=len(rgb_payload),
        depth=depth_header,
        infrared=ir_header,
    )
    header_bytes = header.model_dump_json().encode("utf-8")
    if len(header_bytes) > 64 * 1024:
        raise SensorPacketError("sensor packet header is too large")

    return b"".join(
        (
            _MAGIC,
            _HEADER_LENGTH.pack(len(header_bytes)),
            header_bytes,
            rgb_payload,
            *planes,
        )
    )
