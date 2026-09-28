"""Fail-closed physical Kinect v2 -> VisionRig -> ModelRig acceptance.

This module is an operator evidence generator, not a simulator. The CLI opens the
real Kinect v2 backend, requires RGB/depth/aligned-depth/IR on every accepted
frame, requires meaningful semantic perception, and requires an exact-bound
ModelRig WorldState receipt. Raw frame bytes are never written to the receipt.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from . import __version__
from .contracts import PerceptionEvent
from .kinect_v2 import KinectV2Source
from .modelrig_bridge import BridgePublishResult, ModelRigPerceptionPublisher
from .pipeline import PerceptionPipeline
from .pipeline_factory import build_pipeline
from .runtime import VisionRuntime


_SHA40 = re.compile(r"^[0-9a-f]{40}$")
NonEmptyRef = Annotated[str, Field(min_length=1, max_length=256)]


class KinectPhysicalAcceptanceError(RuntimeError):
    pass


class _StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        allow_inf_nan=False,
        frozen=True,
    )


class KinectPhysicalAcceptanceReceipt(_StrictModel):
    schema: Literal["visionrig/kinect-physical-acceptance/v2"]
    generated_at: datetime
    visionrig_version: str
    visionrig_git_sha: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    release_gate: Literal["visionrig_physical_perception"]
    source_id: Annotated[str, Field(min_length=1, max_length=128)]
    device: Literal["kinect-v2"]
    requested_frames: Annotated[int, Field(ge=1, le=100)]
    captured_frames: Annotated[int, Field(ge=1, le=100)]
    first_frame_sequence: Annotated[int, Field(ge=0)]
    last_frame_sequence: Annotated[int, Field(ge=0)]
    sequences_strictly_contiguous: Literal[True]
    rgb_frames: Annotated[int, Field(ge=1)]
    raw_depth_frames: Annotated[int, Field(ge=1)]
    aligned_depth_frames: Annotated[int, Field(ge=1)]
    infrared_frames: Annotated[int, Field(ge=1)]
    infrared_semantic_frames: Annotated[int, Field(ge=1)]
    infrared_observations: Annotated[int, Field(ge=1)]
    perception_schema: Literal["visionrig/perception-event/v4"]
    semantic_events: Annotated[int, Field(ge=1)]
    semantic_observations: Annotated[int, Field(ge=1)]
    modelrig_receipts: Annotated[int, Field(ge=1)]
    modelrig_world_changed_receipts: Annotated[int, Field(ge=1)]
    modelrig_event_refs: Annotated[tuple[NonEmptyRef, ...], Field(min_length=1, max_length=100)]
    modelrig_evidence_refs: Annotated[tuple[NonEmptyRef, ...], Field(min_length=1, max_length=100)]
    real_sensor_required: Literal[True]
    exact_checkout_required: Literal[True]
    raw_frames_persisted: Literal[False]
    identity_authority: Literal[False]
    durable_memory_write_authority: Literal[False]
    execution_authority: Literal[False]
    scheduling_authority: Literal[False]
    production_authority: Literal[False]
    passed: Literal[True]


class _Publisher(Protocol):
    def publish(self, event: PerceptionEvent) -> BridgePublishResult: ...
    def close(self) -> None: ...


def _git(*args: str, root: Path | None = None) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise KinectPhysicalAcceptanceError("git checkout identity unavailable") from exc
    if result.returncode != 0:
        raise KinectPhysicalAcceptanceError(
            "git checkout identity unavailable: " + (result.stderr or result.stdout)[-300:]
        )
    return result.stdout.strip()


def require_exact_clean_checkout(expected_sha: str, *, root: Path | None = None) -> str:
    if _SHA40.fullmatch(expected_sha) is None:
        raise KinectPhysicalAcceptanceError("--expected-sha must be lowercase 40-hex")
    head = _git("rev-parse", "HEAD", root=root)
    if head != expected_sha:
        raise KinectPhysicalAcceptanceError(
            f"checkout HEAD {head} does not match expected SHA {expected_sha}"
        )
    dirty = _git("status", "--porcelain", root=root)
    if dirty:
        raise KinectPhysicalAcceptanceError(
            "physical acceptance requires an exact clean checkout"
        )
    return head


def _array_summary(value: Any, *, name: str, rgb: bool = False) -> tuple[int, ...]:
    try:
        import numpy as np  # type: ignore[import-not-found]
    except ImportError as exc:
        raise KinectPhysicalAcceptanceError(
            'physical Kinect acceptance requires VisionRig installed with ".[kinect-v2]"'
        ) from exc

    array = np.asarray(value)
    if array.size <= 0:
        raise KinectPhysicalAcceptanceError(f"{name} is empty")
    if rgb:
        if array.ndim != 3 or array.shape[2] < 3:
            raise KinectPhysicalAcceptanceError(f"{name} is not a color image")
    elif array.ndim not in {1, 2}:
        raise KinectPhysicalAcceptanceError(f"{name} has an invalid plane shape")
    if not rgb and not bool(np.any(array > 0)):
        raise KinectPhysicalAcceptanceError(f"{name} contains no measured signal")
    return tuple(int(item) for item in array.shape)


def _semantic_observation_count(event: PerceptionEvent) -> int:
    return (
        len(event.entities)
        + len(event.relations)
        + len(event.landmarks)
        + len(event.depth)
        + (1 if event.scene_label is not None else 0)
    )


def collect_kinect_physical_acceptance(
    *,
    source: KinectV2Source,
    pipeline: PerceptionPipeline,
    publisher: _Publisher,
    frame_count: int,
    git_sha: str,
    version: str = __version__,
) -> KinectPhysicalAcceptanceReceipt:
    """Collect one bounded physical evidence run.

    The public CLI supplies a real KinectV2Source/KinectNextBackend. Tests may
    inject a fake backend into KinectV2Source, but CI never runs the physical CLI
    and therefore cannot create release evidence by itself.
    """
    if not 1 <= frame_count <= 100:
        raise KinectPhysicalAcceptanceError("frame_count must be between 1 and 100")
    if _SHA40.fullmatch(git_sha) is None:
        raise KinectPhysicalAcceptanceError("git_sha must be lowercase 40-hex")
    if source.source.device != "kinect-v2":
        raise KinectPhysicalAcceptanceError("physical acceptance requires Kinect v2")

    runtime = VisionRuntime(pipeline)
    sequences: list[int] = []
    semantic_events = 0
    semantic_observations = 0
    rgb_frames = raw_depth_frames = aligned_depth_frames = infrared_frames = 0
    infrared_semantic_frames = infrared_observations = 0
    event_refs: list[str] = []
    evidence_refs: list[str] = []
    world_changed = 0

    try:
        for _ in range(frame_count):
            frame = source.read()
            if frame is None:
                raise KinectPhysicalAcceptanceError(
                    "Kinect ended before the requested physical evidence window completed"
                )
            if frame.source.source_id != source.source.source_id:
                raise KinectPhysicalAcceptanceError("Kinect source identity changed mid-run")
            if frame.source.device != "kinect-v2":
                raise KinectPhysicalAcceptanceError("Kinect device identity changed mid-run")

            _array_summary(frame.payload, name="RGB frame", rgb=True)
            rgb_frames += 1

            depth = frame.sensor_data.get("depth_mm")
            aligned = frame.sensor_data.get("color_aligned_depth_mm")
            infrared = frame.sensor_data.get("infrared")
            if depth is None or aligned is None or infrared is None:
                raise KinectPhysicalAcceptanceError(
                    "Kinect frame is missing depth, aligned depth or infrared"
                )
            _array_summary(depth, name="depth plane")
            raw_depth_frames += 1
            aligned_shape = _array_summary(aligned, name="color-aligned depth plane")
            rgb_shape = tuple(int(item) for item in getattr(frame.payload, "shape", ()))
            if len(rgb_shape) < 2 or aligned_shape[:2] != rgb_shape[:2]:
                raise KinectPhysicalAcceptanceError(
                    "color-aligned depth does not match RGB dimensions"
                )
            aligned_depth_frames += 1
            _array_summary(infrared, name="infrared plane")
            infrared_frames += 1

            sequences.append(frame.sequence)
            if len(sequences) > 1 and sequences[-1] != sequences[-2] + 1:
                raise KinectPhysicalAcceptanceError(
                    "Kinect frame sequence is not strictly contiguous"
                )

            event = runtime.process_direct(frame)
            if event.schema_id != "visionrig/perception-event/v4":
                raise KinectPhysicalAcceptanceError(
                    f"physical acceptance requires PerceptionEvent/v4, got {event.schema_id}"
                )
            if not event.infrared:
                raise KinectPhysicalAcceptanceError(
                    "Kinect infrared signal did not produce a bounded PerceptionEvent/v4 infrared summary"
                )
            infrared_semantic_frames += 1
            infrared_observations += len(event.infrared)
            count = _semantic_observation_count(event)
            if count <= 0:
                continue

            semantic_events += 1
            semantic_observations += count
            result = publisher.publish(event)
            if result.status in {"unavailable", "rejected"}:
                raise KinectPhysicalAcceptanceError(
                    "ModelRig rejected physical VisionRig evidence: "
                    + (result.reason or result.status)
                )
            if result.receipt is not None:
                event_refs.append(result.receipt.visionrig_event_ref)
                evidence_refs.append(result.receipt.evidence_ref)
                if result.receipt.world_changed:
                    world_changed += 1
    finally:
        source.close()
        publisher.close()

    if len(sequences) != frame_count:
        raise KinectPhysicalAcceptanceError("physical evidence window is incomplete")
    if infrared_semantic_frames != frame_count or infrared_observations < frame_count:
        raise KinectPhysicalAcceptanceError(
            "physical evidence window did not preserve infrared perception on every frame"
        )
    if semantic_events <= 0 or semantic_observations <= 0:
        raise KinectPhysicalAcceptanceError(
            "no meaningful semantic perception was observed; place a detectable subject "
            "in the Kinect field of view and retry"
        )
    if not event_refs or not evidence_refs:
        raise KinectPhysicalAcceptanceError(
            "no exact-bound ModelRig perception receipt was observed"
        )
    if world_changed <= 0:
        raise KinectPhysicalAcceptanceError(
            "ModelRig accepted evidence but no receipt proved a WorldState change"
        )

    return KinectPhysicalAcceptanceReceipt(
        schema="visionrig/kinect-physical-acceptance/v2",
        generated_at=datetime.now(timezone.utc),
        visionrig_version=version,
        visionrig_git_sha=git_sha,
        release_gate="visionrig_physical_perception",
        source_id=source.source.source_id,
        device="kinect-v2",
        requested_frames=frame_count,
        captured_frames=len(sequences),
        first_frame_sequence=sequences[0],
        last_frame_sequence=sequences[-1],
        sequences_strictly_contiguous=True,
        rgb_frames=rgb_frames,
        raw_depth_frames=raw_depth_frames,
        aligned_depth_frames=aligned_depth_frames,
        infrared_frames=infrared_frames,
        infrared_semantic_frames=infrared_semantic_frames,
        infrared_observations=infrared_observations,
        perception_schema="visionrig/perception-event/v4",
        semantic_events=semantic_events,
        semantic_observations=semantic_observations,
        modelrig_receipts=len(event_refs),
        modelrig_world_changed_receipts=world_changed,
        modelrig_event_refs=tuple(event_refs),
        modelrig_evidence_refs=tuple(evidence_refs),
        real_sensor_required=True,
        exact_checkout_required=True,
        raw_frames_persisted=False,
        identity_authority=False,
        durable_memory_write_authority=False,
        execution_authority=False,
        scheduling_authority=False,
        production_authority=False,
        passed=True,
    )


def _write_receipt(path: Path, receipt: KinectPhysicalAcceptanceReceipt) -> None:
    destination = path.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or destination.is_symlink():
        raise KinectPhysicalAcceptanceError("receipt output cannot be a symlink")
    payload = json.dumps(
        receipt.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    ) + "\n"
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=destination.parent,
        prefix=destination.name + ".",
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    os.replace(temporary, destination)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Produce fail-closed physical Kinect v2 -> ModelRig evidence"
    )
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--model-manifest", required=True)
    parser.add_argument("--modelrig-worker-url", required=True)
    parser.add_argument("--source-id", default="kinect-v2-acceptance")
    parser.add_argument("--frames", type=int, default=5)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("validation/visionrig-kinect-physical-acceptance.json"),
    )
    parser.add_argument("--cpu", action="store_true")
    args = parser.parse_args()

    started = time.monotonic()
    try:
        head = require_exact_clean_checkout(args.expected_sha)
        bundle = build_pipeline(
            yolo_manifest=args.model_manifest,
            kinect_depth=True,
            spatial_relations=True,
            prefer_cuda=not args.cpu,
        )
        source = KinectV2Source(source_id=args.source_id)
        publisher = ModelRigPerceptionPublisher(args.modelrig_worker_url)
        receipt = collect_kinect_physical_acceptance(
            source=source,
            pipeline=bundle.pipeline,
            publisher=publisher,
            frame_count=args.frames,
            git_sha=head,
        )
        _write_receipt(args.output, receipt)
    except KinectPhysicalAcceptanceError as exc:
        raise SystemExit(f"Kinect physical acceptance: FAIL: {exc}") from exc

    elapsed = max(time.monotonic() - started, 1e-9)
    print(
        "Kinect physical acceptance: PASS "
        f"frames={receipt.captured_frames} semantic={receipt.semantic_events} "
        f"modelrig_receipts={receipt.modelrig_receipts} elapsed_s={elapsed:.2f} "
        f"receipt={args.output}"
    )


if __name__ == "__main__":
    main()
