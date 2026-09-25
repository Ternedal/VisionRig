"""Reference Windows/Linux webcam/screen producer for the sensor gateway."""
from __future__ import annotations

import argparse
import os
import time
from pathlib import Path
from typing import Any

from .producer import GatewayFrameProducer, ProducerError
from .producer_state import ProducerStateError, ProducerStateStore
from .screen_source import MssScreenSource
from .sources import CameraSource, ImageFileSource


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

    if args.camera is not None:
        source_id = args.source_id or f"camera-{args.camera}"
        source = CameraSource(args.camera, source_id=source_id)
        source_type = "camera"
    elif args.screen is not None:
        source_id = args.source_id or f"screen-{args.screen}"
        source = MssScreenSource(args.screen, source_id=source_id)
        source_type = "screen"
    else:
        path = Path(args.image)
        source_id = args.source_id or "image-file"
        source = ImageFileSource(path, source_id=source_id)
        source_type = "image"

    try:
        producer = GatewayFrameProducer(
            gateway_url=args.gateway_url,
            token=token,
            source_id=source_id,
            source_type=source_type,
            device=source.source.device,
            state_store=ProducerStateStore(_default_state_path()),
        )
    except ProducerStateError as exc:
        source.close()
        raise SystemExit(f"producer state unavailable: {exc}") from exc

    interval = 1.0 / args.fps
    deadline = time.monotonic()
    frames = 0

    try:
        while args.max_frames == 0 or frames < args.max_frames:
            frame = source.read()
            if frame is None:
                break
            payload = _encode_jpeg(frame.payload, args.jpeg_quality)
            result = producer.send_encoded(payload)
            frames += 1

            if args.verbose or result.status != "accepted":
                stats = producer.stats()
                print(
                    f"seq={result.frame_sequence} status={result.status} "
                    f"pending_dropped={stats.pending_dropped_frames} "
                    f"accepted={stats.accepted}"
                )

            if source_type == "image":
                break

            deadline += interval
            delay = deadline - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            else:
                deadline = time.monotonic()
    except (ProducerError, ProducerStateError) as exc:
        raise SystemExit(f"producer stopped: {exc}") from exc
    finally:
        source.close()

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
