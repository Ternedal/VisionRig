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


## 7. Finalize the one-commit v1 release

After the v1 promotion attestation passes, create **exactly one** release commit
whose direct parent is the promoted 0.99.0 candidate.

The release commit must contain the required version changes in:

- `pyproject.toml`
- `src/visionrig/__init__.py`
- `tests/test_version.py`

It may additionally update release-only documentation:

- `README.md`
- `docs/ARCHITECTURE.md`
- `docs/RELEASE.md`

No other source, workflow, dependency or runtime file may change in that commit.
The finalizer also verifies that the three required version files differ from the
candidate by the exact 0.99.0 -> 1.0.0 transition only.

After the release commit is pushed to `main`, require the normal four-job CI
workflow to pass for the **release commit SHA itself**, then run:

```powershell
visionrig-release-finalize `
  --ci-run-id <release-commit-main-push-run-id> `
  validation/visionrig-v1-release-promotion.json
```

Default output:

`validation/visionrig-v1-release-finalization.json`

The finalization attestation binds:

- the exact promoted candidate SHA and promotion-attestation bytes;
- the exact 1.0.0 release commit SHA;
- the canonical changed-path set;
- the green exact-SHA main CI run and all four required job ids.

It is content-addressed as:

`visionrig-release-finalization:<release-sha>:<sha256>`

A PASS sets `release_finalized=true` while still keeping
`production_activation=false`. Only after that PASS should the 1.0.0 tag or
release be published.


## Release status / doctor

At any point in the v1 process, inspect the local release artifacts without
creating evidence or granting authority:

```powershell
visionrig-release-status
```

For machine-readable output:

```powershell
visionrig-release-status --json
```

The status tool validates each artifact with the same release validators used by
the promotion/finalization commands and then verifies the **actual file-byte
bindings** across the chain:

- physical bundle bytes -> promotion attestation;
- promotion attestation bytes -> finalization attestation.

Missing evidence is reported as a blocker rather than manufactured. Invalid or
cross-bound artifacts fail closed. The tool always reports
`production_activation=false`.

Useful automation gates:

```powershell
visionrig-release-status --require-ready
visionrig-release-status --require-finalized
```

`--require-ready` succeeds only after a valid, byte-bound promotion
attestation. `--require-finalized` succeeds only after the complete v1
finalization chain is valid and the installed package reports 1.0.0.


## One-command candidate gate runner

For the rig-side candidate workflow on Windows, use the fail-closed wrapper:

```powershell
.\run-v1-release-gate.ps1 -StatusOnly
```

To collect Android and Quest physical receipts, bundle them, and bind the bundle
to the exact checkout:

```powershell
.\run-v1-release-gate.ps1 -QualifyAndroid -QualifyQuest
```

To include Kinect v2:

```powershell
.\run-v1-release-gate.ps1 \
  -QualifyAndroid \
  -QualifyQuest \
  -QualifyKinect \
  -KinectModelManifest <model-manifest.json> \
  -ModelRigWorkerUrl http://127.0.0.1:<port>
```

After the exact candidate SHA has a green `main` push workflow, add the run id
to create the promotion attestation in the same invocation:

```powershell
.\run-v1-release-gate.ps1 \
  -QualifyAndroid \
  -QualifyQuest \
  -CandidateCiRunId <github-actions-run-id>
```

The wrapper refuses dirty/non-`main` checkouts, binds
`VISIONRIG_GIT_SHA` to the exact checkout, requires the 0.99.0 candidate
version, never manufactures missing physical evidence, never bumps the package
version, and never grants production authority.
