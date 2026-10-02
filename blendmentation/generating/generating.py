"""Generating steps, composed like augmentations.

Steps run in a fixed order whatever order they are given in: labels (BBox,
RotationMatrix, OutputField) first, so a skipped datapoint is never rendered,
then Render, AOVToImage and Segmentation. Render and AOVToImage share one render.

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


class Segmentation:
    """Saves black and white masks of the visible pixels and adds them to the labels
    under "masks", as {"class", "objects", "mask"}. Uses a separate, fast workbench
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
