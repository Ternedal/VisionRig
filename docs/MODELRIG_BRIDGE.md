# ModelRig perception bridge

VisionRig can optionally publish **semantic PerceptionEvent/v3 changes** to the
ModelRig worker's Consciousness Core VisionRig admission endpoint.

The bridge is off by default. Raw RGB, IR and depth-map data never cross it.

## Enable in the VisionRig service

Set:

~~~text
VISIONRIG_MODELRIG_BRIDGE=1
VISIONRIG_MODELRIG_WORKER_URL=http://127.0.0.1:8099
~~~

ModelRig must separately enable its receiving surface:

~~~text
KALIV_CONSCIOUSNESS_VISIONRIG_ENABLED=1
~~~

The VisionRig publisher refuses non-loopback worker URLs.

## Capture CLI

For direct camera/Kinect testing:

~~~powershell
visionrig-capture --kinect-v2 `
  --model-manifest .\models\yolo.json `
  --landmarks `
  --modelrig-worker-url http://127.0.0.1:8099 `
  --verbose
~~~

Supplying `--modelrig-worker-url` is the explicit capture-CLI opt-in.

## Semantic change gate

A live camera may produce 30 frames per second. Those frames must not become 30
new cognitive events per second.

The publisher therefore fingerprints only bounded semantic state:

- entity-kind counts;
- non-OCR labels;
- relation-predicate counts;
- nearest metric depth in 25 cm buckets;
- metric-depth count;
- OCR item count, but never OCR text;
- landmark-group presence, but never raw landmarks.

Changes in confidence, bounding-box jitter, track ids and raw frame sequence do
not by themselves cause a new publication.

A fingerprint is committed only after ModelRig returns a receipt bound to the
exact VisionRig event. If delivery fails, the same semantic change remains
eligible for retry.

## Failure behavior

ModelRig delivery is optional downstream integration. VisionRig first commits
the local PerceptionEvent to its world snapshot and journal. Publisher failure
is isolated and recorded in event-sink / bridge statistics; it does not turn a
completed local perception into a failed frame.

Network/unavailable responses activate a short synchronous retry backoff. There
is no background retry thread or hidden autonomous delivery loop.

## Receipt binding

VisionRig verifies that ModelRig returns:

- the SHA-256 reference of the exact serialized PerceptionEvent/v3;
- the same observed frame sequence;
- `epistemic_status=inferred`;
- zero model calls and no execution/scheduling/durable-memory authority.

A mismatched receipt is rejected and the semantic fingerprint is not committed.
