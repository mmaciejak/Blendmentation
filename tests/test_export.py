import json
import os
import re
import xml.etree.ElementTree as ElementTree

import numpy as np
import pytest

from blendmentation.export import export, polygons


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
    yaml = open(os.path.join(dataset, "dataset.yaml")).read()
    assert "train: images.txt\nval: images.txt\n" in yaml and "./000001.png\n" in open(os.path.join(dataset, "images.txt")).read()
    assert not os.path.exists(os.path.join(dataset, "000003.txt"))  # no image
    export.yolo(dataset, classes=["table", "car"])
    assert open(os.path.join(dataset, "000000.txt")).read().startswith("1 ")
    with pytest.raises(ValueError):
        export.yolo(dataset, classes=["car"])


def disk(radius, shape=(120, 160), center=(80, 60)):
    ys, xs = np.mgrid[0:shape[0], 0:shape[1]] + 0.5
    return (xs - center[0]) ** 2 + (ys - center[1]) ** 2 < radius ** 2


def iou(first, second):
    return (first & second).sum() / (first | second).sum()


def test_mask_polygon():
    """The outline goes through the pixels at the mask's edge (their x, y indices, like
    OpenCV's contours), so drawn back like OpenCV fills it, it is the mask; parts are
    joined, holes filled, and a polygon has at least 3 points."""
    pixel = np.zeros((5, 5), dtype=bool)
    pixel[2, 3] = True
    assert polygons.mask_polygon(pixel).tolist() == [[3, 2]] * 3
    assert polygons.mask_polygon(np.zeros((5, 5), dtype=bool)) is None
    stairs = np.array([[1, 0, 0], [1, 1, 0], [0, 1, 1]], dtype=bool)
    plus = np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]], dtype=bool)
    # diagonal edges go straight from step to step, as in OpenCV
    assert polygons.mask_polygon(plus).tolist() == [[1, 0], [2, 1], [1, 2], [0, 1]]

    border = disk(50, center=(150, 60))  # cut by the image border
    line = np.zeros((6, 9), dtype=bool)
    line[2, 1:8] = True  # one pixel thin
    ring = disk(40) & ~disk(15)
    for mask, expected in ((disk(20), disk(20)), (border, border), (np.ones((4, 7), dtype=bool), None),
                           (stairs, None), (plus, None), (line, None), (ring, disk(40))):  # the hole filled
        drawn = polygons.draw(polygons.mask_polygon(mask), mask.shape)
        assert (drawn == (mask if expected is None else expected)).all()

    split = disk(30)
    split[:, 70:90] = False  # something in front of the middle: two pieces
    diagonal = np.zeros((6, 6), dtype=bool)
    diagonal[1, 1] = diagonal[2, 2] = diagonal[4, 4] = True  # corners touching: three parts
    assert len(polygons.outlines(diagonal)) == 3
    for mask in (split, diagonal):
        drawn = polygons.draw(polygons.mask_polygon(mask), mask.shape)
        # every pixel, and the zero-width bridge between the pieces drawn as a line
        assert drawn[mask].all() and (drawn & ~mask).sum() <= 21

    exact, simplified = polygons.mask_polygon(disk(50)), polygons.mask_polygon(disk(50), 1)
    assert len(simplified) < len(exact) / 3
    assert iou(polygons.draw(simplified, (120, 160)), disk(50)) > 0.97
    assert all(any((exact == point).all(axis=1)) for point in simplified)  # a subset of the outline
    assert len(polygons.mask_polygon(line, 10)) == 3


