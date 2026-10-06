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


def test_aovs_and_passes_skip_empty(scene, cube, out):
    scene.render.engine = "CYCLES"
    obj = cube("Cube")  # pass_index 0, so the object index pass is all 0
    material = new_material(obj, "Mat")
    add_aov(material, "Albedo", "COLOR", (1, 0.25, 0, 1))
    add_aov(material, "Empty", "VALUE", 0.0)
    steps = [G.Render(), G.AOVToImage(["Albedo", "Empty"], skip_empty=True),
             G.Passes(["Depth", "Normal", "ObjectIndex"], skip_empty=True)]
    assert G.Compose(steps, out, (64, 48))()
    data = label(out)
    assert data["aovs"] == {"Albedo": "000000_Albedo.exr", "Empty": None}
    assert data["passes"] == {"Depth": "000000_Depth.exr", "Normal": "000000_Normal.exr", "ObjectIndex": None}
    assert "000000_Empty.exr" not in os.listdir(out) and "000000_ObjectIndex.exr" not in os.listdir(out)
    # off by default
    assert G.Compose([G.AOVToImage(["Empty"]), G.Passes(["ObjectIndex"])], out, (64, 48))()
    assert label(out, 1)["aovs"] == {"Empty": "000001_Empty.exr"} and "000001_ObjectIndex.exr" in os.listdir(out)


def test_layer_empty():
    pixels = np.zeros((2, 2, 4))
    pixels[..., 3] = 1
    assert bpy_generating.layer_empty(pixels, ("R", "G", "B", "A"))
    pixels[0, 0, 1] = 0.001
    assert not bpy_generating.layer_empty(pixels, ("R", "G", "B", "A"))
    assert bpy_generating.layer_empty(np.zeros((2, 2, 1)), ("A",))
    assert not bpy_generating.layer_empty(np.full((2, 2, 1), -1.0), ("Y",))


def test_segmentation_skip_empty(scene, cube, out):
    visible = cube("visible")
    hidden = cube("hidden", (0, 5, 0), 0.5)  # fully behind visible
    far = cube("far", (60, 0, 0))
    classes = {"car": [visible, hidden], "gone": [far]}
    steps = [G.Render(), G.Segmentation(classes, per="both", skip_empty=True), G.SegmentationImage()]
    assert G.Compose(steps, out, (64, 48))()
    masks = [(m["objects"], m["per"], m["mask"]) for m in label(out)["masks"]]
    assert masks == [
        (["visible"], "instance", "000000_mask_0.png"),
        (["hidden"], "instance", None),
        (["far"], "instance", None),
        (["visible", "hidden"], "class", "000000_mask_car.png"),
        (["far"], "class", None),
    ]
    files = os.listdir(out)
    assert "000000_mask_1.png" not in files and "000000_mask_gone.png" not in files and "000000_segmentation.png" in files
    # only empty masks: the preview still works
    assert G.Compose([G.Render(), G.Segmentation({"gone": [far]}, skip_empty=True), G.SegmentationImage()], out, (64, 48))()


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


def test_truncation():
    assert bpy_generating.truncation([0.2, 0.2, 0.8, 0.8], False) == 0.0
    assert bpy_generating.truncation([0.5, 0.2, 1.5, 0.8], False) == pytest.approx(0.5)
    assert bpy_generating.truncation([-0.5, -0.5, 0.5, 0.5], False) == pytest.approx(0.75)
    assert bpy_generating.truncation([0.2, 0.2, 0.8, 0.8], True) == 1.0
    assert bpy_generating.truncation([1.2, 0.2, 1.8, 0.8], False) == 1.0
    assert bpy_generating.truncation(None, True) == 1.0


