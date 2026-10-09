import random

import pytest

bpy = pytest.importorskip("bpy")

from blendmentation import bpy_paths  # noqa: E402
from blendmentation.augmentations import augmentations as A  # noqa: E402
from blendmentation.state import state  # noqa: E402
from conftest import add_aov, new_material  # noqa: E402


def plain(value):
    return tuple(value) if hasattr(value, "__len__") and not isinstance(value, str) else value


def scatter_modifier(obj):
    """A geometry nodes modifier "GN" on obj with a float input "Density", and its path."""
    group = bpy.data.node_groups.new("GN", "GeometryNodeTree")
    group.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    group.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    socket = group.interface.new_socket("Density", in_out="INPUT", socket_type="NodeSocketFloat")
    group.links.new(group.nodes.new("NodeGroupInput").outputs[0], group.nodes.new("NodeGroupOutput").inputs[0])
    modifier = obj.modifiers.new("GN", "NODES")
    modifier.node_group = group
    bpy.context.view_layer.update()
    return bpy_paths.modifier_input_path(modifier, socket.identifier)


def test_restoring(scene, cube):
    """Every augmentation's changes are set back, also inside a OneOf and a Chain."""
    random.seed(2)
    obj = cube("Cube")
    material = new_material(obj, "Mat")
    new_material(obj, "Other")
    obj.shape_key_add(name="Basis")
    obj.shape_key_add(name="Key 1")
    floor = cube("Floor", location=(0, 0, -3))
    density = scatter_modifier(obj)
    light = bpy.data.objects.new("Light", bpy.data.lights.new("Light", "POINT"))
    scene.collection.objects.link(light)
    camera = scene.camera
    path = bpy.data.objects.new("Path", bpy.data.curves.new("Path", "CURVE"))
    path.data.splines.new("POLY").points.add(1)
    path.data.splines[0].points[1].co = (4, 0, 0, 1)
    scene.collection.objects.link(path)
    group = bpy.data.node_groups.new("Seeds", "GeometryNodeTree")
    distribute = group.nodes.new("GeometryNodeDistributePointsOnFaces")

    principled = material.node_tree.nodes["Principled BSDF"]
    values = {
        "location": lambda: plain(obj.location),
        "rotation": lambda: plain(obj.rotation_euler),
        "scale": lambda: plain(obj.scale),
        "hide_render": lambda: obj.hide_render,
        "base color": lambda: plain(principled.inputs["Base Color"].default_value),
        "roughness": lambda: principled.inputs["Roughness"].default_value,
        "shape key": lambda: obj.data.shape_keys.key_blocks["Key 1"].value,
        "faces": lambda: tuple(polygon.material_index for polygon in obj.data.polygons),
        "active slot": lambda: obj.active_material_index,
        "modifier": lambda: bpy_paths.get_value(density, obj.modifiers["GN"]),
        "energy": lambda: light.data.energy,
        "color": lambda: plain(light.data.color),
        "type": lambda: light.data.type,
        "shadow": lambda: light.data.use_shadow,
        "seed": lambda: distribute.inputs["Seed"].default_value,
        "camera location": lambda: plain(camera.location),
        "camera rotation": lambda: plain(camera.rotation_euler),
        "lens": lambda: camera.data.lens,
        "dof": lambda: (camera.data.dof.use_dof, camera.data.dof.aperture_fstop, camera.data.dof.focus_distance),
    }
    before = {name: value() for name, value in values.items()}

    with state.restoring():
        A.Compose([
            A.Translation(x=1, y=1, z=1), A.Rotation(x=30, z=30), A.Scale(x=20),
            A.SimpleMaterial("Mat", hue=(0, 1), saturation=(0.5, 1), roughness=(0, 1)),
            A.Chain([
                A.Number('data.shape_keys.key_blocks["Key 1"].value', value_range=(0.3, 0.7)),
                A.MaterialSlot(["Other"]),
            ]),
            A.Modifier('modifiers["GN"]', {"Density": A.Input((5, 10))}),
            A.Visibility(p=0),
            A.KeepAbove(floor, margin=1),
        ])([obj])
        A.Compose([
            A.OneOf([A.Number("data.energy", value_range=(600, 1400))]),
            A.Node('bpy.data.materials["Mat"].node_tree.nodes["Principled BSDF"]', {"Roughness": A.Input((0, 1))}),
            A.Vector("data.color", value_range=(0, 0.5)),
            A.Menu("data.type", options=["SPOT"]),
            A.Boolean("data.use_shadow", p=0, otherwise=False),
        ])([light])
        A.Seed(group)()
        A.PlaceOnCurve(path, align=True)(camera)
        A.Compose([
            A.LookAt(obj, distance=(5, 6)),
            A.FocalLength((20, 30), target=obj, keep_size=True),
            A.DepthOfField(obj, f_stop=(1, 2)),
        ])([camera])
        changed = {name: value() for name, value in values.items()}
        assert [name for name in before if before[name] == changed[name]] == []

    after = {name: value() for name, value in values.items()}
    for name in before:
        assert after[name] == pytest.approx(before[name], abs=1e-5), name


