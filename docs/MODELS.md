# VisionRig model artifacts

VisionRig does not silently download or bundle inference models.

A deployment model is admitted through a versioned JSON manifest. The manifest
pins the artifact checksum, provenance string, artifact license, class labels and
the preprocessing/detection thresholds expected by the adapter.

Example:

```json
{
  "schema_id": "visionrig/yolo-model-manifest/v1",
  "artifact": "detector.onnx",
  "sha256": "<64 lowercase hex characters>",
  "source": "where this exact artifact came from",
  "artifact_license": "license applying to this exact artifact",
  "labels": ["person", "bicycle", "car"],
  "input_size": 640,
  "confidence_threshold": 0.25,
  "iou_threshold": 0.45
}
```

The artifact path is resolved relative to the manifest. VisionRig hashes the
artifact before ONNX Runtime is constructed and fails closed on mismatch.

This is intentionally separate from Python-package licensing: a model artifact
can have different provenance and license terms from the runtime code.
