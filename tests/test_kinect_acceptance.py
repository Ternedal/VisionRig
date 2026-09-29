import hashlib
import json

import httpx
import numpy as np
import pytest

from visionrig import kinect_acceptance as acceptance
from visionrig.contracts import BoundingBox, VisualEntity
from visionrig.infrared import InfraredSummaryStage
from visionrig.kinect_v2 import KinectV2FrameSet, KinectV2Source
from visionrig.modelrig_bridge import ModelRigPerceptionPublisher
from visionrig.pipeline import PerceptionPipeline, StageResult


class _Backend:
    def __init__(self, frames):
        self.frames = list(frames)
        self.closed = False

    def read(self):
        return self.frames.pop(0) if self.frames else None

    def close(self):
        self.closed = True


class _EntityStage:
    name = "physical-acceptance-entity"

    def process(self, frame, current):
        return StageResult(
            entities=(
                VisualEntity(
                    entity_id=f"person-{frame.sequence}",
                    kind="person",
                    label="person",
                    confidence=0.95,
                    bbox=BoundingBox(x=0.1, y=0.1, width=0.3, height=0.7),
                ),
            ),
        )


def _frame(*, infrared=True):
    return KinectV2FrameSet(
        color_bgr=np.zeros((4, 6, 3), dtype=np.uint8),
        depth_mm=np.full((2, 3), 1500, dtype=np.uint16),
        color_aligned_depth_mm=np.full((4, 6), 1500, dtype=np.uint16),
        infrared=(
            np.full((2, 3), 200, dtype=np.uint16)
            if infrared
            else None
        ),
    )


