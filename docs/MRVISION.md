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
