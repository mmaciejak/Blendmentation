import math
import os

import numpy as np
import pytest

bpy = pytest.importorskip("bpy")

from bpy_extras.object_utils import world_to_camera_view  # noqa: E402
from mathutils import Vector  # noqa: E402

from blendmentation.generating import bpy_generating, generating as G  # noqa: E402
from conftest import add_aov, label, new_material, read_image  # noqa: E402


def mask(out, file_name):
    return read_image(os.path.join(out, file_name), 0) > 0.5


def projected_bbox(scene, objects, width, height):
    scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage = width, height, 100
    bpy.context.view_layer.update()
    return bpy_generating.bbox(scene, scene.camera, objects, bpy.context.evaluated_depsgraph_get(), width, height)


def test_render_aovs_and_passes_share_one_render(scene, cube, out, renders):
    scene.render.engine = "CYCLES"
    obj = cube("Cube")
    obj.pass_index = 7
    material = new_material(obj, "Mat")
    add_aov(material, "Mask", "VALUE", 0.5)
    add_aov(material, "Albedo", "COLOR", (1, 0.25, 0, 1))
    scene.render.image_settings.file_format = "JPEG"

    steps = [G.Passes(["Depth", "Normal", "UV", "ObjectIndex"]), G.AOVToImage(["Mask", "Albedo"]), G.Render()]
    assert G.Compose(steps, out, (64, 48))() is True
    assert renders == ["CYCLES"]
    data = label(out)
    assert data["image"] == "000000.png"
    assert data["aovs"] == {"Mask": "000000_Mask.exr", "Albedo": "000000_Albedo.exr"}
    assert set(data["passes"]) == {"Depth", "Normal", "UV", "ObjectIndex"}

    center = (24, 32)  # row, column
    path = lambda name: os.path.join(out, f"000000_{name}.exr")  # noqa: E731
    assert read_image(path("Mask"))[center][0] == pytest.approx(0.5)
    assert read_image(path("Albedo"))[center][:3] == pytest.approx([1, 0.25, 0])
    assert read_image(path("Depth"))[center][0] == pytest.approx(9.5, abs=1e-3)  # camera at 10, face at 0.5
    assert read_image(path("Normal"))[center] == pytest.approx([0, -1, 0], abs=1e-3)
    assert read_image(path("ObjectIndex"))[center][0] == 7
    # settings restored
    assert scene.render.image_settings.file_format == "JPEG"
    assert not bpy.context.view_layer.use_pass_z and not bpy.context.view_layer.use_pass_normal

    # AOVs without Render: still one render, no image
    renders.clear()
    assert G.Compose([G.AOVToImage(["Albedo"], file_format="PNG")], out, (64, 48))()
    assert renders == ["CYCLES"] and "000001.png" not in os.listdir(out) and "000001_Albedo.png" in os.listdir(out)


def test_preview_and_render_only(cube, out):
    cube("Cube")
    generator = G.Compose([G.Render()], out, (64, 48))
    generator.preview(2)
    generator()
    assert sorted(os.listdir(out)) == ["000000.png", "000001.png"]  # no labels for an image alone
    assert read_image(os.path.join(out, "000000.png")).shape[:2] == (24, 32)


def test_pass_not_supported_by_engine(scene, cube, out):
    cube("Cube")
    with pytest.raises(RuntimeError, match="UV"):
        G.Compose([G.Passes(["UV"])], out, (16, 12))()
    assert not bpy.context.view_layer.use_pass_uv


