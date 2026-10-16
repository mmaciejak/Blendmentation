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
    import blendmentation

    assert blendmentation.__version__ == version


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
        assert hasattr(bpy.types, "NODE_PT_blendmentation") and hasattr(bpy.types, "VIEW3D_PT_blendmentation")
        assert hasattr(bpy.ops.object, "blendmentation_copy_shape_keys_template")
        assert addon.template_prefix(bpy.context) == "augmentations.", "not enabled as an add-on: the default"

        material = bpy.data.materials.new("Mat")
        material.use_nodes = True
        node = material.node_tree.nodes["Principled BSDF"]
        text = addon.copy_template(node, bpy.context.window_manager, prefix="aug.")
        assert """\naug.Node('bpy.data.materials["Mat"].node_tree.nodes["Principled BSDF"]', {""" in text

        bpy.ops.mesh.primitive_cube_add()
        obj = bpy.context.active_object
        assert not bpy.ops.object.blendmentation_copy_shape_keys_template.poll(), "no shape keys"
        obj.shape_key_add(name="Basis")
        obj.shape_key_add(name="Smile")
        assert bpy.ops.object.blendmentation_copy_shape_keys_template.poll()
        assert bpy.ops.object.blendmentation_copy_shape_keys_template() == {"FINISHED"}
        text = addon.copy_shape_keys_template(obj, bpy.context.window_manager, prefix="aug.")
        assert 'here "Cube"' in text and "\naug.Chain([" in text
        assert bpy.ops.object.blendmentation_copy_shape_keys_template(relative=False) == {"FINISHED"}

        assert not bpy.ops.object.blendmentation_copy_modifier_template.poll(), "no modifier"
        obj.modifiers.new("Bevel", "BEVEL")
        assert bpy.ops.object.blendmentation_copy_modifier_template.poll(), "any modifier"
        group = bpy.data.node_groups.new("GN", "GeometryNodeTree")
        group.interface.new_socket("Height", in_out="INPUT", socket_type="NodeSocketFloat")
        obj.modifiers.new("GN", "NODES").node_group = group
        assert bpy.ops.object.blendmentation_copy_modifier_template.poll()
        assert bpy.ops.object.blendmentation_copy_modifier_template(relative=False) == {"FINISHED"}
        text = addon.copy_modifier_template(obj.modifiers["GN"], bpy.context.window_manager, prefix="aug.")
        assert """\naug.Modifier('modifiers["GN"]', {""" in text
    finally:
        blendmentation.unregister()
    assert not hasattr(bpy.types, "NODE_OT_blendmentation_copy_template")
    assert not hasattr(bpy.types, "VIEW3D_PT_blendmentation")
