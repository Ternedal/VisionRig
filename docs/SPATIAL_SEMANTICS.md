# Spatial semantics

VisionRig has a bounded spatial stage that combines flat entity detections with
metric sensor depth when it is actually available.

## Supported geometric relations

The current deterministic stage may emit:

- `left_of` / `right_of`;
- `above` / `below`;
- `near`;
- `inside`.

These four relation families are **2D image-plane observations**. They do not
claim metric 3D truth.

For example, `cup left_of person` means the cup bounding box is visually left
of the person bounding box in the current frame. It does not by itself establish
a stable real-world arrangement.

## Metric depth ordering

When both entities have a positive measured `distance_m`, the stage may also
emit:

- `in_front_of`;
- `behind`.

The default minimum separation is 0.20 m. These relations are never inferred
from generic monocular relative depth, because a normalized model output is not
a calibrated physical distance.

## Deliberate omissions

The relation contract also has room for:

- `looking_at`;
- `holding`;
- `moving_towards`;
- `moving_away`.

The geometry stage does not fabricate them. Those predicates need stronger
evidence from pose/hands, depth and temporal tracking.

## Bounded behavior

The stage:

- considers at most 32 highest-confidence boxed entities by default;
- emits at most 128 new relations;
- deduplicates against relations produced by earlier stages;
- sorts output deterministically by confidence and IDs;
- scales heuristic geometry confidence by both entity confidences.

This prevents quadratic scene complexity from turning into unbounded event
payloads.

## Detector kinds

YOLO manifests can now explicitly map model labels to VisionRig entity kinds.

Example:

```json
{
  "schema_id": "visionrig/yolo-model-manifest/v1",
  "artifact": "face-detector.onnx",
  "sha256": "<sha256>",
  "source": "exact artifact provenance",
  "artifact_license": "artifact license",
  "labels": ["face"],
  "label_kinds": {
    "face": "face"
  }
}
```

Allowed kinds are:

`person`, `face`, `object`, `text`, `hand`, `body`, `unknown`.

Unmapped `person` keeps the historical automatic `person` kind. Other
unmapped detector labels remain `object`.

This makes face/body/object-specific `.mrvision` matching possible without
hard-coding model-specific label names into VisionRig.