@pytest.mark.parametrize("steps", [
    [G.Render(), G.AOVToImage(["Nope"])],
    [G.Render("TIFF")],
    [G.Render(), G.Passes(["Depthh"])],
    [G.Render(), G.BBox([])],
    [G.Render(), G.BBox({"car": "not a list"})],
    [G.Render(), G.Segmentation({"car": []}, per="pixel")],
    [G.Render(), G.RotationMatrix([])],
    [G.Render(), G.OutputField("x", "location")],
    [G.Render(), G.Keypoints(["not a dict"])],
    [G.Render(), G.BBox({}), G.BBoxImage("TIFF")],
    [G.Render(), G.BBox({}), G.BBoxImage(line_width=0)],
    [G.Render(), G.Segmentation({}), G.SegmentationImage(opacity=2)],
    [G.Render(), G.Segmentation({}), G.SegmentationImage("EXR")],
])
def test_config_errors_before_render(scene, out, renders, steps):
    scene.render.engine = "CYCLES"
    with pytest.raises((TypeError, ValueError)):
        G.Compose(steps, out, (16, 12))()
    assert renders == []


def test_bboxes_masks_and_classes(scene, cube, out, renders):
    car1 = cube("car1", (-2.5, 0, 0))
    car2 = cube("car2", (-1.6, 3, 0), 2.0)  # partly behind car1
    top = cube("top", (2.5, 0, 1), 0.8)
    leg = cube("leg", (2.5, 0, -1), 0.6)
    blocker = cube("blocker", (1.5, -4, 0.6), 0.4)  # not labeled, in front of the table
    classes = {"car": [car1, car2], "table": [[top, leg]]}

    steps = [G.Segmentation(classes, per="both"), G.Render(), G.BBox(classes)]
    assert G.Compose(steps, out, (320, 240))()
    assert renders == ["BLENDER_WORKBENCH", "BLENDER_WORKBENCH"]
    data = label(out)
    assert [(b["class"], b["objects"]) for b in data["bboxes"]] == [("car", ["car1"]), ("car", ["car2"]), ("table", ["top", "leg"])]
    assert [(m["class"], m["mask"], m["per"]) for m in data["masks"]] == [
        ("car", "000000_mask_0.png", "instance"), ("car", "000000_mask_1.png", "instance"),
        ("table", "000000_mask_2.png", "instance"), ("car", "000000_mask_car.png", "class"),
        ("table", "000000_mask_table.png", "class"),
    ]
    instance = [mask(out, f"000000_mask_{i}.png") for i in range(3)]
    car_mask, table_mask = mask(out, "000000_mask_car.png"), mask(out, "000000_mask_table.png")
    assert all(m.any() for m in instance)
    assert (car_mask == (instance[0] | instance[1])).all() and (table_mask == instance[2]).all()
    # car2 is hidden where car1 is, its bbox (geometric) still overlaps car1
    assert not instance[1][instance[0]].any()
    assert data["bboxes"][1]["bbox"][0] < data["bboxes"][0]["bbox"][2]
    # group bbox is the union of its members
    a, b = projected_bbox(scene, [top], 320, 240), projected_bbox(scene, [leg], 320, 240)
    union = [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]
    assert data["bboxes"][2]["bbox"] == pytest.approx(union)
    # the unlabeled blocker hides part of the table
    box = projected_bbox(scene, [blocker], 320, 240)
    assert not table_mask[int((box[1] + box[3]) / 2), int((box[0] + box[2]) / 2)]


