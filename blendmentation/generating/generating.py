"""Save a datapoint per call: the render, passes, AOVs, masks, preview images and a
JSON label, `<index>.json`.

The steps can be listed in any order; they always run as: label steps (`BBox`,
`Pose`, `RotationMatrix`, `OutputField`, `CameraData`, `Keypoints`), so a datapoint skipped
by `BBox` (`iou_deconflict`, `max_truncation`, `max_occlusion`) is never rendered,
then `Background`, then `Render`, then `AOVToImage`, `Passes` and `BBoxImage`, then `Segmentation`, then
`SegmentationImage`. All steps share one render, except that `Segmentation` with EEVEE or
Workbench adds a flat Workbench render, reusing the one of `BBox(max_occlusion=...)` when
both have the same classes, and `Segmentation(full_masks=True)` adds the renders of the
masks that ignore occlusion. `Pose` saves the 6D pose of every instance, and with full
masks, `CameraData` and the `Depth` pass, `export.bop` writes a BOP dataset.
`Background` puts a random color, noise or image behind the render and the preview
images.

`BBox` and `Segmentation` take classes as `{class name: [instances]}`, where an
instance is an object, or a sublist of objects labeled as one:
`{"car": [car_1, car_2], "table": [[table_top, table_legs]]}`. A class can also be a
dict with its own `BBox` skip settings, `{"instances": [...], "max_truncation": 0.5}`,
which win over the `BBox` arguments; `Segmentation` ignores them, so both steps can
share one classes dict.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Literal, Optional, Union

from . import bpy_generating as bpy_g

if TYPE_CHECKING:
    from bpy.types import Object  # pyright: ignore[reportMissingModuleSource]  (bpy.types only exists at runtime)

#: an object, or a sublist of objects labeled as one instance
Instances = Sequence[Union["Object", Sequence["Object"]]]
#: {class name: [instances] | {"instances": [instances], "iou_deconflict": ..., "max_truncation": ..., "max_occlusion": ...}}
Classes = dict[str, Union[Instances, dict[str, Any]]]
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
    None. Objects hidden in the render (e.g. by `Visibility`) are left out of the box,
    and an instance with all its objects hidden gets None and never skips the datapoint.

    In the label JSON: `"bboxes": [{"class", "objects", "bbox"}]`.

    Args:
        classes: `{class name: [instances]}`, an instance is an object or a sublist of
            objects labeled as one (its box is the union of the members). A class can
            instead be `{"instances": [instances], "iou_deconflict": ...,
            "max_truncation": ..., "max_occlusion": ...}` to set its own skip settings
            (all keys but `instances` optional).
        iou_deconflict: skip the datapoint, before rendering, when any two boxes
            overlap more than this IoU. None = never skip.
        max_truncation: skip the datapoint, before rendering, when more than this
            fraction (0-1) of any instance's box is out of frame, measured on its
            unclipped box. An instance partly behind the camera or fully out of frame
            counts as 1, so `max_truncation=0` keeps only datapoints with every instance
            fully in frame. None = never skip.
        max_occlusion: skip the datapoint, before the beauty render, when objects in no
            class (clutter, distractors) cover more than this fraction (0-1) of any
            instance: 1 - its visible pixels / its pixels with those objects hidden.
            Class objects covering each other don't count (that is `iou_deconflict`),
            and an instance with no pixels even then (out of frame, or behind another
            class object) counts as 0. None = never skip.

    Note:
        The `BBox` arguments are the defaults for every class. A setting a class sets
        in its dict wins over them, even when it is None (no limit for that class).
        A class's `max_truncation` and `max_occlusion` apply to each of its instances.
        `max_occlusion` is measured with two flat Workbench renders, whatever the
        engine, which only run when some class has a limit and the cheaper checks
        passed; Workbench draws every object solid, so transparent and alpha-clipped
        objects occlude fully, shader displacement is missing, and shadows don't count. For
        `iou_deconflict`, two boxes conflict when their IoU is over the lower of their
        two classes' limits, so a strict class can't overlap anything, and a pair is
        only free when both classes have no limit.

    Example:
        ```python
        classes = {
            "car": [car_1, car_2],                                       # uses the BBox arguments
            "table": {"instances": [[top, legs]], "max_truncation": 0.8},  # may be cut more
        }
        generating.BBox(classes, iou_deconflict=0.5, max_truncation=0.3, max_occlusion=0.5)
        ```
    """

    stage = 0

    def __init__(
        self,
        classes: Classes,
        iou_deconflict: Optional[float] = None,
        max_truncation: Optional[float] = None,
        max_occlusion: Optional[float] = None,
    ):
        self.classes = classes
        self.iou_deconflict = iou_deconflict
        self.max_truncation = max_truncation
        self.max_occlusion = max_occlusion

    def _settings(self):
        return {
            "iou_deconflict": self.iou_deconflict,
            "max_truncation": self.max_truncation,
            "max_occlusion": self.max_occlusion,
        }

    def check(self):
        bpy_g.check_bboxes(self.classes, self._settings())

    def __call__(self, frame):
        return bpy_g.bboxes(frame, self.classes, self._settings())


