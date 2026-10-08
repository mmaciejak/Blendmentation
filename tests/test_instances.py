"""Labels of instances made by geometry nodes (generating.Instances)."""

import os

import numpy as np
import pytest

bpy = pytest.importorskip("bpy")

from blendmentation.generating import bpy_generating, generating as G  # noqa: E402
from conftest import label, read_image  # noqa: E402

# x, z of the instances, seen by the camera at (0, -10, 0) looking along +Y
POINTS = [(-2, -1.5), (2, -1.5), (-2, 1.5), (2, 1.5)]


def mask(out, file_name):
    return read_image(os.path.join(out, file_name), 0) > 0.5


def scatter(name, source, points=POINTS, separate_children=True, ids=False):
    """A mesh of loose points with geometry nodes instancing source (an object, as an instance,
    or a collection) on them. With a collection and separate_children, every point picks one
    of its objects in turn, otherwise every point gets the whole collection. With ids, the
    points get an id attribute, like Distribute Points on Faces gives them."""
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([(x, 0, z) for x, z in points], [], [])
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    tree = bpy.data.node_groups.new(name, "GeometryNodeTree")
    tree.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    nodes, link = tree.nodes, tree.links.new
    group_input, group_output = nodes.new("NodeGroupInput"), nodes.new("NodeGroupOutput")
    on_points = nodes.new("GeometryNodeInstanceOnPoints")
    if isinstance(source, bpy.types.Collection):
        info = nodes.new("GeometryNodeCollectionInfo")
        info.inputs["Collection"].default_value = source
        info.inputs["Separate Children"].default_value = separate_children
        info.inputs["Reset Children"].default_value = True
        on_points.inputs["Pick Instance"].default_value = separate_children
    else:
        info = nodes.new("GeometryNodeObjectInfo")
        info.inputs["Object"].default_value = source
        info.inputs["As Instance"].default_value = True
    points_output = group_input.outputs[0]
    if ids:
        store = nodes.new("GeometryNodeStoreNamedAttribute")
        store.data_type = "INT"
        store.inputs["Name"].default_value = "id"
        random_ids = nodes.new("FunctionNodeRandomValue")
        random_ids.data_type = "INT"
        enabled(random_ids.inputs, "Min").default_value = -(2**30)
        enabled(random_ids.inputs, "Max").default_value = 2**30
        link(points_output, store.inputs["Geometry"])
        link(enabled(random_ids.outputs, "Value"), enabled(store.inputs, "Value"))
        points_output = store.outputs["Geometry"]
    link(points_output, on_points.inputs["Points"])
    link(info.outputs["Instances" if isinstance(source, bpy.types.Collection) else "Geometry"], on_points.inputs["Instance"])
    link(on_points.outputs[0], group_output.inputs[0])
    obj.modifiers.new("Scatter", "NODES").node_group = tree
    return obj


def enabled(sockets, name):
    """The socket by name, blender 4.x nodes have one per data type."""
    return next(socket for socket in sockets if socket.name == name and socket.enabled)


def hidden_collection(name, objects):
    """A collection of the objects, excluded from the view layer, as instance sources often are."""
    collection = bpy.data.collections.new(name)
    bpy.context.scene.collection.children.link(collection)
    for obj in objects:
        for owner in list(obj.users_collection):
            owner.objects.unlink(obj)
        collection.objects.link(obj)
        obj.location = (0, 0, 0)
    bpy.context.view_layer.layer_collection.children[name].exclude = True
    return collection


def reference_bbox(scene, cube, location, size, width, height):
    """Bbox of a cube of the size at the location, made and deleted for the comparison."""
    obj = cube("reference", location, size)
    scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage = width, height, 100
    bpy.context.view_layer.update()
    box = bpy_generating.bbox(scene, scene.camera, [obj], bpy.context.evaluated_depsgraph_get(), width, height)
    bpy.data.objects.remove(obj)
    return box


def datablock_counts():
    return {name: len(getattr(bpy.data, name)) for name in ("objects", "meshes", "materials", "node_groups")}