def test_yolo_segment(dataset):
    """Each line is the polygon of an instance mask, normalized, which drawn back is the
    mask. Hidden and out-of-frame instances are left out."""
    export.yolo(dataset, task="segment")
    lines = open(os.path.join(dataset, "000000.txt")).read().splitlines()
    label = load(dataset)
    masks = [m for m in label["masks"] if m["per"] == "instance"]
    visible = [m for m in masks if export.read_mask(dataset, m["mask"]).any()]
    assert len(lines) == len(visible) == 3  # car1, car2 (cut by the border), the table
    for line, entry in zip(lines, visible):
        class_id, *values = line.split()
        assert class_id == str(["car", "table"].index(entry["class"]))
        points = np.array(values, dtype=float).reshape(-1, 2)
        assert len(points) >= 3 and points.min() >= 0 and points.max() <= 1
        reference = export.read_mask(dataset, entry["mask"])
        # as Ultralytics draws it (polygon2mask): scaled, truncated to pixels, filled
        drawn = polygons.draw((points * (320, 240)).astype(int), reference.shape)
        if entry["class"] == "car":
            assert (drawn == reference).all()
        else:  # top and leg apart: two pieces and a bridge
            assert drawn[reference].all() and (drawn & ~reference).sum() < 40

    export.yolo(dataset, task="segment", tolerance=1)  # shorter, close
    simplified = open(os.path.join(dataset, "000000.txt")).read().splitlines()
    assert sum(map(len, simplified)) < sum(map(len, lines))
    for line, entry in zip(simplified, visible):
        points = (np.array(line.split()[1:], dtype=float).reshape(-1, 2) * (320, 240)).astype(int)
        reference = export.read_mask(dataset, entry["mask"])
        assert iou(polygons.draw(points, reference.shape), reference) > 0.95
    # only the images, not the masks next to them
    assert open(os.path.join(dataset, "images.txt")).read() == "./000000.png\n./000001.png\n./000002.png\n"
    with pytest.raises(ValueError, match="task"):
        export.yolo(dataset, task="pose")
    with pytest.raises(ValueError, match="tolerance"):
        export.yolo(dataset, task="segment", tolerance=-1)


def test_yolo_segment_needs_instance_masks(out, cube):
    from blendmentation.generating import generating as G

    classes = {"car": [cube("car1", (-2, 0, 0))]}
    G.Compose([G.Render(), G.BBox(classes)], out, (64, 48))()
    with pytest.raises(ValueError, match='task="segment" needs instance masks'):
        export.yolo(out, task="segment")


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

    directory = export.voc(dataset, output=os.path.join(dataset, "visible"), bbox_from="mask")
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
    assert [m["mask"] for m in load(out)["masks"]] == ["000000_mask_car_0.png", "000000_mask_car_1.png", None, None]
    data = json.load(open(export.coco(out)))
    assert [a["objects"] for a in data["annotations"]] == [["car1"], ["car2"]]
    assert all("segmentation" in a for a in data["annotations"])
    assert len(json.load(open(export.coco(out, output=os.path.join(out, "visible.json"), bbox_from="mask")))["annotations"]) == 2


@pytest.mark.filterwarnings("ignore::DeprecationWarning:pycocotools")
@pytest.mark.parametrize("bbox_from", ["label", "mask"])
def test_coco_with_pycocotools(dataset, bbox_from):
    """The whole export read by pycocotools: images, categories, every annotation's mask,
    area and box, and COCOeval scoring the annotations against themselves at AP 1."""
    coco_api = pytest.importorskip("pycocotools.coco")
    from pycocotools import cocoeval, mask as mask_utils

    output = os.path.join(dataset, f"coco_{bbox_from}.json")
    coco = coco_api.COCO(export.coco(dataset, output=output, bbox_from=bbox_from))
    assert [image["file_name"] for image in coco.loadImgs(coco.getImgIds())] == [
        "000000.png", "000001.png", "000002.png"]
    assert all((image["width"], image["height"]) == (320, 240) for image in coco.imgs.values())
    assert [category["name"] for category in coco.loadCats(coco.getCatIds())] == ["car", "table"]

    results = []
    for image_id in coco.getImgIds():
        label = load(dataset, image_id)
        masks = {tuple(m["objects"]): m["mask"] for m in label["masks"] if m["per"] == "instance"}
        boxes = {tuple(b["objects"]): b["bbox"] for b in label["bboxes"]}
        annotations = coco.loadAnns(coco.getAnnIds(imgIds=[image_id]))
        # hidden has no visible pixels, far is out of frame
        assert [a["objects"] for a in annotations] == [["car1"], ["car2"], ["top", "leg"]]
        for annotation in annotations:
            reference = export.read_mask(dataset, masks[tuple(annotation["objects"])])
            assert (coco.annToMask(annotation).astype(bool) == reference).all()
            rle = coco.annToRLE(annotation)
            assert annotation["area"] == int(reference.sum()) == int(mask_utils.area(rle))
            if bbox_from == "mask":
                assert annotation["bbox"] == pytest.approx(mask_utils.toBbox(rle).tolist())
            else:
                x_min, y_min, x_max, y_max = boxes[tuple(annotation["objects"])]
                assert annotation["bbox"] == pytest.approx([x_min, y_min, x_max - x_min, y_max - y_min])
            results.append({"image_id": image_id, "category_id": annotation["category_id"],
                            "bbox": annotation["bbox"], "segmentation": rle, "score": 1.0})

    for iou_type in ("bbox", "segm"):
        detections = [dict(result) for result in results]
        if iou_type == "bbox":
            for detection in detections:
                del detection["segmentation"]
        evaluation = cocoeval.COCOeval(coco, coco.loadRes(detections), iou_type)
        evaluation.evaluate()
        evaluation.accumulate()
        evaluation.summarize()
        assert evaluation.stats[0] == pytest.approx(1.0), iou_type  # AP at IoU 0.5:0.95
        assert evaluation.stats[8] == pytest.approx(1.0), iou_type  # AR, 100 detections


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
    cube("wall", (0.5, -3, 0))  # in no class, covers the right half of the target
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


