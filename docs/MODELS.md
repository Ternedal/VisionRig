# VisionRig model artifacts

VisionRig does not silently download or bundle inference models.

Deployment artifacts are admitted through versioned JSON manifests. Every
manifest pins the artifact SHA-256, provenance string and artifact license.
The artifact must live inside the manifest directory; path traversal is rejected.

## Detector

```json
{
  "schema_id": "visionrig/yolo-model-manifest/v1",
  "artifact": "detector.onnx",
  "sha256": "<64 lowercase hex>",
  "source": "provenance for this exact artifact",
  "artifact_license": "license for this exact artifact",
  "labels": ["person", "face", "cup"],
  "label_kinds": {
    "face": "face",
    "cup": "object"
  },
  "input_size": 640,
  "confidence_threshold": 0.25,
  "iou_threshold": 0.45
}
```

`label_kinds` is optional and must only reference labels present in the
manifest. It lets model-specific classes such as `face`, `hand` or `body`
feed the correct VisionRig entity kind without hard-coded label conventions.

## Relative depth

```json
{
  "schema_id": "visionrig/depth-model-manifest/v1",
  "artifact": "depth.onnx",
  "sha256": "<64 lowercase hex>",
  "source": "provenance for this exact artifact",
  "artifact_license": "artifact license",
  "input_width": 384,
  "input_height": 384,
  "mean": [0.485, 0.456, 0.406],
  "std": [0.229, 0.224, 0.225],
  "higher_is_nearer": true
}
```

VisionRig normalizes each depth frame to `0..1`, where 0 is nearer and 1 is
farther after applying `higher_is_nearer`. It emits relative, not metric, depth.

## Visual embedding

```json
{
  "schema_id": "visionrig/embedding-model-manifest/v1",
  "artifact": "embedding.onnx",
  "sha256": "<64 lowercase hex>",
  "source": "provenance for this exact artifact",
  "artifact_license": "artifact license",
  "model_id": "my-vision-encoder/v1",
  "input_width": 224,
  "input_height": 224,
  "mean": [0.48145466, 0.4578275, 0.40821073],
  "std": [0.26862954, 0.26130258, 0.27577711]
}
```

Embedding vectors are L2-normalized and stored only in a bounded transient
sidecar. They are not serialized into PerceptionEvent.
