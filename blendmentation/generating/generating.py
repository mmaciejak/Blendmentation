"""Generating steps, composed like augmentations.

Steps run in a fixed order whatever order they are given in: labels (BBox,
RotationMatrix, OutputField, CameraData, Keypoints) first, so a skipped datapoint
is never rendered, then Render, AOVToImage / Passes / BBoxImage, Segmentation and
SegmentationImage. Render, AOVToImage, Passes, BBoxImage and SegmentationImage share one render.

BBox and Segmentation take classes as {class name: [instances]}, an instance is
an object, or a sublist of objects labeled as one object, e.g.
{"car": [car_1, car_2], "table": [[table_top, table_leg_1, table_leg_2]]}
"""

from . import bpy_generating as bpy_g


class Compose:
    """Compose generating steps together. Every call generates one datapoint in path:
    <index>.png, <index>.json with the labels, and the files of the other steps.

    Args:
        steps (list): list of generating steps
        path (str): directory to save the files to, "" = next to the .blend file
        resolution (tuple): (width, height) of the images
    """

    def __init__(self, steps, path, resolution):
        for preview, source in ((BBoxImage, BBox), (SegmentationImage, Segmentation)):
            if any(isinstance(step, preview) for step in steps) and not any(isinstance(step, source) for step in steps):
                raise ValueError(f"{preview.__name__} draws the output of a {source.__name__} step, add {source.__name__} to the steps")
        self.steps = sorted(steps, key=lambda step: step.stage)
        self.path = path
        self.resolution = resolution

    def __call__(self, custom_dict: dict = None):
        """Generates one datapoint.

        Args:
            custom_dict (dict): optional - key and values to be saved in the labels,
            apart from the standard data

        Returns:
            bool: False if the datapoint was skipped, e.g. by BBox iou_deconflict
        """
        return bpy_g.generate(self.path, self.resolution, self.steps, custom_dict)

    def preview(self, scaling_factor, custom_dict: dict = None):
        """Generates one datapoint with limited image resolution, for a quick preview.

        Args:
            scaling_factor (float): resolution is divided by this factor
        """
        resolution = tuple(int(size / scaling_factor) for size in self.resolution)
        return bpy_g.generate(self.path, resolution, self.steps, custom_dict)


class BBox:
    """Adds the 2D bbox of every instance to the labels under "bboxes", as
    {"class", "objects", "bbox"}. bbox is in pixels [x_min, y_min, x_max, y_max]
    from the top left corner, None when out of frame. Occlusion by other objects
    is not taken into account.

    Args:
        classes (dict): {class name: [objects or sublists of objects]}
        iou_deconflict (float): datapoints with bboxes overlapping over this IoU
            are skipped, before rendering. None = never skip
    """

    stage = 0

    def __init__(self, classes: dict, iou_deconflict: float = None):
        self.classes = classes
        self.iou_deconflict = iou_deconflict

    def check(self):
        bpy_g.to_instances(self.classes)

    def __call__(self, frame):
        return bpy_g.bboxes(frame, self.classes, self.iou_deconflict)


class RotationMatrix:
    """Adds the 3x3 rotation of every object relative to the camera to the labels
    under "rotation_matrices", as {"object", "rotation_matrix"}.

    Args:
        objects (list): objects to output the rotation of
    """

    stage = 0

    def __init__(self, objects: list):
        self.objects = objects

    def check(self):
        bpy_g.check_objects(self.objects, "RotationMatrix")

    def __call__(self, frame):
        bpy_g.rotation_matrices(frame, self.objects)


class OutputField:
    """Saves the value at a data path to the labels under name.

    Args:
        name (str): key in the labels
        data_path (str): path to the value. Paths starting with "bpy." are absolute
            (right click > Copy Full Data Path) and saved once, e.g.
            'bpy.data.lights["Light"].energy'. Other paths are relative to objects
            and saved as {object name: value}, e.g. 'data.shape_keys.key_blocks["Key 1"].value'
        objects (list): objects relative paths are resolved on
    """

    stage = 0

    def __init__(self, name: str, data_path: str, objects: list = None):
        self.name = name
        self.data_path = data_path
        self.objects = objects

    def check(self):
        bpy_g.check_output_field(self.name, self.data_path, self.objects)

    def __call__(self, frame):
        bpy_g.output_field(frame, self.name, self.data_path, self.objects)


class CameraData:
    """Adds the active camera to the labels under "camera": name, type, matrix_world,
    extrinsics_opencv (3x4 world to camera [R|t], OpenCV axes: x right, y down, z forward),
    clip_start and clip_end, and for perspective cameras lens, sensor size and fit and
    intrinsics (3x3 K in pixels, top-left image origin), for orthographic ones ortho_scale."""

    stage = 0

    def __call__(self, frame):
        bpy_g.camera_data(frame)


