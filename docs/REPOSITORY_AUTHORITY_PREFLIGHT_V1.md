# VisionRig V1 — read-only repository policy observation (draft only)

This diagnostic is an isolated **draft branch** experiment for the
cross-repository authority gap tracked in
[ModelRig #2109](https://github.com/Ternedal/ModelRig/issues/2109).
It is **not available in the qualified frozen main source** and does not
authorize merging any changes, production activation or release.

The current V1 freeze pins `66ea9bfce1c4d219e4bac1d85324a0e6e3f042e5` for `Ternedal/VisionRig`.
Run only on an explicitly reviewed, clean **main** checkout of the exact
release revision. Never use `git pull` or `git reset` to make the
checkout match without an explicit re-freeze and qualification.

## Actual operator shape once separately reviewed/landed

```powershell
# Record the current state BEFORE doing anything.
git branch --show-current
git rev-parse HEAD
git status --porcelain --untracked-files=all

# Read-only inspection; no GitHub settings writes.
.\verify-repository-authority.ps1 -Json -ExpectedSha '66ea9bfce1c4d219e4bac1d85324a0e6e3f042e5'
```

The script exits nonzero unless the checkout is on clean `main`, the local
HEAD matches the explicitly requested SHA and the live GitHub `main`
branch API reports that **same revision**. It then reads the live branch
protection API and verifies selected strict required checks with GitHub App
source IDs, PR review, conversation resolution, admin enforcement and
disallowed force push/deletion. A GitHub API 403/malformed result is never
converted to green.

JSON exposes `policy_observation_passed` and a bounded error list,
but **always** sets `live_repository_authority_passed=false`,
`release_gate_satisfied=false`, `release_ready=false` and
`production_activation=false`. This does **not** independently review
all bypass/ruleset/permission semantics or provide an immutable audited
cross-repository ref. The ModelRig V1 repository-authority system gate must
remain PENDING until a separate reviewer collects complete authoritative
live evidence from the four exact revisions and validates the results.

This draft modifies no branch protection, app permissions, workflow,
runtime, freeze tag or actual release evidence.