class RotationMatrix:
    """Adds the rotation of every object relative to the camera to the label, as a 3x3
    matrix with the scale removed, in Blender's camera axes (x right, y up, looking down
    -z). For the full 6D pose in OpenCV axes, use `Pose`.

    In the label JSON: `"rotation_matrices": [{"object", "rotation_matrix"}]`.

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


class Pose:
    """Adds the 6D pose of every instance relative to the camera to the label, for pose
    estimation.

    `R` (3x3, row by row) and `t` map the instance's local coordinates to the camera's,
    in OpenCV axes (x right, y down, z forward), like `CameraData`'s
    `extrinsics_opencv`: `p_camera = R @ p_local + t`. `t` is in Blender units (see
    `unit_scale` in `CameraData`). The object's scale is not in `R`; it is saved as
    `scale`, so a 3D model of the object should have it applied. An instance of several
    objects has the pose of its first object. An instance with all its objects hidden in
    the render (e.g. by `Visibility`) has `R`, `t` and `scale` None.

    In the label JSON: `"poses": [{"class", "objects", "R", "t", "scale"}]`.

    Args:
        classes: `{class name: [instances]}`, the same dict as for `BBox` and
            `Segmentation`; skip settings in a class dict are ignored. `export.bop`
            matches poses with masks by class and objects.

    Example:
        ```python
        generating.Pose({"car": [car_1, car_2]})
        ```
    """

    stage = 0

    def __init__(self, classes: Classes):
        self.classes = classes

    def check(self):
        bpy_g.check_poses(self.classes)

    def __call__(self, frame):
        bpy_g.poses(frame, self.classes)


class OutputField:
    """Saves the value at a data path to the label.

    Values are converted to JSON: datablocks become their name, menus the option name,
    and vectors and matrices nested lists.

    In the label JSON: `name: value` for an absolute path, `name: {object name: value}`
    for a relative one.

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
    `aperture_blades`, `aperture_rotation` in radians and `aperture_ratio`), and
    `unit_scale`, the scene's meters per Blender unit (Scene Properties > Units > Unit
    Scale), which every distance in the labels and the `Depth` pass is multiplied by to
    get meters.
    `focus_distance` is the distance to the focal plane Blender uses, so with a focus
    object it is the distance to that object along the view axis.
    A perspective camera adds the lens, sensor size and fit and `intrinsics` (3x3 `K` in
    pixels, top-left image origin, with sensor fit, lens shift and pixel aspect), an
    orthographic one `ortho_scale`.

    In the label JSON: `"camera": {...}`.

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
    it is in frame and a ray cast from the camera reaches it. Objects hidden in the render
    don't block the ray, and a point on one of them is not visible.

    In the label JSON: `"keypoints": [{"name", "position", "depth", "in_frame",
    "visible"}]`, with `position` in pixels from the top-left corner and `depth` along the
    view axis.

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


