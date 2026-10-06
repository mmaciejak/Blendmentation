import random

import pytest

bpy = pytest.importorskip("bpy")

from blendmentation import bpy_paths  # noqa: E402
from blendmentation.augmentations import augmentations as A  # noqa: E402
from blendmentation.state import state  # noqa: E402
from conftest import add_aov, new_material  # noqa: E402


def snapshot(obj, material, light, paths):
    principled = material.node_tree.nodes["Principled BSDF"]
    values = {
        "location": tuple(obj.location),
        "rotation": tuple(obj.rotation_euler),
        "scale": tuple(obj.scale),
        "color": tuple(principled.inputs["Base Color"].default_value),
        "roughness": principled.inputs["Roughness"].default_value,
        "hide_render": obj.hide_render,
        "lens": bpy.context.scene.camera.data.lens,
        "dof": (bpy.context.scene.camera.data.dof.use_dof, bpy.context.scene.camera.data.dof.aperture_fstop,
                bpy.context.scene.camera.data.dof.focus_distance),
    }
    for path, owner in paths:
        value = bpy_paths.get_value(path, owner)
        values[path] = tuple(value) if hasattr(value, "__len__") else value
    return values


def test_restore(scene, cube):
    random.seed(2)
    obj = cube("Cube")
    material = new_material(obj, "Mat")
    obj.shape_key_add(name="Basis")
    obj.shape_key_add(name="Key 1")
    light = bpy.data.objects.new("Light", bpy.data.lights.new("Light", "POINT"))
    scene.collection.objects.link(light)

    object_augs = A.Compose([
        A.Translation(x=1, y=1, z=1), A.Rotation(x=30, z=30), A.Scale(x=20),
        A.Material("Mat", hue=(0, 1), saturation=(0.5, 1), roughness=(0, 1)),
        A.Number('data.shape_keys.key_blocks["Key 1"].value', value_range=(0.3, 0.7)),
        A.Visibility(p=0),
    ])
    light_augs = A.Compose([A.Number("data.energy", value_range=(600, 1400)), A.Vector("data.color", value_range=(0, 0.5))])
    camera_augs = A.Compose([
        A.LookAt(obj, distance=(5, 6)),
        A.FocalLength((20, 30), target=obj, keep_size=True),
        A.DepthOfField(obj, f_stop=(1, 2)),
    ])
    paths = [('data.shape_keys.key_blocks["Key 1"].value', obj), ("data.energy", light), ("data.color", light)]

    initial = state.State([obj, light, scene.camera], fields=object_augs.augmentations + light_augs.augmentations)
    before = snapshot(obj, material, light, paths)
    object_augs([obj])
    light_augs([light])
    camera_augs([scene.camera])
    changed = snapshot(obj, material, light, paths)
    assert all(before[key] != changed[key] for key in before), [k for k in before if before[k] == changed[k]]

    initial.restore()
    after = snapshot(obj, material, light, paths)
    for key in before:
        assert before[key] == pytest.approx(after[key], abs=1e-5), key
    assert tuple(scene.camera.location) == pytest.approx((0, -10, 0))


def test_relative_field_must_resolve(cube):
    obj = cube("Cube")
    with pytest.raises(ValueError, match="does not resolve"):
        state.State([obj], fields=["data.energy"])


def test_restore_aov_node(cube):
    """In blender 4.0 an AOV Output node's name is its AOV name, not its key in the node tree."""
    obj = cube("Cube")
    material = new_material(obj, "Mat")
    add_aov(material, "Albedo", "VALUE", 0.25)
    node = next(node for node in material.node_tree.nodes if node.bl_idname == "ShaderNodeOutputAOV")
    initial = state.State([obj])
    node.inputs["Value"].default_value = 0.75
    initial.restore()
    assert node.inputs["Value"].default_value == pytest.approx(0.25)
