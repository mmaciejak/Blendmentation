# Development

## Tests

The tests run with pytest and use Blender as a Python module, so they need a
`bpy`-compatible Python and OpenGL for Workbench (a GPU, or Mesa on Linux):

```sh
uv venv --python 3.13 .venv
uv pip install -e ".[module,test]"
.venv/bin/python -m pytest
```

Without `bpy`, only the tests that don't need Blender run, and the rest are skipped.

GitHub Actions (`.github/workflows/tests.yml`) runs the suite on every push to `main` or
`dev` and every pull request: with `bpy` 5.x (Python 3.13) and `bpy` 4.5 (Python 3.11) on Linux
with Mesa software rendering, and without Blender on Python 3.10 and 3.12. Blender 4.0
has no `bpy` wheel, so it is only checked in the app.

## Branches

`main` always holds the latest release, because the installation instructions clone it
or pip install it from GitHub, and the docs site is deployed from it. Development
happens on `dev` (or feature branches merged into `dev`); `main` only moves on a
release.

## Changelog

`CHANGELOG.md` (shown on the [Changelog](changelog.md) page) follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Every user-facing change adds
a line under `## [Unreleased]`, in `Added`, `Changed`, `Deprecated`, `Removed` or
`Fixed`, in the same commit as the change. Entries are written for users: what they
can do now or what behaves differently, not how it was implemented.

## Releasing

Releases are made by `.github/workflows/release.yml`:

1. On `dev`, set the new `version` in `pyproject.toml`, and in `CHANGELOG.md` rename
   `## [Unreleased]` to `## [<version>] - <YYYY-MM-DD>`, add an empty
   `## [Unreleased]` above it, and update the compare links at the bottom. Commit both.
   The tests fail when the version in `pyproject.toml` has no changelog section with
   entries.
2. Merge `dev` into `main`: `git checkout main && git merge --ff-only dev && git push`.
3. Tag that commit with the same version and push the tag:
   `git tag v0.7.3 && git push origin v0.7.3`.

The workflow runs the tests, checks that the tag matches the version, builds the wheel
and sdist, and creates a GitHub release with both files attached. Its notes are the
version's section of `CHANGELOG.md` (`.github/scripts/release_notes.py`); without one,
the release fails before anything is published. It is not
published to PyPI yet; a specific release can be installed from its wheel, e.g.
`pip install https://github.com/mmaciejak/Blendmentation/releases/download/v0.7.3/blendmentation-0.7.3-py3-none-any.whl`.

## Known issues

- **EEVEE AOV bug (Blender 5.2):** a Value AOV listed before a Color AOV in the view
  layer renders as all zeros. Put Color AOVs first in the list. Cycles is not affected.
- **Blender 6:** Blender 5.2 warns that `Material.use_nodes` and light node trees will
  be removed in Blender 6.
