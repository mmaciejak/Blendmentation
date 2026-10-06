"""Prints the CHANGELOG.md section of a version, for the GitHub release notes.

    python .github/scripts/release_notes.py 0.5.0 > notes.md

Exits with an error when the version has no section or the section has no entries, so a
release can't go out without its changelog.
"""

import re
import sys
from pathlib import Path

CHANGELOG = Path(__file__).resolve().parents[2] / "CHANGELOG.md"


def section(text, version):
    """The body of the `## [version]` section, without its heading, or None."""
    heading = re.compile(rf"^## \[{re.escape(version)}\]")
    lines, inside = [], False
    for line in text.splitlines():
        if inside and (line.startswith("## ") or re.match(r"^\[[^\]]+\]: ", line)):
            break
        if inside:
            lines.append(line)
        elif heading.match(line):
            inside = True
    return "\n".join(lines).strip() if inside else None


def main():
    version = sys.argv[1].removeprefix("v")
    notes = section(CHANGELOG.read_text(encoding="utf-8"), version)
    if notes is None:
        sys.exit(f"CHANGELOG.md has no '## [{version}]' section, rename '## [Unreleased]' to it")
    if not re.search(r"^- ", notes, re.M):
        sys.exit(f"The CHANGELOG.md section of {version} has no entries")
    print(notes)


if __name__ == "__main__":
    main()