def test_max_truncation_skips_before_render(scene, cube, out, renders):
    inside = cube("inside", (-1, 0, 0), 0.5)
    # about 55% of its box is right of the frame
    edge = cube("edge", (3.6, 0, 0), 2)
    steps = [G.Render(), G.BBox({"x": [inside, edge]}, max_truncation=0.3)]
    assert G.Compose(steps, out, (64, 48))() is False
    assert renders == [] and os.listdir(out) == []
    assert G.Compose([G.BBox({"x": [inside, edge]}, max_truncation=0.7)], out, (64, 48))()
    assert G.Compose([G.BBox({"x": [inside]}, max_truncation=0)], out, (64, 48))()
    # partly behind the camera counts as fully out of frame
    behind = cube("behind", (0, -10, 0), 2)
    assert G.Compose([G.BBox({"x": [inside, behind]}, max_truncation=0.99)], out, (64, 48))() is False
    with pytest.raises(ValueError, match="max_truncation"):
        G.Compose([G.BBox({"x": [inside]}, max_truncation=2)], out, (64, 48))()


def test_class_settings_win_over_bbox_arguments():
    classes = {
        "plain": ["a"],
        "loose": {"instances": ["b"], "max_truncation": 0.9},
        "free": {"instances": ["c"], "max_truncation": None, "iou_deconflict": None},
    }
    settings = bpy_generating.class_settings(classes, {"iou_deconflict": 0.5, "max_truncation": 0.3})
    assert settings["plain"] == {"iou_deconflict": 0.5, "max_truncation": 0.3}
    assert settings["loose"] == {"iou_deconflict": 0.5, "max_truncation": 0.9}
    assert settings["free"] == {"iou_deconflict": None, "max_truncation": None}


def test_iou_conflict_uses_the_lower_limit():
    a, b = [0, 0, 10, 10], [5, 0, 15, 10]  # IoU 1/3
    assert bpy_generating.iou_conflict([a, b], [0.2, 0.9])
    assert bpy_generating.iou_conflict([a, b], [None, 0.2])
    assert not bpy_generating.iou_conflict([a, b], [0.5, 0.9])
    assert not bpy_generating.iou_conflict([a, b], [None, None])


def test_per_class_skip_settings(scene, cube, out, renders):
    inside = cube("inside", (-1, 0, 0), 0.5)
    edge = cube("edge", (3.6, 0, 0), 2)  # about 55% out of frame

    def generate(classes, **kwargs):
        return G.Compose([G.BBox(classes, **kwargs)], out, (64, 48))()

    # a class's own max_truncation, even None, wins over the global one
    assert generate({"in": [inside], "edge": {"instances": [edge], "max_truncation": 0.7}}, max_truncation=0.1)
    assert generate({"in": [inside], "edge": {"instances": [edge], "max_truncation": None}}, max_truncation=0.1)
    assert generate({"in": [inside], "edge": {"instances": [edge], "max_truncation": 0.3}}, max_truncation=0.9) is False
    assert generate({"in": [inside], "edge": {"instances": [edge]}}, max_truncation=0.3) is False

    a, b = cube("a", (0, 0, 1)), cube("b", (0.2, 0, 1))
    assert generate({"a": {"instances": [a], "iou_deconflict": None}, "b": [b]}, iou_deconflict=0.1) is False
    assert generate({"a": {"instances": [a], "iou_deconflict": None}, "b": {"instances": [b], "iou_deconflict": None}},
                    iou_deconflict=0.1)
    assert generate({"a": {"instances": [a], "iou_deconflict": 0.1}, "b": [b]}) is False
    assert renders == []

    # Segmentation takes the same classes dict and ignores the settings
    seg_out = os.path.join(out, "segmentation")
    os.makedirs(seg_out)
    assert G.Compose([G.Segmentation({"a": {"instances": [a], "max_truncation": 0}})], seg_out, (64, 48))()
    assert label(seg_out)["masks"][0]["class"] == "a"


