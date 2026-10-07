import json
import os
import xml.etree.ElementTree as ElementTree

import numpy as np
import pytest

from blendmentation.export import export


def test_rle_and_mask_bbox():
    mask = np.zeros((4, 5), dtype=bool)
    mask[1:3, 2:4] = True
    # column-major: columns 0, 1 and the first pixel of column 2 are 9 zeros, then
    # 2 ones, 2 zeros (end of column 2, start of 3), 2 ones, 5 zeros
    assert export.rle(mask) == {"size": [4, 5], "counts": [9, 2, 2, 2, 5]}
    assert export.rle(np.ones((2, 2), dtype=bool))["counts"] == [0, 4]
    assert export.mask_bbox(mask) == [2.0, 1.0, 4.0, 3.0]
    assert export.mask_bbox(np.zeros((2, 2), dtype=bool)) is None


@pytest.fixture
def dataset(out, cube):
    """3 datapoints with bboxes and masks, plus one without an image."""
    bpy = pytest.importorskip("bpy")
    from blendmentation.generating import generating as G

    car1 = cube("car1", (-2, 0, 0))
    car2 = cube("car2", (3.4, 0, 0), 1.5)   # cut by the right border
    hidden = cube("hidden", (-3, 5, 0), 0.5)  # fully behind car1
    far = cube("far", (60, 0, 0))            # out of frame
    top, leg = cube("top", (1, 0, 1), 0.8), cube("leg", (1, 0, -0.5), 0.4)
    classes = {"car": [car1, car2, hidden, far], "table": [[top, leg]]}
    generator = G.Compose([G.Render(), G.BBox(classes), G.Segmentation(classes, per="both")], out, (320, 240))
    for i in range(3):
        car1.location.z = 0.1 * i
        bpy.context.view_layer.update()
        generator()
    G.Compose([G.BBox(classes)], out, (320, 240))()
    return out


def load(out, index=0):
    with open(os.path.join(out, f"{index:06d}.json")) as file:
        return json.load(file)


def test_yolo(dataset):
    export.yolo(dataset)
    lines = open(os.path.join(dataset, "000000.txt")).read().splitlines()
    assert len(lines) == 4  # far is out of frame, hidden has a geometric bbox
    x_min, y_min, x_max, y_max = load(dataset)["bboxes"][0]["bbox"]
    class_id, cx, cy, w, h = lines[0].split()
    assert class_id == "0" and float(cx) == pytest.approx((x_min + x_max) / 640, abs=1e-5)
    assert float(w) == pytest.approx((x_max - x_min) / 320, abs=1e-5)
    assert open(os.path.join(dataset, "classes.txt")).read() == "car\ntable\n"
    assert not os.path.exists(os.path.join(dataset, "000003.txt"))  # no image
    export.yolo(dataset, classes=["table", "car"])
    assert open(os.path.join(dataset, "000000.txt")).read().startswith("1 ")
    with pytest.raises(ValueError):
        export.yolo(dataset, classes=["car"])


def test_voc(dataset):
    directory = export.voc(dataset)
    assert sorted(os.listdir(directory)) == ["000000.xml", "000001.xml", "000002.xml"]
    root = ElementTree.parse(os.path.join(directory, "000000.xml")).getroot()
    objects = root.findall("object")
    assert root.find("filename").text == "000000.png" and root.find("size/width").text == "320"
    assert [o.find("name").text for o in objects] == ["car", "car", "car", "table"]
    assert [o.find("truncated").text for o in objects] == ["0", "1", "0", "0"]


def test_yolo_and_voc_visible_boxes(dataset):
    label = load(dataset)
    masks = {tuple(m["objects"]): m["mask"] for m in label["masks"] if m["per"] == "instance"}
    # hidden is fully behind car1, far out of frame: neither has visible pixels
    expected = [export.mask_bbox(export.read_mask(dataset, masks[key])) for key in (("car1",), ("car2",), ("top", "leg"))]

    export.yolo(dataset, bbox_from="mask")
    lines = [line.split() for line in open(os.path.join(dataset, "000000.txt")).read().splitlines()]
    assert [line[0] for line in lines] == ["0", "0", "1"]
    for line, (x_min, y_min, x_max, y_max) in zip(lines, expected):
        assert [float(v) for v in line[1:]] == pytest.approx(
            [(x_min + x_max) / 640, (y_min + y_max) / 480, (x_max - x_min) / 320, (y_max - y_min) / 240], abs=1e-5)

    directory = export.voc(dataset, output_dir=os.path.join(dataset, "visible"), bbox_from="mask")
    objects = ElementTree.parse(os.path.join(directory, "000000.xml")).getroot().findall("object")
    assert [o.find("name").text for o in objects] == ["car", "car", "table"]
    assert [o.find("truncated").text for o in objects] == ["0", "1", "0"]
    x_min, y_min, x_max, y_max = expected[0]
    box = objects[0].find("bndbox")
    assert [int(box.find(tag).text) for tag in ("xmin", "ymin", "xmax", "ymax")] == [x_min + 1, y_min + 1, x_max, y_max]

    with pytest.raises(ValueError, match="bbox_from must be"):
        export.yolo(dataset, bbox_from="visible")
    with pytest.raises(ValueError, match="bbox_from must be"):
        export.voc(dataset, bbox_from="visible")


