"""Builds the scenes the usage examples expect, for checking that the examples run.

- `example`: the scene of `example.py` (its docstring describes it);
- `quick`: the scene of the Full example on the Quick start page (the paragraph above it
  describes it), which the README example runs in too.

Small and fast to render (Cycles, 2 samples, 320x240), with a `backgrounds` folder next
to the saved `scene.blend`. In Blender or with bpy as a Python module:

    blender -b --factory-startup --python scripts/example_scenes.py -- build example OUT
    python scripts/example_scenes.py build quick OUT

`run` opens a scene and runs a script in it, for bpy as a module (in Blender, pass the
.blend and `--python` instead):

    python scripts/example_scenes.py run OUT/scene.blend script.py

`scripts/check_examples.py` does all of it for several Blender versions.
"""

import math
import os
import sys

import bpy


def material(name, aov=False):
    """A Principled BSDF material, with an AOV Output node writing "Albedo" if aov."""
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    if aov:
        node = mat.node_tree.nodes.new("ShaderNodeOutputAOV")
        node.aov_name = "Albedo"
        node.inputs["Color"].default_value = (1, 0.5, 0, 1)
    return mat


def cube(name, location, size=2.0, scale=(1, 1, 1)):
    bpy.ops.mesh.primitive_cube_add(size=size, location=location, scale=scale)
    obj = bpy.context.object
    obj.name = name
    return obj


def distractor(name, materials):
    """The object in no class: a few materials in its slots, a second UV map and a
    Subdivision Surface modifier "Subdivision"."""
    obj = cube(name, (0, -2, 0), 0.8)
    obj.modifiers.new("Subdivision", "SUBSURF")
    for mat in materials:
        obj.data.materials.append(material(mat))
    obj.data.uv_layers.new(name="Other UV")
    return obj


def scatter(floor):
    """A geometry nodes group "Scatter" in a modifier "Scatter" on the floor: pebbles from
    a collection "Pebbles" on Distribute Points on Faces, with a float input "Density"."""
    pebbles = bpy.data.collections.new("Pebbles")
    for i in range(2):
        bpy.ops.mesh.primitive_ico_sphere_add(radius=0.1 + 0.05 * i, location=(0, 0, 0))
        pebble = bpy.context.object
        pebble.name = f"Pebble.{i}"
        for collection in pebble.users_collection:
            collection.objects.unlink(pebble)
        pebbles.objects.link(pebble)

    group = bpy.data.node_groups.new("Scatter", "GeometryNodeTree")
    group.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    density = group.interface.new_socket("Density", in_out="INPUT", socket_type="NodeSocketFloat")
    density.default_value = 2
    group.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    nodes, link = group.nodes, group.links.new
    group_input, group_output = nodes.new("NodeGroupInput"), nodes.new("NodeGroupOutput")
    distribute = nodes.new("GeometryNodeDistributePointsOnFaces")
    instance = nodes.new("GeometryNodeInstanceOnPoints")
    info = nodes.new("GeometryNodeCollectionInfo")
    info.inputs["Collection"].default_value = pebbles
    info.inputs["Separate Children"].default_value = True
    info.inputs["Reset Children"].default_value = True
    instance.inputs["Pick Instance"].default_value = True
    join = nodes.new("GeometryNodeJoinGeometry")
    link(group_input.outputs["Geometry"], distribute.inputs["Mesh"])
    link(group_input.outputs["Density"], distribute.inputs["Density"])
    link(distribute.outputs["Points"], instance.inputs["Points"])
    link(info.outputs[0], instance.inputs["Instance"])
    link(group_input.outputs["Geometry"], join.inputs[0])
    link(instance.outputs[0], join.inputs[0])
    link(join.outputs[0], group_output.inputs["Geometry"])
    floor.modifiers.new("Scatter", "NODES").node_group = group


def build(kind, out):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 2
    scene.cycles.device = "CPU"
    scene.render.resolution_x, scene.render.resolution_y = 320, 240
    scene.render.film_transparent = True
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    bpy.ops.object.camera_add(location=(0, -10, 2), rotation=(math.radians(80), 0, 0))
    scene.camera = bpy.context.object
    bpy.ops.object.light_add(type="POINT", location=(2, -3, 4))
    bpy.context.object.name = bpy.context.object.data.name = "Light"
    aov = bpy.context.view_layer.aovs.add()
    aov.name, aov.type = "Albedo", "COLOR"
    world = bpy.data.worlds.new("World")
    world.use_nodes = True  # a "Background" node
    scene.world = world

    if kind == "example":
        paint = material("material.001", aov=True)
        for name, x in (("Part.001", -1.5), ("Part.002", 1.5)):
            part = cube(name, (x, 0, 0))
            part.data.materials.append(paint)
            part.shape_key_add(name="Basis")
            part.shape_key_add(name="Key 1").data[0].co.z += 0.5
        distractor("Clutter", ["Red", "Blue"])
    elif kind == "quick":
        paint = material("CarPaint", aov=True)
        for name, x in (("Car.001", -2), ("Car.002", 2)):
            cube(name, (x, 0, 0)).data.materials.append(paint)
        cube("TableTop", (0, 1.5, 0.3), 1, (1, 0.6, 0.1))
        cube("TableLegs", (0, 1.5, -0.4), 1, (0.2, 0.2, 0.6))
        distractor("Plant", ["Green", "Brown"])
    else:
        raise ValueError(f'the scene is "example" or "quick", got {kind!r}')

    bpy.ops.mesh.primitive_plane_add(size=20, location=(0, 0, -1))
    floor = bpy.context.object
    floor.name = "Floor"
    scatter(floor)
    bpy.ops.curve.primitive_bezier_circle_add(radius=10, location=(0, 0, 2))
    bpy.context.object.name = "CameraPath"

    os.makedirs(out, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(out, "scene.blend"))
    image = bpy.data.images.new("background", 64, 48)
    image.pixels = [0.2, 0.4, 0.6, 1.0] * (64 * 48)
    os.makedirs(os.path.join(out, "backgrounds"), exist_ok=True)
    image.filepath_raw = os.path.join(out, "backgrounds", "background.png")
    image.file_format = "PNG"
    image.save()
    print("BUILT", kind, bpy.app.version_string)


def run(blend, script):
    bpy.ops.wm.open_mainfile(filepath=blend)
    with open(script) as file:
        code = file.read()
    exec(compile(code, script, "exec"), {"__name__": "__main__", "__file__": script})
    print("RAN", script, bpy.app.version_string)


if __name__ == "__main__":
    args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    command, *rest = args
    {"build": build, "run": run}[command](*rest)
