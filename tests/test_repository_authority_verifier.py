"""Run verify-repository-authority.ps1 for real.

The verifier is the operator's entry point for the Kaliv repository_authority
release gate. It is run here in PowerShell against a fake ``gh``, in a
throw-away checkout on ``main``: the verdict must be one JSON line, bound to
the checkout's HEAD, and a deficit must be a verdict rather than a crash.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "verify-repository-authority.ps1"
# PowerShell 7 everywhere, and Windows PowerShell 5.1 where it exists. The two
# differ exactly where this script is fragile: under 5.1 a native command that
# writes to stderr can terminate the script before its exit code is read.
SHELLS = [name for name in ("pwsh", "powershell") if shutil.which(name)]

pytestmark = pytest.mark.skipif(not SHELLS, reason="PowerShell is unavailable")

# A fake GitHub CLI. It answers from JSON files in FAKE_GH_DIR and fails, as
# the real one does, for an endpoint that has no file.
FAKE_GH = r'''
import os, sys
from pathlib import Path

root = Path(os.environ["FAKE_GH_DIR"])
args = sys.argv[1:]
if os.environ.get("FAKE_GH_NOISE"):
    print("warning: a newer release of gh is available", file=sys.stderr)
if args[:1] != ["api"] or len(args) < 2:
    sys.exit(1)
path = args[1]
if path.endswith("/branches/main/protection"):
    name = "protection.json"
elif path.endswith("/branches/main"):
    name = "branch.json"
else:
    print("unexpected endpoint " + path, file=sys.stderr)
    sys.exit(1)
target = root / name
if not target.is_file():
    print('{"message":"Not Found"}', file=sys.stderr)
    sys.exit(1)
sys.stdout.write(target.read_text(encoding="utf-8"))
'''


def _expected_checks() -> list[dict]:
    """The checks the verifier itself requires, read from its source."""
    block = re.search(r"\$ExpectedChecks = @\((.*?)\n\)", SCRIPT.read_text(encoding="utf-8"), re.S)
    assert block, "the verifier no longer declares $ExpectedChecks"
    checks = [
        {"context": context, "app_id": int(app_id)}
        for context, app_id in re.findall(r'context = "([^"]+)"; app_id = (\d+)', block.group(1))
    ]
    assert checks
    return checks


def _git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        [
            "git",
            "-c", "user.name=contract",
            "-c", "user.email=contract@example.invalid",
            "-c", "commit.gpgsign=false",
            *args,
        ],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _protection() -> dict:
    """Branch protection that satisfies the verifier."""
    return {
        "required_pull_request_reviews": {"required_approving_review_count": 0},
        "required_status_checks": {"strict": True, "checks": _expected_checks()},
        "enforce_admins": {"enabled": True},
        "allow_force_pushes": {"enabled": False},
        "allow_deletions": {"enabled": False},
        "required_conversation_resolution": {"enabled": True},
    }


class Checkout:
    """A clean checkout on ``main`` holding the verifier."""

    def __init__(self, base: Path, shell: str) -> None:
        self.shell = shutil.which(shell)
        self.noisy_gh = False
        self.root = base / "checkout"
        self.root.mkdir()
        shutil.copy(SCRIPT, self.root)
        _git(self.root, "init", "--quiet", "--initial-branch=main")
        _git(self.root, "add", ".")
        _git(self.root, "commit", "--quiet", "-m", "verifier contract fixture")
        self.head = _git(self.root, "rev-parse", "HEAD")
        self.tools = base / "tools"
        self.tools.mkdir()
        script = self.tools / "fake_gh.py"
        script.write_text(FAKE_GH, encoding="utf-8")
        if os.name == "nt":
            (self.tools / "gh.cmd").write_text(
                f'@"{sys.executable}" "{script}" %*\r\n', encoding="ascii"
            )
        else:
            launcher = self.tools / "gh"
            launcher.write_text(
                f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="ascii"
            )
            launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR)
        self.github = base / "github"
        self.github.mkdir()

    def github_state(
        self,
        *,
        head: str | None = None,
        protected: bool = True,
        protection: dict | None,
    ) -> None:
        for stale in self.github.iterdir():
            stale.unlink()
        branch = {"name": "main", "protected": protected, "commit": {"sha": head or self.head}}
        (self.github / "branch.json").write_text(json.dumps(branch), encoding="utf-8")
        if protection is not None:
            (self.github / "protection.json").write_text(json.dumps(protection), encoding="utf-8")

    def run(self, *extra: str) -> subprocess.CompletedProcess:
        environment = dict(os.environ)
        environment["PATH"] = str(self.tools) + os.pathsep + environment.get("PATH", "")
        environment["FAKE_GH_DIR"] = str(self.github)
        environment.pop("FAKE_GH_NOISE", None)
        if self.noisy_gh:
            environment["FAKE_GH_NOISE"] = "1"
        return subprocess.run(
            [
                self.shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-File",
                str(self.root / "verify-repository-authority.ps1"),
                *extra,
            ],
            cwd=str(self.root),
            capture_output=True,
            text=True,
            env=environment,
            timeout=300,
            check=False,
        )

    def verdict(self) -> tuple[int, dict]:
        completed = self.run("-Json")
        lines = [line for line in completed.stdout.splitlines() if line.strip()]
        assert len(lines) == 1, (
            f"-Json must print exactly one line; rc={completed.returncode}\n"
            f"stdout={completed.stdout[-600:]}\nstderr={completed.stderr[-600:]}"
        )
        return completed.returncode, json.loads(lines[0])


@pytest.fixture(params=SHELLS)
def checkout(tmp_path: Path, request: pytest.FixtureRequest) -> Checkout:
    return Checkout(tmp_path, request.param)


def test_a_passing_verdict_is_one_json_line_bound_to_the_checkout(checkout: Checkout) -> None:
    checkout.github_state(protection=_protection())
    code, verdict = checkout.verdict()
    assert code == 0, verdict
    assert verdict["passed"] is True
    assert verdict["format"].endswith("-repository-authority")
    assert verdict["version"] == 1
    assert verdict["branch"] == "main"
    assert verdict["head_revision"] == checkout.head
    assert verdict["github_head_revision"] == checkout.head
    assert verdict["protected"] is True
    assert verdict["authority_mode"] == "classic"
    assert verdict["production_activation"] is False
    # Lists stay lists: one required check is a list of one, no error is an
    # empty list. A PowerShell pipeline would give a bare object and null.
    assert verdict["errors"] == []
    assert verdict["required_status_checks"] == _expected_checks()


def test_main_elsewhere_than_the_checkout_fails(checkout: Checkout) -> None:
    checkout.github_state(head="f" * 40, protection=_protection())
    code, verdict = checkout.verdict()
    assert code == 2
    assert verdict["passed"] is False
    assert verdict["authority_mode"] is None
    assert verdict["head_revision"] == checkout.head
    assert verdict["github_head_revision"] == "f" * 40
    assert verdict["errors"] == ["GitHub main head does not match the checkout revision"]


def _without_first_check(protection: dict) -> None:
    protection["required_status_checks"]["checks"] = protection["required_status_checks"]["checks"][1:]


def _wrong_source(protection: dict) -> None:
    protection["required_status_checks"]["checks"][0]["app_id"] = 1


@pytest.mark.parametrize(
    ("mutate", "error"),
    [
        (lambda p: p["required_status_checks"].update(strict=False), "required_status_checks.strict is not true"),
        (lambda p: p.update(required_status_checks=None), "required_status_checks is missing"),
        (_without_first_check, "missing required check/source binding"),
        (_wrong_source, "missing required check/source binding"),
        (lambda p: p.update(required_pull_request_reviews=None), "pull requests are not required"),
        (lambda p: p.update(enforce_admins={"enabled": False}), "administrator enforcement is not enabled"),
        (lambda p: p.update(required_conversation_resolution={"enabled": False}), "conversation resolution is not required"),
        (lambda p: p.update(allow_force_pushes={"enabled": True}), "force pushes are allowed"),
        (lambda p: p.update(allow_deletions={"enabled": True}), "main deletion is allowed"),
    ],
)
def test_a_deficit_is_a_verdict_with_exit_2(checkout: Checkout, mutate, error: str) -> None:
    protection = _protection()
    mutate(protection)
    checkout.github_state(protection=protection)
    code, verdict = checkout.verdict()
    assert code == 2
    assert verdict["passed"] is False
    assert any(error in item for item in verdict["errors"]), verdict["errors"]


def test_missing_protection_is_a_verdict_not_a_crash(checkout: Checkout) -> None:
    checkout.github_state(protected=False, protection=None)
    code, verdict = checkout.verdict()
    assert code == 2
    assert verdict["protected"] is False
    assert "main protection is unavailable" in verdict["errors"]
    assert "GitHub reports main as unprotected" in verdict["errors"]


def test_stderr_from_gh_is_not_part_of_the_answer(checkout: Checkout) -> None:
    # gh may warn on stderr and still answer on stdout. The warning must
    # neither end the script (Windows PowerShell 5.1) nor be parsed as JSON.
    checkout.noisy_gh = True
    checkout.github_state(protection=_protection())
    code, verdict = checkout.verdict()
    assert code == 0, verdict
    assert verdict["passed"] is True

    checkout.github_state(protected=False, protection=None)
    code, verdict = checkout.verdict()
    assert code == 2
    assert "main protection is unavailable" in verdict["errors"]


def test_human_readable_output(checkout: Checkout) -> None:
    checkout.github_state(protection=_protection())
    completed = checkout.run()
    assert completed.returncode == 0, completed.stderr[-400:]
    assert "Repository authority: PASS" in completed.stdout
    assert f"Head: {checkout.head}" in completed.stdout

    weak = _protection()
    weak["enforce_admins"] = {"enabled": False}
    checkout.github_state(protection=weak)
    completed = checkout.run()
    assert completed.returncode == 2
    assert "Repository authority: FAIL" in completed.stdout
    assert "administrator enforcement is not enabled" in completed.stdout


def test_only_an_exact_clean_main_checkout_is_verified(checkout: Checkout) -> None:
    checkout.github_state(protection=_protection())
    (checkout.root / "stray.txt").write_text("x\n", encoding="utf-8")
    completed = checkout.run("-Json")
    assert completed.returncode not in (0, 2)
    assert completed.stdout.strip() == "", "a refused run prints no verdict"
    assert "exact clean main checkout" in completed.stderr
    (checkout.root / "stray.txt").unlink()

    _git(checkout.root, "checkout", "--quiet", "-b", "feature")
    completed = checkout.run("-Json")
    assert completed.returncode not in (0, 2)
    assert completed.stdout.strip() == ""
    assert "must run from branch main" in completed.stderr
