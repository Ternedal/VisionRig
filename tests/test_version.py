from __future__ import annotations

import tomllib
from pathlib import Path

from visionrig import __version__


def test_runtime_version_matches_package_metadata() -> None:
    project = tomllib.loads(
        Path("pyproject.toml").read_text(encoding="utf-8")
    )
    assert project["project"]["version"] == __version__


def test_release_candidate_version() -> None:
    assert __version__ == "0.99.0"
