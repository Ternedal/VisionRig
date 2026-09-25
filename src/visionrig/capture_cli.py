"""Camera/video runner for VisionRig perception."""
from __future__ import annotations

import argparse
import time

from .pipeline_factory import build_pipeline
from .runtime import VisionRuntime
from .sources import CameraSource, VideoFileSource


def main() -> None:
    parser = argparse.ArgumentParser(description="Run VisionRig capture/perception")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--camera", type=int)
    group.add_argument("--video")
    group.add_argument("--kinect-v2", action="store_true")
    parser.add_argument("--max-frames", type=int, default=100)
    parser.add_argument("--model-manifest", help="YOLO model manifest")
    parser.add_argument("--depth-manifest", help="depth model manifest")
    parser.add_argument("--embedding-manifest", help="embedding model manifest")
    parser.add_argument("--ocr", action="store_true")
    parser.add_argument("--landmarks", action="store_true")
    parser.add_argument("--cpu", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    if args.kinect_v2:
        source = KinectV2Source()
    elif args.camera is not None:
        source = CameraSource(args.camera)
    else:
        source = VideoFileSource(args.video)
    bundle = build_pipeline(
        yolo_manifest=args.model_manifest,
        depth_manifest=args.depth_manifest,
        embedding_manifest=args.embedding_manifest,
        ocr=args.ocr,
        landmarks=args.landmarks,
        kinect_depth=args.kinect_v2,
        prefer_cuda=not args.cpu,
    )
    runtime = VisionRuntime(bundle.pipeline)

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
                    f"landmarks={len(event.landmarks)} depth={len(event.depth)} "
                    f"dropped={event.dropped_frames}"
                )
    finally:
        source.close()

    elapsed = max(time.monotonic() - started, 1e-9)
    stats = runtime.stats()
    entity_count = len(last_event.entities) if last_event is not None else 0
    embeddings = len(bundle.embedding_store) if bundle.embedding_store is not None else 0
    print(
        f"processed={processed} fps={processed / elapsed:.2f} "
        f"dropped={stats.dropped_total} last_entities={entity_count} "
        f"embeddings={embeddings}"
    )


if __name__ == "__main__":
    main()
