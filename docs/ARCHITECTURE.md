# VisionRig architecture

## Authority boundary

VisionRig owns the technical transformation from visual inputs into typed
observations. It is **not** authoritative about durable identity, intent,
emotion, memory, actions or Consciousness Core state.

```text
Input adapters
 camera | Kinect v2 | screen | Kaliv VR | image/video
                    |
                    v
          BoundedFrameQueue
     fresh frames > stale latency
                    |
                    v
       +--------------------------+
       |        VisionRig         |
       |--------------------------|
       | detect + track           |
       | OCR                      |
       | pose / hands / face      |
       | monocular/hardware depth  |
       | visual embeddings        |
       +------------+-------------+
                    |
          PerceptionEvent v3
   entities/landmarks/depth(m)
                    |
          semantic change gate
      raw frames never cross boundary
                    |
                    v
       ModelRig C20 evidence adapter
                    |
             semantic fusion
                    |
                    v
          Consciousness Core
       world / attention / time

 visual embeddings
       |
       +--> bounded sidecar --> future .mrvision recognition
```

## Design rules

1. Raw pixels do not enter Consciousness Core.
2. Raw embedding vectors do not enter Consciousness Core.
3. Every public observation carries provenance, sequence and confidence where
   the producing model actually supplies one.
4. Core contracts stay CPU-only and model-agnostic.
5. GPU/model stacks are optional adapters.
6. VisionRig state is ephemeral by default; durable memory belongs to ModelRig.
7. Recognition results are hints, never unquestioned identity truth.
8. A model adapter cannot gain tool, body, voice, memory-write or action authority.
9. Live capture is bounded; overload drops stale frames rather than accumulating latency.
10. Tracking ids express short-term continuity only.
11. ONNX deployment artifacts are explicitly manifested, checksummed and
    provenance/licence annotated; VisionRig never silently downloads them.

## Contract change: v1 -> v2

V1 was intentionally replaced before the ModelRig bridge existed. V2 adds typed
landmark and depth surfaces so pose/hands/depth do not get smuggled through
generic labels. No downstream production consumer had been established, making
this the safe point for the breaking contract correction.

## Slices

### V0 — foundation — complete
Strict contracts, pipeline protocol, ephemeral world snapshot and local API.

### V1 — capture/runtime — complete
Webcam/image/video/Kinect v2 sources, bounded frame queue, backpressure and capability probing.

### V2A — detection/tracking — complete
Verified YOLO-style ONNX detector and per-source short-term IoU tracking.

### V2B — multimodal perception — implemented
Tesseract OCR, MediaPipe landmarks, generic ONNX relative depth, bounded
sidecar visual embeddings, v2 event contracts and CLI composition.

### V3 — recognition and .mrvision — implemented
Encrypted/local visual profile with face/body/object/place embeddings, explicit
enrollment, revocation and revisioning. Global models remain shared.

### V3B — metric depth + 3D ordering — implemented
Hardware sensors can preserve measured distance in metres. Spatial fusion emits
front/behind ordering only from metric depth and keeps monocular estimates
explicitly relative.

### V4 — ModelRig bridge — implemented
Publish only semantic PerceptionEvent v3 changes to a strict loopback ModelRig
C20 adapter. Exact event refs are receipt-bound; OCR text, identity hints and
raw frame modalities remain outside cognitive context. Delivery failure is
isolated from local perception.

### V5 — sensor control and Kaliv ingress — implemented foundation
Authenticated Windows/screen/camera and Kaliv VR/passthrough producer transport,
persistent sensor catalog/discovery, stable source identity, desired-state control, effective-state convergence,
and managed local webcam/Kinect control are implemented. Native Kaliv Android/
Quest producer clients remain a separate client-delivery slice.


## Contract change: v2 -> v3

V3 adds optional `distance_m` to each depth observation and adds
`in_front_of` / `behind` relation predicates. The metric field is populated
only by adapters with a calibrated/measured distance source. Relative monocular
depth remains valid but cannot manufacture metric ordering.
