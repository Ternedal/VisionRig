# .mrvision profiles

`.mrvision` is VisionRig's local, revisioned enrollment/recognition profile.

It is **not** a general perception model and it is **not** identity authority.

Shared models such as detector, depth, OCR, pose and embedding encoders remain
global deployment artifacts. A profile contains only derived enrollment vectors
and bounded metadata needed to recognize previously enrolled visual subjects.

## What is stored

An enrollment contains:

- kind: face, body, object or place;
- operator/user supplied label;
- optional external subject reference;
- exact embedding model id;
- L2-normalized embedding vector;
- enrollment quality;
- source/provenance ref;
- enrollment time;
- revocation state.

Raw pixels are not stored in the profile.

## Revision model

Every semantic change returns a new immutable profile revision.

```text
mrvision revision 1
       |
       +-- enroll face --> revision 2
                              |
                              +-- enroll place --> revision 3
                                                     |
                                                     +-- revoke sample --> revision 4
```

Each new revision binds `parent_profile_ref` to the canonical SHA-256 reference
of the prior profile.

Exact duplicate enrollment is idempotent and creates no revision.

## Recognition semantics

Recognition performs cosine matching only against:

- active (non-revoked) enrollments;
- the requested enrollment kind;
- the exact same embedding model id;
- vectors with the same dimensionality.

A `RecognitionMatch` is a **hint**. Its contract hard-codes
`identity_authority=false`.

A later ModelRig integration may decide how visual recognition evidence should
affect world state, but it cannot treat a raw VisionRig match as Person/Profile
identity authority.

## Encryption

Serialized `.mrvision` files use AES-256-GCM.

The encryption key is supplied externally and is never stored in the envelope.
VisionRig exposes `generate_profile_key()`, `seal_profile()` and
`open_profile()`; product key-management policy is intentionally separate from
the profile format.

The authenticated-data binding includes the canonical profile reference, so
ciphertext substitution, wrong keys and tampering fail closed.

Install encryption support with:

```powershell
pip install -e ".[profile]"
```

Do not commit `.mrvision` files or keys to Git.


## Practical enrollment workflow

VisionRig now exposes `visionrig-profile` as the operator-facing training
surface. The word "training" here means deriving and storing enrollment
embeddings; it does not fine-tune the shared detector or embedding model.

Create a new encrypted profile and a separate local key file:

~~~powershell
visionrig-profile init `
  --profile D:\VisionRig\profiles\person.mrvision `
  --key-file D:\VisionRig\keys\person.key
~~~

Enroll one or more curated samples:

~~~powershell
visionrig-profile enroll `
  --profile D:\VisionRig\profiles\person.mrvision `
  --key-file D:\VisionRig\keys\person.key `
  --embedding-manifest D:\VisionRig\models\embed.json `
  --kind face `
  --label "Anders" `
  --subject-ref person:anders `
  --image D:\Enrollment\front.jpg `
  --image D:\Enrollment\side.jpg
~~~

For a sample that contains more than the intended subject, use an optional
normalized crop:

~~~text
--crop X Y WIDTH HEIGHT
~~~

Each image becomes one revisioned enrollment sample. Its provenance is stored as
an SHA-256 content reference; the local filesystem path and raw image bytes are
not stored in the profile.

Inspect metadata without exposing vectors:

~~~powershell
visionrig-profile inspect `
  --profile D:\VisionRig\profiles\person.mrvision `
  --key-file D:\VisionRig\keys\person.key
~~~

Revoke a bad/outdated sample:

~~~powershell
visionrig-profile revoke `
  --profile D:\VisionRig\profiles\person.mrvision `
  --key-file D:\VisionRig\keys\person.key `
  --enrollment-id venr-... `
  --reason "outdated sample"
~~~

The key is never accepted as a command-line value. Profile writes are atomic and
initialization refuses to overwrite an existing profile or key.