class Background:
    """Puts a random background behind the transparent render, in the image of `Render`
    and in the preview images (`BBoxImage`, `SegmentationImage`).

    Each datapoint picks one mode by the weights:

    - `"color"`: one random uniform color.
    - `"white_noise"`: random gray square cells.
    - `"color_noise"`: random colored square cells.
    - `"image"`: a random image from `images_path` (and its subfolders; PNG, JPEG, BMP,
      TIFF or TGA), scaled to cover the frame and cropped at a random position.

    The render must be transparent: Render Properties > Film > Transparent on, and the
    output color RGBA. The background is composited over the render's alpha, so the
    labels (masks, boxes), AOVs and passes don't change. The image is then saved
    opaque as RGB: 8-bit for PNG and JPEG, 32-bit float for EXR (the background
    converted from sRGB to linear). The backgrounds come from Python's `random`, so
    `random.seed()` makes them reproducible.

    In the label JSON: `"background": {"mode", ...}` with `"color": [r, g, b]` (0-255),
    `"noise_size"` (pixels) or `"image"` (path relative to `images_path`).

    Args:
        weights: `{mode: weight}`, the relative probability of each mode. A mode with
            weight 0 or left out is never used. None = equal weights for all modes, the
            image mode only with `images_path`.
        noise_size: `(min, max)` size of the noise cells in pixels, sampled per
            datapoint; 1 is per-pixel noise.
        images_path: folder of background images, only needed when `"image"` has a
            weight above 0 (or `weights` is None and you want images). Without
            images, leave it None and give `"image"` weight 0 or leave it out. `//`
            paths are relative to the .blend file.

    Raises:
        ValueError: when generating, if the render isn't transparent RGBA, a weight or
            `noise_size` is invalid, or the image mode has no images.

    Example:
        ```python
        generating.Background(
            weights={"color": 1, "white_noise": 1, "color_noise": 1, "image": 3},
            noise_size=(1, 8),
            images_path="//backgrounds",
        )
        # no images: image weight 0 (or left out), no images_path needed
        generating.Background(weights={"color": 1, "color_noise": 2, "image": 0})
        ```
    """

    stage = 1

    def __init__(
        self,
        weights: Optional[dict[Literal["color", "white_noise", "color_noise", "image"], float]] = None,
        noise_size: tuple[float, float] = (1, 8),
        images_path: Optional[str] = None,
    ):
        self.weights = weights
        self.noise_size = noise_size
        self.images_path = images_path
        self.images = None

    def check(self):
        bpy_g.check_background(self.weights, self.noise_size, self.images_path)
        # listed once, a folder of backgrounds can be big
        if self.images is None:
            self.images = bpy_g.background_images(self.weights, self.images_path)

    def __call__(self, frame):
        bpy_g.background(frame, self.weights, self.noise_size, self.images_path, self.images)


