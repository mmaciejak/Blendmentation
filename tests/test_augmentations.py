import colorsys
import math
import random

import pytest

bpy = pytest.importorskip("bpy")

from bpy_extras.object_utils import world_to_camera_view  # noqa: E402
from mathutils import Vector  # noqa: E402

from blendmentation import bpy_paths  # noqa: E402
from blendmentation.augmentations import augmentations as A  # noqa: E402
from conftest import new_material  # noqa: E402


@pytest.fixture(autouse=True)
def seed():
    random.seed(1)


def geonode_cube(cube):
    """Cube with a geometry nodes modifier, inputs Height (float, moves the geometry up),
    Count (int), Flag (bool), Offset (vector). Returns the cube and a path builder."""
    obj = cube("Cube")
    group = bpy.data.node_groups.new("GN", "GeometryNodeTree")
    group.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    group.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    sockets = {
        name: group.interface.new_socket(name, in_out="INPUT", socket_type=kind)
        for name, kind in (("Height", "NodeSocketFloat"), ("Count", "NodeSocketInt"),
                           ("Flag", "NodeSocketBool"), ("Offset", "NodeSocketVector"))
    }
    group_input = group.nodes.new("NodeGroupInput")
    group_output = group.nodes.new("NodeGroupOutput")
    transform = group.nodes.new("GeometryNodeTransform")
    combine = group.nodes.new("ShaderNodeCombineXYZ")
    group.links.new(group_input.outputs["Geometry"], transform.inputs["Geometry"])
    group.links.new(group_input.outputs["Height"], combine.inputs["Z"])
    group.links.new(combine.outputs[0], transform.inputs["Translation"])
    group.links.new(transform.outputs["Geometry"], group_output.inputs["Geometry"])
    modifier = obj.modifiers.new("GN", "NODES")
    modifier.node_group = group

    def path(name):
        identifier = sockets[name].identifier
        if hasattr(modifier, "properties"):  # blender 5
            return getattr(modifier.properties.inputs, identifier).path_from_id("value")
        return f'modifiers["GN"]["{identifier}"]'

    return obj, path


def test_transforms(cube):
    obj = cube("Cube")
    obj.rotation_mode = "QUATERNION"
    A.Compose([A.Translation(x=0.5, z=(1, 2)), A.Rotation(z=30), A.Scale(x=(-10, 20))])([obj])
    assert -0.5 <= obj.location.x <= 0.5 and obj.location.y == 0 and 1 <= obj.location.z <= 2
    assert 0.9 <= obj.scale.x <= 1.2 and obj.scale.y == 1
    angle = math.degrees(obj.rotation_quaternion.angle)
    assert 0 <= angle <= 30


def test_probability(cube):
    obj = cube("Cube")
    never = A.Translation(x=1, p=0)
    never(obj)
    assert never.applied is False and never.actual_x is None and obj.location.x == 0

    always = A.Number("location[1]", value_range=(1, 2))
    always(obj)
    assert always.applied is True and 1 <= obj.location.y <= 2

    obj.hide_render = True
    off = A.Boolean("hide_render", p=0)
    off(obj)
    assert off.applied is False and off.actual is False and obj.hide_render is False

    skipped = A.Compose([A.Number("location[2]", value_range=(1, 2))], p=0)
    skipped([obj])
    assert skipped.applied is False and obj.location.z == 0

    skipped([obj], p=1)
    assert skipped.applied is True and 1 <= obj.location.z <= 2
    obj.location.z = 0
    always_compose = A.Compose([A.Number("location[2]", value_range=(1, 2))])
    always_compose([obj], p=0)
    assert always_compose.applied is False and obj.location.z == 0

    half = A.Rotation(z=10, p=0.5)
    applied = []
    for _ in range(200):
        half(obj)
        applied.append(half.applied)
        assert (half.actual_z is None) != half.applied
    assert 60 < sum(applied) < 140

    for p in (-0.1, 1.5):
        with pytest.raises(ValueError):
            A.Boolean("hide_render", p=p)
        with pytest.raises(ValueError):
            A.Compose([], p=p)
        with pytest.raises(ValueError):
            A.Compose([])([obj], p=p)


