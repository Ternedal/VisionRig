# Package and release audit — VisionRig

Source inspection: 2026-10-23; `pyproject.toml` and `docs/RELEASE.md` on main. **No CI or hardware tests executed.**

- Package `visionrig` **0.99.0**, Python **>=3.11**; setuptools >=75,<76.
- Base service dependencies include FastAPI, httpx, Pydantic and Uvicorn with upper version limits.
- Extras split capture, producer, inference, OCR, landmarks, profile encryption and Kinect v2 support.
- Declared commands include `visionrig-gateway`, `visionrig-producer`, `visionrig-qualify-physical`, `visionrig-release-evidence`, `visionrig-release-promote`, `visionrig-release-finalize` and `visionrig-release-status`.
- The documented 1.0 release process requires exact-SHA CI, real Android/Quest/Kinect sensor evidence and a ModelRig admission/cognition receipt. A release finalization report has `production_activation=false` by design.

## Operator checks

- [ ] Confirm exact release candidate SHA and a clean checkout.
- [ ] Confirm Python, Kotlin, Android and Quest CI for that revision.
- [ ] Capture distinct physical reports for each sensor type.
- [ ] Verify ModelRig receipt binding, promotion evidence and release-commit CI.
- [ ] Do not equate a version string with production activation.

See [release guide](RELEASE.md), [architecture](ARCHITECTURE.md) and [integration acceptance](INTEGRATION_ACCEPTANCE_2026-10-23.md).