def test_bbox_image(scene, cube, out, renders):
    car = cube("car", (-2, 0, 0))
    table = cube("table", (2, 0, 0))
    steps = [G.BBoxImage(line_width=3), G.Render(), G.BBox({"car": [car], "table": [table]})]
    assert G.Compose(steps, out, (320, 240))()
    assert renders == ["BLENDER_WORKBENCH"]
    data = label(out)
    assert data["image"] == "000000.png" and data["bbox_image"] == "000000_bboxes.png"
    image = read_image(os.path.join(out, "000000.png"))
    drawn = read_image(os.path.join(out, "000000_bboxes.png"))
    for entry, color in zip(data["bboxes"], bpy_generating.CLASS_COLORS):
        x_min, y_min, x_max, y_max = entry["bbox"]
        middle_row = int((y_min + y_max) / 2)
        edge = drawn[middle_row, int(x_min) + 1]
        assert edge[:3] == pytest.approx(np.array(color) / 255, abs=1e-3) and edge[3] == 1
        # the main image has no box, and inside the box the copy is unchanged
        assert image[middle_row, int(x_min) + 1][:3] != pytest.approx(edge[:3], abs=0.05)
        center = (middle_row, int((x_min + x_max) / 2))
        assert drawn[center] == pytest.approx(image[center], abs=1 / 255)
        # the class name tab sits above the box
        assert drawn[int(y_min) - 2, int(x_min) + 1][:3] == pytest.approx(np.array(color) / 255, abs=1e-3)

    # without Render, still one render and no main image, JPEG without class names
    renders.clear()
    assert G.Compose([G.BBoxImage("JPEG", show_class=False), G.BBox({"car": [car]})], out, (64, 48))()
    assert renders == ["BLENDER_WORKBENCH"]
    assert "000001_bboxes.jpg" in os.listdir(out) and "000001.png" not in os.listdir(out)

    with pytest.raises(ValueError, match="BBox"):
        G.Compose([G.Render(), G.BBoxImage()], out, (64, 48))


def test_segmentation_image(scene, cube, out, renders):
    car1, car2 = cube("car1", (-2.5, 0, 0)), cube("car2", (-1.2, 0, 0))
    table = cube("table", (2, 0, 0))
    classes = {"car": [car1, car2], "table": [table]}
    steps = [G.SegmentationImage(opacity=0.5, line_width=2), G.Segmentation(classes), G.Render()]
    assert G.Compose(steps, out, (320, 240))()
    # the beauty render and the mask render, the preview draws on the kept beauty render
    assert renders == ["BLENDER_WORKBENCH", "BLENDER_WORKBENCH"]
    data = label(out)
    assert data["segmentation_image"] == "000000_segmentation.png"
    image = read_image(os.path.join(out, "000000.png"))
    drawn = read_image(os.path.join(out, "000000_segmentation.png"))
    masks = [mask(out, entry["mask"]) for entry in data["masks"]]
    colors = [np.array(bpy_generating.CLASS_COLORS[i]) / 255 for i in (0, 0, 1)]
    for m, color in zip(masks, colors):
        rows, columns = np.nonzero(m)
        center = (int(rows.mean()), int(columns.mean()))
        assert drawn[center][:3] == pytest.approx(0.5 * image[center][:3] + 0.5 * color, abs=2 / 255)
        # outline on the left edge
        assert drawn[center[0], columns[rows == center[0]].min()][:3] == pytest.approx(color, abs=1e-3)
    background = ~np.any(masks, axis=0)
    for m in masks:  # class name tabs above the masks
        rows, columns = np.nonzero(m)
        background[max(rows.min() - 30, 0):rows.min(), columns.min():columns.min() + 80] = False
    assert drawn[~background][:, :3] != pytest.approx(image[~background][:, :3], abs=1 / 255)
    assert drawn[background] == pytest.approx(image[background], abs=1 / 255)

    # without Render the beauty render happens after the masks, class masks only, JPEG
    renders.clear()
    steps = [G.SegmentationImage("JPEG", line_width=0, show_class=False), G.Segmentation(classes, per="class")]
    assert G.Compose(steps, out, (64, 48))()
    assert renders == ["BLENDER_WORKBENCH", "BLENDER_WORKBENCH"]
    assert "000001_segmentation.jpg" in os.listdir(out) and "000001.png" not in os.listdir(out)

    with pytest.raises(ValueError, match="Segmentation"):
        G.Compose([G.Render(), G.SegmentationImage()], out, (64, 48))


