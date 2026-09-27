"""Bounded binary multimodal sensor packet contract.

SensorPacket v1/v2 transports one encoded RGB frame plus optional color-aligned
uint16 metric depth and uint16 infrared planes without base64 expansion. V2 can
compress numeric planes with bounded zlib decompression.
"""
from __future__ import annotations

from dataclasses import dataclass
import struct
from typing import Any, Literal
import zlib

from pydantic import BaseModel, ConfigDict, Field, ValidationError


SENSOR_PACKET_MEDIA_TYPE = "application/vnd.visionrig.sensor-packet"
_MAGIC_V1 = b"VRSP1\x00"
_MAGIC_V2 = b"VRSP2\x00"
_HEADER_LENGTH = struct.Struct(">I")
_MAX_PLANE_RAW_BYTES = 32 * 1024 * 1024


class SensorPacketError(ValueError):
    pass


class SensorPacketPlane(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    width: int = Field(ge=1, le=8192)
    height: int = Field(ge=1, le=8192)
    dtype: str = Field(pattern="^uint16$")
    byte_length: int = Field(ge=1)
    raw_byte_length: int | None = Field(default=None, ge=2)
    compression: Literal["none", "zlib"] = "none"
    alignment: str | None = None


class SensorPacketHeader(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: Literal[
        "visionrig/sensor-packet/v1",
        "visionrig/sensor-packet/v2",
    ]
    rgb_content_type: str
    rgb_byte_length: int = Field(ge=1)
    depth: SensorPacketPlane | None = None
    infrared: SensorPacketPlane | None = None


@dataclass(frozen=True, slots=True)
class SensorPacketTransport:
    schema: str
    packet_schema: str
    packet_bytes: int
    rgb_bytes: int
    depth_wire_bytes: int
    depth_raw_bytes: int
    depth_compression: str | None
    infrared_wire_bytes: int
    infrared_raw_bytes: int
    infrared_compression: str | None
    numeric_wire_bytes: int
    numeric_raw_bytes: int
    numeric_compression_ratio: float | None


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


def _expected_raw_plane_bytes(plane: SensorPacketPlane, *, label: str) -> int:
    expected = plane.width * plane.height * 2
    if expected > _MAX_PLANE_RAW_BYTES:
        raise SensorPacketError(f"{label} raw plane exceeds safety limit")
    declared_raw = plane.raw_byte_length if plane.raw_byte_length is not None else expected
    if declared_raw != expected:
        raise SensorPacketError(
            f"{label} raw_byte_length must equal width*height*2 for uint16"
        )
    if plane.compression == "none" and plane.byte_length != expected:
        raise SensorPacketError(
            f"{label} byte_length must equal width*height*2 when uncompressed"
        )
    return expected


def _decode_plane_bytes(
    plane: SensorPacketPlane,
    payload: bytes,
    *,
    label: str,
) -> bytes:
    expected = _expected_raw_plane_bytes(plane, label=label)
    if len(payload) != plane.byte_length:
        raise SensorPacketError(f"{label} payload length does not match header")
    if plane.compression == "none":
        return payload
    try:
        decoder = zlib.decompressobj()
        raw = decoder.decompress(payload, expected + 1)
        if len(raw) > expected or decoder.unconsumed_tail:
            raise SensorPacketError(f"{label} decompressed payload exceeds declared size")
        remaining = expected - len(raw)
        flushed = decoder.flush(max(1, remaining))
        if len(raw) + len(flushed) > expected:
            raise SensorPacketError(f"{label} decompressed payload exceeds declared size")
        raw += flushed
    except zlib.error as exc:
        raise SensorPacketError(f"{label} zlib payload is invalid") from exc
    if len(raw) != expected:
        raise SensorPacketError(f"{label} decompressed length does not match header")
    if not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
        raise SensorPacketError(f"{label} zlib stream contains trailing data")
    return raw


def inspect_sensor_packet(payload: bytes) -> SensorPacketHeader:
    if len(payload) < len(_MAGIC_V1) + _HEADER_LENGTH.size:
        raise SensorPacketError("sensor packet is truncated")
    magic = payload[: len(_MAGIC_V1)]
    if magic not in {_MAGIC_V1, _MAGIC_V2}:
        raise SensorPacketError("sensor packet magic/version is invalid")
    offset = len(_MAGIC_V1)
    (header_length,) = _HEADER_LENGTH.unpack(
        payload[offset : offset + _HEADER_LENGTH.size]
    )
    offset += _HEADER_LENGTH.size
    if header_length < 2 or header_length > 64 * 1024:
        raise SensorPacketError("sensor packet header length is invalid")
    if offset + header_length > len(payload):
        raise SensorPacketError("sensor packet header is truncated")
    try:
        header = SensorPacketHeader.model_validate_json(
            payload[offset : offset + header_length]
        )
    except (ValidationError, ValueError) as exc:
        raise SensorPacketError("sensor packet header is invalid") from exc

    expected_schema = (
        "visionrig/sensor-packet/v1"
        if magic == _MAGIC_V1
        else "visionrig/sensor-packet/v2"
    )
    if header.schema_id != expected_schema:
        raise SensorPacketError("sensor packet magic/schema version mismatch")
    if header.schema_id.endswith("/v1"):
        for plane in (header.depth, header.infrared):
            if plane is not None and (
                plane.compression != "none"
                or plane.raw_byte_length is not None
            ):
                raise SensorPacketError("SensorPacket/v1 planes must be uncompressed")
    return header


def describe_sensor_packet_transport(payload: bytes) -> SensorPacketTransport:
    header = inspect_sensor_packet(payload)

    def plane_sizes(plane: SensorPacketPlane | None) -> tuple[int, int, str | None]:
        if plane is None:
            return 0, 0, None
        raw = _expected_raw_plane_bytes(plane, label="plane")
        return plane.byte_length, raw, plane.compression

    depth_wire, depth_raw, depth_compression = plane_sizes(header.depth)
    ir_wire, ir_raw, ir_compression = plane_sizes(header.infrared)
    numeric_wire = depth_wire + ir_wire
    numeric_raw = depth_raw + ir_raw
    ratio = (
        round(numeric_wire / numeric_raw, 6)
        if numeric_raw > 0
        else None
    )
    return SensorPacketTransport(
        schema="visionrig/sensor-packet-transport/v1",
        packet_schema=header.schema_id,
        packet_bytes=len(payload),
        rgb_bytes=header.rgb_byte_length,
        depth_wire_bytes=depth_wire,
        depth_raw_bytes=depth_raw,
        depth_compression=depth_compression,
        infrared_wire_bytes=ir_wire,
        infrared_raw_bytes=ir_raw,
        infrared_compression=ir_compression,
        numeric_wire_bytes=numeric_wire,
        numeric_raw_bytes=numeric_raw,
        numeric_compression_ratio=ratio,
    )


def decode_sensor_packet(payload: bytes) -> DecodedSensorPacket:
    if len(payload) < len(_MAGIC_V1) + _HEADER_LENGTH.size:
        raise SensorPacketError("sensor packet is truncated")
    if payload[: len(_MAGIC_V1)] not in {_MAGIC_V1, _MAGIC_V2}:
        raise SensorPacketError("sensor packet magic/version is invalid")

    offset = len(_MAGIC_V1)
    (header_length,) = _HEADER_LENGTH.unpack(
        payload[offset : offset + _HEADER_LENGTH.size]
    )
    offset += _HEADER_LENGTH.size
    header = inspect_sensor_packet(payload)
    offset += header_length

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
        depth_bytes = _decode_plane_bytes(
            header.depth,
            payload[offset:end_depth],
            label="depth",
        )
        offset = end_depth

    infrared_bytes: bytes | None = None
    if header.infrared is not None:
        end_ir = offset + header.infrared.byte_length
        if end_ir > len(payload):
            raise SensorPacketError("sensor packet infrared payload is truncated")
        infrared_bytes = _decode_plane_bytes(
            header.infrared,
            payload[offset:end_ir],
            label="infrared",
        )
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
    compression: Literal["none", "zlib", "auto"] = "none",
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

    if compression not in {"none", "zlib", "auto"}:
        raise SensorPacketError("unsupported plane compression")

    planes: list[bytes] = []
    depth_header = None
    if depth_mm is not None:
        depth = np.asarray(depth_mm, dtype="<u2")
        if depth.ndim != 2:
            raise SensorPacketError("depth plane must be 2D")
        raw_depth_bytes = depth.tobytes(order="C")
        if len(raw_depth_bytes) > _MAX_PLANE_RAW_BYTES:
            raise SensorPacketError("depth raw plane exceeds safety limit")
        compressed_depth_bytes = zlib.compress(raw_depth_bytes)
        depth_compression: Literal["none", "zlib"]
        if compression == "zlib":
            depth_compression = "zlib"
            depth_bytes = compressed_depth_bytes
        elif compression == "auto" and len(compressed_depth_bytes) < len(raw_depth_bytes):
            depth_compression = "zlib"
            depth_bytes = compressed_depth_bytes
        else:
            depth_compression = "none"
            depth_bytes = raw_depth_bytes
        depth_header = SensorPacketPlane(
            width=int(depth.shape[1]),
            height=int(depth.shape[0]),
            dtype="uint16",
            byte_length=len(depth_bytes),
            raw_byte_length=(
                len(raw_depth_bytes) if depth_compression == "zlib" else None
            ),
            compression=depth_compression,
            alignment="color",
        )
        planes.append(depth_bytes)

    ir_header = None
    if infrared is not None:
        ir = np.asarray(infrared, dtype="<u2")
        if ir.ndim != 2:
            raise SensorPacketError("infrared plane must be 2D")
        raw_ir_bytes = ir.tobytes(order="C")
        if len(raw_ir_bytes) > _MAX_PLANE_RAW_BYTES:
            raise SensorPacketError("infrared raw plane exceeds safety limit")
        compressed_ir_bytes = zlib.compress(raw_ir_bytes)
        ir_compression: Literal["none", "zlib"]
        if compression == "zlib":
            ir_compression = "zlib"
            ir_bytes = compressed_ir_bytes
        elif compression == "auto" and len(compressed_ir_bytes) < len(raw_ir_bytes):
            ir_compression = "zlib"
            ir_bytes = compressed_ir_bytes
        else:
            ir_compression = "none"
            ir_bytes = raw_ir_bytes
        ir_header = SensorPacketPlane(
            width=int(ir.shape[1]),
            height=int(ir.shape[0]),
            dtype="uint16",
            byte_length=len(ir_bytes),
            raw_byte_length=(
                len(raw_ir_bytes) if ir_compression == "zlib" else None
            ),
            compression=ir_compression,
        )
        planes.append(ir_bytes)

    packet_version = "v2" if compression in {"zlib", "auto"} else "v1"
    header = SensorPacketHeader(
        schema_id=f"visionrig/sensor-packet/{packet_version}",
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
            _MAGIC_V2 if packet_version == "v2" else _MAGIC_V1,
            _HEADER_LENGTH.pack(len(header_bytes)),
            header_bytes,
            rgb_payload,
            *planes,
        )
    )