class Keypoints:
    """Projects 3D points to the image and adds them to the labels under "keypoints", as
    {"name", "position", "depth", "in_frame", "visible"}. position is in pixels from the
    top left corner, depth the distance along the camera view, visible is False when
    the point is out of frame or hidden behind geometry.

    Args:
        points (dict) : {name: source}, a source is an object (its origin),
            (mesh object, vertex index), (mesh object, "vertex group") for the center
            of the group, (armature, "bone") for the bone head, or a point (x, y, z).
            Vertices include deformations like armatures and modifiers.
    """

    stage = 0

    def __init__(self, points: dict):
        self.points = points

    def check(self):
        bpy_g.check_keypoints(self.points)

    def __call__(self, frame):
        bpy_g.keypoints(frame, self.points)


class Render:
    """Renders the image with the scene engine to <index>.<ext>.

    Args:
        file_format (str): "PNG", "JPEG" or "OPEN_EXR"
    """

    stage = 1

    def __init__(self, file_format: str = "PNG"):
        self.file_format = file_format

    def check(self):
        bpy_g.check_render(self.file_format)

    def __call__(self, frame):
        bpy_g.render_image(frame, self.file_format)


class AOVToImage:
    """Saves shader AOVs as images <index>_<aov>.<ext>. Uses the same render as Render,
    without Render it renders the scene once without saving the image. Needs Cycles or EEVEE.

    Args:
        names (list): names of the AOVs, as in View Layer > Passes > Shader AOV
        file_format (str): "OPEN_EXR" (32 bit float) or "PNG" (8 bit, values clamped to 0-1)
    """

    stage = 2

    def __init__(self, names: list, file_format: str = "OPEN_EXR"):
        self.names = names
        self.file_format = file_format

    def check(self):
        bpy_g.check_aov_images(self.names, self.file_format)

    def __call__(self, frame):
        bpy_g.aov_images(frame, self.names, self.file_format)


class Passes:
    """Saves built-in render passes as images <index>_<pass>.<ext>. Uses the same render
    as Render and AOVToImage, the passes are enabled in the view layer only for that render.

    Args:
        names (list): any of "Depth", "Mist", "Normal", "Position", "Vector", "UV",
            "ObjectIndex", "MaterialIndex". Engines support different passes, EEVEE has
            no UV and index passes, workbench only Depth
        file_format (str): "OPEN_EXR" (32 bit float) or "PNG" (8 bit, values clamped
            to 0-1, so only useful for some passes like Normal)
    """

    stage = 2

    def __init__(self, names: list, file_format: str = "OPEN_EXR"):
        self.names = names
        self.file_format = file_format

    def check(self):
        bpy_g.check_passes(self.names, self.file_format)

    def prepare(self, frame):
        bpy_g.prepare_passes(frame, self.names)

    def __call__(self, frame):
        bpy_g.render_passes(frame, self.names, self.file_format)


class BBoxImage:
    """Saves a copy of the rendered image with the bboxes of the BBox step drawn on it,
    to <index>_bboxes.<ext>, for checking the labels. Uses the same render as Render,
    the main image stays clean. Each class has its own color.

    Args:
        file_format (str): "PNG" or "JPEG"
        line_width (int): outline width in pixels
        show_class (bool): writes the class name above each box
    """

    stage = 2

    def __init__(self, file_format: str = "PNG", line_width: int = 2, show_class: bool = True):
        self.file_format = file_format
        self.line_width = line_width
        self.show_class = show_class

    def check(self):
        bpy_g.check_preview("BBoxImage", self.file_format, self.line_width, 1)

    def __call__(self, frame):
        bpy_g.bbox_image(frame, self.file_format, self.line_width, self.show_class)


class Segmentation:
    """Saves black and white masks of the visible pixels and adds them to the labels
    under "masks", as {"class", "objects", "mask", "per"}, per is "instance" or "class". Uses a separate, fast workbench
    render. Objects that are not in classes still occlude.

    Args:
        classes (dict): {class name: [objects or sublists of objects]}
        per (str): "instance" - a mask per instance, <index>_mask_<n>.png, n is the
            position of the instance counting through all classes, "class" - a mask per
            class, <index>_mask_<class>.png, or "both"
    """

    stage = 3

    def __init__(self, classes: dict, per: str = "instance"):
        self.classes = classes
        self.per = per

    def check(self):
        bpy_g.check_segmentation(self.classes, self.per)

    def __call__(self, frame):
        bpy_g.segmentation(frame, self.classes, self.per)


class SegmentationImage:
    """Saves a copy of the rendered image with the masks of the Segmentation step drawn
    over it, to <index>_segmentation.<ext>, for checking the labels. Uses the same render
    as Render, the main image stays clean. Each class has its own color, the same as in
    BBoxImage, and each instance is outlined (with only class masks, each class).

    Args:
        file_format (str): "PNG" or "JPEG"
        opacity (float): 0-1, how much the class color covers the image
        line_width (int): outline width in pixels, 0 = no outline
        show_class (bool): writes the class name above each mask
    """

    stage = 4

    def __init__(self, file_format: str = "PNG", opacity: float = 0.5, line_width: int = 2, show_class: bool = True):
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