def test_visibility(cube):
    obj = cube("Cube")
    hide = A.Visibility(p=0)
    hide(obj)
    assert hide.applied is False and hide.actual is False and obj.hide_render and not obj.hide_viewport
    show = A.Visibility(p=1)
    show(obj)
    assert show.applied is True and show.actual is True and not obj.hide_render

    half = A.Visibility()
    shown = []
    for _ in range(200):
        half(obj)
        assert obj.hide_render is not half.actual
        shown.append(half.actual)
    assert 60 < sum(shown) < 140


def test_keep_above(cube):
    floor = cube("Floor", location=(0, 0, -1), size=4)  # top at z = 1
    obj = cube("Cube", location=(0, 0, 0))  # bottom at -0.5
    keep = A.KeepAbove(floor, margin=0.1)
    keep(obj)
    assert keep.actual == pytest.approx(1.6) and obj.location.z == pytest.approx(1.6)

    # already above with the margin: not moved, never moved down
    obj.location.z = 3
    keep(obj)
    assert keep.actual == 0 and obj.location.z == 3

    # not over the surface: not moved
    obj.location = (10, 0, -5)
    keep(obj)
    assert keep.actual == 0 and obj.location.z == -5

    # sees the transforms of the augmentations before it, rotation included
    obj.location = (0, 0, 0)
    A.Compose([A.Translation(z=(-2, -1)), A.Rotation(x=(45, 45)), keep])([obj])
    bpy.context.view_layer.update()
    lowest = min((obj.matrix_world @ v.co).z for v in obj.data.vertices)
    assert lowest == pytest.approx(1.1, abs=1e-5)

    # a peak of the surface between the object's vertices
    floor.location = (0, 0, 0)
    floor.scale = (0.1, 0.1, 0.1)  # a small block, top at 0.2, under the cube's bottom face
    obj.location, obj.rotation_euler = (0, 0, 0), (0, 0, 0)
    obj.scale = (2, 2, 1)  # bottom vertices at x, y = ±1, outside the block
    keep(obj)
    assert obj.location.z == pytest.approx(0.8)

    # parented: moves in world space
    parent = cube("Parent", location=(0, 0, 0))
    parent.rotation_euler = (0, math.radians(90), 0)
    obj.parent = parent
    obj.location, obj.scale = (0, 0, 0), (1, 1, 1)
    keep(obj)
    bpy.context.view_layer.update()
    assert obj.matrix_world.translation.z == pytest.approx(0.8)

    with pytest.raises(ValueError):
        A.KeepAbove(obj)(obj)
    bpy.ops.object.empty_add()
    with pytest.raises(TypeError):
        A.KeepAbove(bpy.context.object)(obj)


def test_material(cube):
    obj = cube("Cube")
    material = new_material(obj, "Mat")
    augmentation = A.Material("Mat", hue=(0, 1), saturation=(0.4, 0.9), value=(0.2, 0.8), roughness=(0.1, 0.2), metallic=(0.5, 0.6))
    augmentation(obj)
    principled = material.node_tree.nodes["Principled BSDF"]
    h, s, v = colorsys.rgb_to_hsv(*principled.inputs["Base Color"].default_value[:3])
    actual = augmentation.actual
    assert abs(h - actual["hue"]) < 1e-4 and abs(s - actual["saturation"]) < 1e-4 and abs(v - actual["value"]) < 1e-4
    assert 0.1 <= principled.inputs["Roughness"].default_value <= 0.2
    assert 0.5 <= principled.inputs["Metallic"].default_value <= 0.6
    with pytest.raises(KeyError):
        A.Material("Nope", roughness=(0, 1))(obj)


def test_number_vector_boolean(cube):
    obj, path = geonode_cube(cube)
    obj.shape_key_add(name="Basis")
    obj.shape_key_add(name="Key 1")
    A.Number('data.shape_keys.key_blocks["Key 1"].value', value_range=(0.3, 0.7))(obj)
    assert 0.3 <= obj.data.shape_keys.key_blocks["Key 1"].value <= 0.7

    A.Number(path("Height"), value_range=(3, 3))(obj)
    depsgraph = bpy.context.evaluated_depsgraph_get()
    top = max(v.co.z for v in obj.evaluated_get(depsgraph).to_mesh().vertices)
    assert abs(top - 3.5) < 1e-5, "geometry nodes input did not reach the evaluated mesh"  # 0.5 + 3

    bpy_paths.set_value(path("Count"), 10, obj)
    count = A.Number(path("Count"), value_range=(15, 15))
    count(obj)
    assert count.actual == 15 and isinstance(bpy_paths.get_value(path("Count"), obj), int)

    A.Vector("location", value_range=((0, 0, None), (1, 1, None)))(obj)
    assert 0 <= obj.location.x <= 1 and obj.location.z == 0
    A.Vector(path("Offset"), value_range=(2, 3))(obj)
    assert all(2 <= c <= 3 for c in bpy_paths.get_value(path("Offset"), obj))

    A.Boolean(path("Flag"), p=1.0)(obj)
    A.Boolean("hide_render", p=1.0)(obj)
    assert bpy_paths.get_value(path("Flag"), obj) and obj.hide_render