def test_augmentation_stats(tmp_path):
    """Two datapoints of a Compose over two objects, p=0.5, with a Menu (one entry per
    datapoint: an absolute path) and a Visibility in a OneOf (expected unknown for the
    Menu below it, since the OneOf picks per object)."""
    menu = {"id": "a.1", "type": "Menu", "reached": 0.5, "applied": 0.25, "ignores_object": True,
            "values": [{"value": "SUN", "probability": 0.25}, {"value": "POINT", "probability": 0.25}]}
    nested = {"id": "a.0.1", "type": "Number", "reached": 0.25, "applied": 0.25, "ignores_object": True}
    visibility = {"id": "a.0.0", "type": "Visibility", "reached": 0.25, "applied": 0.25, "ignores_object": False,
                  "values": [{"value": True, "probability": 0.25}]}
    one_of = {"id": "a.0", "type": "OneOf", "reached": 0.5, "applied": 0.5, "ignores_object": False,
              "children": [visibility, nested]}
    root = {"id": "a", "type": "Compose", "reached": 1.0, "applied": 0.5, "ignores_object": False,
            "children": [one_of, menu]}
    (tmp_path / "augmentations.json").write_text(json.dumps({"augmentations": [root]}))
    labels = [
        {"a": {"x": {"applied": True}, "y": {"applied": True}},
         "a.0": {"x": {"applied": True, "value": 0}, "y": {"applied": True, "value": 1}},
         "a.0.0": {"x": {"applied": True, "value": True}},
         "a.0.1": {"null": {"applied": True, "value": 3}},
         "a.1": {"null": {"applied": False, "value": "POINT"}}},
        {"a": {"x": {"applied": False}, "y": {"applied": False}}},
    ]
    for index, record in enumerate(labels):
        (tmp_path / f"{index:06d}.json").write_text(json.dumps({"resolution": [4, 4], "augmentations": record}))
    (tmp_path / "000002.json").write_text(json.dumps({"resolution": [4, 4]}))  # no record: ignored

    stats = export.augmentation_stats(str(tmp_path))
    assert stats["a"] == {"type": "Compose", "name": None, "expected": 0.5, "observed": 0.5, "applied": 2, "calls": 4,
                          "skipped": {"observed": None, "applied": 0, "calls": 0}}
    assert stats["a.0.0"]["observed"] == 0.25 and stats["a.0.0"]["values"] == [
        {"value": True, "expected": 0.25, "observed": 0.25, "skipped_observed": None}]
    assert stats["a.1"]["calls"] == 2 and stats["a.1"]["observed"] == 0 and stats["a.1"]["expected"] == 0.25
    assert stats["a.1"]["values"] == [{"value": "SUN", "expected": 0.25, "observed": 0.0, "skipped_observed": None},
                                      {"value": "POINT", "expected": 0.25, "observed": 0.5, "skipped_observed": None}]
    assert stats["a.0.1"]["expected"] is None and stats["a.0.1"]["observed"] == 0.5
    with pytest.raises(FileNotFoundError):
        export.augmentation_stats(str(tmp_path), graph_file="missing.json")


def test_augmentation_report(tmp_path):
    menu = {"id": "0.0", "type": "Menu", "reached": 1.0, "applied": 1.0, "ignores_object": False,
            "settings": {"options": ["</script><b>x</b>"]}, "values": [{"value": "</script><b>x</b>", "probability": 1.0}]}
    root = {"id": "0", "type": "Compose", "reached": 1.0, "applied": 1.0, "ignores_object": False, "children": [menu]}
    (tmp_path / "augmentations.json").write_text(json.dumps({"augmentations": [root]}))
    record = {"0": {"a": {"applied": True}}, "0.0": {"a": {"applied": True, "value": "</script><b>x</b>"}}}
    (tmp_path / "000000.json").write_text(json.dumps({"augmentations": record}))

    output = export.augmentation_report(str(tmp_path))
    assert output == os.path.join(str(tmp_path), "augmentations.html")
    with open(output) as file:
        page = file.read()
    assert "__DATA__" not in page and page.count("</script>") == 1, "the data can't end the script"
    data = json.loads(re.search(r"const DATA = (.*);\n", page).group(1).replace("<\\/", "</"))
    assert data["datapoints"] == 1 and data["roots"] == [root]
    assert data["stats"]["0.0"]["observed"] == 1.0
    other = export.augmentation_report(str(tmp_path), output=str(tmp_path / "report.html"))
    assert other.endswith("report.html") and os.path.exists(other)