def test_visible_boxes_need_instance_masks(out, cube):
    from blendmentation.generating import generating as G

    classes = {"car": [cube("car1", (-2, 0, 0))]}
    G.Compose([G.Render(), G.BBox(classes), G.Segmentation(classes, per="class")], out, (64, 48))()
    for export_format in (export.yolo, export.voc, export.coco):
        with pytest.raises(ValueError, match="needs instance masks"):
            export_format(out, bbox_from="mask")


def test_coco(dataset):
    file_name = export.coco(dataset)
    data = json.load(open(file_name))
    assert [image["file_name"] for image in data["images"]] == ["000000.png", "000001.png", "000002.png"]
    assert data["categories"] == [{"id": 1, "name": "car"}, {"id": 2, "name": "table"}]
    first = [a for a in data["annotations"] if a["image_id"] == 0]
    assert [a["objects"] for a in first] == [["car1"], ["car2"], ["top", "leg"]]  # hidden skipped

    label = load(dataset)
    masks = {tuple(m["objects"]): m["mask"] for m in label["masks"] if m["per"] == "instance"}
    for annotation in first:
        reference = export.read_mask(dataset, masks[tuple(annotation["objects"])])
        assert annotation["area"] == int(reference.sum())
        decoded = decode_rle(annotation["segmentation"])
        assert (decoded == reference).all()
    x_min, y_min, x_max, y_max = label["bboxes"][0]["bbox"]
    assert first[0]["bbox"] == pytest.approx([x_min, y_min, x_max - x_min, y_max - y_min])

    visible = json.load(open(export.coco(dataset, output=os.path.join(dataset, "visible.json"), bbox_from="mask")))
    assert all(float(v).is_integer() for a in visible["annotations"] for v in a["bbox"])


def test_coco_skips_masks_not_written(out, cube):
    from blendmentation.generating import generating as G

    car1, car2 = cube("car1", (-2, 0, 0)), cube("car2", (2, 0, 0))
    hidden, far = cube("hidden", (-3, 5, 0), 0.5), cube("far", (60, 0, 0))
    classes = {"car": [car1, car2, hidden, far]}
    G.Compose([G.Render(), G.BBox(classes), G.Segmentation(classes, skip_empty=True)], out, (320, 240))()
    assert [m["mask"] for m in load(out)["masks"]] == ["000000_mask_0.png", "000000_mask_1.png", None, None]
    data = json.load(open(export.coco(out)))
    assert [a["objects"] for a in data["annotations"]] == [["car1"], ["car2"]]
    assert all("segmentation" in a for a in data["annotations"])
    assert len(json.load(open(export.coco(out, output=os.path.join(out, "visible.json"), bbox_from="mask")))["annotations"]) == 2


def test_coco_with_pycocotools(dataset):
    coco_api = pytest.importorskip("pycocotools.coco")
    file_name = export.coco(dataset)
    coco = coco_api.COCO(file_name)
    label = load(dataset)
    masks = {tuple(m["objects"]): m["mask"] for m in label["masks"] if m["per"] == "instance"}
    for annotation in coco.loadAnns(coco.getAnnIds(imgIds=[0])):
        reference = export.read_mask(dataset, masks[tuple(annotation["objects"])])
        assert (coco.annToMask(annotation).astype(bool) == reference).all()


def decode_rle(segmentation):
    height, width = segmentation["size"]
    flat = np.zeros(height * width, dtype=bool)
    position, value = 0, False
    for count in segmentation["counts"]:
        flat[position:position + count] = value
        position += count
        value = not value
    return flat.reshape((height, width), order="F")


