"""Exports a generated dataset to standard formats: COCO, YOLO and Pascal VOC.

Run it once after generating, on the generating path. It reads the `<index>.json`
labels; only datapoints with an image (a `Render` step) and with bboxes or instance
masks are exported. It works without Blender. COCO with masks needs numpy and
OpenImageIO, which come with Blender.
"""

import json
import os
import re
import xml.etree.ElementTree as ElementTree
from collections.abc import Sequence
from typing import Literal, Optional

LABEL = re.compile(r"^(\d+)\.json$")


def dataset_path(path):
    """Resolves "//" paths relative to the .blend file when running in blender."""
    if path.startswith("//"):
        import bpy

        path = bpy.path.abspath(path)
    return os.path.abspath(path)


def load_labels(path):
    """Returns (index, label) of every datapoint with an image, sorted by index."""
    labels = []
    for name in os.listdir(path):
        match = LABEL.match(name)
        if match:
            with open(os.path.join(path, name)) as file:
                label = json.load(file)
            if "image" in label:
                labels.append((int(match.group(1)), label))
    return sorted(labels, key=lambda item: item[0])


def class_names(labels, classes):
    """Class names in order: as given, or by first appearance in the labels."""
    found = []
    for _, label in labels:
        for entry in label.get("bboxes", []) + label.get("masks", []):
            if entry["class"] not in found:
                found.append(entry["class"])
    if classes is None:
        return found
    unknown = [name for name in found if name not in classes]
    if unknown:
        raise ValueError(f"Classes {unknown} are in the labels but not in classes")
    return list(classes)


def read_mask(path, file_name):
    try:
        import OpenImageIO as oiio
    except ImportError as error:
        raise ImportError("Reading masks needs OpenImageIO: pip install OpenImageIO") from error
    import numpy  # noqa: F401, needed by get_pixels

    buffer = oiio.ImageBuf(os.path.join(path, file_name))
    pixels = buffer.get_pixels(oiio.UINT8)
    if pixels is None:
        raise RuntimeError(f"Cannot read mask {file_name}: {buffer.geterror()}")
    return pixels[:, :, 0] > 0


def mask_bbox(mask):
    """[x_min, y_min, x_max, y_max] in pixels of the mask, None if empty."""
    import numpy as np

    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return None
    return [float(xs.min()), float(ys.min()), float(xs.max() + 1), float(ys.max() + 1)]


def rle(mask):
    """Uncompressed COCO run-length encoding: run lengths in column-major order,
    starting with a run of zeros."""
    import numpy as np

    flat = mask.flatten(order="F").astype(np.uint8)
    changes = np.flatnonzero(np.diff(flat)) + 1
    bounds = np.concatenate(([0], changes, [len(flat)]))
    counts = np.diff(bounds).tolist()
    if flat[0] == 1:
        counts = [0] + counts
    return {"size": [int(mask.shape[0]), int(mask.shape[1])], "counts": counts}


def instances(label):
    """Instances of a label: (class, objects) -> {"bbox", "mask"}, from bboxes and instance masks."""
    found = {}
    for entry in label.get("bboxes", []):
        found.setdefault((entry["class"], tuple(entry["objects"])), {})["bbox"] = entry["bbox"]
    for entry in label.get("masks", []):
        if entry.get("per", "instance") == "instance":
            found.setdefault((entry["class"], tuple(entry["objects"])), {})["mask"] = entry["mask"]
    return found


def coco(path: str, output: Optional[str] = None, classes: Optional[Sequence[str]] = None,
         bbox_from: Literal["label", "mask"] = "label") -> str:
    """Writes a COCO detection and instance segmentation JSON.

    Masks are stored as uncompressed RLE from the instance masks. Instances whose mask
    is empty (fully hidden), or was not written because it was empty
    (`Segmentation(skip_empty=True)`), are skipped. Each annotation also has an
    `"objects"` field with the object names.

    Args:
        path: generating path with the `<index>.json` labels. `//` paths are relative
            to the .blend file when running in Blender.
        output: JSON file to write. None = `<path>/coco.json`.
        classes: class names in category id order (ids start at 1). None = in order of
            first appearance.
        bbox_from: `"label"` for the boxes of the `BBox` step, which include hidden
            parts, or `"mask"` for the extent of the visible pixels in the instance masks.

    Returns:
        Path of the written file.

    Raises:
        ValueError: an unknown `bbox_from`, or a class in the labels missing from `classes`.

    Example:
        ```python
        export.coco("//dataset", classes=["car", "table"])
        ```
    """
    if bbox_from not in ("label", "mask"):
        raise ValueError('bbox_from must be "label" or "mask"')
    path = dataset_path(path)
    labels = load_labels(path)
    names = class_names(labels, classes)
    category_ids = {name: number for number, name in enumerate(names, start=1)}

    images, annotations = [], []
    for index, label in labels:
        width, height = label["resolution"]
        images.append({"id": index, "file_name": label["image"], "width": width, "height": height})
        for (class_name, objects), instance in instances(label).items():
            if "mask" in instance and instance["mask"] is None:
                # empty mask not written, Segmentation(skip_empty=True)
                continue
            mask = read_mask(path, instance["mask"]) if "mask" in instance else None
            if mask is not None and not mask.any():
                # fully hidden behind other objects
                continue
            box = instance.get("bbox")
            if mask is not None and (bbox_from == "mask" or box is None):
                box = mask_bbox(mask)
            elif bbox_from == "mask":
                raise ValueError(f'bbox_from="mask" needs instance masks, {label["image"]} has none for {list(objects)}')
            if box is None:
                continue
            x_min, y_min, x_max, y_max = box
            annotation = {
                "id": len(annotations) + 1,
                "image_id": index,
                "category_id": category_ids[class_name],
                "bbox": [x_min, y_min, x_max - x_min, y_max - y_min],
                "area": (x_max - x_min) * (y_max - y_min),
                "iscrowd": 0,
                "objects": list(objects),
            }
            if mask is not None:
                annotation["segmentation"] = rle(mask)
                annotation["area"] = int(mask.sum())
            annotations.append(annotation)

    output = output or os.path.join(path, "coco.json")
    data = {
        "images": images,
        "annotations": annotations,
        "categories": [{"id": category_ids[name], "name": name} for name in names],
    }
    with open(output, "w") as file:
        json.dump(data, file)
    return output


