# VisionRig architecture

## Authority boundary

VisionRig owns the technical transformation from visual inputs into typed
observations. It is **not** authoritative about identity, intent, emotion,
durable memory, actions, or Consciousness Core state.

```text
Input adapters
 camera | screen | Kaliv VR | image/video
                    |
                    v
          BoundedFrameQueue
     fresh frames > stale latency
                    |
                    v
       +-------------------------+
       |       VisionRig         |
       |-------------------------|
       | detection / tracking    |
       | OCR / pose / hands      |
       | embeddings / depth      |
       | scene observations      |
       +------------+------------+
                    |
             PerceptionEvent v1
                    |
                    v
       ModelRig integration layer
                    |
             semantic fusion
                    |
                    v
          Consciousness Core
       world / attention / time
```

## Design rules

1. Raw pixels do not enter Consciousness Core.
2. Every observation carries source, timestamp, sequence and confidence.
3. Core contracts stay CPU-only and model-agnostic.
4. GPU/model stacks are optional adapters.
5. VisionRig state is ephemeral by default; durable memory belongs to ModelRig.
6. Recognition results are hints, never unquestioned identity truth.
7. A model adapter cannot gain tool, body, voice, memory-write or action authority.
8. Live capture is bounded. On overload, stale frames are dropped rather than
   allowing unbounded visual latency.
9. Dropped frames are counted and propagated into downstream observations.
10. Tracking ids express short-term continuity only; they are explicitly not
    durable identity.

## Slices

### V0 — foundation — complete
Strict contracts, pipeline protocol, ephemeral world snapshot and local API.

### V1 — capture/runtime — complete
Optional OpenCV webcam/image/video sources, bounded frame queue, freshness
backpressure, dropped-frame accounting and runtime capability probing.

### V2A — object detection + tracking — implemented
Optional YOLOv8-style ONNX adapter with CPU/CUDA provider selection, normalized
bounding boxes and NMS, plus dependency-free per-source IoU tracking.

A production model artifact is deliberately not bundled into the repository.
Model provenance, checksum, licensing and acceptance thresholds must be explicit
before a concrete model becomes authoritative deployment input.

### V2B — perception expansion — next
OCR, pose/hands, depth, scene embeddings and performance/load controls.

### V3 — recognition and .mrvision
Local encrypted visual profile with face/body/object/place embeddings and explicit
enrollment. Global perception models remain shared; profiles contain personal
recognition data, not duplicate foundation models.

### V4 — ModelRig bridge
Versioned adapter that maps PerceptionEvent into ModelRig world observations and
attention candidates. Integration remains fail-closed if schema versions differ.

### V5 — Kaliv sensors
Android camera, Windows screen/camera and Kaliv VR/passthrough producers through
the same source contract.