def _publisher(
    *,
    replayed: bool = False,
    cognition_queued: bool = True,
    cognition_event_id: str | None = "__auto__",
    evidence_ref: str = "world-evidence-event:" + "a" * 64,
):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        canonical = json.dumps(
            body,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
        event_ref = "visionrig-event:" + hashlib.sha256(canonical).hexdigest()
        effective_cognition_event_id = cognition_event_id
        if cognition_event_id == "__auto__":
            effective_cognition_event_id = "cevt-" + hashlib.sha256(
                ("world-evidence-attention|world_change|" + evidence_ref).encode("utf-8")
            ).hexdigest()[:32]
        return httpx.Response(
            200,
            json={
                "schema": "kaliv-consciousness-core/visionrig-admission/v1",
                "visionrig_event_ref": event_ref,
                "evidence_ref": evidence_ref,
                "cognition_event_id": None if replayed else effective_cognition_event_id,
                "world_changed": not replayed,
                "replayed": replayed,
                "cognition_event_queued": False if replayed else cognition_queued,
                "epistemic_status": "inferred",
                "confidence": 0.9,
                "attention_salience": 0.7,
                "observed_sequence": body["frame_sequence"],
                "model_calls": 0,
                "self_state_store_write_applied": False,
                "durable_memory_write_authority": False,
                "execution_authority": False,
                "scheduling_authority": False,
                "production_activation": False,
            },
        )

    return ModelRigPerceptionPublisher(
        "http://127.0.0.1:8099",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_physical_acceptance_requires_all_modalities_semantics_and_world_receipt():
    backend = _Backend([_frame(), _frame(), _frame()])
    source = KinectV2Source(source_id="kinect-lab", backend=backend)

    receipt = acceptance.collect_kinect_physical_acceptance(
        source=source,
        pipeline=PerceptionPipeline((_EntityStage(), InfraredSummaryStage())),
        publisher=_publisher(),
        frame_count=3,
        git_sha="1" * 40,
        version="test",
    )

    assert receipt.passed is True
    assert receipt.release_gate == "visionrig_physical_perception"
    assert receipt.source_id == "kinect-lab"
    assert receipt.captured_frames == 3
    assert receipt.rgb_frames == 3
    assert receipt.raw_depth_frames == 3
    assert receipt.aligned_depth_frames == 3
    assert receipt.infrared_frames == 3
    assert receipt.infrared_semantic_frames == 3
    assert receipt.infrared_observations == 3
    assert receipt.perception_schema == "visionrig/perception-event/v4"
    assert receipt.semantic_events == 3
    assert receipt.semantic_observations == 3
    # First semantic state publishes; later identical semantic state may suppress.
    assert receipt.modelrig_receipts >= 1
    assert receipt.modelrig_world_changed_receipts >= 1
    assert receipt.modelrig_cognition_queued_receipts >= 1
    assert receipt.modelrig_cognition_event_ids
    assert receipt.schema == "visionrig/kinect-physical-acceptance/v3"
    assert receipt.sequences_strictly_contiguous is True
    assert receipt.raw_frames_persisted is False
    assert receipt.production_authority is False
    assert backend.closed is True

    rendered = receipt.model_dump(mode="json")
    for forbidden in ("depth_mm", "color_aligned_depth_mm", "infrared", "color_bgr", "payload"):
        assert forbidden not in rendered


def test_physical_acceptance_fails_closed_when_infrared_is_missing():
    backend = _Backend([_frame(infrared=False)])
    source = KinectV2Source(backend=backend)

    with pytest.raises(
        acceptance.KinectPhysicalAcceptanceError,
        match="missing depth, aligned depth or infrared",
    ):
        acceptance.collect_kinect_physical_acceptance(
            source=source,
            pipeline=PerceptionPipeline((_EntityStage(), InfraredSummaryStage())),
            publisher=_publisher(),
            frame_count=1,
            git_sha="2" * 40,
        )

    assert backend.closed is True


def test_physical_acceptance_fails_when_raw_ir_is_not_semantically_summarized():
    backend = _Backend([_frame()])
    source = KinectV2Source(backend=backend)

    with pytest.raises(
        acceptance.KinectPhysicalAcceptanceError,
        match="did not produce a bounded PerceptionEvent/v4 infrared summary",
    ):
        acceptance.collect_kinect_physical_acceptance(
            source=source,
            pipeline=PerceptionPipeline((_EntityStage(),)),
            publisher=_publisher(),
            frame_count=1,
            git_sha="7" * 40,
        )

    assert backend.closed is True


def test_physical_acceptance_refuses_empty_semantics_even_with_real_modalities():
    backend = _Backend([_frame(), _frame()])
    source = KinectV2Source(backend=backend)

    with pytest.raises(
        acceptance.KinectPhysicalAcceptanceError,
        match="no meaningful semantic perception",
    ):
        acceptance.collect_kinect_physical_acceptance(
            source=source,
            pipeline=PerceptionPipeline((InfraredSummaryStage(),)),
            publisher=_publisher(),
            frame_count=2,
            git_sha="3" * 40,
        )


def test_physical_acceptance_requires_contiguous_sensor_sequence():
    source = KinectV2Source(backend=_Backend([_frame(), _frame()]))
    first = source.read()
    assert first is not None
    # The public source owns its sequence; prove the acceptance validator catches
    # a bad producer by using a minimal source shim around two explicit frames.
    class _BadSource:
        def __init__(self):
            self.source = source.source
            self._frames = [
                first,
                type(first)(
                    source=first.source,
                    sequence=2,
                    payload=first.payload,
                    sensor_data=first.sensor_data,
                ),
            ]
        def read(self):
            return self._frames.pop(0) if self._frames else None
        def close(self):
            source.close()

    with pytest.raises(
        acceptance.KinectPhysicalAcceptanceError,
        match="not strictly contiguous",
    ):
        acceptance.collect_kinect_physical_acceptance(
            source=_BadSource(),  # type: ignore[arg-type]
            pipeline=PerceptionPipeline((_EntityStage(), InfraredSummaryStage())),
            publisher=_publisher(),
            frame_count=2,
            git_sha="4" * 40,
        )


def test_exact_checkout_requires_expected_sha_and_clean_tree(monkeypatch):
    replies = iter(["5" * 40, ""])
    monkeypatch.setattr(acceptance, "_git", lambda *args, root=None: next(replies))
    assert acceptance.require_exact_clean_checkout("5" * 40) == "5" * 40

    monkeypatch.setattr(acceptance, "_git", lambda *args, root=None: "6" * 40)
    with pytest.raises(acceptance.KinectPhysicalAcceptanceError, match="does not match"):
        acceptance.require_exact_clean_checkout("5" * 40)

    replies = iter(["5" * 40, " M src/visionrig/kinect_v2.py"])
    monkeypatch.setattr(acceptance, "_git", lambda *args, root=None: next(replies))
    with pytest.raises(acceptance.KinectPhysicalAcceptanceError, match="clean checkout"):
        acceptance.require_exact_clean_checkout("5" * 40)


def test_physical_acceptance_rejects_replayed_modelrig_admission():
    backend = _Backend([_frame()])
    source = KinectV2Source(source_id="kinect-lab", backend=backend)

    with pytest.raises(
        acceptance.KinectPhysicalAcceptanceError,
        match="replayed physical VisionRig evidence instead of freshly admitting it",
    ):
        acceptance.collect_kinect_physical_acceptance(
            source=source,
            pipeline=PerceptionPipeline((_EntityStage(), InfraredSummaryStage())),
            publisher=_publisher(replayed=True),
            frame_count=1,
            git_sha="8" * 40,
        )

    assert backend.closed is True


def test_physical_acceptance_rejects_missing_cognition_queue_proof():
    backend = _Backend([_frame()])
    source = KinectV2Source(source_id="kinect-lab", backend=backend)

    with pytest.raises(
        acceptance.KinectPhysicalAcceptanceError,
        match="invalid ModelRig bridge receipt",
    ):
        acceptance.collect_kinect_physical_acceptance(
            source=source,
            pipeline=PerceptionPipeline((_EntityStage(), InfraredSummaryStage())),
            publisher=_publisher(cognition_queued=False),
            frame_count=1,
            git_sha="9" * 40,
        )

    assert backend.closed is True


def test_physical_acceptance_rejects_invalid_cognition_event_id():
    backend = _Backend([_frame()])
    source = KinectV2Source(source_id="kinect-lab", backend=backend)

    with pytest.raises(
        acceptance.KinectPhysicalAcceptanceError,
        match="invalid ModelRig bridge receipt",
    ):
        acceptance.collect_kinect_physical_acceptance(
            source=source,
            pipeline=PerceptionPipeline((_EntityStage(), InfraredSummaryStage())),
            publisher=_publisher(cognition_event_id=None),
            frame_count=1,
            git_sha="a" * 40,
        )

    assert backend.closed is True


def test_physical_acceptance_rejects_malformed_evidence_ref():
    backend = _Backend([_frame()])
    source = KinectV2Source(source_id="kinect-lab", backend=backend)

    with pytest.raises(
        acceptance.KinectPhysicalAcceptanceError,
        match="invalid ModelRig bridge receipt",
    ):
        acceptance.collect_kinect_physical_acceptance(
            source=source,
            pipeline=PerceptionPipeline((_EntityStage(), InfraredSummaryStage())),
            publisher=_publisher(evidence_ref="world-evidence:" + "a" * 64),
            frame_count=1,
            git_sha="b" * 40,
        )

    assert backend.closed is True