def test_menu(cube):
    obj = cube("Cube")
    material = new_material(obj, "Mat")
    menu = A.Menu('bpy.data.materials["Mat"].node_tree.nodes["Principled BSDF"].distribution')
    seen = set()
    for _ in range(30):
        menu()
        seen.add(menu.actual)
    assert seen == {"GGX", "MULTI_GGX"}
    light = bpy.data.objects.new("L", bpy.data.lights.new("L", "POINT"))
    A.Menu("data.type", options=["SPOT", "AREA"], weights=[0, 1])(light)
    assert light.data.type == "AREA"
    assert material.node_tree.nodes["Principled BSDF"].distribution in seen


@pytest.mark.parametrize("make, error", [
    (lambda obj: A.Number("location", value_range=(0, 1))(obj), TypeError),
    (lambda obj: A.Vector("location[0]", value_range=(0, 1))(obj), TypeError),
    (lambda obj: A.Vector("location", value_range=((0, 0), (1, 1)))(obj), ValueError),
    (lambda obj: A.Boolean("location[0]")(obj), TypeError),
    (lambda obj: A.Menu("location[0]")(obj), ValueError),
    (lambda obj: A.Menu("data.type", options=["A"], weights=[1, 2]), ValueError),
    (lambda obj: A.Number("data.energy", value_range=(0, 1))(), ValueError),
])
def test_data_path_errors(cube, make, error):
    obj = cube("Cube")
    with pytest.raises(error):
        make(obj)


def roll_of(camera):
    """Signed angle around the camera local Z from the upright x axis."""
    matrix = camera.matrix_world.to_3x3().normalized()
    forward = (matrix @ Vector((0, 0, -1))).normalized()
    x_camera = (matrix @ Vector((1, 0, 0))).normalized()
    x_upright = forward.cross(Vector((0, 0, 1))).normalized()
    return math.degrees(math.atan2(x_upright.cross(x_camera).dot(-forward), x_upright.dot(x_camera)))


def assert_centered(scene, point):
    bpy.context.view_layer.update()
    view = world_to_camera_view(scene, scene.camera, point)
    assert abs(view.x - 0.5) < 1e-5 and abs(view.y - 0.5) < 1e-5 and view.z > 0


def test_look_at(scene, cube):
    camera = scene.camera
    target = cube("Target", (1, 2, 0.5))
    target.data.vertices[0].co.x += 3  # origin is not the bbox center
    bpy.context.view_layer.update()
    center = target.matrix_world @ (sum((Vector(c) for c in target.bound_box), Vector()) / 8)

    look_at = A.LookAt(target, distance=(4, 8), elevation=(10, 40), azimuth=(0, 360), focal_length=(20, 85))
    for _ in range(10):
        look_at(camera)
        actual = look_at.actual
        assert_centered(scene, center)
        offset = camera.matrix_world.translation - center
        assert 4 <= actual["distance"] <= 8 and abs(offset.length - actual["distance"]) < 1e-4
        assert 10 <= actual["elevation"] <= 40
        assert camera.data.lens == actual["focal_length"] and 20 <= camera.data.lens <= 85
        assert abs(roll_of(camera)) < 1e-4

    A.LookAt(target, roll=25)(camera)
    assert abs(roll_of(camera) - 25) < 1e-3

    camera.location = center + Vector((0, -6, 3))
    bpy.context.view_layer.update()
    keep = A.LookAt(target, azimuth=(100, 110))
    keep(camera)
    assert abs(keep.actual["distance"] - math.sqrt(45)) < 1e-4

    parent = bpy.data.objects.new("rig", None)
    scene.collection.objects.link(parent)
    parent.location, parent.rotation_euler, parent.scale = (3, -2, 1), (0.3, 0.2, 1.0), (2, 2, 2)
    camera.parent = parent
    bpy.context.view_layer.update()
    A.LookAt(target, distance=6)(camera)
    assert_centered(scene, center)
    camera.parent = None

    A.LookAt((0.5, -1.0, 2.0), distance=7)(camera)
    assert_centered(scene, Vector((0.5, -1.0, 2.0)))

    light = bpy.data.objects.new("spot", bpy.data.lights.new("spot", "SPOT"))
    scene.collection.objects.link(light)
    A.LookAt(target, distance=5, elevation=45)(light)
    direction = (light.matrix_world.to_3x3() @ Vector((0, 0, -1))).normalized()
    assert (direction - (center - light.matrix_world.translation).normalized()).length < 1e-5
    with pytest.raises(TypeError):
        A.LookAt(target, focal_length=50)(light)
    with pytest.raises(ValueError):
        A.LookAt(target, distance=0)(camera)


