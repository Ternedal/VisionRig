"""Source-level regression for read-only, SHA-bound V1 policy observation.

This does not run GitHub or grant repository/release authority. The actual
PowerShell verifier must still be exercised by a human on the Windows rig.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODE = (ROOT / "verify-repository-authority.ps1").read_text(encoding="utf-8")


def test_exact_checkout_and_live_sha_binding():
    assert "[switch]$Json" in CODE
    assert "[string]$ExpectedSha" in CODE
    assert "git -C $root branch --show-current" in CODE
    assert "git -C $root status --porcelain --untracked-files=all" in CODE
    assert "git -C $root rev-parse HEAD" in CODE
    assert "live remote main SHA differs from exact local HEAD" in CODE
    assert "local HEAD differs from explicitly pinned ExpectedSha" in CODE


def test_fails_closed_if_live_protection_unavailable():
    assert 'repos/$Repo/branches/$Branch/protection' in CODE
    assert "live GitHub protection data inaccessible or invalid" in CODE
    assert "administrator enforcement is not enabled" in CODE
    assert "missing required check/source binding" in CODE
    assert "force pushes are allowed" in CODE
    assert "main deletion is allowed" in CODE
    assert "$null -eq $protection.allow_force_pushes" in CODE
    assert "$null -eq $protection.allow_deletions" in CODE


def test_read_only_observation_cannot_claim_release():
    assert "live_repository_authority_passed = $false" in CODE
    assert "release_gate_satisfied = $false" in CODE
    assert "release_ready = $false" in CODE
    assert "production_activation = $false" in CODE
    assert "-Apply" not in CODE
    assert "gh api $Path" in CODE
    for forbidden in ("-X PUT", "-X PATCH", "-X DELETE", "--method PUT", "--method PATCH"):
        assert forbidden not in CODE