def test_many_instances(scene, cube, out):
    objects = [cube(f"C{i}", ((i % 20 - 9.5) * 0.3, 0, (i // 20 - 7) * 0.3), 0.2) for i in range(300)]
    G.Compose([G.Segmentation({"c": objects}, per="both")], out, (400, 300))()
    for i in (0, 255, 256, 299):
        ys, xs = np.nonzero(mask(out, f"000000_mask_{i}.png"))
        box = projected_bbox(scene, [objects[i]], 400, 300)
        assert len(xs) and box[0] - 1 <= xs.mean() <= box[2] + 1 and box[1] - 1 <= ys.mean() <= box[3] + 1
    total = sum(mask(out, f"000000_mask_{i}.png").sum() for i in range(300))
    assert mask(out, "000000_mask_c.png").sum() == total


def test_iou_deconflict_skips_before_render(scene, cube, out, renders):
    a, b = cube("a"), cube("b", (0.2, 0, 0))
    classes = {"x": [a, b]}
    assert G.Compose([G.Render(), G.Segmentation(classes), G.BBox(classes, iou_deconflict=0.1)], out, (64, 48))() is False
    assert renders == [] and os.listdir(out) == []


def test_output_fields_and_rotation(scene, cube, out):
    obj = cube("Cube", (1, 2, 3))
    material = new_material(obj, "Mat")
    obj.active_material = material
    light = bpy.data.lights.new("Light", "POINT")
    light.energy = 123.0
    steps = [
        G.OutputField("energy", 'bpy.data.lights["Light"].energy'),
        G.OutputField("engine", "bpy.context.scene.render.engine"),
        G.OutputField("camera_matrix", "bpy.context.scene.camera.matrix_world"),
        G.OutputField("material", 'bpy.data.objects["Cube"].active_material'),
        G.OutputField("location", "location", objects=[obj]),
        G.RotationMatrix([obj]),
    ]
    G.Compose(steps, out, (16, 12))()
    data = label(out)
    assert data["energy"] == 123.0 and data["engine"] == "BLENDER_WORKBENCH" and data["material"] == "Mat"
    assert len(data["camera_matrix"]) == 4 and data["location"] == {"Cube": [1.0, 2.0, 3.0]}
    assert data["rotation_matrices"][0]["object"] == "Cube"
    with pytest.raises(ValueError, match="does not resolve"):
        G.Compose([G.OutputField("shape", "data.shape_keys.key_blocks[0].value", objects=[obj])], out, (16, 12))()


@pytest.mark.parametrize("resolution, fit, shift, aspect", [
    ((640, 480), "AUTO", (0, 0), (1, 1)),
    ((480, 640), "AUTO", (0, 0), (1, 1)),
    ((640, 480), "VERTICAL", (0.1, -0.05), (1, 1)),
    ((640, 480), "HORIZONTAL", (-0.2, 0.15), (1, 1)),
    ((500, 500), "AUTO", (0.05, 0.1), (2, 1)),
    ((300, 500), "AUTO", (0, 0.2), (1, 1.5)),
])
def test_camera_data_projects_like_blender(scene, out, resolution, fit, shift, aspect):
    camera = scene.camera
    camera.location = (1.2, -7, 2.3)
    camera.rotation_euler = (math.radians(75), math.radians(5), math.radians(10))
    camera.data.sensor_fit = fit
    camera.data.shift_x, camera.data.shift_y = shift
    scene.render.pixel_aspect_x, scene.render.pixel_aspect_y = aspect
    G.Compose([G.CameraData()], out, resolution)()
    info = label(out)["camera"]
    K, Rt = np.array(info["intrinsics"]), np.array(info["extrinsics_opencv"])
    scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage = *resolution, 100
    for point in ((0, 0, 0), (1, 1, 1), (-1, 0.5, -1)):
        u, v, w = K @ Rt @ np.array([*point, 1.0])
        reference = world_to_camera_view(scene, camera, Vector(point))
        assert u / w == pytest.approx(reference.x * resolution[0], abs=1e-2)
        assert v / w == pytest.approx((1 - reference.y) * resolution[1], abs=1e-2)


def test_camera_data_ortho(scene, out):
    scene.camera.data.type = "ORTHO"
    G.Compose([G.CameraData()], out, (64, 48))()
    info = label(out)["camera"]
    assert "intrinsics" not in info and info["ortho_scale"] == scene.camera.data.ortho_scale


def test_camera_data_depth_of_field(scene, out):
    dof = scene.camera.data.dof
    dof.use_dof, dof.focus_distance, dof.aperture_fstop, dof.aperture_blades = True, 7.5, 2.8, 6
    G.Compose([G.CameraData()], out, (16, 12))()
    info = label(out)["camera"]["depth_of_field"]
    assert info["use_dof"] is True and info["focus_object"] is None and info["aperture_blades"] == 6
    assert info["focus_distance"] == pytest.approx(7.5) and info["f_stop"] == pytest.approx(2.8)

    # with a focus object, the distance is along the view axis (camera at y=-10 looking along +y)
    target = bpy.data.objects.new("Target", None)
    scene.collection.objects.link(target)
    target.location = (3, 2, -1)
    dof.focus_object = target
    G.Compose([G.CameraData()], out, (16, 12))()
    info = label(out, 1)["camera"]["depth_of_field"]
    assert info["focus_object"] == "Target" and info["focus_distance"] == pytest.approx(12)


def test_keypoints(scene, cube, out):
    body = cube("Cube")
    empties = {}
    for name, location in (("front", (0.3, -2, 0.2)), ("hidden", (0, 2, 0)), ("outside", (40, 0, 0)), ("behind", (0, -12, 0))):
        empties[name] = bpy.data.objects.new(name, None)
        empties[name].location = location
        scene.collection.objects.link(empties[name])
    front_vertex = next(v.index for v in body.data.vertices if v.co.y < 0)
    back_vertex = next(v.index for v in body.data.vertices if v.co.y > 0)
    group = body.vertex_groups.new(name="back")
    group.add([v.index for v in body.data.vertices if v.co.y > 0], 1.0, "REPLACE")

    bpy.ops.object.armature_add(location=(1.6, -1.2, -0.6))
    armature = bpy.context.object
    small = cube("small", (1.6, -1.2, 0.0), 0.4)
    small.vertex_groups.new(name="Bone").add([v.index for v in small.data.vertices], 1.0, "REPLACE")
    small.modifiers.new("Armature", "ARMATURE").object = armature
    small_vertex = next(v.index for v in small.data.vertices if v.co.y < 0)

    points = {**empties, "front_vertex": (body, front_vertex), "back_vertex": (body, back_vertex),
              "back_group": (body, "back"), "bone": (armature, "Bone"), "point": (0.0, -1.5, -0.4),
              "deformed": (small, small_vertex)}
    G.Compose([G.Keypoints(points)], out, (320, 240))()
    found = {k["name"]: k for k in label(out)["keypoints"]}
    expected = {"front": (True, True), "hidden": (True, False), "outside": (False, False), "behind": (False, False),
                "front_vertex": (True, True), "back_vertex": (True, False), "back_group": (True, False),
                "bone": (True, True), "point": (True, True), "deformed": (True, True)}
    for name, flags in expected.items():
        assert (found[name]["in_frame"], found[name]["visible"]) == flags, name
    assert found["back_group"]["position"][0] == pytest.approx(160, abs=1e-3)

    armature.pose.bones["Bone"].location = (0, 0.5, 0)  # moves up in world space
    G.Compose([G.Keypoints({"deformed": (small, small_vertex)})], out, (320, 240))()
    assert label(out, 1)["keypoints"][0]["position"][1] < found["deformed"]["position"][1] - 10


def test_next_index_counts_all_outputs(out):
    for name in ("000004_Mask.exr", "000002.json", "000007_mask_car.png", "notes.txt"):
        open(os.path.join(out, name), "w").close()
    assert bpy_generating.next_index(out) == 8