def test_bboxes_poses_and_keypoints(scene, cube, out):
    car = cube("car", size=0.5)
    ball = cube("ball", size=0.3)
    sources = hidden_collection("sources", [car, ball])
    parent = scatter("Scatter", sources)  # ball, car, ball, car (alphabetical, in turn)
    classes = {"car": [G.Instances(parent, of=car)], "ball": [G.Instances(parent, of=[ball])]}
    # on the line from the camera through the car instance at point 1, behind it
    behind = (POINTS[1][0] * 1.3, 3, POINTS[1][1] * 1.3)
    steps = [G.BBox(classes, max_truncation=0), G.Pose(classes), G.Keypoints({"behind": behind, "free": (0, 3, 0)})]
    assert G.Compose(steps, out, (320, 240))()
    data = label(out)

    assert [(b["class"], b["objects"]) for b in data["bboxes"]] == [
        ("car", ["Scatter/1/car"]), ("car", ["Scatter/3/car"]), ("ball", ["Scatter/0/ball"]), ("ball", ["Scatter/2/ball"]),
    ]
    for entry, point, size in zip(data["bboxes"], (POINTS[1], POINTS[3], POINTS[0], POINTS[2]), (0.5, 0.5, 0.3, 0.3)):
        expected = reference_bbox(scene, cube, (point[0], 0, point[1]), size, 320, 240)
        assert entry["bbox"] == pytest.approx(expected)
    # OpenCV camera axes: x right, y down, z forward from the camera at (0, -10, 0)
    assert data["poses"][0]["t"] == pytest.approx([POINTS[1][0], -POINTS[1][1], 10])
    assert data["poses"][0]["scale"] == pytest.approx([1, 1, 1])
    # the car instance hides the point although its source is not rendered
    found = {k["name"]: k["visible"] for k in data["keypoints"]}
    assert found == {"behind": False, "free": True}


def test_collection_instance_is_one_instance(scene, cube, out):
    body = cube("body", size=0.5)
    wheel = cube("wheel", size=0.2)
    sources = hidden_collection("car", [body, wheel])
    wheel.location = (0.4, 0, 0)
    parent = scatter("Scatter", sources, separate_children=False)
    classes = {"car": [G.Instances(parent, of=sources)]}
    assert G.Compose([G.BBox(classes), G.Segmentation(classes)], out, (320, 240))()
    data = label(out)
    # the path inside the instance tells its objects apart
    assert [b["objects"] for b in data["bboxes"]] == [[f"Scatter/{i}.0.0/body", f"Scatter/{i}.0.2/wheel"] for i in range(4)]
    first = data["bboxes"][0]
    # the box and the mask cover the body and the wheel
    left = reference_bbox(scene, cube, (POINTS[0][0], 0, POINTS[0][1]), 0.5, 320, 240)
    assert first["bbox"][0] == pytest.approx(left[0]) and first["bbox"][2] > left[2]
    pixels = mask(out, "000000_mask_0.png")
    columns = np.nonzero(pixels.any(axis=0))[0]
    assert columns.min() == pytest.approx(left[0], abs=1.5) and columns.max() > left[2]


def test_masks_occlusion_and_full_masks(scene, cube, out, renders):
    car = cube("car", size=0.5)
    hidden_collection("sources", [car])
    # ids on the points: persistent ids hold them instead of the instance index
    parent = scatter("Scatter", car, ids=True)
    house = cube("house", (0, 0, 0), 1)
    # in no class, in front of the left half of instance 0: at 0.7 of the way from the camera
    wall = cube("wall", (POINTS[0][0] * 0.7 - 0.1, -3, POINTS[0][1] * 0.7), 0.2)
    classes = {"car": [G.Instances(parent, of=car)], "house": [house]}
    counts = datablock_counts()
    engine = scene.render.engine
    steps = [G.BBox(classes, max_occlusion=0.9), G.Segmentation(classes, per="both", full_masks=True)]
    assert G.Compose(steps, out, (320, 240))()
    data = label(out)

    instance_masks = [m for m in data["masks"] if m["per"] == "instance"]
    assert [m["objects"] for m in instance_masks] == [[f"Scatter/{i}/car"] for i in range(4)] + [["house"]]
    boxes = [b["bbox"] for b in data["bboxes"]]
    visible = [mask(out, m["mask"]) for m in instance_masks]
    full = [mask(out, m["mask"]) for m in data["full_masks"]]
    for pixels, whole, box in zip(visible, full, boxes):
        rows, columns = np.nonzero(whole)
        # the full mask fills its box
        assert columns.min() == pytest.approx(box[0], abs=1.5) and columns.max() + 1 == pytest.approx(box[2], abs=1.5)
        assert rows.min() == pytest.approx(box[1], abs=1.5) and rows.max() + 1 == pytest.approx(box[3], abs=1.5)
        assert (pixels <= whole).all()
    # the wall hides part of instance 0 only
    assert visible[0].sum() < 0.8 * full[0].sum()
    assert all((v == f).all() for v, f in zip(visible[1:], full[1:]))
    car_mask = mask(out, "000000_mask_car.png")
    assert (car_mask == np.any(visible[:4], axis=0)).all()

    # a stricter limit skips the datapoint, the occlusion is measured on the instance
    assert G.Compose([G.BBox(classes, max_occlusion=0.2)], out, (320, 240))() is False

    # cycles id renders only, everything restored
    assert set(renders) == {"CYCLES"}
    assert datablock_counts() == counts and scene.render.engine == engine
    assert [m.name for m in parent.modifiers] == ["Scatter"] and "blendmentation_id" not in house
    assert bpy.context.view_layer.material_override is None and not wall.hide_render


