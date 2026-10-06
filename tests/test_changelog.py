"""CHANGELOG.md has to be up to date for a release: the release workflow uses the section
of the version as the GitHub release notes. Runs without bpy."""

import re
import runpy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
notes = runpy.run_path(str(ROOT / ".github" / "scripts" / "release_notes.py"))


def changelog():
    return (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")


def test_current_version_has_entries():
    # tomllib needs python 3.11
    version = re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(), re.M).group(1)
    section = notes["section"](changelog(), version)
    assert section and re.search(r"^- ", section, re.M), f"CHANGELOG.md needs a '## [{version}]' section with entries"


def test_unreleased_section_exists():
    assert notes["section"](changelog(), "Unreleased") is not None, "keep a '## [Unreleased]' section on top"


def test_section_stops_at_next_version_and_links():
    text = "## [1.1.0]\n\n- new\n\n## [1.0.0] - 2026-01-01\n\n- old\n\n[1.0.0]: https://example.com\n"
    assert notes["section"](text, "1.1.0") == "- new"
    assert notes["section"](text, "1.0.0") == "- old"
    assert notes["section"](text, "0.9.0") is None


@pytest.mark.parametrize("version", ["0.1.0", "0.2.0", "0.3.0", "0.4.0"])
def test_released_versions(version):
    assert notes["section"](changelog(), version)