def test_skip_stats(tmp_path):
    root = {"id": "a", "type": "Compose", "reached": 1.0, "applied": 1.0, "ignores_object": False,
            "children": [{"id": "a.0", "type": "Visibility", "reached": 1.0, "applied": 0.5, "ignores_object": False,
                          "values": [{"value": True, "probability": 0.5}, {"value": False, "probability": 0.5}]}]}
    (tmp_path / "augmentations.json").write_text(json.dumps({"augmentations": [root]}))
    (tmp_path / "000000.json").write_text(json.dumps({"augmentations": {
        "a": {"x": {"applied": True}}, "a.0": {"x": {"applied": False, "value": False}}}}))
    lines = [
        {"skip": {"reason": "max_truncation", "class": "car", "objects": ["x"], "value": 0.4, "limit": 0.3},
         "augmentations": {"a": {"x": {"applied": True}}, "a.0": {"x": {"applied": True, "value": True}}}},
        {"skip": {"reason": "iou_deconflict", "class": ["car", "table"], "objects": [["x"], ["t"]], "value": 0.6,
                  "limit": 0.5},
         "augmentations": {"a": {"x": {"applied": True}}, "a.0": {"x": {"applied": True, "value": True}}}},
        {"skip": {"reason": "max_truncation", "class": "car", "objects": ["x"], "value": 0.9, "limit": 0.3},
         "augmentations": {"a": {"x": {"applied": True}}, "a.0": {"x": {"applied": False, "value": False}}}},
    ]
    (tmp_path / "skipped.jsonl").write_text("".join(json.dumps(line) + "\n" for line in lines))

    assert export.skip_stats(str(tmp_path)) == {"kept": 1, "skipped": 3, "reasons": {
        "max_truncation": {"count": 2, "classes": {"car": 2}},
        "iou_deconflict": {"count": 1, "classes": {"car + table": 1}}}}
    stats = export.augmentation_stats(str(tmp_path))["a.0"]
    assert stats["observed"] == 0 and stats["skipped"] == {"observed": 2 / 3, "applied": 2, "calls": 3}
    assert stats["values"] == [{"value": True, "expected": 0.5, "observed": 0.0, "skipped_observed": 2 / 3},
                               {"value": False, "expected": 0.5, "observed": 1.0, "skipped_observed": 1 / 3}]
    page = open(export.augmentation_report(str(tmp_path))).read()
    data = json.loads(re.search(r"const DATA = (.*);\n", page).group(1).replace("<\\/", "</"))
    assert data["skips"]["skipped"] == 3 and data["stats"]["a.0"]["skipped"]["applied"] == 2


def test_prefixed_labels(tmp_path):
    """Labels of every prefix are exported, numbered in order so the ids stay unique."""
    box = {"class": "car", "objects": ["car"], "bbox": [0, 0, 2, 2]}
    for name in ("000000", "000001", "run_000000", "run-b_000003"):
        (tmp_path / f"{name}.json").write_text(json.dumps({"resolution": [4, 4], "image": f"{name}.png", "bboxes": [box]}))
    (tmp_path / "coco2.json").write_text("{}")  # read as prefix "coco", but no image
    (tmp_path / "notes.json").write_text("{}")
    labels = export.load_labels(str(tmp_path))
    assert [(number, label["image"]) for number, label in labels] == [
        (0, "000000.png"), (1, "000001.png"), (2, "run-b_000003.png"), (3, "run_000000.png")]
    coco = json.load(open(export.coco(str(tmp_path))))
    assert [image["id"] for image in coco["images"]] == [0, 1, 2, 3]
    assert [annotation["image_id"] for annotation in coco["annotations"]] == [0, 1, 2, 3]
    export.yolo(str(tmp_path))
    assert (tmp_path / "run_000000.txt").exists() and (tmp_path / "run-b_000003.txt").exists()
    assert [name for _, _, name in export.label_files(str(tmp_path), "run_")] == ["run_000000.json"]

    os.remove(tmp_path / "run_000000.json")
    os.remove(tmp_path / "run-b_000003.json")  # without prefixes, the ids are the indices
    assert [number for number, _ in export.load_labels(str(tmp_path))] == [0, 1]