def test_bop(out, cube):
    bpy = pytest.importorskip("bpy")
    from blendmentation.generating import generating as G

    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.unit_settings.scale_length = 0.5  # half a meter per blender unit
    target = cube("target")
    wall = cube("wall", (0.5, -3, 0))  # in no class, covers the right half of the target
    hidden = cube("hidden", (-3, 0, 0))
    hidden.hide_render = True
    classes = {"box": [target, hidden]}
    steps = [G.Render(), G.Pose(classes), G.CameraData(), G.Passes(["Depth"]),
             G.Segmentation(classes, full_masks=True)]
    generator = G.Compose(steps, out, (320, 240))
    assert generator() and generator()

    scene_dir = export.bop(out)
    assert scene_dir == os.path.join(out, "bop", "train_pbr", "000000")
    assert sorted(os.listdir(scene_dir)) == ["depth", "mask", "mask_visib", "rgb", "scene_camera.json",
                                             "scene_gt.json", "scene_gt_info.json"]
    assert sorted(os.listdir(os.path.join(scene_dir, "mask"))) == ["000000_000000.png", "000001_000000.png"]
    assert sorted(os.listdir(os.path.join(scene_dir, "rgb"))) == ["000000.png", "000001.png"]

    def read(name):
        with open(os.path.join(scene_dir, name)) as file:
            return json.load(file)

    gt, info, camera = read("scene_gt.json")["0"], read("scene_gt_info.json")["0"], read("scene_camera.json")["0"]
    # the hidden instance is left out, t in mm with the unit scale
    pose = load(out)["poses"][0]
    assert len(gt) == 1 and gt[0]["obj_id"] == 1
    assert gt[0]["cam_t_m2c"] == pytest.approx([v * 500 for v in pose["t"]])
    assert gt[0]["cam_t_m2c"][2] == pytest.approx(5000)
    assert gt[0]["cam_R_m2c"] == pytest.approx([v for row in pose["R"] for v in row])
    assert camera["cam_K"] == pytest.approx([v for row in load(out)["camera"]["intrinsics"] for v in row])
    assert camera["depth_scale"] == 1.0
    # half of the target is behind the wall
    assert info[0]["visib_fract"] == pytest.approx(0.5, abs=0.05)
    assert info[0]["px_count_valid"] == info[0]["px_count_all"]
    x, y, w, h = info[0]["bbox_obj"]
    assert info[0]["bbox_visib"][0] == x and info[0]["bbox_visib"][2] < w
    full = export.read_mask(os.path.join(scene_dir, "mask"), "000000_000000.png")
    assert full.sum() == info[0]["px_count_all"]
    # 16-bit depth in mm: the target's front face is 9.5 units = 4.75 m away, the background 0
    import OpenImageIO as oiio

    raw = oiio.ImageBuf(os.path.join(scene_dir, "depth", "000000.png"))
    assert raw.spec().format == oiio.UINT16
    pixels = raw.get_pixels(oiio.UINT16)[:, :, 0]
    assert pixels[120, 150] == 4750 and pixels[5, 5] == 0
    assert read_json(os.path.join(out, "bop", "obj_ids.json")) == {"1": "box"}
    assert read_json(os.path.join(out, "bop", "camera.json"))["width"] == 320

    # a class whose scale changes can't have one model
    target.scale = (1.5, 1.5, 1.5)
    generator()
    with pytest.raises(ValueError, match="one 3D model per class"):
        export.bop(out)


def read_json(path):
    with open(path) as file:
        return json.load(file)


def test_bop_without_masks(tmp_path):
    """Poses and camera only, works without blender."""
    out = str(tmp_path)
    open(os.path.join(out, "000000.jpg"), "wb").write(b"jpeg")
    camera = {"intrinsics": [[100, 0, 32], [0, 100, 24], [0, 0, 1]], "unit_scale": 1.0, "clip_end": 100,
              "extrinsics_opencv": [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 2]]}
    pose = {"class": "mug", "objects": ["Mug"], "R": [[1, 0, 0], [0, 1, 0], [0, 0, 1]], "t": [0, 0, 1], "scale": [1, 1, 1]}
    hidden = {"class": "cup", "objects": ["Cup"], "R": None, "t": None, "scale": None}
    with open(os.path.join(out, "000000.json"), "w") as file:
        json.dump({"resolution": [64, 48], "image": "000000.jpg", "camera": camera, "poses": [hidden, pose]}, file)
    with open(os.path.join(out, "000001.json"), "w") as file:
        json.dump({"resolution": [64, 48], "image": "000000.jpg", "camera": camera}, file)
    with pytest.raises(ValueError, match="needs Pose and CameraData"):
        export.bop(out)
    os.remove(os.path.join(out, "000001.json"))

    scene_dir = export.bop(out, output=os.path.join(out, "data"), classes=["cup", "mug"], scene_id=3, split="test")
    assert scene_dir == os.path.join(out, "data", "test", "000003")
    assert sorted(os.listdir(scene_dir)) == ["rgb", "scene_camera.json", "scene_gt.json"]
    assert read_json(os.path.join(scene_dir, "scene_gt.json")) == {
        "0": [{"cam_R_m2c": [1, 0, 0, 0, 1, 0, 0, 0, 1], "cam_t_m2c": [0, 0, 1000], "obj_id": 2}]
    }
    assert read_json(os.path.join(scene_dir, "scene_camera.json"))["0"]["cam_t_w2c"] == [0, 0, 2000]
    assert os.listdir(os.path.join(scene_dir, "rgb")) == ["000000.jpg"]
    with pytest.raises(ValueError, match="not in classes"):
        export.bop(out, classes=["cup"])
