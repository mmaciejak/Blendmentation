"""The public names of each module, importable from its package."""

import importlib

import pytest


@pytest.mark.parametrize("package", ["augmentations", "generating", "state", "export"])
def test_package_exports(package):
    if package != "export":
        pytest.importorskip("bpy")
    short = importlib.import_module(f"blendmentation.{package}")
    module = importlib.import_module(f"blendmentation.{package}.{package}")
    assert short.__all__ == module.__all__
    for name in module.__all__:
        assert getattr(short, name) is getattr(module, name), name
    # the module itself stays reachable, as before
    assert getattr(short, package) is module
