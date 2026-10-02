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
