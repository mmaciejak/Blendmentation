"""Shared fixtures. Blender tests run with bpy as a python module (pip install bpy),
every test starts from an empty scene with a camera at (0, -10, 0) looking along +Y.
Without bpy only the tests that don't need blender run."""

import json
import math
import os

import pytest

try:
    import bpy
except ImportError:
    bpy = None


@pytest.fixture(autouse=True)
def scene():
    if bpy is None:
        return None
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.cycles.samples = 1
    scene.cycles.device = "CPU"
    bpy.ops.object.camera_add(location=(0, -10, 0), rotation=(math.radians(90), 0, 0))
    scene.camera = bpy.context.object
    return scene


@pytest.fixture
def cube():
    if bpy is None:
        pytest.skip("needs bpy")

    def make(name, location=(0, 0, 0), size=1.0):
        bpy.ops.mesh.primitive_cube_add(size=size, location=location)
        obj = bpy.context.object
        obj.name = name
        return obj

    return make


@pytest.fixture
def renders():
    """Engines of the renders started during the test."""
    started = []

    def count(*args):
        started.append(bpy.context.scene.render.engine)

    bpy.app.handlers.render_init.append(count)
    yield started
    bpy.app.handlers.render_init.remove(count)


@pytest.fixture
def out(tmp_path):
    return str(tmp_path)


def label(out, index=0):
    with open(os.path.join(out, f"{index:06d}.json")) as file:
        return json.load(file)


def read_image(path, channel=None):
    """Pixels of an image as float array (height, width, channels), or one channel."""
    import OpenImageIO as oiio

    pixels = oiio.ImageBuf(path).get_pixels(oiio.FLOAT)
    return pixels if channel is None else pixels[:, :, channel]


def add_aov(material, name, kind, value):
    """Adds a view layer AOV and an AOV output node writing a constant value."""
    aov = bpy.context.view_layer.aovs.add()
    aov.name = name
    aov.type = kind
    node = material.node_tree.nodes.new("ShaderNodeOutputAOV")
    setattr(node, "aov_name" if hasattr(node, "aov_name") else "name", name)
    node.inputs["Value" if kind == "VALUE" else "Color"].default_value = value
    return aov


def new_material(obj, name):
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    obj.data.materials.append(material)
    return material
