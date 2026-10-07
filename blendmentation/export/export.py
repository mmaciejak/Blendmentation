"""Exports a generated dataset to standard formats: COCO, YOLO, Pascal VOC and BOP.

Run it once after generating, on the generating path. It reads the `<index>.json`
labels; only datapoints with an image (a `Render` step) and with bboxes or instance
masks are exported (with poses for BOP). It works without Blender. COCO with masks,
`bbox_from="mask"` and BOP with masks or depth need numpy and OpenImageIO, which come
with Blender.

COCO, YOLO and VOC take `bbox_from`, which box to write:

- `"label"` (default): the `BBox` step's boxes, around the whole object including the
  parts hidden behind other objects (amodal). Needs a `BBox` step.
- `"mask"`: the extent of the visible pixels of each instance mask (modal), the boxes
  most detection datasets use. Needs a `Segmentation` step with `per="instance"` or
  `"both"` and the same classes. Fully hidden instances, and ones whose mask was not
  written (`skip_empty`), are left out.
"""

import json
import os
import re
import shutil
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


def label_boxes(path, label, bbox_from):
    """(class, objects, [x_min, y_min, x_max, y_max]) of every instance of a label with a
    box in the frame: the BBox boxes, or with bbox_from="mask" the visible pixels' extent."""
    if bbox_from == "label":
        return [(entry["class"], entry["objects"], entry["bbox"]) for entry in label.get("bboxes", [])
                if entry["bbox"] is not None]
    boxes = []
    for (class_name, objects), instance in instances(label).items():
        if "mask" not in instance:
            raise ValueError(
                f'bbox_from="mask" needs instance masks, {label["image"]} has none for {list(objects)}: '
                'add Segmentation(classes, per="instance") with the classes of BBox'
            )
        if instance["mask"] is None:
            # empty mask not written, Segmentation(skip_empty=True)
            continue
        box = mask_bbox(read_mask(path, instance["mask"]))
        if box is not None:
            boxes.append((class_name, list(objects), box))
    return boxes


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


def yolo(path: str, classes: Optional[Sequence[str]] = None, bbox_from: Literal["label", "mask"] = "label") -> str:
    """Writes YOLO detection labels.

    Writes `<index>.txt` next to every image, with one `class x_center y_center width
    height` line per bbox (normalized to 0-1), `classes.txt`, and `dataset.yaml`
    pointing at the folder, ready for Ultralytics.

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
    are the datapoint indices, and `obj_id`s number the classes from 1.

    Every datapoint needs `Pose` and `CameraData` with a perspective camera. For masks
    and `scene_gt_info.json`, add `Segmentation(classes, full_masks=True)` with the
    classes of `Pose`, and for depth `Passes(["Depth"])`. Instances hidden in the render
    are left out. Distances are converted to mm with the camera's `unit_scale`.

    Note:
        BOP has one 3D model per class (`models/obj_<obj_id>.ply` in mm, not written
        here), and a pose is relative to that model's origin. So every object of a class
        must have the same mesh, origin and scale; the export raises when the scales
        differ, so keep `Scale` augmentations off the posed objects. An instance of
        several objects has the pose of its first object. Depth outside the 16-bit
        range (65535 x `depth_scale` mm) and the background are 0.

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
