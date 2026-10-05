"""Save a datapoint per call: the render, passes, AOVs, masks, preview images and a
JSON label, `<index>.json`.

The steps can be listed in any order; they always run as: label steps (`BBox`,
`RotationMatrix`, `OutputField`, `CameraData`, `Keypoints`), so a datapoint skipped
by `iou_deconflict` is never rendered, then `Render`, then `AOVToImage`, `Passes`
and `BBoxImage`, then `Segmentation`, then `SegmentationImage`. All steps except
`Segmentation` share one render.

`BBox` and `Segmentation` take classes as `{class name: [instances]}`, where an
instance is an object, or a sublist of objects labeled as one:
`{"car": [car_1, car_2], "table": [[table_top, table_legs]]}`.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Literal, Optional, Union

from . import bpy_generating as bpy_g

if TYPE_CHECKING:
    from bpy.types import Object  # pyright: ignore[reportMissingModuleSource]  (bpy.types only exists at runtime)

#: {class name: [instances]}, an instance is an object or a sublist of objects labeled as one
Classes = dict[str, Sequence[Union["Object", Sequence["Object"]]]]
#: an object, (mesh object, vertex index or vertex group), (armature, bone name) or a point (x, y, z)
KeypointSource = Union["Object", tuple["Object", Union[int, str]], tuple[float, float, float]]


class Compose:
    """Generates one datapoint per call from a list of steps.

    Every call saves `<index>.png`, `<index>.json` with the labels, and the files of
    the other steps to `path`. The index is the next free number in the folder, so
    generating can be stopped and resumed. The settings of every step are checked
    before anything renders, and the scene's resolution and output settings are
    restored afterwards.

    Args:
        steps: generating steps, in any order.
        path: folder to save the files to. `//` paths are relative to the .blend file,
            and `""` is its folder.
        resolution: `(width, height)` of the images in pixels.

    Raises:
        ValueError: a `BBoxImage` without a `BBox` step, or a `SegmentationImage`
            without a `Segmentation` step.

    Example:
        ```python
        classes = {"car": [car_1, car_2], "table": [[table_top, table_legs]]}
        generator = generating.Compose(
            [generating.Render(), generating.BBox(classes), generating.Segmentation(classes)],
            path="//dataset",
            resolution=(640, 480),
        )
        generator()
        ```
    """

    def __init__(self, steps: Sequence[Any], path: str, resolution: tuple[int, int]):
        for preview, source in ((BBoxImage, BBox), (SegmentationImage, Segmentation)):
            if any(isinstance(step, preview) for step in steps) and not any(isinstance(step, source) for step in steps):
                raise ValueError(f"{preview.__name__} draws the output of a {source.__name__} step, add {source.__name__} to the steps")
        self.steps = sorted(steps, key=lambda step: step.stage)
        self.path = path
        self.resolution = resolution

    def __call__(self, custom_dict: Optional[dict[str, Any]] = None) -> bool:
        """Generates one datapoint.

        Args:
            custom_dict: extra keys and values to save in the label.

        Returns:
            False if the datapoint was skipped, e.g. by `BBox(iou_deconflict=...)`,
                otherwise True.
        """
        return bpy_g.generate(self.path, self.resolution, self.steps, custom_dict)

    def preview(self, scaling_factor: float, custom_dict: Optional[dict[str, Any]] = None) -> bool:
        """Generates one datapoint at a reduced resolution, for a quick look.

        Args:
            scaling_factor: the resolution is divided by this factor.
            custom_dict: extra keys and values to save in the label.

        Returns:
            False if the datapoint was skipped, otherwise True.
        """
        resolution = tuple(int(size / scaling_factor) for size in self.resolution)
        return bpy_g.generate(self.path, resolution, self.steps, custom_dict)


class BBox:
    """Adds the 2D bounding box of every instance to the label.

    Boxes are in pixels as `[x_min, y_min, x_max, y_max]` from the top-left corner,
    computed from the evaluated geometry (modifiers included). Occlusion is not taken
    into account, so hidden parts are inside the box. An instance out of frame gets
    None.

    Label: `"bboxes": [{"class", "objects", "bbox"}]`.

    Args:
        classes: `{class name: [instances]}`, an instance is an object or a sublist of
            objects labeled as one (its box is the union of the members).
        iou_deconflict: skip the datapoint, before rendering, when any two boxes
            overlap more than this IoU. None = never skip.

    Example:
        ```python
        generating.BBox({"car": [car_1, car_2], "table": [[top, legs]]}, iou_deconflict=0.5)
        ```
    """

    stage = 0

    def __init__(self, classes: Classes, iou_deconflict: Optional[float] = None):
        self.classes = classes
        self.iou_deconflict = iou_deconflict

    def check(self):
        bpy_g.to_instances(self.classes)

    def __call__(self, frame):
        return bpy_g.bboxes(frame, self.classes, self.iou_deconflict)


class RotationMatrix:
    """Adds the rotation of every object relative to the camera to the label, as a 3x3
    matrix with the scale removed.

    Label: `"rotation_matrices": [{"object", "rotation_matrix"}]`.

    Args:
        objects: objects to save the rotation of.

    Example:
        ```python
        generating.RotationMatrix([car_1, car_2])
        ```
    """

    stage = 0

    def __init__(self, objects: Sequence[Object]):
        self.objects = objects

    def check(self):
        bpy_g.check_objects(self.objects, "RotationMatrix")

    def __call__(self, frame):
        bpy_g.rotation_matrices(frame, self.objects)


class OutputField:
    """Saves the value at a data path to the label.

    Values are converted to JSON: datablocks become their name, menus the option name,
    and vectors and matrices nested lists.

    Label: `name: value` for an absolute path, `name: {object name: value}` for a
    relative one.

    Args:
        name: key in the label.
        data_path: path to the value. Paths starting with `bpy.` are absolute (right
            click > Copy Full Data Path) and saved once, e.g.
            `'bpy.data.lights["Light"].energy'`. Other paths are relative to `objects`.
        objects: objects a relative path is resolved on.

    Example:
        ```python
        generating.OutputField("light_energy", 'bpy.data.lights["Light"].energy')
        generating.OutputField("smile", 'data.shape_keys.key_blocks["Smile"].value', objects=[face])
        ```
    """

    stage = 0

    def __init__(self, name: str, data_path: str, objects: Optional[Sequence[Object]] = None):
        self.name = name
        self.data_path = data_path
        self.objects = objects

    def check(self):
        bpy_g.check_output_field(self.name, self.data_path, self.objects)

    def __call__(self, frame):
        bpy_g.output_field(frame, self.name, self.data_path, self.objects)


class CameraData:
    """Adds the active camera to the label.

    Saves the name, type, `matrix_world`, `extrinsics_opencv` (3x4 world-to-camera
    `[R|t]` with OpenCV axes: x right, y down, z forward), `clip_start`, `clip_end` and
    `depth_of_field` (`use_dof`, `focus_object`, `focus_distance`, `f_stop`,
    `aperture_blades`, `aperture_rotation` in radians and `aperture_ratio`).
    `focus_distance` is the distance to the focal plane Blender uses, so with a focus
    object it is the distance to that object along the view axis.
    A perspective camera adds the lens, sensor size and fit and `intrinsics` (3x3 `K` in
    pixels, top-left image origin, with sensor fit, lens shift and pixel aspect), an
    orthographic one `ortho_scale`.

    Label: `"camera": {...}`.

    Example:
        ```python
        generating.CameraData()
        ```
    """

    stage = 0

    def __call__(self, frame):
        bpy_g.camera_data(frame)


class Keypoints:
    """Projects 3D points to the image and adds them to the label.

    Vertices include deformations from armatures and modifiers. A point is visible when
    it is in frame and a ray cast from the camera reaches it.

    Label: `"keypoints": [{"name", "position", "depth", "in_frame", "visible"}]`, with
    `position` in pixels from the top-left corner and `depth` along the view axis.

    Args:
        points: `{name: source}`, where a source is an object (its origin),
            `(mesh object, vertex index)`, `(mesh object, "vertex group")` for the center
            of the group, `(armature, "bone")` for the bone head, or a point `(x, y, z)`.

    Example:
        ```python
        generating.Keypoints({"car_origin": car_1, "car_corner": (car_1, 0), "hand": (rig, "hand.L")})
        ```
    """

    stage = 0

    def __init__(self, points: dict[str, KeypointSource]):
        self.points = points

    def check(self):
        bpy_g.check_keypoints(self.points)

    def __call__(self, frame):
        bpy_g.keypoints(frame, self.points)


class Render:
    """Renders the image with the scene's engine to `<index>.<ext>`.

    Label: `"image": file name`.

    Args:
        file_format: `"PNG"`, `"JPEG"` or `"OPEN_EXR"`.

    Example:
        ```python
        generating.Render("JPEG")
        ```
    """

    stage = 1

    def __init__(self, file_format: Literal["PNG", "JPEG", "OPEN_EXR"] = "PNG"):
        self.file_format = file_format

    def check(self):
        bpy_g.check_render(self.file_format)

    def __call__(self, frame):
        bpy_g.render_image(frame, self.file_format)


class AOVToImage:
    """Saves shader AOVs as images `<index>_<aov>.<ext>`.

    Uses the same render as `Render`; without `Render`, the scene is still rendered
    once, but no image is saved. Each AOV must be added in View Layer Properties >
    Passes > Shader AOV, and the engine must be Cycles or EEVEE.

    Label: `"aovs": {name: file name}`.

    Args:
        names: names of the AOVs.
        file_format: `"OPEN_EXR"` (32-bit float) or `"PNG"` (8-bit, clamped to 0-1).

    Example:
        ```python
        generating.AOVToImage(["Albedo"])
        ```
    """

    stage = 2

    def __init__(self, names: Sequence[str], file_format: Literal["OPEN_EXR", "PNG"] = "OPEN_EXR"):
        self.names = names
        self.file_format = file_format

    def check(self):
        bpy_g.check_aov_images(self.names, self.file_format)

    def __call__(self, frame):
        bpy_g.aov_images(frame, self.names, self.file_format)


class Passes:
    """Saves built-in render passes as images `<index>_<pass>.<ext>`.

    Uses the same render as `Render` and `AOVToImage`. Each pass is enabled in the view
    layer for that render only. Cycles has all passes, EEVEE no `UV` or index passes,
    and Workbench only `Depth`; in Cycles, `Vector` also needs motion blur off.

    Label: `"passes": {name: file name}`.

    Args:
        names: any of `"Depth"`, `"Mist"`, `"Normal"`, `"Position"`, `"Vector"`,
            `"UV"`, `"ObjectIndex"` and `"MaterialIndex"`.
        file_format: `"OPEN_EXR"` (32-bit float) or `"PNG"` (8-bit, clamped to 0-1, so
            only useful for some passes like Normal).

    Example:
        ```python
        generating.Passes(["Depth", "Normal"])
        ```
    """

    stage = 2

    def __init__(self, names: Sequence[str], file_format: Literal["OPEN_EXR", "PNG"] = "OPEN_EXR"):
        self.names = names
        self.file_format = file_format

    def check(self):
        bpy_g.check_passes(self.names, self.file_format)

    def prepare(self, frame):
        bpy_g.prepare_passes(frame, self.names)

    def __call__(self, frame):
        bpy_g.render_passes(frame, self.names, self.file_format)


class BBoxImage:
    """Saves a copy of the rendered image with the boxes of the `BBox` step drawn on it,
    to `<index>_bboxes.<ext>`, for checking the labels.

    Uses the same render as `Render`, and the main image stays clean. Each class has
    its own color, and its name is written above each box in capitals. Needs a `BBox`
    step in the same `Compose`.

    Label: `"bbox_image": file name`.

    Args:
        file_format: `"PNG"` or `"JPEG"`.
        line_width: outline width in pixels, at least 1.
        show_class: write the class name above each box.

    Example:
        ```python
        generating.BBoxImage(line_width=3)
        ```
    """

    stage = 2

    def __init__(self, file_format: Literal["PNG", "JPEG"] = "PNG", line_width: int = 2, show_class: bool = True):
        self.file_format = file_format
        self.line_width = line_width
        self.show_class = show_class

    def check(self):
        bpy_g.check_preview("BBoxImage", self.file_format, self.line_width, 1)

    def __call__(self, frame):
        bpy_g.bbox_image(frame, self.file_format, self.line_width, self.show_class)


class Segmentation:
    """Saves black-and-white masks of the visible pixels of every instance or class.

    Uses one extra, fast Workbench render whatever the engine. Objects that are not in
    `classes` still hide what is behind them. Files are `<index>_mask_<n>.png` per
    instance, where `n` counts instances across all classes, and
    `<index>_mask_<class>.png` per class.

    Label: `"masks": [{"class", "objects", "mask", "per"}]`, with `per` `"instance"`
    or `"class"`.

    Args:
        classes: `{class name: [instances]}`, an instance is an object or a sublist of
            objects labeled as one (one mask).
        per: `"instance"`, `"class"` or `"both"`.

    Example:
        ```python
        generating.Segmentation({"car": [car_1, car_2]}, per="both")
        ```
    """

    stage = 3

    def __init__(self, classes: Classes, per: Literal["instance", "class", "both"] = "instance"):
        self.classes = classes
        self.per = per

    def check(self):
        bpy_g.check_segmentation(self.classes, self.per)

    def __call__(self, frame):
        bpy_g.segmentation(frame, self.classes, self.per)


class SegmentationImage:
    """Saves a copy of the rendered image with the masks of the `Segmentation` step drawn
    over it, to `<index>_segmentation.<ext>`, for checking the labels.

    Uses the same render as `Render`, and the main image stays clean. Each mask is
    laid over in its class color (the same as in `BBoxImage`), each instance outlined,
    and its class name written above. Instance masks are used when there are any,
    otherwise the class masks. Needs a `Segmentation` step in the same `Compose`.

    Label: `"segmentation_image": file name`.

    Args:
        file_format: `"PNG"` or `"JPEG"`.
        opacity: how much the class color covers the image, 0-1.
        line_width: outline width in pixels, 0 = no outline.
        show_class: write the class name above each mask.

    Example:
        ```python
        generating.SegmentationImage(opacity=0.4)
        ```
    """

    stage = 4

    def __init__(self, file_format: Literal["PNG", "JPEG"] = "PNG", opacity: float = 0.5, line_width: int = 2,
                 show_class: bool = True):
        self.file_format = file_format
        self.opacity = opacity
        self.line_width = line_width
        self.show_class = show_class

    def check(self):
        bpy_g.check_preview("SegmentationImage", self.file_format, self.line_width, 0, self.opacity)

    def prepare(self, frame):
        frame.keep_beauty = True

    def __call__(self, frame):
        bpy_g.segmentation_image(frame, self.file_format, self.opacity, self.line_width, self.show_class)
