"""Minimal capture exerciser for camera/video sources."""
from __future__ import annotations

import argparse
import time

from .pipeline import PerceptionPipeline
from .runtime import VisionRuntime
from .sources import CameraSource, VideoFileSource


def main() -> None:
    parser = argparse.ArgumentParser(description="Exercise VisionRig capture transport")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--camera", type=int)
    group.add_argument("--video")
    parser.add_argument("--max-frames", type=int, default=100)
    args = parser.parse_args()

    source = (
        CameraSource(args.camera)
        if args.camera is not None
        else VideoFileSource(args.video)
    )
    runtime = VisionRuntime(PerceptionPipeline())

    started = time.monotonic()
    processed = 0
    try:
        while processed < args.max_frames:
            frame = source.read()
            if frame is None:
                break
            runtime.submit(frame)
            if runtime.process_next() is not None:
                processed += 1
    finally:
        source.close()

    elapsed = max(time.monotonic() - started, 1e-9)
    stats = runtime.stats()
    print(
        f"processed={processed} fps={processed / elapsed:.2f} "
        f"dropped={stats.dropped_total}"
    )


if __name__ == "__main__":
    main()