class Render:
    """Renders the image with the scene's engine to `<index>.<ext>`.

    In the label JSON: `"image": file name`.

    Args:
        file_format: `"PNG"`, `"JPEG"` or `"OPEN_EXR"`.

    Example:
        ```python
        generating.Render("JPEG")
        ```
    """

    stage = 2

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

    In the label JSON: `"aovs": {name: file name}`, None for an AOV skipped by
    `skip_empty`.

    Args:
        names: names of the AOVs.
        file_format: `"OPEN_EXR"` (32-bit float) or `"PNG"` (8-bit, clamped to 0-1).
        skip_empty: don't write an AOV that is empty, every pixel 0 (black) in the
            render, alpha ignored; its file name in the label is None. The datapoint is
            still generated.

    Example:
        ```python
        generating.AOVToImage(["Albedo"])
        ```
    """

    stage = 3

    def __init__(
        self, names: Sequence[str], file_format: Literal["OPEN_EXR", "PNG"] = "OPEN_EXR", skip_empty: bool = False
    ):
        self.names = names
        self.file_format = file_format
        self.skip_empty = skip_empty

    def check(self):
        bpy_g.check_aov_images(self.names, self.file_format)

    def __call__(self, frame):
        bpy_g.aov_images(frame, self.names, self.file_format, self.skip_empty)


class Passes:
    """Saves built-in render passes as images `<index>_<pass>.<ext>`.

    Uses the same render as `Render` and `AOVToImage`. Each pass is enabled in the view
    layer for that render only. Cycles has all passes, EEVEE no `UV` or index passes,
    and Workbench only `Depth`; in Cycles, `Vector` also needs motion blur off. With a
    `Segmentation` step in Cycles, `ObjectIndex` holds its instance ids (1, 2, ...) and 0
    for other objects, instead of the objects' pass indices.

    In the label JSON: `"passes": {name: file name}`, None for a pass skipped by
    `skip_empty`.

    Args:
        names: any of `"Depth"`, `"Mist"`, `"Normal"`, `"Position"`, `"Vector"`,
            `"UV"`, `"ObjectIndex"` and `"MaterialIndex"`.
        file_format: `"OPEN_EXR"` (32-bit float) or `"PNG"` (8-bit, clamped to 0-1, so
            only useful for some passes like Normal).
        skip_empty: don't write a pass that is empty, every pixel 0 (black) in the
            render, alpha ignored; its file name in the label is None. The datapoint is
            still generated. `Depth` is never empty, its background is far away.

    Example:
        ```python
        generating.Passes(["Depth", "Normal"])
        ```
    """

    stage = 3

    def __init__(
        self, names: Sequence[str], file_format: Literal["OPEN_EXR", "PNG"] = "OPEN_EXR", skip_empty: bool = False
    ):
        self.names = names
        self.file_format = file_format
        self.skip_empty = skip_empty

    def check(self):
        bpy_g.check_passes(self.names, self.file_format)

    def prepare(self, frame):
        bpy_g.prepare_passes(frame, self.names)

    def __call__(self, frame):
        bpy_g.render_passes(frame, self.names, self.file_format, self.skip_empty)


class BBoxImage:
    """Saves a copy of the rendered image with the boxes of the `BBox` step drawn on it,
    to `<index>_bboxes.<ext>`, for checking the labels.

    Uses the same render as `Render`, and the main image stays clean. Each class has
    its own color, and its name is written above each box in capitals. Needs a `BBox`
    step in the same `Compose`.

    In the label JSON: `"bbox_image": file name`.

    Args:
        file_format: `"PNG"` or `"JPEG"`.
        line_width: outline width in pixels, at least 1.
        show_class: write the class name above each box.

    Example:
        ```python
        generating.BBoxImage(line_width=3)
        ```
    """

    stage = 3

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

    Objects that are not in `classes` still hide what is behind them. Files are
    `<index>_mask_<n>.png` per instance, where `n` counts instances across all classes
    (and across `Segmentation` steps of the same `Compose`), and
    `<index>_mask_<class>.png` per class, with `_2`, `_3`, ... added when that name is
    already taken (two class names that differ only in characters not allowed in file
    names, or the same class in two steps). Masks are not anti-aliased.

    With `full_masks=True`, every instance also gets a full mask
    `<index>_mask_<n>_full.png`: its whole silhouette, as if no other object were in
    the scene (BOP's `mask`, next to the visible `mask_visib`). They need extra renders
    with every other object hidden; instances whose boxes don't overlap share one, so
    a scene of separate objects needs one.

    How the masks are made depends on the render engine:

    - **Cycles**: from the Object Index pass of the beauty render, so they match it and
      cost no extra render. Each instance gets its number as pass index for that render
      (restored afterwards). A surface counts when its alpha is at least the view
      layer's Alpha Threshold (Passes > Data, default 0.5), so alpha-clipped materials
      (leaf cards, decals, fences) and shader displacement are right. Volumes are not
      in the pass, so they neither get a mask nor hide anything.
    - **EEVEE and Workbench**: one extra, fast Workbench render, which draws every
      object solid: alpha-clipped and transparent parts are in the mask and hide what is
      behind them, and shader displacement is missing. EEVEE has no Object Index pass.

    In the label JSON: `"masks": [{"class", "objects", "mask", "per"}]`, with `per`
    `"instance"` or `"class"`, and `mask` None for a mask skipped by `skip_empty`; with
    `full_masks`, also `"full_masks": [{"class", "objects", "mask"}]`.

    Args:
        classes: `{class name: [instances]}`, an instance is an object or a sublist of
            objects labeled as one (one mask). A class given as a dict with
            `"instances"` (see `BBox`) works too; its skip settings are ignored here.
        per: `"instance"`, `"class"` or `"both"`.
        skip_empty: don't write empty masks (an instance or class with no visible
            pixel: out of frame or fully hidden); its `mask` in the label is None. The
            datapoint is still generated, and export skips these instances like empty
            masks. Applies to the full masks too.
        full_masks: also save the full mask of every instance, ignoring occlusion.

    Note:
        In Cycles, a second `Segmentation` with other classes in the same `Compose`, or
        more than 32767 instances, uses the Workbench render. The full masks are rendered
        like the visible ones: in Cycles from the Object Index pass of a 1 sample render,
        otherwise with Workbench. Instances share a full mask render when their boxes
        projected from the mesh vertices are apart, so with Cycles shader displacement
        that pushes a surface far out of its mesh, a full mask may miss where another
        instance covers it.

    Example:
        ```python
        generating.Segmentation({"car": [car_1, car_2]}, per="both", skip_empty=True)
        generating.Segmentation({"car": [car_1, car_2]}, full_masks=True)  # for export.bop
        ```
    """

    stage = 4

    def __init__(
        self,
        classes: Classes,
        per: Literal["instance", "class", "both"] = "instance",
        skip_empty: bool = False,
        full_masks: bool = False,
    ):
        self.classes = classes
        self.per = per
        self.skip_empty = skip_empty
        self.full_masks = full_masks

    def check(self):
        bpy_g.check_segmentation(self.classes, self.per)

    def prepare(self, frame):
        bpy_g.prepare_segmentation(frame, self.classes)

    def __call__(self, frame):
        bpy_g.segmentation(frame, self.classes, self.per, self.skip_empty, self.full_masks)


class SegmentationImage:
    """Saves a copy of the rendered image with the masks of the `Segmentation` step drawn
    over it, to `<index>_segmentation.<ext>`, for checking the labels.

    Uses the same render as `Render`, and the main image stays clean. Each mask is
    laid over in its class color (the same as in `BBoxImage`), each instance outlined,
    and its class name written above. Instance masks are used when there are any,
    otherwise the class masks. Needs a `Segmentation` step in the same `Compose`.

    In the label JSON: `"segmentation_image": file name`.

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

    stage = 5

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
