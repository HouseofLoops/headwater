"""The version must agree everywhere a release reads or shows it.

release.yml tags whatever app/__version__.py says, the Dockerfile writes it
into the image's OCI label, and docs/DOCKERHUB.md tells users which tag is
latest. scripts/increment_version.py bumps all three; this catches a manual
edit that updates only one.
"""

import re
from pathlib import Path

from app.__version__ import __version__

ROOT = Path(__file__).resolve().parent.parent


def _find(path: str, pattern: str) -> str:
    match = re.search(pattern, (ROOT / path).read_text())
    assert match, f"no version string found in {path}"
    return match.group(1)


def test_dockerfile_label_matches_version():
    assert _find("Dockerfile", r'org\.opencontainers\.image\.version="([^"]+)"') == __version__


def test_dockerhub_latest_tag_matches_version():
    assert _find("docs/DOCKERHUB.md", r"\| `latest`, `([^`]+)` \|") == __version__
