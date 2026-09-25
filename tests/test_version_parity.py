from pathlib import Path
import tomllib

import visionrig
from visionrig.api import create_app


def test_package_and_api_versions_match_project_metadata() -> None:
    root = Path(__file__).resolve().parents[1]
    metadata = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    project_version = metadata["project"]["version"]

    assert visionrig.__version__ == project_version
    assert create_app().version == project_version
