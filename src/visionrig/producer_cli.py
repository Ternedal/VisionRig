"""Reference Windows/Linux webcam/screen producer for the sensor gateway."""
from __future__ import annotations

import argparse
import os
import time
from pathlib import Path
from typing import Any, Callable, Protocol

from .producer import GatewayFrameProducer, ProducerError
from .producer_state import ProducerStateError, ProducerStateStore
from .screen_source import MssScreenSource
from .sources import CameraSource, FrameSource, ImageFileSource


class _ProducerClient(Protocol):
    def fetch_desired_state(self): ...
    def send_heartbeat(
        self,
        *,
        capabilities: tuple[str, ...] = (),
        capture_active: bool | None = None,
        applied_revision: int | None = None,
    ): ...
    def send_encoded(self, payload: bytes, *, content_type: str = "image/jpeg"): ...
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
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Run capture while respecting the remote desired-state contract.

    Desired-state lookup is fail-closed: if the control plane cannot be read,
    ProducerError propagates and any open capture source is closed by finally.
    A disabled source remains visible through heartbeats, but no capture device
    is opened and no frame sequence is consumed.
    """
    interval = 1.0 / fps
    next_control_check = 0.0
    deadline = monotonic()
    frames = 0
    source: FrameSource | None = None
    enabled: bool | None = None

    try:
        while max_frames == 0 or frames < max_frames:
            now = monotonic()
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
                )

            if source is None:
                source = source_factory()
                deadline = monotonic()

            frame = source.read()
            if frame is None:
                break

            payload = encode_jpeg(frame.payload, jpeg_quality)
            result = producer.send_encoded(payload)
            frames += 1

            if verbose or result.status != "accepted":
                stats = producer.stats()
                print(
                    f"seq={result.frame_sequence} status={result.status} "
                    f"pending_dropped={stats.pending_dropped_frames} "
                    f"accepted={stats.accepted}"
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
        description="Send webcam/screen/image frames to VisionRig sensor gateway"
    )
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--camera", type=int)
    source_group.add_argument("--screen", type=int)
    source_group.add_argument("--image")

    parser.add_argument(
        "--gateway-url",
        default=os.getenv("VISIONRIG_GATEWAY_URL"),
        help="e.g. http://100.x.y.z:8111; may also use VISIONRIG_GATEWAY_URL",
    )
    parser.add_argument("--source-id")
    parser.add_argument("--fps", type=float, default=5.0)
    parser.add_argument("--jpeg-quality", type=int, default=80)
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
    if args.max_frames < 0:
        parser.error("--max-frames must be >= 0")
    if not 0.25 <= args.control_poll_seconds <= 60:
        parser.error("--control-poll-seconds must be between 0.25 and 60")

    if args.camera is not None:
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