def projected_width(scene, obj):
    bpy.context.view_layer.update()
    xs = [world_to_camera_view(scene, scene.camera, obj.matrix_world @ Vector(c)).x for c in obj.bound_box]
    return (max(xs) - min(xs)) * scene.render.resolution_x


def test_focal_length_keep_size(scene):
    bpy.ops.mesh.primitive_plane_add(size=2, location=(0, 0, 0), rotation=(math.radians(90), 0, 0))  # faces the camera
    plane = bpy.context.object
    camera = scene.camera
    width = projected_width(scene, plane)

    focal = A.FocalLength((20, 100), target=plane, keep_size=True)
    for _ in range(5):
        focal(camera)
        assert 20 <= camera.data.lens <= 100 and focal.actual["focal_length"] == camera.data.lens
        assert projected_width(scene, plane) == pytest.approx(width, abs=1e-3)
        assert focal.actual["distance"] == pytest.approx(10 * camera.data.lens / 50, rel=1e-5)
        assert_centered(scene, Vector((0, 0, 0)))

    location = camera.location.copy()
    A.FocalLength(35)(camera)
    assert camera.data.lens == 35 and camera.location == location  # without keep_size the camera stays


def test_focal_length_errors(scene):
    light = bpy.data.objects.new("L", bpy.data.lights.new("L", "POINT"))
    with pytest.raises(ValueError, match="needs a target"):
        A.FocalLength(50, keep_size=True)
    with pytest.raises(TypeError):
        A.FocalLength(50)(light)
    scene.camera.data.type = "ORTHO"
    with pytest.raises(TypeError, match="perspective"):
        A.FocalLength(50)(scene.camera)


def test_depth_of_field(scene, cube):
    target = cube("Target")
    camera = scene.camera
    A.Compose([A.LookAt(target, distance=(6, 8), elevation=(0, 30)), A.DepthOfField(target, f_stop=(1.4, 4))])([camera])
    dof = camera.data.dof
    distance = (camera.matrix_world.translation - Vector((0, 0, 0))).length
    assert dof.use_dof and 1.4 <= dof.aperture_fstop <= 4
    assert dof.focus_distance == pytest.approx(distance, rel=1e-5) and dof.focus_object is None

    dof.use_dof = False
    skipped = A.DepthOfField(target, f_stop=2.8, p=0.0)
    skipped(camera)
    assert not dof.use_dof and dof.aperture_fstop != 2.8 and skipped.actual is None
    with pytest.raises(TypeError):
        A.DepthOfField(target)(bpy.data.objects.new("empty", None))


def test_results(cube):
    objs = [cube(f"Cube.{i}") for i in range(3)]
    visibility = A.Visibility()
    translation = A.Translation(x=1, p=0.5)
    compose = A.Compose([visibility, translation, lambda obj: None])  # any callable, no results
    compose(objs)
    assert list(visibility.results) == [obj.name for obj in objs]
    for obj in objs:
        assert visibility.results[obj.name] is (not obj.hide_render)
        moved = translation.results[obj.name]
        if moved is None:
            assert obj.location.x == 0
        else:
            assert moved == pytest.approx((obj.location.x, 0, 0))
    assert visibility.actual is visibility.results[objs[-1].name]
    assert len(set(visibility.results.values())) == 2  # drawn per object (seeded)

    # each Compose call starts fresh, also when skipped
    compose(objs[:1])
    assert list(visibility.results) == [objs[0].name]
    compose(objs, p=0)
    assert visibility.results == {} and translation.results == {}

    # direct calls add up; absolute paths without an object are stored under None
    visibility(objs[0])
    visibility(objs[1])
    assert list(visibility.results) == [objs[0].name, objs[1].name]
    lift = A.Number('bpy.data.objects["Cube.0"].location[2]', value_range=(1, 2))
    lift()
    assert list(lift.results) == [None] and lift.results[None] == lift.actual


