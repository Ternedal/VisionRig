# Perception contract v3

VisionRig perception v3 adds a narrow, explicit metric-depth surface.

## Depth observation

A depth observation now carries:

- `relative_depth`: normalized 0..1 depth, available from monocular or hardware adapters;
- `distance_m`: optional positive finite metric distance in metres;
- `confidence`: optional producer confidence;
- `method`: provenance/method identifier.

`distance_m` must remain absent when the producer cannot justify calibrated
metric distance. VisionRig does not convert generic monocular model scores into
fake metres.

## New spatial predicates

The relation vocabulary adds:

- `in_front_of`;
- `behind`.

The built-in spatial stage emits these only when both participating entities
have metric distance and their separation exceeds the configured threshold.

## Schema IDs

- perception event: `visionrig/perception-event/v3`
- world snapshot: `visionrig/world-snapshot/v3`

No ModelRig production bridge consumed v2 when this contract was introduced, so
the version bump is intentional rather than silently changing the meaning of
v2.