def test_hidden_in_render(scene, cube, out):
    car1 = cube("car1", (-2, 0, 0))
    car2 = cube("car2", (2, 0, 0))
    top = cube("top", (0, 0, 1), 0.8)
    leg = cube("leg", (0, 0, -1), 0.6)
    # in front of car1, hidden in the render but not in the viewport
    wall = cube("wall", (-2, -3, 0), 2)
    for obj in (car2, leg, wall):
        obj.hide_render = True
    classes = {"car": [car1, car2], "table": [[top, leg]]}
    front = (car1, next(v.index for v in car1.data.vertices if v.co.y < 0))
    steps = [G.Render(), G.BBox(classes, max_truncation=0, max_occlusion=0), G.Segmentation(classes),
             G.Keypoints({"car1": front, "car2": car2})]
    assert G.Compose(steps, out, (320, 240))()
    data = label(out)
    boxes = [b["bbox"] for b in data["bboxes"]]
    assert boxes[0] == pytest.approx(projected_bbox(scene, [car1], 320, 240))
    # a hidden instance has no box and doesn't skip, a hidden member is left out
    assert boxes[1] is None and boxes[2] == pytest.approx(projected_bbox(scene, [top], 320, 240))
    assert [mask(out, f"000000_mask_{i}.png").any() for i in range(3)] == [True, False, True]
    found = {k["name"]: k for k in data["keypoints"]}
    assert found["car1"]["visible"] and found["car2"]["in_frame"] and not found["car2"]["visible"]
    assert car2.hide_render and wall.hide_render and not wall.hide_viewport

    wall.hide_render = False
    G.Compose([G.Keypoints({"car1": front})], out, (320, 240))()
    assert not label(out, 1)["keypoints"][0]["visible"]


def test_max_occlusion(scene, cube, out, renders):
    scene.render.engine = "CYCLES"
    target = cube("target")
    # a box in no class between the camera and the target, covering its right half
    wall = cube("wall", (0.5, -3, 0))

    def generate(classes, **kwargs):
        return G.Compose([G.Render(), G.BBox(classes, **kwargs)], out, (320, 240))()

    assert generate({"t": [target]}, max_occlusion=0.3) is False
    # two workbench id renders, no beauty render, nothing written, the wall shown again
    assert renders == ["BLENDER_WORKBENCH"] * 2 and os.listdir(out) == [] and not wall.hide_render
    assert generate({"t": [target]}, max_occlusion=0.7)
    # a class's own value wins
    assert generate({"t": {"instances": [target], "max_occlusion": 0.7}}, max_occlusion=0.1)
    assert generate({"t": {"instances": [target], "max_occlusion": 0.3}}, max_occlusion=0.9) is False
    # class objects covering each other don't count
    assert generate({"t": [target], "wall": [wall]}, max_occlusion=0)


def test_segmentation_reuses_the_occlusion_render(scene, cube, out, renders):
    target, wall = cube("target"), cube("wall", (0.5, -3, 0))
    classes = {"t": [target]}
    steps = [G.Segmentation(classes), G.BBox(classes, max_occlusion=0.7)]
    assert G.Compose(steps, out, (320, 240))()
    assert len(renders) == 2
    visible = mask(out, "000000_mask_0.png")
    # only the left half of the target is visible, so its mask stops at the image center
    assert visible.any() and not visible[:, 161:].any()
    renders.clear()
    assert G.Compose([G.Segmentation(classes)], out, (320, 240))()
    assert len(renders) == 1 and (mask(out, "000001_mask_0.png") == visible).all() and not wall.hide_render


@pytest.mark.parametrize("value, match", [
    ({"objects": []}, "unknown keys"),
    ({"max_truncation": 0.5}, "needs an 'instances' list"),
    ({"instances": [], "iou_deconflict": 1.5}, "iou_deconflict"),
    ({"instances": [], "max_occlusion": -0.1}, "max_occlusion"),
    ({"instances": "not a list"}, "must map to a list"),
])
def test_class_dict_errors(scene, out, value, match):
    with pytest.raises((TypeError, ValueError), match=match):
        G.Compose([G.BBox({"x": value})], out, (64, 48))()


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


def transparent(scene):
    scene.render.film_transparent = True
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"


def write_test_image(path, color, size=(40, 20)):
    import OpenImageIO as oiio

    buffer = oiio.ImageBuf(oiio.ImageSpec(size[0], size[1], 3, oiio.UINT8))
    buffer.set_pixels(oiio.ROI(), np.full((size[1], size[0], 3), color, dtype=np.uint8))
    assert buffer.write(path)


