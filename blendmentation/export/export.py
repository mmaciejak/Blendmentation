"""Exports a generated dataset to standard formats: COCO, YOLO, Pascal VOC and BOP.

Run it once after generating, on the generating path. It reads the `<index>.json`
labels; only datapoints with an image (a `Render` step) and with bboxes or instance
masks are exported (with poses for BOP). Labels of every `prefix` (`run1_000012.json`,
from `generating.Compose(prefix=...)`) are read, so runs merged into one folder export
together. Image ids are the datapoint indices, or, when any label has a prefix, the
position of the label sorted by prefix and index, so they stay unique. It works without Blender. COCO with masks,
`bbox_from="mask"` and BOP with masks or depth need numpy and OpenImageIO, which come
with Blender.

COCO, YOLO and VOC take `bbox_from`, which box to write:

- `"label"` (default): the `BBox` step's boxes, around the whole object including the
  parts hidden behind other objects (amodal). Needs a `BBox` step.
- `"mask"`: the extent of the visible pixels of each instance mask (modal), the boxes
  most detection datasets use. Needs a `Segmentation` step with `per="instance"` or
  `"both"` and the same classes. Fully hidden instances, and ones whose mask was not
  written (`skip_empty`), are left out.

`augmentation_stats` compares how often each augmentation ran over the dataset with
the probabilities of its graph, from a `generating.Augmentations` step, `skip_stats`
counts the datapoints `BBox` skipped by reason, and `augmentation_report` draws it all
on an HTML page.
"""

import json
import os
import re
import shutil
import xml.etree.ElementTree as ElementTree
from collections.abc import Sequence
from typing import Literal, Optional

# <prefix><index>.json, a prefix doesn't end with a digit
LABEL = re.compile(r"^(.*\D)?(\d+)\.json$")
# written by generating.Augmentations for every datapoint a step skipped, after the prefix
SKIPPED_FILE = "skipped.jsonl"


def dataset_path(path):
    """Resolves "//" paths relative to the .blend file when running in blender."""
    if path.startswith("//"):
        import bpy

        path = bpy.path.abspath(path)
    return os.path.abspath(path)


def label_files(path, prefix=None):
    """(prefix, index, file name) of every label file, sorted by prefix and index; with a
    prefix only the ones with it ("" for none)."""
    files = []
    for name in os.listdir(path):
        match = LABEL.match(name)
        if match and (prefix is None or (match.group(1) or "") == prefix):
            files.append((match.group(1) or "", int(match.group(2)), name))
    return sorted(files)


def load_labels(path):
    """Returns (image id, label) of every datapoint with an image, sorted by prefix and
    index. The id is the index, or the position when any label has a prefix."""
    labels = []
    for prefix, index, name in label_files(path):
        with open(os.path.join(path, name)) as file:
            label = json.load(file)
        if "image" in label:
            labels.append((prefix, index, label))
    prefixed = any(prefix for prefix, _, _ in labels)
    return [(number if prefixed else index, label) for number, (_, index, label) in enumerate(labels)]


def class_names(labels, classes, keys=("bboxes", "masks")):
    """Class names in order: as given, or by first appearance in the labels under keys."""
    found = []
    for _, label in labels:
        for entry in [entry for key in keys for entry in label.get(key, [])]:
            if entry["class"] not in found:
                found.append(entry["class"])
    if classes is None:
        return found
    unknown = [name for name in found if name not in classes]
    if unknown:
        raise ValueError(f"Classes {unknown} are in the labels but not in classes")
    return list(classes)


def import_oiio():
    try:
        import OpenImageIO as oiio
    except ImportError as error:
        raise ImportError("Reading masks and depth needs OpenImageIO: pip install OpenImageIO") from error
    import numpy  # noqa: F401, needed by get_pixels

    return oiio


def read_mask(path, file_name):
    oiio = import_oiio()
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


BBOX_FROM = ("label", "mask")


def check_bbox_from(bbox_from):
    if bbox_from not in BBOX_FROM:
        raise ValueError('bbox_from must be "label" or "mask"')


