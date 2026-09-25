"""Camera/video runner for VisionRig capture and optional real perception."""
from __future__ import annotations

import argparse
import time

from .pipeline import PerceptionPipeline
from .pipeline_factory import build_yolo_pipeline
from .runtime import VisionRuntime
from .sources import CameraSource, VideoFileSource


def main() -> None:
    parser = argparse.ArgumentParser(description="Run VisionRig capture transport")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--camera", type=int)
    group.add_argument("--video")
    parser.add_argument("--max-frames", type=int, default=100)
    parser.add_argument(
        "--model-manifest",
        help="Verified visionrig/yolo-model-manifest/v1 JSON file",
    )
    parser.add_argument(
        "--cpu",
        action="store_true",
        help="Do not select CUDAExecutionProvider even when available",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    source = (
        CameraSource(args.camera)
        if args.camera is not None
        else VideoFileSource(args.video)
    )
    pipeline = (
        build_yolo_pipeline(args.model_manifest, prefer_cuda=not args.cpu)
        if args.model_manifest
        else PerceptionPipeline()
    )
    runtime = VisionRuntime(pipeline)

    started = time.monotonic()
    processed = 0
    last_event = None
    try:
        while processed < args.max_frames:
            frame = source.read()
            if frame is None:
                break
            runtime.submit(frame)
            event = runtime.process_next()
            if event is None:
                continue
            last_event = event
            processed += 1
            if args.verbose:
                print(
                    f"frame={event.frame_sequence} entities={len(event.entities)} "
                    f"dropped={event.dropped_frames}"
                )
    finally:
        source.close()

    elapsed = max(time.monotonic() - started, 1e-9)
    stats = runtime.stats()
    entity_count = len(last_event.entities) if last_event is not None else 0
    print(
        f"processed={processed} fps={processed / elapsed:.2f} "
        f"dropped={stats.dropped_total} last_entities={entity_count}"
    )


if __name__ == "__main__":
    main()