def yolo(path: str, classes: Optional[Sequence[str]] = None) -> str:
    """Writes YOLO detection labels.

    Writes `<index>.txt` next to every image, with one `class x_center y_center width
    height` line per bbox (normalized to 0-1), `classes.txt`, and `dataset.yaml`
    pointing at the folder, ready for Ultralytics.

    Args:
        path: generating path with the `<index>.json` labels.
        classes: class names in class id order (ids start at 0). None = in order of
            first appearance.

    Returns:
        Path of `dataset.yaml`.

    Raises:
        ValueError: a class in the labels missing from `classes`.

    Example:
        ```python
        export.yolo("//dataset")
        ```
    """
    path = dataset_path(path)
    labels = load_labels(path)
    names = class_names(labels, classes)
    class_ids = {name: number for number, name in enumerate(names)}

    for _, label in labels:
        width, height = label["resolution"]
        lines = []
        for entry in label.get("bboxes", []):
            if entry["bbox"] is None:
                continue
            x_min, y_min, x_max, y_max = entry["bbox"]
            lines.append(
                f"{class_ids[entry['class']]} {(x_min + x_max) / 2 / width:.6f} {(y_min + y_max) / 2 / height:.6f} "
                f"{(x_max - x_min) / width:.6f} {(y_max - y_min) / height:.6f}"
            )
        stem = os.path.splitext(label["image"])[0]
        with open(os.path.join(path, f"{stem}.txt"), "w") as file:
            file.write("\n".join(lines) + ("\n" if lines else ""))

    with open(os.path.join(path, "classes.txt"), "w") as file:
        file.write("\n".join(names) + "\n")
    yaml = os.path.join(path, "dataset.yaml")
    with open(yaml, "w") as file:
        file.write(f"path: {path}\ntrain: .\nval: .\nnames:\n")
        file.writelines(f"  {number}: {json.dumps(name)}\n" for number, name in enumerate(names))
    return yaml


def voc(path: str, output_dir: Optional[str] = None) -> str:
    """Writes Pascal VOC XML annotations, one file per image, with 1-based pixel bboxes.

    Boxes touching the image border are marked `truncated`.

    Args:
        path: generating path with the `<index>.json` labels.
        output_dir: folder for the XML files. None = `<path>/Annotations`.

    Returns:
        The output folder.

    Example:
        ```python
        export.voc("//dataset")
        ```
    """
    path = dataset_path(path)
    output_dir = output_dir or os.path.join(path, "Annotations")
    os.makedirs(output_dir, exist_ok=True)

    for _, label in load_labels(path):
        width, height = label["resolution"]
        root = ElementTree.Element("annotation")
        ElementTree.SubElement(root, "folder").text = os.path.basename(path)
        ElementTree.SubElement(root, "filename").text = label["image"]
        size = ElementTree.SubElement(root, "size")
        for tag, value in (("width", width), ("height", height), ("depth", 3)):
            ElementTree.SubElement(size, tag).text = str(value)
        ElementTree.SubElement(root, "segmented").text = "0"
        for entry in label.get("bboxes", []):
            if entry["bbox"] is None:
                continue
            x_min, y_min, x_max, y_max = entry["bbox"]
            obj = ElementTree.SubElement(root, "object")
            ElementTree.SubElement(obj, "name").text = entry["class"]
            ElementTree.SubElement(obj, "pose").text = "Unspecified"
            # bboxes are clipped to the image, touching the border means truncated
            truncated = x_min <= 0 or y_min <= 0 or x_max >= width or y_max >= height
            ElementTree.SubElement(obj, "truncated").text = str(int(truncated))
            ElementTree.SubElement(obj, "difficult").text = "0"
            box = ElementTree.SubElement(obj, "bndbox")
            for tag, value, limit in (
                ("xmin", x_min + 1, width), ("ymin", y_min + 1, height), ("xmax", x_max, width), ("ymax", y_max, height)
            ):
                ElementTree.SubElement(box, tag).text = str(min(max(int(round(value)), 1), limit))
        ElementTree.indent(root)
        stem = os.path.splitext(label["image"])[0]
        ElementTree.ElementTree(root).write(os.path.join(output_dir, f"{stem}.xml"))
    return output_dir
