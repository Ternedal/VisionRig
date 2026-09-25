# .mrvision service runtime

VisionRig can load one encrypted .mrvision profile into the perception service.

## Configuration

Set all three paths:

```powershell
$env:VISIONRIG_EMBEDDING_MANIFEST="D:\VisionRig\models\embed.json"
$env:VISIONRIG_MRVISION_PROFILE="D:\VisionRig\profiles\person.mrvision"
$env:VISIONRIG_MRVISION_KEY_FILE="D:\VisionRig\keys\person.key"
```

Optional threshold:

```powershell
$env:VISIONRIG_RECOGNITION_THRESHOLD="0.80"
```

The key file may contain either exactly 32 raw bytes or base64 encoding of
exactly 32 bytes. The key itself is never accepted as a command-line argument or
environment value.

The profile/key settings are fail-closed:
- profile and key path must be configured together;
- profile recognition requires an embedding manifest;
- missing/corrupt/wrong-key profiles abort service composition;
- threshold must remain in 0..1.

## Runtime recognition

With a profile loaded, the embedding stage provides two recognition paths:

```text
full frame embedding --------> place enrollment match
                                   |
                                   v
                      mrvision-place:<hint>
                      scene_label + confidence

entity crop embeddings ------> face/body/object enrollment match
                                   |
                                   v
                           mrvision:<hint>
                           identity_hint
```

Both are non-authoritative hints. They do not modify detector labels and they do
not become Person/Profile identity authority in ModelRig.

A pre-existing upstream scene label is not overwritten by place recognition.

## Key management boundary

The service accepts a key-file path, but it does not own product key lifecycle.
For production, place the key file on a local protected volume with operating
system ACLs appropriate to the VisionRig service account. Do not commit profile
keys to Git or place them in a shared project folder.