def visible_instances(path, label, need_masks):
    """(class, objects, instance, mask) of every instance of a label (see `instances`)
    that is not fully hidden: mask is its instance mask read as a boolean array, None when
    it has none. An instance whose mask is empty, or was not written because it was
    (Segmentation(skip_empty=True)), is left out.

    Raises:
        ValueError: need_masks (bbox_from="mask") and an instance has no instance mask
    """
    for (class_name, objects), instance in instances(label).items():
        if "mask" not in instance:
            if need_masks:
                raise ValueError(
                    f'bbox_from="mask" needs instance masks, {label["image"]} has none for {list(objects)}: '
                    'add Segmentation(classes, per="instance") with the classes of BBox'
                )
            yield class_name, objects, instance, None
            continue
        if instance["mask"] is None:
            continue
        mask = read_mask(path, instance["mask"])
        if mask.any():
            yield class_name, objects, instance, mask


def label_boxes(path, label, bbox_from):
    """(class, objects, [x_min, y_min, x_max, y_max]) of every instance of a label with a
    box in the frame: the BBox boxes, or with bbox_from="mask" the visible pixels' extent."""
    if bbox_from == "label":
        return [(entry["class"], entry["objects"], entry["bbox"]) for entry in label.get("bboxes", [])
                if entry["bbox"] is not None]
    return [(class_name, list(objects), mask_bbox(mask))
            for class_name, objects, _, mask in visible_instances(path, label, need_masks=True)]


def coco(path: str, output: Optional[str] = None, classes: Optional[Sequence[str]] = None,
         bbox_from: Literal["label", "mask"] = "label") -> str:
    """Writes a COCO detection and instance segmentation JSON.

    Masks are stored as uncompressed RLE from the instance masks. Instances whose mask
    is empty (fully hidden), or was not written because it was empty
    (`Segmentation(skip_empty=True)`), are skipped. Each annotation also has an
    `"objects"` field with the object names.

    [COCO example in the Quick start](quick-start.md#coco){ .md-button }

    !!! warning "Visible boxes need instance masks"
        By default (`bbox_from="label"`) the boxes come from the `BBox` step and cover
        the whole object, including the parts hidden behind other objects. For boxes
        of only the visible pixels, which most detection datasets use, pass
        `bbox_from="mask"`. That needs a `Segmentation` step with `per="instance"` or
        `per="both"`, with the same classes as `BBox`, in the generating `Compose`;
        without instance masks the export raises.

    Args:
        path: generating path with the `<index>.json` labels. `//` paths are relative
            to the .blend file when running in Blender.
        output: JSON file to write. None = `<path>/coco.json`.
        classes: class names in category id order (ids start at 1). None = in order of
            first appearance.
        bbox_from: `"label"` for the boxes of the `BBox` step, around the whole object
            including hidden parts (amodal), or `"mask"` for the extent of the visible
            pixels in the instance masks (modal, as in the COCO dataset itself), which
            needs `Segmentation` with `per="instance"` or `"both"`. With `"label"`, an
            instance with a mask but no `BBox` box gets its mask's box. See the module
            description.

    Returns:
        Path of the written file.

    Raises:
        ValueError: an unknown `bbox_from`, `bbox_from="mask"` with an instance that
            has no instance mask, or a class in the labels missing from `classes`.

    Example:
        ```python
        export.coco("//dataset", classes=["car", "table"])
        ```
    """
    check_bbox_from(bbox_from)
    path = dataset_path(path)
    labels = load_labels(path)
    names = class_names(labels, classes)
    category_ids = {name: number for number, name in enumerate(names, start=1)}

    images, annotations = [], []
    for index, label in labels:
        width, height = label["resolution"]
        images.append({"id": index, "file_name": label["image"], "width": width, "height": height})
        for class_name, objects, instance, mask in visible_instances(path, label, need_masks=bbox_from == "mask"):
            box = instance.get("bbox")
            if mask is not None and (bbox_from == "mask" or box is None):
                box = mask_bbox(mask)
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


