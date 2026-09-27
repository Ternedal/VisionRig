"""Reference Windows/Linux webcam/screen producer for the sensor gateway."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import os
import time
from pathlib import Path
from typing import Any, Callable, Literal, Protocol

from .kinect_v2 import KinectV2Source
from .producer import GatewayFrameProducer, ProducerError, ProducerProtocolError
from .producer_state import ProducerStateError, ProducerStateStore
from .screen_source import MssScreenSource
from .sensor_packet import (
    SENSOR_PACKET_PAYLOAD_WARNING_UTILIZATION,
    encode_sensor_packet,
)
from .sources import CameraSource, FrameSource, ImageFileSource


@dataclass(frozen=True, slots=True)
class PacketTransportPlan:
    max_payload_bytes: int
    compression: Literal["none", "zlib", "auto"]
    target_utilization: float
    packet_encoder: Callable[[Any, bytes], bytes]


def _select_packet_compression(
    supported: tuple[str, ...],
) -> Literal["none", "zlib", "auto"]:
    normalized = {value.strip().lower() for value in supported}
    has_none = "none" in normalized
    has_zlib = "zlib" in normalized
    if has_none and has_zlib:
        return "auto"
    if has_zlib:
        return "zlib"
    if has_none:
        return "none"
    raise ProducerProtocolError(
        "VisionRig gateway/core advertises no supported SensorPacket compression"
    )


class _ProducerClient(Protocol):
    def fetch_capabilities(self): ...
    def fetch_desired_state(self): ...
    def send_heartbeat(
        self,
        *,
        capabilities: tuple[str, ...] = (),
        capture_active: bool | None = None,
        applied_revision: int | None = None,
        negotiated_max_payload_bytes: int | None = None,
        capability_refreshed_utc: str | None = None,
        capability_refresh_seconds: float | None = None,
        negotiated_packet_compression: str | None = None,
    ): ...
    def send_encoded(self, payload: bytes, *, content_type: str = "image/jpeg"): ...
    def send_packet(self, payload: bytes): ...
    def stats(self): ...


def _encode_jpeg(image: Any, quality: int) -> bytes:
    try:
        import cv2  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            'reference producer requires VisionRig ".[producer]"'
        ) from exc
    ok, encoded = cv2.imencode(
        ".jpg",
        image,
        [int(cv2.IMWRITE_JPEG_QUALITY), quality],
    )
    if not ok:
        raise RuntimeError("failed to JPEG-encode captured frame")
    return encoded.tobytes()


def _encode_kinect_packet(
    frame: Any,
    rgb_jpeg: bytes,
    *,
    compression: Literal["none", "zlib", "auto"] = "auto",
) -> bytes:
    depth = frame.sensor_data.get("color_aligned_depth_mm")
    if depth is None:
        raise RuntimeError(
            "Kinect remote producer requires color-aligned depth data"
        )
    return encode_sensor_packet(
        rgb_payload=rgb_jpeg,
        rgb_content_type="image/jpeg",
        depth_mm=depth,
        infrared=frame.sensor_data.get("infrared"),
        compression=compression,
        packet_version="v2",
    )


def _encode_packet_with_budget(
    *,
    frame: Any,
    packet_encoder: Callable[[Any, bytes], bytes],
    encode_jpeg: Callable[[Any, int], bytes],
    initial_quality: int,
    min_quality: int,
    max_packet_bytes: int,
    target_utilization: float = SENSOR_PACKET_PAYLOAD_WARNING_UTILIZATION,
) -> tuple[bytes, int, float]:
    if max_packet_bytes < 1024:
        raise ValueError("max_packet_bytes must be >= 1024")
    if not 30 <= min_quality <= initial_quality <= 100:
        raise ValueError(
            "JPEG quality must satisfy 30 <= min_quality <= initial_quality <= 100"
        )
    if not 0.0 < target_utilization < 1.0:
        raise ValueError("target_utilization must be between 0 and 1")

    target_bytes = max(
        1,
        int(max_packet_bytes * target_utilization) - 1,
    )
    quality = initial_quality
    best_packet: bytes | None = None
    best_quality = quality

    while True:
        rgb_jpeg = encode_jpeg(frame.payload, quality)
        packet = packet_encoder(frame, rgb_jpeg)
        best_packet = packet
        best_quality = quality

        if len(packet) <= target_bytes:
            return packet, quality, len(packet) / max_packet_bytes
        if quality <= min_quality:
            break
        quality = max(min_quality, quality - 5)

    assert best_packet is not None
    if len(best_packet) > max_packet_bytes:
        raise RuntimeError(
            "SensorPacket exceeds producer packet budget even at minimum JPEG "
            f"quality {best_quality}: {len(best_packet)} > {max_packet_bytes} bytes"
        )
    return (
        best_packet,
        best_quality,
        len(best_packet) / max_packet_bytes,
    )


def _default_state_path() -> Path:
    configured = os.getenv("VISIONRIG_PRODUCER_STATE")
    if configured:
        return Path(configured)
    return Path.home() / ".visionrig" / "producer-state.json"


def _run_controlled_capture(
    *,
    producer: _ProducerClient,
    source_factory: Callable[[], FrameSource],
    source_type: str,
    capabilities: tuple[str, ...],
    fps: float,
    jpeg_quality: int,
    max_frames: int,
    control_poll_seconds: float,
    verbose: bool,
    encode_jpeg: Callable[[Any, int], bytes] = _encode_jpeg,
    packet_encoder: Callable[[Any, bytes], bytes] | None = None,
    packet_budget_bytes: int | None = None,
    packet_budget_provider: Callable[[], int] | None = None,
    packet_transport_provider: Callable[[], PacketTransportPlan] | None = None,
    capability_refresh_seconds: float = 30.0,
    min_jpeg_quality: int = 30,
    monotonic: Callable[[], float] = time.monotonic,
    utcnow: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Run capture while respecting the remote desired-state contract.

    Desired-state lookup is fail-closed: if the control plane cannot be read,
    ProducerError propagates and any open capture source is closed by finally.
    A disabled source remains visible through heartbeats, but no capture device
    is opened and no frame sequence is consumed.
    """
    if packet_budget_provider is not None and packet_encoder is None:
        raise ValueError("packet_budget_provider requires packet_encoder")
    if packet_transport_provider is not None and packet_encoder is None:
        raise ValueError("packet_transport_provider requires packet_encoder")
    if (
        packet_budget_provider is not None
        and packet_transport_provider is not None
    ):
        raise ValueError(
            "use either packet_budget_provider or packet_transport_provider"
        )
    if not 1.0 <= capability_refresh_seconds <= 3600.0:
        raise ValueError("capability_refresh_seconds must be between 1 and 3600")

    interval = 1.0 / fps
    next_control_check = 0.0
    next_capability_check = 0.0
    current_packet_budget = packet_budget_bytes
    current_packet_encoder = packet_encoder
    current_packet_compression: str | None = None
    current_packet_target_utilization = SENSOR_PACKET_PAYLOAD_WARNING_UTILIZATION
    capability_refreshed_utc: str | None = None
    deadline = monotonic()
    frames = 0
    source: FrameSource | None = None
    enabled: bool | None = None

    try:
        while max_frames == 0 or frames < max_frames:
            now = monotonic()

            should_refresh_transport = (
                (
                    packet_budget_provider is not None
                    or packet_transport_provider is not None
                )
                and (
                    current_packet_budget is None
                    or now >= next_capability_check
                )
            )
            if should_refresh_transport:
                refreshed_compression = current_packet_compression
                refreshed_encoder = current_packet_encoder
                if packet_transport_provider is not None:
                    plan = packet_transport_provider()
                    refreshed_budget = plan.max_payload_bytes
                    refreshed_compression = plan.compression
                    refreshed_encoder = plan.packet_encoder
                    refreshed_target_utilization = plan.target_utilization
                else:
                    refreshed_target_utilization = (
                        SENSOR_PACKET_PAYLOAD_WARNING_UTILIZATION
                    )
                    assert packet_budget_provider is not None
                    refreshed_budget = packet_budget_provider()

                if not 1024 <= refreshed_budget <= 64 * 1024 * 1024:
                    raise RuntimeError(
                        "negotiated packet budget must be between 1 KiB and 64 MiB"
                    )
                if refreshed_encoder is None:
                    raise RuntimeError("negotiated packet encoder is unavailable")
                if verbose and (
                    refreshed_budget != current_packet_budget
                    or refreshed_compression != current_packet_compression
                    or (
                        refreshed_target_utilization
                        != current_packet_target_utilization
                    )
                ):
                    print(
                        "producer_capabilities "
                        f"effective_packet_cap={refreshed_budget} "
                        f"packet_compression={refreshed_compression or 'default'} "
                        f"packet_target_utilization={refreshed_target_utilization:.3f}"
                    )
                current_packet_budget = refreshed_budget
                current_packet_encoder = refreshed_encoder
                current_packet_compression = refreshed_compression
                current_packet_target_utilization = refreshed_target_utilization
                capability_refreshed_utc = utcnow().isoformat()
                next_capability_check = now + capability_refresh_seconds

            if enabled is None or now >= next_control_check:
                desired = producer.fetch_desired_state()
                next_control_check = now + control_poll_seconds

                if not desired.enabled:
                    if source is not None:
                        source.close()
                        source = None
                    if enabled is not False and verbose:
                        print("control=disabled capture=paused")
                    enabled = False
                    producer.send_heartbeat(
                        capabilities=capabilities,
                        capture_active=False,
                        applied_revision=desired.revision,
                        negotiated_max_payload_bytes=(
                            current_packet_budget
                            if (
                                packet_budget_provider is not None
                                or packet_transport_provider is not None
                            )
                            else None
                        ),
                        capability_refreshed_utc=(
                            capability_refreshed_utc
                            if (
                                packet_budget_provider is not None
                                or packet_transport_provider is not None
                            )
                            else None
                        ),
                        capability_refresh_seconds=(
                            capability_refresh_seconds
                            if (
                                packet_budget_provider is not None
                                or packet_transport_provider is not None
                            )
                            else None
                        ),
                        negotiated_packet_compression=(
                            current_packet_compression
                            if packet_transport_provider is not None
                            else None
                        ),
                    )
                    sleep(control_poll_seconds)
                    continue

                if enabled is False and verbose:
                    print("control=enabled capture=resumed")
                enabled = True
                if source is None:
                    source = source_factory()
                    deadline = monotonic()
                producer.send_heartbeat(
                    capabilities=capabilities,
                    capture_active=True,
                    applied_revision=desired.revision,
                    negotiated_max_payload_bytes=(
                        current_packet_budget
                        if (
                            packet_budget_provider is not None
                            or packet_transport_provider is not None
                        )
                        else None
                    ),
                    capability_refreshed_utc=(
                        capability_refreshed_utc
                        if (
                            packet_budget_provider is not None
                            or packet_transport_provider is not None
                        )
                        else None
                    ),
                    capability_refresh_seconds=(
                        capability_refresh_seconds
                        if (
                            packet_budget_provider is not None
                            or packet_transport_provider is not None
                        )
                        else None
                    ),
                    negotiated_packet_compression=(
                        current_packet_compression
                        if packet_transport_provider is not None
                        else None
                    ),
                )

            if source is None:
                source = source_factory()
                deadline = monotonic()

            frame = source.read()
            if frame is None:
                break

            effective_quality = jpeg_quality
            packet_utilization = None
            if current_packet_encoder is None:
                payload = encode_jpeg(frame.payload, jpeg_quality)
                result = producer.send_encoded(payload)
            elif current_packet_budget is None:
                payload = encode_jpeg(frame.payload, jpeg_quality)
                result = producer.send_packet(current_packet_encoder(frame, payload))
            else:
                packet, effective_quality, packet_utilization = (
                    _encode_packet_with_budget(
                        frame=frame,
                        packet_encoder=current_packet_encoder,
                        encode_jpeg=encode_jpeg,
                        initial_quality=jpeg_quality,
                        min_quality=min_jpeg_quality,
                        max_packet_bytes=current_packet_budget,
                        target_utilization=current_packet_target_utilization,
                    )
                )
                result = producer.send_packet(packet)
            frames += 1

            if verbose or result.status != "accepted":
                stats = producer.stats()
                packet_suffix = (
                    f" jpeg_quality={effective_quality} "
                    f"packet_utilization={packet_utilization:.3f}"
                    if packet_utilization is not None
                    else ""
                )
                print(
                    f"seq={result.frame_sequence} status={result.status} "
                    f"pending_dropped={stats.pending_dropped_frames} "
                    f"accepted={stats.accepted}"
                    f"{packet_suffix}"
                )

            if source_type == "image":
                break

            deadline += interval
            delay = deadline - monotonic()
            if delay > 0:
                sleep(delay)
            else:
                deadline = monotonic()
    finally:
        if source is not None:
            source.close()

    return frames


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Send webcam/screen/image/Kinect data to VisionRig sensor gateway"
    )
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--camera", type=int)
    source_group.add_argument("--screen", type=int)
    source_group.add_argument("--image")
    source_group.add_argument("--kinect-v2", action="store_true")

    parser.add_argument(
        "--gateway-url",
        default=os.getenv("VISIONRIG_GATEWAY_URL"),
        help="e.g. http://100.x.y.z:8111; may also use VISIONRIG_GATEWAY_URL",
    )
    parser.add_argument("--source-id")
    parser.add_argument("--fps", type=float, default=5.0)
    parser.add_argument("--jpeg-quality", type=int, default=80)
    parser.add_argument(
        "--min-jpeg-quality",
        type=int,
        default=30,
        help="minimum adaptive Kinect RGB JPEG quality; default 30",
    )
    parser.add_argument(
        "--max-packet-bytes",
        type=int,
        default=os.getenv(
            "VISIONRIG_PRODUCER_MAX_PACKET_BYTES",
            str(8 * 1024 * 1024),
        ),
        help=(
            "producer-side Kinect SensorPacket budget; default 8 MiB or "
            "VISIONRIG_PRODUCER_MAX_PACKET_BYTES"
        ),
    )
    parser.add_argument(
        "--capability-refresh-seconds",
        type=float,
        default=float(
            os.getenv(
                "VISIONRIG_PRODUCER_CAPABILITY_REFRESH_SECONDS",
                "30",
            )
        ),
        help=(
            "refresh authenticated gateway/core transport capabilities; "
            "default 30 seconds"
        ),
    )
    parser.add_argument("--max-frames", type=int, default=0)
    parser.add_argument(
        "--control-poll-seconds",
        type=float,
        default=2.0,
        help="desired-state/heartbeat interval; default 2 seconds",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    token = os.getenv("VISIONRIG_PRODUCER_TOKEN")
    if not token:
        parser.error("VISIONRIG_PRODUCER_TOKEN is required")
    if not args.gateway_url:
        parser.error("--gateway-url or VISIONRIG_GATEWAY_URL is required")
    if args.fps <= 0 or args.fps > 60:
        parser.error("--fps must be > 0 and <= 60")
    if not 30 <= args.jpeg_quality <= 100:
        parser.error("--jpeg-quality must be between 30 and 100")
    if not 30 <= args.min_jpeg_quality <= args.jpeg_quality:
        parser.error(
            "--min-jpeg-quality must be between 30 and --jpeg-quality"
        )
    if not 1024 <= args.max_packet_bytes <= 64 * 1024 * 1024:
        parser.error("--max-packet-bytes must be between 1 KiB and 64 MiB")
    if not 1 <= args.capability_refresh_seconds <= 3600:
        parser.error(
            "--capability-refresh-seconds must be between 1 and 3600"
        )
    if args.max_frames < 0:
        parser.error("--max-frames must be >= 0")
    if not 0.25 <= args.control_poll_seconds <= 60:
        parser.error("--control-poll-seconds must be between 0.25 and 60")

    packet_encoder = None
    if args.kinect_v2:
        source_id = args.source_id or "kinect-v2-0"
        source_type = "camera"
        device = "kinect-v2"
        capabilities = ("rgb", "depth", "infrared")
        source_factory = lambda: KinectV2Source(source_id=source_id)
        packet_encoder = _encode_kinect_packet
    elif args.camera is not None:
        source_id = args.source_id or f"camera-{args.camera}"
        source_type = "camera"
        device = str(args.camera)
        capabilities = ("rgb",)
        source_factory = lambda: CameraSource(args.camera, source_id=source_id)
    elif args.screen is not None:
        source_id = args.source_id or f"screen-{args.screen}"
        source_type = "screen"
        device = f"monitor:{args.screen}"
        capabilities = ("screen",)
        source_factory = lambda: MssScreenSource(args.screen, source_id=source_id)
    else:
        path = Path(args.image)
        source_id = args.source_id or "image-file"
        source_type = "image"
        device = str(path)
        capabilities = ("image",)
        source_factory = lambda: ImageFileSource(path, source_id=source_id)

    producer = GatewayFrameProducer(
        gateway_url=args.gateway_url,
        token=token,
        source_id=source_id,
        source_type=source_type,
        device=device,
        state_store=ProducerStateStore(_default_state_path()),
    )

    packet_transport_provider = None
    if args.kinect_v2:
        def packet_transport_provider() -> PacketTransportPlan:
            capabilities_contract = producer.fetch_capabilities()
            compression = _select_packet_compression(
                capabilities_contract.sensor_packet_compressions
            )
            warning_utilization = (
                capabilities_contract.packet_payload_warning_utilization
                if capabilities_contract.packet_payload_warning_utilization
                is not None
                else SENSOR_PACKET_PAYLOAD_WARNING_UTILIZATION
            )
            return PacketTransportPlan(
                max_payload_bytes=min(
                    args.max_packet_bytes,
                    capabilities_contract.max_payload_bytes,
                ),
                compression=compression,
                target_utilization=warning_utilization,
                packet_encoder=(
                    lambda frame, rgb_jpeg, selected=compression: (
                        _encode_kinect_packet(
                            frame,
                            rgb_jpeg,
                            compression=selected,
                        )
                    )
                ),
            )

    try:
        _run_controlled_capture(
            producer=producer,
            source_factory=source_factory,
            source_type=source_type,
            capabilities=capabilities,
            fps=args.fps,
            jpeg_quality=args.jpeg_quality,
            max_frames=args.max_frames,
            control_poll_seconds=args.control_poll_seconds,
            verbose=args.verbose,
            packet_encoder=packet_encoder,
            packet_transport_provider=packet_transport_provider,
            capability_refresh_seconds=args.capability_refresh_seconds,
            min_jpeg_quality=args.min_jpeg_quality,
        )
    except (ProducerError, ProducerStateError) as exc:
        raise SystemExit(f"producer stopped: {exc}") from exc

    stats = producer.stats()
    print(
        f"captured={stats.captured} accepted={stats.accepted} "
        f"dropped_overload={stats.dropped_overload} "
        f"dropped_unavailable={stats.dropped_unavailable} "
        f"pending_dropped={stats.pending_dropped_frames} "
        f"next_sequence={stats.next_sequence}"
    )


if __name__ == "__main__":
    main()
