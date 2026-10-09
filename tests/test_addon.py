"""The package as a Blender add-on. The bl_info check runs without bpy."""

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def bl_info():
    tree = ast.parse((ROOT / "blendmentation" / "__init__.py").read_text(encoding="utf-8"))
    return next(ast.literal_eval(node.value) for node in tree.body
                if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", None) == "bl_info")


def test_bl_info_version():
    version = re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(), re.M).group(1)
    assert ".".join(map(str, bl_info()["version"])) == version, "bl_info's version must match pyproject.toml"


def test_register():
    bpy = pytest.importorskip("bpy")
    import blendmentation
    from blendmentation import addon

    blendmentation.register()
    try:
        assert hasattr(bpy.types, "NODE_OT_blendmentation_copy_template")
        assert hasattr(bpy.ops.node, "blendmentation_copy_template")
        # no node editor here, so it can't run
        assert not bpy.ops.node.blendmentation_copy_template.poll()
        assert addon.template_prefix(bpy.context) == "augmentations.", "not enabled as an add-on: the default"

        material = bpy.data.materials.new("Mat")
        material.use_nodes = True
        node = material.node_tree.nodes["Principled BSDF"]
        text = addon.copy_template(node, bpy.context.window_manager, prefix="aug.")
        assert text.startswith("""aug.Node('bpy.data.materials["Mat"].node_tree.nodes["Principled BSDF"]', {""")
    finally:
        blendmentation.unregister()
    assert not hasattr(bpy.types, "NODE_OT_blendmentation_copy_template")