def test_cycles_beauty_and_unlabeled_instances(scene, cube, out):
    scene.render.engine = "CYCLES"
    car = cube("car", size=0.5)
    ball = cube("ball", size=0.5)
    sources = hidden_collection("sources", [car, ball])
    parent = scatter("Scatter", sources)
    classes = {"car": [G.Instances(parent, of=car)]}
    assert G.Compose([G.Render(), G.Segmentation(classes, full_masks=True)], out, (320, 240))()
    data = label(out)
    # ball instances are not labeled and not in the masks
    assert [m["objects"] for m in data["masks"]] == [["Scatter/1/car"], ["Scatter/3/car"]]
    for entry in data["masks"]:
        pixels = mask(out, entry["mask"])
        assert pixels.any() and (pixels[:, :160].any() != pixels[:, 160:].any())
    assert (mask(out, data["masks"][0]["mask"]) == mask(out, data["full_masks"][0]["mask"])).all()


def test_hidden_parent(scene, cube, out):
    car = cube("car", size=0.5)
    parent = scatter("Scatter", car)
    classes = {"car": [car, G.Instances(parent, of=car)]}
    parent.hide_render = True
    assert G.Compose([G.BBox(classes), G.Segmentation(classes)], out, (320, 240))()
    data = label(out)
    assert [b["objects"] for b in data["bboxes"]] == [["car"]]
    assert [m["objects"] for m in data["masks"]] == [["car"]]
    assert mask(out, "000000_mask_0.png").any()


def test_checks(scene, cube, out):
    car = cube("car", size=0.5)
    parent = scatter("Scatter", car)
    with pytest.raises(TypeError, match="sublist"):
        G.Compose([G.BBox({"car": [[G.Instances(parent)]]})], out, (32, 24))()
    with pytest.raises(TypeError, match="of="):
        G.Compose([G.BBox({"car": [G.Instances(parent, of="car")]})], out, (32, 24))()
    empty = bpy.data.objects.new("Empty", None)
    scene.collection.objects.link(empty)
    empty.instance_type = "COLLECTION"
    empty.instance_collection = hidden_collection("sources", [car])
    # boxes work, masks need geometry nodes
    assert G.Compose([G.BBox({"car": [G.Instances(empty)]})], out, (32, 24))()
    assert label(out)["bboxes"][0]["objects"] == ["Empty/0/car"]
    with pytest.raises(TypeError, match="geometry nodes"):
        G.Compose([G.Segmentation({"car": [G.Instances(empty)]})], out, (32, 24))()
    with pytest.raises(TypeError, match="geometry nodes"):
        G.Compose([G.BBox({"car": [G.Instances(empty)]}, max_occlusion=0.5)], out, (32, 24))()


def test_visible_source_occludes_only_visible_masks(scene, cube, out):
    # the source itself is in the scene, in front of the left half of instance 1, and in no class
    car = cube("car", (POINTS[1][0] * 0.7 - 0.1, -3, POINTS[1][1] * 0.7), 0.2)
    parent = scatter("Scatter", car)
    for obj in parent.modifiers[0].node_group.nodes:
        if obj.bl_idname == "GeometryNodeObjectInfo":
            obj.transform_space = "ORIGINAL"  # instances unmoved by the source's location
    classes = {"car": [G.Instances(parent, of=car)]}
    assert G.Compose([G.BBox(classes), G.Segmentation(classes, full_masks=True)], out, (320, 240))()
    data = label(out)
    visible = mask(out, data["masks"][1]["mask"])
    full = mask(out, data["full_masks"][1]["mask"])
    assert visible.sum() < 0.8 * full.sum() and (visible <= full).all()
    rows, columns = np.nonzero(full)
    box = data["bboxes"][1]["bbox"]
    assert columns.min() == pytest.approx(box[0], abs=1.5) and columns.max() + 1 == pytest.approx(box[2], abs=1.5)
