# Kaliv Quest passthrough producer

Native Meta Quest passthrough camera adapter for VisionRig.

Supported target:
- Quest 3 / Quest 3S;
- Horizon OS v74+;
- Android Camera2 passthrough camera API;
- both `android.permission.CAMERA` and `horizonos.permission.HEADSET_CAMERA`;
- passthrough feature enabled.

The adapter discovers Meta passthrough cameras using the official vendor tags:
- `com.meta.extra_metadata.camera_source`;
- `com.meta.extra_metadata.position`.

It runs a YUV_420_888 Camera2 stream, encodes the latest bounded frame as JPEG,
and feeds the shared Kaliv producer state machine. Desired-state disable closes
the camera. Control-plane, permission, camera, session and transport failures
fail closed.

Quest 2 is intentionally not declared supported because Meta's Passthrough
Camera Access API exposes forward-facing RGB frames only on Quest 3 / Quest 3S.

The app remains responsible for requesting runtime permissions before running
the producer session.
