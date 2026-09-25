"""Optional MediaPipe pose/hand landmark stage."""
from __future__ import annotations

from typing import Any
from uuid import uuid4

from .contracts import LandmarkObservation, VisualLandmark
from .pipeline import Frame, StageResult


class LandmarkUnavailable(RuntimeError):
    pass


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _landmark_observation(
    *,
    group: str,
    raw_landmarks: Any,
    names: tuple[str, ...],
) -> LandmarkObservation | None:
    if raw_landmarks is None:
        return None
    items = getattr(raw_landmarks, "landmark", None)
    if not items:
        return None

    points: list[VisualLandmark] = []
    for index, point in enumerate(items):
        name = names[index] if index < len(names) else f"landmark_{index}"
        visibility = getattr(point, "visibility", 1.0)
        points.append(
            VisualLandmark(
                name=name.lower(),
                x=_clamp01(getattr(point, "x", 0.0)),
                y=_clamp01(getattr(point, "y", 0.0)),
                z=max(-1.0, min(1.0, float(getattr(point, "z", 0.0)))),
                confidence=_clamp01(visibility),
            )
        )
    if not points:
        return None
    return LandmarkObservation(
        observation_id=f"lmk-{uuid4()}",
        group=group,
        landmarks=tuple(points),
    )


class MediaPipeLandmarkStage:
    name = "mediapipe_landmarks"

    def __init__(
        self,
        *,
        pose: bool = True,
        hands: bool = True,
        face: bool = False,
        min_detection_confidence: float = 0.5,
        min_tracking_confidence: float = 0.5,
    ) -> None:
        try:
            import cv2  # type: ignore[import-not-found]
            import mediapipe as mp  # type: ignore[import-not-found]
        except ImportError as exc:
            raise LandmarkUnavailable(
                'landmarks require VisionRig ".[landmarks]"'
            ) from exc

        self._cv2 = cv2
        self._mp = mp
        self._pose_enabled = pose
        self._hands_enabled = hands
        self._face_enabled = face
        self._holistic = mp.solutions.holistic.Holistic(
            static_image_mode=False,
            model_complexity=1,
            enable_segmentation=False,
            refine_face_landmarks=False,
            min_detection_confidence=min_detection_confidence,
            min_tracking_confidence=min_tracking_confidence,
        )
        self._pose_names = tuple(member.name for member in mp.solutions.pose.PoseLandmark)
        self._hand_names = tuple(member.name for member in mp.solutions.hands.HandLandmark)

    def process(self, frame: Frame, current: StageResult) -> StageResult:
        image = frame.payload
        if not hasattr(image, "shape"):
            return current
        rgb = self._cv2.cvtColor(image, self._cv2.COLOR_BGR2RGB)
        result = self._holistic.process(rgb)

        observations: list[LandmarkObservation] = []
        if self._pose_enabled:
            obs = _landmark_observation(
                group="pose",
                raw_landmarks=result.pose_landmarks,
                names=self._pose_names,
            )
            if obs:
                observations.append(obs)
        if self._hands_enabled:
            for group, raw in (
                ("left_hand", result.left_hand_landmarks),
                ("right_hand", result.right_hand_landmarks),
            ):
                obs = _landmark_observation(
                    group=group,
                    raw_landmarks=raw,
                    names=self._hand_names,
                )
                if obs:
                    observations.append(obs)
        if self._face_enabled:
            face = result.face_landmarks
            if face is not None:
                names = tuple(f"face_{i}" for i in range(len(face.landmark)))
                obs = _landmark_observation(
                    group="face",
                    raw_landmarks=face,
                    names=names,
                )
                if obs:
                    observations.append(obs)

        return StageResult(
            entities=current.entities,
            relations=current.relations,
            landmarks=current.landmarks + tuple(observations),
            depth=current.depth,
            scene_label=current.scene_label,
            scene_confidence=current.scene_confidence,
        )

    def close(self) -> None:
        self._holistic.close()