def test_otherwise_data_paths(cube):
    """A skipped data path augmentation sets otherwise, or keeps the value with None."""
    obj, path = geonode_cube(cube)
    light = bpy.data.objects.new("L", bpy.data.lights.new("L", "POINT"))
    light.data.energy = 100

    energy = A.Number("data.energy", value_range=(500, 600), p=0, otherwise=10)
    energy(light)
    assert energy.applied is False and energy.actual == 10 and light.data.energy == 10
    kept = A.Number("data.energy", value_range=(500, 600), p=0)
    kept(light)
    assert kept.actual is None and light.data.energy == 10
    count = A.Number(path("Count"), value_range=(1, 5), p=0, otherwise=7.4)
    count(obj)
    assert count.actual == 7 and bpy_paths.get_value(path("Count"), obj) == 7

    color = A.Vector("data.color", value_range=(0, 1), p=0, otherwise=(1, None, 0.5))
    light.data.color = (0.2, 0.3, 0.4)
    color(light)
    assert tuple(light.data.color) == pytest.approx((1, 0.3, 0.5))
    A.Vector(path("Offset"), value_range=(2, 3), p=0, otherwise=0)(obj)
    assert tuple(bpy_paths.get_value(path("Offset"), obj)) == (0, 0, 0)

    light.data.use_shadow = True
    A.Boolean("data.use_shadow", p=0)(light)       # default: False
    assert not light.data.use_shadow
    A.Boolean("data.use_shadow", p=0, otherwise=None)(light)
    assert not light.data.use_shadow
    A.Boolean("data.use_shadow", p=0, otherwise=True)(light)
    assert light.data.use_shadow

    menu = A.Menu("data.type", options=["SPOT", "AREA"], p=0, otherwise="SUN")
    menu(light)
    assert menu.actual == "SUN" and light.data.type == "SUN"
    A.Menu("data.type", options=["SPOT", "AREA"], p=0)(light)
    assert light.data.type == "SUN"


def test_otherwise_objects_and_cameras(scene, cube):
    obj = cube("Cube")
    keep = A.Visibility(p=0, otherwise=None)
    keep(obj)
    assert keep.actual is None and not obj.hide_render
    A.Visibility(p=0)(obj)                          # default: hidden
    assert obj.hide_render

    camera = scene.camera
    camera.data.lens = 35
    lens = A.FocalLength((24, 85), p=0, otherwise=50)
    lens(camera)
    assert camera.data.lens == 50 and lens.actual == {"focal_length": 50}

    dof = camera.data.dof
    dof.use_dof = True
    off = A.DepthOfField(obj, f_stop=2, p=0, otherwise=False)
    off(camera)
    assert off.actual is False and not dof.use_dof
    on = A.DepthOfField(obj, f_stop=2, p=1, otherwise=False)
    on(camera)
    assert dof.use_dof and on.actual["f_stop"] == pytest.approx(2)


@pytest.mark.parametrize("make", [
    lambda: A.Number("data.energy", value_range=(0, 1), otherwise="high"),
    lambda: A.Number("data.energy", value_range=(0, 1), otherwise=True),
    lambda: A.Vector("data.color", value_range=(0, 1), otherwise=(1, "a", 0)),
    lambda: A.Boolean("data.use_shadow", otherwise=1),
    lambda: A.Visibility(otherwise="hidden"),
    lambda: A.FocalLength(50, otherwise=(24, 85)),
    lambda: A.FocalLength(50, target=(0, 0, 0), keep_size=True, otherwise=50),
    lambda: A.DepthOfField(otherwise=True),
])
def test_otherwise_errors(make):
    with pytest.raises(ValueError):
        make()


def test_otherwise_not_set_when_compose_skipped(cube):
    obj = cube("Cube")
    A.Compose([A.Visibility(p=0)], p=0)([obj])
    assert not obj.hide_render
