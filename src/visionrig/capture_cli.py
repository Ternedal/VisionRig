"""Camera/video runner for VisionRig perception."""
from __future__ import annotations

import argparse
import os
import time
from typing import Callable, Protocol

from .kinect_v2 import KinectV2Source
from .modelrig_bridge import ModelRigPerceptionPublisher
from .pipeline import Frame
from .pipeline_factory import build_pipeline
from .producer import GatewayFrameProducer, ProducerError
from .runtime import VisionRuntime
from .sources import CameraSource, FrameSource, VideoFileSource


class _ControlClient(Protocol):
    def fetch_desired_state(self): ...
    def send_heartbeat(
        self,
        *,
        capabilities: tuple[str, ...] = (),
        capture_active: bool | None = None,
    ): ...


def _run_capture(
    *,
    runtime: VisionRuntime,
    source_factory: Callable[[], FrameSource],
    max_frames: int,
    verbose: bool,
    control: _ControlClient | None = None,
    capabilities: tuple[str, ...] = (),
    control_poll_seconds: float = 2.0,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[int, object | None]:
    """Run local perception, optionally under the shared sensor control contract.

    In managed mode the capture device is opened only while desired state is
    enabled. A local logical sequence is applied across source reopen cycles so
    downstream PerceptionEvent frame_sequence remains monotonic.
    """
    processed = 0
    last_event = None
    logical_sequence = 0
    next_control_check = 0.0
    source: FrameSource | None = None
    enabled: bool | None = None

    try:
        while processed < max_frames:
            now = monotonic()
            if control is not None and (enabled is None or now >= next_control_check):
                desired = control.fetch_desired_state()
                next_control_check = now + control_poll_seconds

                if not desired.enabled:
                    if source is not None:
                        source.close()
                        source = None
                    enabled = False
                    control.send_heartbeat(
                        capabilities=capabilities,
                        capture_active=False,
                    )
                    if verbose:
                        print("control=disabled capture=paused")
                    sleep(control_poll_seconds)
                    continue

                if source is None:
                    source = source_factory()
                enabled = True
                control.send_heartbeat(
                    capabilities=capabilities,
                    capture_active=True,
                )
                if verbose:
                    print("control=enabled capture=active")

            if source is None:
                source = source_factory()

            frame = source.read()
            if frame is None:
                break

            if control is not None:
                frame = Frame(
                    source=frame.source,
                    sequence=logical_sequence,
                    payload=frame.payload,
                    dropped_frames=frame.dropped_frames,
                    sensor_data=frame.sensor_data,
                )
                logical_sequence += 1

            runtime.submit(frame)
            event = runtime.process_next()
            if event is None:
                continue
            last_event = event
            processed += 1

            if verbose:
                print(
                    f"frame={event.frame_sequence} entities={len(event.entities)} "
                    f"landmarks={len(event.landmarks)} depth={len(event.depth)} "
                    f"dropped={event.dropped_frames}"
                )
    finally:
        if source is not None:
            source.close()

    return processed, last_event


def main() -> None:
    parser = argparse.ArgumentParser(description="Run VisionRig capture/perception")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--camera", type=int)
    group.add_argument("--video")
    group.add_argument("--kinect-v2", action="store_true")
    parser.add_argument("--source-id")
    parser.add_argument("--max-frames", type=int, default=100)
    parser.add_argument("--model-manifest", help="YOLO model manifest")
    parser.add_argument("--depth-manifest", help="depth model manifest")
    parser.add_argument("--embedding-manifest", help="embedding model manifest")
    parser.add_argument("--ocr", action="store_true")
    parser.add_argument("--landmarks", action="store_true")
    parser.add_argument("--cpu", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument(
        "--control-gateway-url",
        default=os.getenv("VISIONRIG_GATEWAY_URL"),
        help="optional authenticated sensor gateway for managed local capture",
    )
    parser.add_argument(
        "--control-poll-seconds",
        type=float,
        default=2.0,
        help="desired-state/heartbeat interval in managed mode",
    )
    parser.add_argument(
        "--modelrig-worker-url",
        help=(
            "opt-in semantic perception publishing to ModelRig worker, "
            "e.g. http://127.0.0.1:8099"
        ),
    )
    args = parser.parse_args()

    if args.max_frames <= 0:
        parser.error("--max-frames must be > 0")
    if not 0.25 <= args.control_poll_seconds <= 60:
        parser.error("--control-poll-seconds must be between 0.25 and 60")

    if args.kinect_v2:
        source_id = args.source_id or "kinect-v2-0"
        source_factory = lambda: KinectV2Source(source_id=source_id)
        source_type = "camera"
        device = "kinect-v2"
        capabilities = ("rgb", "depth", "infrared")
    elif args.camera is not None:
        source_id = args.source_id or f"camera-{args.camera}"
        source_factory = lambda: CameraSource(args.camera, source_id=source_id)
        source_type = "camera"
        device = str(args.camera)
        capabilities = ("rgb",)
    else:
        source_id = args.source_id or "video-file"
        source_factory = lambda: VideoFileSource(args.video, source_id=source_id)
        source_type = "video"
        device = str(args.video)
        capabilities = ()

    control = None
    if args.control_gateway_url:
        if source_type == "video":
            parser.error("managed control is supported for camera/Kinect sources, not video")
        token = os.getenv("VISIONRIG_PRODUCER_TOKEN")
        if not token:
            parser.error(
                "VISIONRIG_PRODUCER_TOKEN is required when --control-gateway-url is used"
            )
        control = GatewayFrameProducer(
            gateway_url=args.control_gateway_url,
            token=token,
            source_id=source_id,
            source_type="camera",
            device=device,
        )

    bundle = build_pipeline(
        yolo_manifest=args.model_manifest,
        depth_manifest=args.depth_manifest,
        embedding_manifest=args.embedding_manifest,
        ocr=args.ocr,
        landmarks=args.landmarks,
        kinect_depth=args.kinect_v2,
        prefer_cuda=not args.cpu,
    )
    modelrig_publisher = (
        ModelRigPerceptionPublisher(args.modelrig_worker_url)
        if args.modelrig_worker_url
        else None
    )
    runtime = VisionRuntime(
        bundle.pipeline,
        event_sinks=(
            (modelrig_publisher,)
            if modelrig_publisher is not None
            else ()
        ),
    )

    started = time.monotonic()
    try:
        processed, last_event = _run_capture(
            runtime=runtime,
            source_factory=source_factory,
            max_frames=args.max_frames,
            verbose=args.verbose,
            control=control,
            capabilities=capabilities,
            control_poll_seconds=args.control_poll_seconds,
        )
    except ProducerError as exc:
        raise SystemExit(f"managed capture stopped: {exc}") from exc
    finally:
        if modelrig_publisher is not None:
            modelrig_publisher.close()

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