def test_restoring_on_error(cube):
    obj = cube("Cube")
    with pytest.raises(RuntimeError):
        with state.restoring():
            A.Translation(x=(1, 1))(obj)
            raise RuntimeError("a step failed")
    assert obj.location.x == 0


def test_restoring_updates_matrix_world(cube):
    """After the block, matrix_world is up to date without a view layer update, so the next
    augmentation (LookAt targets, KeepAbove) sees the object where it is."""
    obj = cube("Cube")
    bpy.context.view_layer.update()
    with state.restoring():
        A.Translation(x=(2, 2))(obj)
        bpy.context.view_layer.update()
        assert obj.matrix_world.translation.x == pytest.approx(2)
    assert obj.matrix_world.translation.x == pytest.approx(0)


def test_restoring_first_value(cube):
    """A value changed twice gets its value from before the first change."""
    obj = cube("Cube")
    with state.restoring():
        A.Number("location[0]", value_range=(1, 1))(obj)
        A.Number("location[0]", value_range=(2, 2))(obj)
        A.Translation(x=(1, 1))(obj)
        assert obj.location.x == pytest.approx(3)
    assert obj.location.x == 0


def test_restoring_nested(cube):
    obj = cube("Cube")
    with state.restoring():
        A.Number("location[0]", value_range=(1, 1))(obj)
        with state.restoring():
            A.Number("location[0]", value_range=(2, 2))(obj)
            A.Number("location[1]", value_range=(2, 2))(obj)
        assert tuple(obj.location) == pytest.approx((1, 0, 0))
    assert tuple(obj.location) == (0, 0, 0)


def test_outside_restoring(cube):
    """Outside a block the changes stay, and nothing is recorded for a later block."""
    obj = cube("Cube")
    A.Number("location[0]", value_range=(1, 1))(obj)
    with state.restoring():
        A.Number("location[1]", value_range=(1, 1))(obj)
    assert tuple(obj.location) == pytest.approx((1, 0, 0))


def test_set(scene):
    light = bpy.data.objects.new("Light", bpy.data.lights.new("Light", "POINT"))
    light.data.energy = 100
    with state.restoring():
        state.set(light.data, "energy", 2000)
        assert light.data.energy == 2000
    assert light.data.energy == 100
    state.set(light.data, "energy", 5)
    assert light.data.energy == 5


def test_restoring_aov_node(cube):
    """In blender 4.0 an AOV Output node's name is its AOV name, not its key in the node tree."""
    obj = cube("Cube")
    material = new_material(obj, "Mat")
    add_aov(material, "Albedo", "VALUE", 0.25)
    key, node = next((key, node) for key, node in material.node_tree.nodes.items()
                     if node.bl_idname == "ShaderNodeOutputAOV")
    with state.restoring():
        A.Number(f'active_material.node_tree.nodes["{key}"].inputs["Value"].default_value', value_range=(0.75, 0.75))(obj)
        assert node.inputs["Value"].default_value == pytest.approx(0.75)
    assert node.inputs["Value"].default_value == pytest.approx(0.25)


def test_restoring_world(cube):
    """A world is augmented in place of an object, and set back like one."""
    world = bpy.data.worlds.new("World")
    world.use_nodes = True
    nodes = world.node_tree.nodes
    nodes.new("ShaderNodeMapping")
    background = next(node for node in nodes if node.type == "BACKGROUND")
    strength = background.inputs["Strength"].default_value
    rotation = 'node_tree.nodes["Mapping"].inputs[2].default_value[2]'
    with state.restoring():
        A.Compose([
            A.Number(f'node_tree.nodes["{background.name}"].inputs[1].default_value', value_range=(5, 10)),
            A.Number(rotation, value_range=(1, 6)),
        ])([world])
        assert bpy_paths.get_value(rotation, world) != 0
    assert background.inputs["Strength"].default_value == pytest.approx(strength)
    assert bpy_paths.get_value(rotation, world) == pytest.approx(0)