def yolo(path: str, classes: Optional[Sequence[str]] = None, bbox_from: Literal["label", "mask"] = "label") -> str:
    """Writes YOLO detection labels.

    Writes `<index>.txt` next to every image, with one `class x_center y_center width
    height` line per bbox (normalized to 0-1), `classes.txt`, and `dataset.yaml`
    pointing at the folder, ready for Ultralytics.

    [YOLO examples in the Quick start](quick-start.md#yolo){ .md-button }

    !!! warning "Visible boxes need instance masks"
        By default (`bbox_from="label"`) the boxes come from the `BBox` step and cover
        the whole object, including the parts hidden behind other objects. For boxes
        of only the visible pixels, which most detection datasets use, pass
        `bbox_from="mask"`. That needs a `Segmentation` step with `per="instance"` or
        `per="both"`, with the same classes as `BBox`, in the generating `Compose`;
        without instance masks the export raises.

    Args:
        path: generating path with the `<index>.json` labels.
        classes: class names in class id order (ids start at 0). None = in order of
            first appearance.
        bbox_from: `"label"` for the boxes of the `BBox` step, around the whole object
            including hidden parts (amodal), or `"mask"` for the extent of the visible
            pixels in the instance masks (modal), which needs `Segmentation` with
            `per="instance"` or `"both"`; fully hidden instances are left out. See the
            module description.

    Returns:
        Path of `dataset.yaml`.

    Raises:
        ValueError: an unknown `bbox_from`, `bbox_from="mask"` with an instance that
            has no instance mask, or a class in the labels missing from `classes`.

    Example:
        ```python
        export.yolo("//dataset")
        export.yolo("//dataset", bbox_from="mask")  # boxes of the visible pixels
        ```
    """
    check_bbox_from(bbox_from)
    path = dataset_path(path)
    labels = load_labels(path)
    names = class_names(labels, classes)
    class_ids = {name: number for number, name in enumerate(names)}

    for _, label in labels:
        width, height = label["resolution"]
        lines = []
        for class_name, _, (x_min, y_min, x_max, y_max) in label_boxes(path, label, bbox_from):
            lines.append(
                f"{class_ids[class_name]} {(x_min + x_max) / 2 / width:.6f} {(y_min + y_max) / 2 / height:.6f} "
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


def voc(path: str, output_dir: Optional[str] = None, bbox_from: Literal["label", "mask"] = "label") -> str:
    """Writes Pascal VOC XML annotations, one file per image, with 1-based pixel bboxes.

    Boxes touching the image border are marked `truncated`.

    [Pascal VOC example in the Quick start](quick-start.md#pascal-voc){ .md-button }

    !!! warning "Visible boxes need instance masks"
        By default (`bbox_from="label"`) the boxes come from the `BBox` step and cover
        the whole object, including the parts hidden behind other objects. For boxes
        of only the visible pixels, which most detection datasets use, pass
        `bbox_from="mask"`. That needs a `Segmentation` step with `per="instance"` or
        `per="both"`, with the same classes as `BBox`, in the generating `Compose`;
        without instance masks the export raises.

    Args:
        path: generating path with the `<index>.json` labels.
        output_dir: folder for the XML files. None = `<path>/Annotations`.
        bbox_from: `"label"` for the boxes of the `BBox` step, around the whole object
            including hidden parts (amodal), or `"mask"` for the extent of the visible
            pixels in the instance masks (modal, as in Pascal VOC itself), which needs
            `Segmentation` with `per="instance"` or `"both"`; fully hidden instances are
            left out. See the module description.

    Returns:
        The output folder.

    Raises:
        ValueError: an unknown `bbox_from`, or `bbox_from="mask"` with an instance that
            has no instance mask.

    Example:
        ```python
        export.voc("//dataset")
        export.voc("//dataset", bbox_from="mask")  # boxes of the visible pixels
        ```
    """
    check_bbox_from(bbox_from)
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
        for class_name, _, (x_min, y_min, x_max, y_max) in label_boxes(path, label, bbox_from):
            obj = ElementTree.SubElement(root, "object")
            ElementTree.SubElement(obj, "name").text = class_name
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


def read_depth(path, file_name):
    """First channel of a float image, as a (height, width) array."""
    oiio = import_oiio()
    buffer = oiio.ImageBuf(os.path.join(path, file_name))
    pixels = buffer.get_pixels(oiio.FLOAT)
    if pixels is None:
        raise RuntimeError(f"Cannot read depth {file_name}: {buffer.geterror()}")
    return pixels[:, :, 0]


def write_png(file_path, pixels):
    """Writes a (height, width) uint8 or uint16 array as a one channel PNG."""
    oiio = import_oiio()
    import numpy as np

    height, width = pixels.shape
    pixel_format = oiio.UINT16 if pixels.dtype == np.uint16 else oiio.UINT8
    buffer = oiio.ImageBuf(oiio.ImageSpec(width, height, 1, pixel_format))
    buffer.set_pixels(oiio.ROI(), pixels.reshape(height, width, 1))
    if not buffer.write(file_path):
        raise RuntimeError(f"Cannot write {file_path}: {buffer.geterror()}")


def bop_box(mask):
    """[x, y, width, height] in pixels of the mask, [-1, -1, -1, -1] if empty, as BOP."""
    box = mask_bbox(mask)
    if box is None:
        return [-1, -1, -1, -1]
    x_min, y_min, x_max, y_max = (int(value) for value in box)
    return [x_min, y_min, x_max - x_min, y_max - y_min]


def check_scales(labels):
    """Raises when a class has objects of different scales: BOP has one model per class."""
    scales = {}
    for _, label in labels:
        for pose in label["poses"]:
            if pose["scale"] is None:
                continue
            first = scales.setdefault(pose["class"], (pose["scale"], label["image"]))
            if any(abs(a - b) > 1e-4 * max(1.0, abs(b)) for a, b in zip(pose["scale"], first[0])):
                raise ValueError(
                    f"Class '{pose['class']}' has scale {first[0]} in {first[1]} and {pose['scale']} in "
                    f"{label['image']}, but BOP has one 3D model per class: keep its objects at one scale "
                    "(no Scale augmentation on them) and apply that scale to the model"
                )


def bop(path: str, output: Optional[str] = None, classes: Optional[Sequence[str]] = None, scene_id: int = 0,
        split: str = "train_pbr", depth_scale: float = 1.0) -> str:
    """Writes the dataset as one scene of a BOP dataset, for 6D pose estimation.

    Writes `<output>/<split>/<scene_id>/` with `rgb/`, `scene_camera.json` and
    `scene_gt.json`; with masks also `mask/`, `mask_visib/` and `scene_gt_info.json`,
    and with a `Depth` pass `depth/`. `<output>/camera.json` gets the first image's
    camera, and `<output>/obj_ids.json` the class name of every `obj_id`. Image ids
    are the datapoint indices (numbered in order with prefixes), and `obj_id`s number the classes from 1. Instances
    hidden in the render are left out. Distances are converted to mm with the camera's
    `unit_scale`.

    [BOP example in the Quick start](quick-start.md#bop){ .md-button }

    !!! warning "What it needs"
        Steps in the generating `Compose`, in every datapoint:

        - `Render()` as PNG or JPEG: `rgb/`. Required.
        - `Pose(classes)`: `scene_gt.json`. Required.
        - `CameraData()` with a perspective camera: `scene_camera.json`. Required.
        - `Segmentation(classes, full_masks=True)` with `per="instance"` or
          `per="both"` and the same classes as `Pose`: `mask/`, `mask_visib/` and
          `scene_gt_info.json` (visible fraction, boxes, pixel counts). Optional, but
          when any datapoint has masks, every posed instance in every datapoint needs
          both its masks.
        - `Passes(["Depth"])`: `depth/`. Optional.

        In the scene: every object of a class must have the same mesh, origin and
        scale, in every datapoint, because BOP has one 3D model per class and a pose is
        relative to that model's origin. Keep `Scale` augmentations, shape keys and
        other deformations off the posed objects; the export raises when a class's
        scale changes. The models themselves (`models/obj_<obj_id>.ply`, in mm) are not
        written; export them from Blender with that scale applied.

    Note:
        An instance of several objects has the pose of its first object, so its other
        objects must move rigidly with it. Depth outside the 16-bit range (65535 x
        `depth_scale` mm) and the background are 0.

    Args:
        path: generating path with the `<index>.json` labels. `//` paths are relative
            to the .blend file when running in Blender.
        output: BOP dataset folder. None = `<path>/bop`.
        classes: class names in `obj_id` order (ids start at 1). None = in order of
            first appearance in the poses.
        scene_id: number of the scene folder.
        split: name of the split folder, e.g. `"train_pbr"` or `"test"`.
        depth_scale: mm per depth image unit, BOP's `depth_scale`.

    Returns:
        Path of the scene folder.

    Raises:
        ValueError: a datapoint without poses or camera intrinsics, an image that is
            not PNG or JPEG, a pose without both masks when the dataset has masks, a
            class with objects of different scales, or a class in the labels missing
            from `classes`.

    Example:
        ```python
        export.bop("//dataset", classes=["car", "table"])
        ```
    """
    path = dataset_path(path)
    labels = load_labels(path)
    missing = [label["image"] for _, label in labels if "poses" not in label or "intrinsics" not in label.get("camera", {})]
    if missing:
        raise ValueError(f"BOP needs Pose and CameraData with a perspective camera, missing in {missing[:5]}")
    names = class_names(labels, classes, keys=("poses",))
    obj_ids = {name: number for number, name in enumerate(names, start=1)}
    check_scales(labels)
    with_masks = any(label.get("full_masks") or label.get("masks") for _, label in labels)

    output = output or os.path.join(path, "bop")
    scene_dir = os.path.join(output, split, f"{scene_id:06d}")
    folders = ["rgb"] + (["mask", "mask_visib"] if with_masks else [])
    if any(label.get("passes", {}).get("Depth") for _, label in labels):
        folders.append("depth")
    for folder in folders:
        os.makedirs(os.path.join(scene_dir, folder), exist_ok=True)

    def flat(matrix):
        return [value for row in matrix for value in row]

    scene_gt, scene_camera, scene_gt_info = {}, {}, {}
    for index, label in labels:
        width, height = label["resolution"]
        camera = label["camera"]
        to_mm = camera.get("unit_scale", 1.0) * 1000.0
        extension = os.path.splitext(label["image"])[1].lower()
        if extension not in (".png", ".jpg"):
            raise ValueError(f"BOP images are PNG or JPEG, not {label['image']}")
        shutil.copyfile(os.path.join(path, label["image"]), os.path.join(scene_dir, "rgb", f"{index:06d}{extension}"))
        extrinsics = camera["extrinsics_opencv"]
        scene_camera[str(index)] = {
            "cam_K": flat(camera["intrinsics"]),
            "depth_scale": depth_scale,
            "cam_R_w2c": flat(row[:3] for row in extrinsics),
            "cam_t_w2c": [row[3] * to_mm for row in extrinsics],
        }

        depth_valid = None
        depth_file = label.get("passes", {}).get("Depth")
        if depth_file:
            import numpy as np

            depth = read_depth(path, depth_file).astype(np.float64)
            with np.errstate(invalid="ignore", over="ignore"):
                units = depth * to_mm / depth_scale
                valid = np.isfinite(depth) & (depth > 0) & (depth < camera["clip_end"]) & (units <= 65535)
            depth_image = np.where(valid, np.rint(np.where(valid, units, 0)), 0).astype(np.uint16)
            write_png(os.path.join(scene_dir, "depth", f"{index:06d}.png"), depth_image)
            depth_valid = depth_image > 0

        visible = {(entry["class"], tuple(entry["objects"])): entry["mask"]
                   for entry in label.get("masks", []) if entry.get("per", "instance") == "instance"}
        full = {(entry["class"], tuple(entry["objects"])): entry["mask"] for entry in label.get("full_masks", [])}

        def load(file_name):
            import numpy as np

            return np.zeros((height, width), dtype=bool) if file_name is None else read_mask(path, file_name)

        gts, infos = [], []
        for pose in label["poses"]:
            if pose["R"] is None:
                continue
            key = (pose["class"], tuple(pose["objects"]))
            gts.append({
                "cam_R_m2c": flat(pose["R"]),
                "cam_t_m2c": [value * to_mm for value in pose["t"]],
                "obj_id": obj_ids[pose["class"]],
            })
            if not with_masks:
                continue
            if key not in visible or key not in full:
                raise ValueError(
                    f"{label['image']}: {list(key[1])} has no instance mask and full mask, BOP needs "
                    "Segmentation(classes, full_masks=True) with the classes of Pose"
                )
            mask_all = load(full[key])
            # the visible mask is part of the full one, edge pixels of separate renders may differ
            mask_visib = load(visible[key]) & mask_all
            name = f"{index:06d}_{len(gts) - 1:06d}.png"
            write_png(os.path.join(scene_dir, "mask", name), mask_all.astype("uint8") * 255)
            write_png(os.path.join(scene_dir, "mask_visib", name), mask_visib.astype("uint8") * 255)
            px_all, px_visib = int(mask_all.sum()), int(mask_visib.sum())
            infos.append({
                "bbox_obj": bop_box(mask_all),
                "bbox_visib": bop_box(mask_visib),
                "px_count_all": px_all,
                "px_count_valid": int((mask_all & depth_valid).sum()) if depth_valid is not None else px_all,
                "px_count_visib": px_visib,
                "visib_fract": px_visib / px_all if px_all else 0.0,
            })
        scene_gt[str(index)] = gts
        if with_masks:
            scene_gt_info[str(index)] = infos

    files = {"scene_gt.json": scene_gt, "scene_camera.json": scene_camera}
    if with_masks:
        files["scene_gt_info.json"] = scene_gt_info
    for name, data in files.items():
        with open(os.path.join(scene_dir, name), "w") as file:
            json.dump(data, file, indent=2)
    if labels:
        width, height = labels[0][1]["resolution"]
        k = labels[0][1]["camera"]["intrinsics"]
        camera_info = {"cx": k[0][2], "cy": k[1][2], "fx": k[0][0], "fy": k[1][1],
                       "width": width, "height": height, "depth_scale": depth_scale}
        with open(os.path.join(output, "camera.json"), "w") as file:
            json.dump(camera_info, file, indent=2)
    with open(os.path.join(output, "obj_ids.json"), "w") as file:
        json.dump({str(number): name for name, number in obj_ids.items()}, file, indent=2)
    return scene_dir


def graph_nodes(node, root, parents_reach, nodes):
    """Every node of an augmentation graph by id, with its root's id and whether it is
    reached with its root every time (no OneOf or p below 1 between them)."""
    nodes[node["id"]] = dict(node, root=root, always=parents_reach)
    for child in node.get("children", ()):
        always = parents_reach and child["reached"] == nodes[root]["applied"]
        graph_nodes(child, root, always, nodes)


def load_graph(path, graph_file, prefix=""):
    with open(os.path.join(path, prefix + graph_file)) as file:
        return json.load(file)["augmentations"]


def augmentation_records(path, prefix=""):
    """The "augmentations" record of every label with the prefix that has one, by index."""
    records = []
    for _, _, name in label_files(path, prefix):
        with open(os.path.join(path, name)) as file:
            record = json.load(file).get("augmentations")
        if record is not None:
            records.append(record)
    return records


def augmentation_stats(path: str, graph_file: str = "augmentations.json", prefix: str = "") -> dict[str, dict]:
    """Compares what the augmentations did over a dataset with what their graph expects.

    Reads the graph `generating.Augmentations` saved and the `augmentations` record of
    every label. For each augmentation it counts the calls (the objects of the
    `Compose` it is in, over all datapoints) and how often it ran, and for the ones with
    `values` in the graph (a `Menu` with options, a `Boolean`...) how often each value
    was set. Datapoints `BBox` skipped have no label, so a gap between expected and
    observed shows what the skips (or the scene) changed.

    An augmentation that changes the same thing for every object (an absolute data
    path, `Seed`) counts once per datapoint, its last call; its `expected` is None when
    a `OneOf` or a `p` below 1 between it and the `Compose` decides per object whether
    it is called. An augmentation used in several places of the graph has `expected`
    None too, since its record mixes them.

    Args:
        path: the generating path.
        graph_file: file name of the graph, as given to `generating.Augmentations`.
        prefix: the `prefix` of the generating `Compose`: only its labels, graph and
            skipped datapoints are read.

    Returns:
        `{id: {"type", "name", "expected", "observed", "applied", "calls", "skipped",
        "values"}}` by the ids of the graph: `name` is the one given to the augmentation
        (None without), `skipped` the same counts over the skipped datapoints
        (`{"observed", "applied", "calls"}`, from `skipped.jsonl`), `expected` the graph's
        probability that it runs per call,
        `observed` the fraction of `calls` it ran in (`applied`), and `values`
        `[{"value", "expected", "observed", "skipped_observed"}]` for every value expected
        or set.

    Raises:
        FileNotFoundError: there is no graph file in the folder.

    Example:
        ```python
        stats = export.augmentation_stats("//dataset")
        for node_id, node in stats.items():
            print(node_id, node["type"], node["expected"], node["observed"])
        ```
    """
    path = dataset_path(path)
    roots = load_graph(path, graph_file, prefix)
    nodes = {}
    for root in roots:
        graph_nodes(root, root["id"], True, nodes)
    ids = [node_id for node_id, node in nodes.items() if "same_as" not in node]
    stats = count_records(augmentation_records(path, prefix), nodes, ids)
    skipped = count_records([line["augmentations"] for line in skipped_lines(path, prefix)], nodes, ids)
    result = {}
    for node_id, stat in stats.items():
        node = nodes[node_id]
        known = "used_at" not in node and (not node.get("ignores_object") or node["always"] or node_id == node["root"])
        calls, skip = stat["calls"], skipped[node_id]
        entry = {"type": node["type"], "name": node.get("name"), "expected": node["applied"] if known else None,
                 "observed": stat["applied"] / calls if calls else None,
                 "applied": stat["applied"], "calls": calls,
                 "skipped": {"observed": skip["applied"] / skip["calls"] if skip["calls"] else None,
                             "applied": skip["applied"], "calls": skip["calls"]}}
        if "values" in node:
            counts, skip_counts = dict(stat["values"]), dict(skip["values"])
            keys = [json.dumps(item["value"]) for item in node["values"]]
            keys += [key for key in list(counts) + list(skip_counts) if key not in keys]  # set but not listed
            expected = {json.dumps(item["value"]): item["probability"] for item in node["values"]}
            entry["values"] = [{"value": json.loads(key), "expected": expected.get(key) if known else None,
                                "observed": counts.get(key, 0) / calls if calls else None,
                                "skipped_observed": skip_counts.get(key, 0) / skip["calls"] if skip["calls"] else None}
                               for key in dict.fromkeys(keys)]
        result[node_id] = entry
    return result


def count_records(records, nodes, ids):
    """For each node id: how often it was called and ran over the records, and how
    often it set each value."""
    stats = {node_id: {"applied": 0, "calls": 0, "values": {}} for node_id in ids}
    for record in records:
        for node_id, stat in stats.items():
            node = nodes[node_id]
            objects = len(record.get(node["root"], {}))
            entries = list(record.get(node_id, {}).values())
            if node.get("ignores_object") and node_id != node["root"]:
                stat["calls"] += 1 if objects else 0
                entries = entries[-1:]
            else:
                stat["calls"] += objects
            stat["applied"] += sum(1 for entry in entries if entry.get("applied"))
            if "values" in node:
                for entry in entries:
                    if entry.get("value") is not None:
                        key = json.dumps(entry["value"])
                        stat["values"][key] = stat["values"].get(key, 0) + 1
    return stats


def skipped_lines(path, prefix=""):
    """The lines of <path>/<prefix>skipped.jsonl, {"skip", "augmentations"} each; [] without it."""
    file_path = os.path.join(path, prefix + SKIPPED_FILE)
    if not os.path.exists(file_path):
        return []
    with open(file_path) as file:
        return [json.loads(line) for line in file if line.strip()]


def skip_stats(path: str, prefix: str = "") -> dict:
    """Counts the datapoints `generating.BBox` skipped, by reason and class.

    Reads `skipped.jsonl`, which `generating.Augmentations` writes for every skipped
    datapoint, and counts the labels with an `augmentations` record as the kept ones.

    Args:
        path: the generating path.
        prefix: the `prefix` of the generating `Compose`: only its `skipped.jsonl` and
            labels are read.

    Returns:
        `{"kept", "skipped", "reasons": {reason: {"count", "classes": {class: count}}}}`,
        where the reason is the `BBox` setting that skipped it (`"max_truncation"`,
        `"iou_deconflict"`, `"max_occlusion"`), and the class of an `iou_deconflict` skip
        is the two classes joined by `" + "`.

    Example:
        ```python
        skips = export.skip_stats("//dataset")
        print(skips["skipped"], "of", skips["kept"] + skips["skipped"], "skipped")
        for reason, info in skips["reasons"].items():
            print(reason, info["count"], info["classes"])
        ```
    """
    path = dataset_path(path)
    reasons = {}
    lines = skipped_lines(path, prefix)
    for line in lines:
        skip = line.get("skip") or {}
        reason = reasons.setdefault(skip.get("reason", "unknown"), {"count": 0, "classes": {}})
        reason["count"] += 1
        name = skip.get("class")
        name = " + ".join(name) if isinstance(name, list) else name
        if name is not None:
            reason["classes"][name] = reason["classes"].get(name, 0) + 1
    ordered = dict(sorted(reasons.items(), key=lambda item: -item[1]["count"]))
    return {"kept": len(augmentation_records(path, prefix)), "skipped": len(lines), "reasons": ordered}


def augmentation_report(path: str, output: Optional[str] = None, graph_file: str = "augmentations.json",
                        prefix: str = "") -> str:
    """Writes an HTML page with the augmentation graph and how the dataset followed it.

    The augmentations are drawn as a flowchart: the steps of a `Compose` or `Chain` run
    left to right in a frame, a `OneOf` forks to its branches (with their weight and
    share) and they join again, and lines are as thick as the probability that they are
    reached. Each card has a bar with how often it runs,
    expected (the graph) and observed (the dataset, `augmentation_stats`), and for a
    `Menu`, `MaterialSlot` or `UVMap` with its options listed, a `Boolean` or a
    `Visibility`, one per value. A difference more than 3 standard errors from
    chance (e.g. moved objects skipped more often by `BBox` because they leave the
    frame) is marked. The datapoints `BBox` skipped are counted by reason (truncation,
    IoU, occlusion) and class, and a card's "skips" bar, centered on 0, shows how much
    more (red, right, +) or less (green, left, −) often the datapoint was skipped when the
    augmentation ran than when it didn't, which shows the augmentations that cause skips. Clicking a card shows its settings and
    numbers. The page is one
    file that needs no internet, to open in any browser.

    Args:
        path: the generating path, with a `generating.Augmentations` step's output.
        output: file to write, `<path>/<prefix>augmentations.html` by default.
        graph_file: file name of the graph, as given to `generating.Augmentations`.
        prefix: the `prefix` of the generating `Compose`: the report covers that run.

    Returns:
        The path of the HTML file.

    Raises:
        FileNotFoundError: there is no graph file in the folder.

    Example:
        ```python
        export.augmentation_report("//dataset")   # open dataset/augmentations.html in a browser
        ```
    """
    path = dataset_path(path)
    output = os.path.join(path, f"{prefix}augmentations.html") if output is None else dataset_path(output)
    title = f"Augmentations: {os.path.basename(path.rstrip(os.sep)) or path}{f' ({prefix})' if prefix else ''}"
    write_report(output, title, path, load_graph(path, graph_file, prefix), augmentation_stats(path, graph_file, prefix),
                 len(augmentation_records(path, prefix)), skip_stats(path, prefix))
    return output


def write_report(output, title, path, roots, stats=None, datapoints=None, skips=None):
    """Writes the report page for graph roots; without stats it is a preview of the
    graph alone (augmentations.graph_preview)."""
    data = {"title": title, "path": path or "", "preview": stats is None, "datapoints": datapoints,
            "roots": roots, "stats": stats or {}, "skips": skips}
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "report.html")) as file:
        template = file.read()
    # "</" would end the script element the data is in
    page = template.replace("__DATA__", json.dumps(data).replace("</", "<\\/"))
    with open(output, "w") as file:
        file.write(page)
