# VisionRig v1 release evidence

VisionRig's 1.0 gate is evidence-driven. Repository CI and physical sensor
acceptance are separate facts and must stay separate.

## 1. Select the exact release candidate

Use a clean checkout and capture the exact revision:

```powershell
git checkout main
git pull --ff-only
$sha = (git rev-parse HEAD).Trim()
git status --short
```

The working tree must be clean. Start VisionRig with
`VISIONRIG_GIT_SHA=$sha` so `/health.service_revision` is bound to the same
40-hex revision.

## 2. Require green repository CI

The normal repository workflow must be green for that revision:

- Python test suite;
- Kotlin producer-core tests;
- Android producer build/unit tests;
- Quest producer build/unit tests.

The local release-evidence validator deliberately does **not** claim or infer CI
status.

## 3. Produce physical evidence

For Android or Quest sources already online through the authenticated gateway:

```powershell
visionrig-qualify-physical --expected-sha $sha --source-id kaliv-android
visionrig-qualify-physical --expected-sha $sha --source-id kaliv-quest `
  --report validation/visionrig-quest-physical.json
```

Use distinct `--report` paths when qualifying more than one generic physical
source.

For Kinect v2:

```powershell
visionrig-kinect-acceptance `
  --expected-sha $sha `
  --model-manifest <model-manifest.json> `
  --modelrig-worker-url http://127.0.0.1:<port> `
  --output validation/visionrig-kinect-physical-acceptance.json
```

These commands require real sensor input. CI cannot manufacture passing physical
receipts.

## 4. Validate and bundle the receipts

After physical qualification, validate all release evidence against the exact
candidate SHA and package version:

```powershell
visionrig-release-evidence `
  --expected-sha $sha `
  --expected-version 0.99.0 `
  validation/visionrig-physical-perception-latest.json `
  validation/visionrig-kinect-physical-acceptance.json
```

Default output:

`validation/visionrig-release-evidence-bundle.json`

The validator:

- accepts generic physical qualification v2 and Kinect acceptance v3 receipts;
- requires every receipt to match the same exact git SHA and VisionRig version;
- recomputes the generic `release_evidence_ref` hash;
- revalidates exact VisionRig/ModelRig event, evidence and cognition references;
- rejects production/identity/raw-sensor authority escalation;
- rejects duplicate evidence for the same source;
- requires a clean checkout matching `--expected-sha`;
- emits no raw frames or semantic payload.

The bundle includes
`repository_ci_verified_by_this_tool=false` by design. A valid physical bundle
plus an independently green repository CI run are the two release facts needed
for the v1 promotion decision.


## Bundle integrity

Each evidence entry records:

- `receipt_sha256`: SHA-256 over the exact receipt file bytes;
- `receipt_bytes`: the bounded receipt byte length.

The bundle also carries a canonical `bundle_ref`:

`visionrig-release-evidence:<git-sha>:<sha256>`

The bundle hash excludes local file paths and generation time, so the same set
of unchanged receipt bytes and validated metadata produces the same binding on
another machine or directory. Changing any bound receipt digest, byte count,
source identity, revision/version summary, or gate field invalidates the bundle
reference.


## 5. Create the final v1 promotion attestation

After the physical evidence bundle is valid, identify the **green push run on
`main` for the exact same candidate SHA**. The promotion tool verifies the run
directly through the GitHub API and requires all four release jobs to be green:

- `test`
- `kotlin-producer-core`
- `android-producer`
- `quest-producer`

Run:

```powershell
visionrig-release-promote `
  --expected-sha $sha `
  --candidate-version 0.99.0 `
  --target-version 1.0.0 `
  --ci-run-id <github-actions-run-id> `
  validation/visionrig-release-evidence-bundle.json
```

For public GitHub API reads no token is normally required. If rate limits or
repository policy require authentication, set `GITHUB_TOKEN`; the CLI reads it
without persisting it.

Default output:

`validation/visionrig-v1-release-promotion.json`

The promotion attestation is content-addressed as:

`visionrig-release-promotion:<git-sha>:<sha256>`

A PASS proves that one exact 0.99.0 candidate revision has both:

1. revision/version-bound physical evidence; and
2. a completed successful `tests` push workflow on `main` for the same SHA,
   with every required Python/Kotlin/Android/Quest job green.

The attestation sets `release_ready=true` but deliberately keeps
`production_activation=false`. Release readiness is not runtime production
authority.

## 6. Promote the package version

Only after the promotion attestation passes should the release commit change
the package/reported version from `0.99.0` to `1.0.0`. The attestation binds
the exact tested candidate SHA and is retained as evidence for that promotion.
Do not infer a v1 PASS from a version bump alone.
