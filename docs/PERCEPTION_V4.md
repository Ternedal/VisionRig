# PerceptionEvent v4 — bounded infrared observations

VisionRig 0.87.0 advances the public perception event contract from v3 to v4.

## Schema IDs

- perception event: `visionrig/perception-event/v4`
- world snapshot remains: `visionrig/world-snapshot/v3`

## Infrared observation

V4 adds an `infrared` collection containing bounded summary observations:

- `mean_intensity`: normalized 0..1 frame mean;
- `contrast`: normalized 0..1 standard deviation;
- `hotspot_fraction`: fraction of samples at or above the configured normalized hotspot threshold;
- `sample_count`: bounded number of finite samples used;
- `method`: fixed provenance `kinect-v2-infrared-summary`.

Raw infrared arrays never enter PerceptionEvent.

## Runtime behavior

The default pipeline includes `InfraredSummaryStage`. It is a strict no-op when
a frame has no `sensor_data["infrared"]` value, so webcam, screen, image and
video sources are unaffected.

Kinect v2 and remote SensorPacket ingestion keep raw IR frame-local. The summary
stage consumes that local plane and emits only the bounded observation.

## ModelRig boundary

ModelRig admission supports both v3 and v4 during rollout. For v4, only the
normalized summary enters the bounded inferred WorldEvidence proposition. Raw
pixels, identity authority, memory authority, execution authority and production
activation remain excluded.