@pytest.mark.parametrize("mode", ["color", "white_noise", "color_noise", "image"])
def test_background_modes(scene, cube, out, tmp_path, mode):
    transparent(scene)
    car = cube("car")
    images = tmp_path / "backgrounds"
    (images / "sub").mkdir(parents=True)
    write_test_image(str(images / "sub" / "photo.jpg"), (10, 200, 30))
    out = str(tmp_path / "dataset")
    G.Compose([G.Render()], out, (64, 48))()  # without background, for the object pixels
    steps = [G.Background({mode: 1}, noise_size=(1, 3), images_path=str(images)), G.Render(),
             G.BBox({"car": [car]}), G.BBoxImage()]
    assert G.Compose(steps, out, (64, 48))()
    data = label(out, 1)
    assert data["background"]["mode"] == mode
    plain = read_image(os.path.join(out, "000000.png"))
    image = read_image(os.path.join(out, "000001.png"))
    assert image.shape == (48, 64, 3)
    # the object is unchanged, the background behind it is filled
    assert image[24, 32] == pytest.approx(plain[24, 32][:3], abs=1 / 255)
    corner = image[:8, :8]
    if mode == "color":
        assert (np.rint(corner * 255) == data["background"]["color"]).all()
    elif mode == "white_noise":
        assert (corner[..., 0] == corner[..., 1]).all() and len(np.unique(corner)) > 1
    elif mode == "color_noise":
        assert (corner[..., 0] != corner[..., 1]).any()
        assert 1 <= data["background"]["noise_size"] <= 3
    else:
        assert data["background"]["image"] == os.path.join("sub", "photo.jpg")
        assert np.abs(corner - np.array([10, 200, 30]) / 255).max() < 3 / 255
    # the preview has the background too
    preview = read_image(os.path.join(out, "000001_bboxes.png"))
    assert preview[4, 4][:3] == pytest.approx(image[4, 4], abs=1 / 255) and preview[4, 4][3] == 1


def test_background_exr_jpeg_and_seed(scene, cube, out):
    import random

    transparent(scene)
    cube("car")
    random.seed(3)
    assert G.Compose([G.Background({"color": 1}), G.Render("OPEN_EXR")], out, (32, 24))()
    color = np.array(label(out)["background"]["color"])
    image = read_image(os.path.join(out, "000000.exr"))
    assert image.shape == (24, 32, 3)
    assert image[0, 0] == pytest.approx(bpy_generating.srgb_to_linear(color), abs=1e-5)
    # the same seed, the same background
    random.seed(3)
    assert G.Compose([G.Background({"color": 1}), G.Render("JPEG")], out, (32, 24))()
    assert label(out, 1)["background"]["color"] == color.tolist()
    assert read_image(os.path.join(out, "000001.jpg"))[0, 0] == pytest.approx(color / 255, abs=10 / 255)  # JPEG
    # equal weights, image only with images_path
    assert bpy_generating.background_weights(None, None) == {"color": 1, "white_noise": 1, "color_noise": 1, "image": 0}
    # an image weight of 0 needs no images_path
    assert G.Compose([G.Background({"color_noise": 1, "image": 0}), G.Render()], out, (32, 24))()
    assert label(out, 2)["background"]["mode"] == "color_noise"


@pytest.mark.parametrize("setup, background, match", [
    (lambda scene: setattr(scene.render, "film_transparent", False), G.Background(), "Transparent"),
    (lambda scene: setattr(scene.render.image_settings, "color_mode", "RGB"), G.Background(), "RGBA"),
    (None, G.Background({"sky": 1}), "Unknown background modes"),
    (None, G.Background({"color": 0}), "all 0"),
    (None, G.Background(noise_size=(4, 2)), "noise_size"),
    (None, G.Background({"image": 1}), "images_path"),
    (None, G.Background({"image": 1}, images_path="/does/not/exist"), "not a folder"),
])
def test_background_errors(scene, out, renders, setup, background, match):
    transparent(scene)
    if setup:
        setup(scene)
    with pytest.raises(ValueError, match=match):
        G.Compose([G.Render(), background], out, (16, 12))()
    assert renders == []


def test_background_empty_image_folder(scene, out, tmp_path):
    transparent(scene)
    (tmp_path / "empty").mkdir()
    with pytest.raises(ValueError, match="no .png"):
        G.Compose([G.Background({"image": 1}, images_path=str(tmp_path / "empty"))], out, (16, 12))()
