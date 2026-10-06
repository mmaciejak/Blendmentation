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

GitHub Actions (`.github/workflows/tests.yml`) runs the suite on every push to `main` and
every pull request: with `bpy` 5.x (Python 3.13) and `bpy` 4.5 (Python 3.11) on Linux
with Mesa software rendering, and without Blender on Python 3.10 and 3.12. Blender 4.0
has no `bpy` wheel, so it is only checked in the app.

## Releasing

Releases are made by `.github/workflows/release.yml`:

1. Set the new `version` in `pyproject.toml` and commit it.
   Also update the version in the release wheel URLs in `README.md` and
   `docs/installation.md`, which point at the latest release.
2. Tag the commit with the same version and push the tag:
   `git tag v0.2.0 && git push origin v0.2.0`.

The workflow runs the tests, checks that the tag matches the version, builds the wheel
and sdist, and creates a GitHub release with notes and both files attached. It is not
published to PyPI yet; until then, install a release from its wheel, e.g.
`pip install https://github.com/mmaciejak/Blendmentation/releases/download/v0.2.0/blendmentation-0.2.0-py3-none-any.whl`.

## Known issues

- **EEVEE AOV bug (Blender 5.2):** a Value AOV listed before a Color AOV in the view
  layer renders as all zeros. Put Color AOVs first in the list. Cycles is not affected.
- **Blender 6:** Blender 5.2 warns that `Material.use_nodes` and light node trees will
  be removed in Blender 6.
