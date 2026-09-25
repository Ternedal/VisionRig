# VisionRig architecture

## Authority boundary

VisionRig is a perception authority only in the narrow sense that it owns the
technical transformation from a visual input into a typed observation. It is
**not** authoritative about identity, intent, emotion, durable memory, actions,
or Consciousness Core state.

```text
Input adapters
 camera | screen | Kaliv VR | image/video
                    |
                    v
              Capture Frame
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
8. Frame ingestion must remain bounded; future live adapters must use backpressure
   and explicit dropped-frame accounting.

## Planned slices

### V0 — foundation (this commit)
Strict contracts, pipeline protocol, ephemeral world snapshot and local API.

### V1 — real capture
OpenCV/webcam adapter, image/video adapters, bounded async frame queue and runtime
capability probing.

### V2 — perception
ONNX/CUDA detector, tracking, OCR, pose/hands and depth behind independent
capability adapters.

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
