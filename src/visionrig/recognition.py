"""Apply .mrvision recognition hints to already-observed entities and scenes."""
from __future__ import annotations

from .embeddings import EmbeddingStore
from .pipeline import Frame, StageResult
from .profile import MrVisionProfile, match_embedding


_KIND_MAP = {
    "face": "face",
    "person": "body",
    "body": "body",
    "object": "object",
}


class ProfileRecognitionStage:
    """Attach non-authoritative recognition hints to visual entities."""

    name = "mrvision_recognition"

    def __init__(
        self,
        profile: MrVisionProfile,
        store: EmbeddingStore,
        *,
        embedding_model_id: str,
        threshold: float = 0.75,
    ) -> None:
        if not isinstance(profile, MrVisionProfile):
            raise TypeError("profile must be MrVisionProfile")
        if not 0.0 <= threshold <= 1.0:
            raise ValueError("threshold must be between 0 and 1")
        self._profile = profile
        self._store = store
        self._model_id = embedding_model_id
        self._threshold = threshold

    def process(self, frame: Frame, current: StageResult) -> StageResult:
        entities = []
        for entity in current.entities:
            enrollment_kind = _KIND_MAP.get(entity.kind)
            if enrollment_kind is None:
                entities.append(entity)
                continue

            record = self._store.get(
                frame.source.source_id,
                frame.sequence,
                subject_entity_id=entity.entity_id,
                model_id=self._model_id,
            )
            if record is None:
                entities.append(entity)
                continue

            matches = match_embedding(
                self._profile,
                vector=record.vector,
                embedding_model_id=self._model_id,
                kind=enrollment_kind,
                threshold=self._threshold,
                top_k=1,
            )
            if not matches:
                entities.append(entity)
                continue

            match = matches[0]
            target = match.subject_ref or match.label
            hint = ("mrvision:" + target)[:256]
            entities.append(entity.model_copy(update={"identity_hint": hint}))

        return StageResult(
            entities=tuple(entities),
            relations=current.relations,
            landmarks=current.landmarks,
            depth=current.depth,
            scene_label=current.scene_label,
            scene_confidence=current.scene_confidence,
        )


class PlaceRecognitionStage:
    """Attach a non-authoritative place hint from the full-frame embedding."""

    name = "mrvision_place_recognition"

    def __init__(
        self,
        profile: MrVisionProfile,
        store: EmbeddingStore,
        *,
        embedding_model_id: str,
        threshold: float = 0.75,
    ) -> None:
        if not isinstance(profile, MrVisionProfile):
            raise TypeError("profile must be MrVisionProfile")
        if not 0.0 <= threshold <= 1.0:
            raise ValueError("threshold must be between 0 and 1")
        self._profile = profile
        self._store = store
        self._model_id = embedding_model_id
        self._threshold = threshold

    def process(self, frame: Frame, current: StageResult) -> StageResult:
        if current.scene_label is not None:
            return current

        record = self._store.get(
            frame.source.source_id,
            frame.sequence,
            subject_entity_id=None,
            model_id=self._model_id,
        )
        if record is None:
            return current

        matches = match_embedding(
            self._profile,
            vector=record.vector,
            embedding_model_id=self._model_id,
            kind="place",
            threshold=self._threshold,
            top_k=1,
        )
        if not matches:
            return current

        match = matches[0]
        target = match.subject_ref or match.label
        return StageResult(
            entities=current.entities,
            relations=current.relations,
            landmarks=current.landmarks,
            depth=current.depth,
            scene_label=("mrvision-place:" + target)[:256],
            scene_confidence=float(max(0.0, min(1.0, match.score))),
        )
