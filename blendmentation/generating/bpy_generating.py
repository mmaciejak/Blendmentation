import json
import os
import re
import tempfile

import bpy
import numpy as np
import OpenImageIO as oiio

from .. import bpy_paths

IMAGE_SETTINGS = ("media_type", "file_format", "color_mode", "color_depth", "exr_codec")
AOV_FORMATS = {"OPEN_EXR": ".exr", "PNG": ".png"}
RENDER_FORMATS = {"PNG": ".png", "JPEG": ".jpg", "OPEN_EXR": ".exr"}
# output files start with the datapoint index: 000012.png, 000012_mask_0.png, 000012_Albedo.exr
INDEX = re.compile(r"^(\d+)(?:[_.]|$)")


def to_instances(classes):
    """Normalizes {class name: [objects or sublists]} to a list of (class name, [objects]).
    An object alone is an instance of one object, the objects of a sublist are one instance."""
    if not isinstance(classes, dict):
        raise TypeError("classes must be a dict {class name: [objects or sublists of objects]}")
    instances = []
    for class_name, entries in classes.items():
        if not isinstance(entries, (list, tuple)):
            raise TypeError(f"Class '{class_name}' must map to a list of objects or sublists of objects")
        for entry in entries:
            group = list(entry) if isinstance(entry, (list, tuple)) else [entry]
            if not group:
                raise ValueError(f"Class '{class_name}' has an empty sublist")
            instances.append((class_name, group))
    names = [obj.name for _, group in instances for obj in group]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ValueError(f"Objects {duplicates} are in more than one instance")
    return instances


def next_index(path):
    """Returns the first free image index in the output directory."""
    taken = [int(match.group(1)) for match in map(INDEX.match, os.listdir(path)) if match]
    return max(taken, default=-1) + 1


def camera_view_coords(scene, camera, obj, depsgraph):
    """Projects the evaluated object's vertices to normalized camera view coordinates.

    Returns:
        np.ndarray: (n, 2) array of x, y in 0-1 (bottom-left origin) of vertices in front of the camera
    """
    obj_eval = obj.evaluated_get(depsgraph)
    mesh = obj_eval.to_mesh()
    try:
        coords = np.empty(len(mesh.vertices) * 3, dtype=np.float64)
        mesh.vertices.foreach_get("co", coords)
    finally:
        obj_eval.to_mesh_clear()
    coords = coords.reshape(-1, 3)

    to_camera = np.array(camera.matrix_world.inverted() @ obj_eval.matrix_world)
    coords = coords @ to_camera[:3, :3].T + to_camera[:3, 3]
    # the camera looks down its -z axis, drop everything behind it
    coords = coords[coords[:, 2] < 0]

    frame = camera.data.view_frame(scene=scene)
    frame_min_x, frame_max_x = min(v.x for v in frame), max(v.x for v in frame)
    frame_min_y, frame_max_y = min(v.y for v in frame), max(v.y for v in frame)
    frame_z = frame[0].z

    if camera.data.type == "ORTHO":
        scale = np.ones(len(coords))
    else:
        scale = coords[:, 2] / frame_z

    x = (coords[:, 0] - frame_min_x * scale) / ((frame_max_x - frame_min_x) * scale)
    y = (coords[:, 1] - frame_min_y * scale) / ((frame_max_y - frame_min_y) * scale)
    return np.stack([x, y], axis=1)


def bbox(scene, camera, group, depsgraph, width, height):
    """Returns the 2D bbox of the list of objects, as one object, in pixels
    [x_min, y_min, x_max, y_max] with top-left image origin, or None if it is not in the frame.
    Occlusion by other objects is not taken into account.
    """
    view = np.concatenate([camera_view_coords(scene, camera, obj, depsgraph) for obj in group])
    if len(view) == 0:
        return None

    x_min, x_max = np.clip([view[:, 0].min(), view[:, 0].max()], 0.0, 1.0)
    y_min, y_max = np.clip([view[:, 1].min(), view[:, 1].max()], 0.0, 1.0)
    if x_min >= x_max or y_min >= y_max:
        return None

    return [
        float(x_min * width),
        float((1.0 - y_max) * height),
        float(x_max * width),
        float((1.0 - y_min) * height),
    ]


def iou(box_a, box_b):
    """Intersection over union of two [x_min, y_min, x_max, y_max] boxes."""
    inter_w = min(box_a[2], box_b[2]) - max(box_a[0], box_b[0])
    inter_h = min(box_a[3], box_b[3]) - max(box_a[1], box_b[1])
    if inter_w <= 0 or inter_h <= 0:
        return 0.0
    intersection = inter_w * inter_h
    area_a = (box_a[2] - box_a[0]) * (box_a[3] - box_a[1])
    area_b = (box_b[2] - box_b[0]) * (box_b[3] - box_b[1])
    return intersection / (area_a + area_b - intersection)


def max_iou(boxes):
    """Highest IoU between any two boxes."""
    return max(
        (iou(boxes[i], boxes[j]) for i in range(len(boxes)) for j in range(i + 1, len(boxes))),
        default=0.0,
    )


def rotation_to_camera(camera, obj):
    """Object rotation relative to the camera as a 3x3 matrix, with scale removed."""
    relative = camera.matrix_world.inverted() @ obj.matrix_world
    return [list(row) for row in relative.to_3x3().normalized()]


def set_file_format(image_settings, file_format):
    """Sets the output file format, blender 5 also needs the matching media type."""
    if hasattr(image_settings, "media_type"):
        image_settings.media_type = "MULTI_LAYER_IMAGE" if file_format == "OPEN_EXR_MULTILAYER" else "IMAGE"
    image_settings.file_format = file_format


def check_aovs(scene, view_layer, aovs, aov_format):
    """Raises if the AOVs cannot be rendered."""
    if aov_format not in AOV_FORMATS:
        raise ValueError(f"AOV file_format must be one of {list(AOV_FORMATS)}")
    if scene.render.engine == "BLENDER_WORKBENCH":
        raise ValueError("Shader AOVs need Cycles or EEVEE, not Workbench")
    available = [aov.name for aov in view_layer.aovs]
    missing = [name for name in aovs if name not in available]
    if missing:
        raise ValueError(
            f"AOVs {missing} are not in view layer '{view_layer.name}' (has {available}), "
            "add them in View Layer Properties > Passes > Shader AOV"
        )


def check_aov_images(names, file_format):
    check_aovs(bpy.context.scene, bpy.context.view_layer, names, file_format)


def save_aovs(scene, view_layer, aovs, aov_format, path, index):
    """Saves the AOV passes of the last render to <path>/<index>_<aov>.<ext>.
    The render result is saved as a multilayer EXR, the AOV channels are split from it.

    Returns:
        dict: file name for every AOV
    """
    files = {}
    with tempfile.TemporaryDirectory() as temp_dir:
        multilayer = os.path.join(temp_dir, "render.exr")
        set_file_format(scene.render.image_settings, "OPEN_EXR_MULTILAYER")
        scene.render.image_settings.color_depth = "32"
        bpy.data.images["Render Result"].save_render(multilayer, scene=scene)

        # passes are channels named <view layer>.<aov>.<channel>, in one or several EXR parts
        image_input = oiio.ImageInput.open(multilayer)
        if image_input is None:
            raise RuntimeError(f"Cannot read the render result: {oiio.geterror()}")
        parts = []
        while image_input.seek_subimage(len(parts), 0):
            parts.append(image_input.spec().channelnames)
        image_input.close()

        for name in aovs:
            prefix = f"{view_layer.name}.{name}."
            for part, channel_names in enumerate(parts):
                indices = [i for i, channel in enumerate(channel_names) if channel.startswith(prefix)]
                if indices:
                    break
            else:
                raise RuntimeError(f"AOV '{name}' is missing in the render result")

            new_names = ("Y",) if len(indices) == 1 else tuple("RGBA"[: len(indices)])
            buffer = oiio.ImageBufAlgo.channels(oiio.ImageBuf(multilayer, part, 0), tuple(indices), new_names)
            file_name = f"{index:06d}_{name}{AOV_FORMATS[aov_format]}"
            if aov_format == "OPEN_EXR":
                buffer.set_write_format(oiio.FLOAT)
            if not buffer.write(os.path.join(path, file_name)):
                raise RuntimeError(f"Cannot write AOV '{name}': {buffer.geterror()}")
            files[name] = file_name
    return files


def render_ids(scene, groups, temp_dir):
    """Renders every group of objects in a flat color encoding its id (1, 2, ...)
    with workbench, other objects in black so they still occlude.

    Returns:
        np.ndarray: (height, width) array of group ids, 0 = background or other objects
    """
    shading = scene.display.shading
    view_settings = scene.view_settings
    saved = [
        (scene.render, ("engine", "film_transparent", "use_compositing", "use_sequencer")),
        (scene.display, ("render_aa",)),
        (shading, ("type", "light", "color_type", "show_cavity", "show_object_outline", "show_shadows",
                   "show_specular_highlight", "show_xray", "use_dof", "show_backface_culling")),
        (view_settings, ("view_transform", "look", "exposure", "gamma", "use_curve_mapping")),
    ]
    saved = [(owner, {name: getattr(owner, name) for name in names}) for owner, names in saved]
    # instanced collections may use objects that are not in the scene, so all objects are recolored
    editable = [obj for obj in bpy.data.objects if obj.library is None]
    saved_colors = {obj: tuple(obj.color) for obj in editable}
    try:
        scene.render.engine = "BLENDER_WORKBENCH"
        scene.render.film_transparent = True
        scene.render.use_compositing = False
        scene.render.use_sequencer = False
        scene.display.render_aa = "OFF"
        shading.type = "SOLID"
        shading.light = "FLAT"
        shading.color_type = "OBJECT"
        for name in ("show_cavity", "show_object_outline", "show_shadows", "show_specular_highlight",
                     "show_xray", "use_dof", "show_backface_culling"):
            setattr(shading, name, False)
        view_settings.view_transform = "Standard"
        view_settings.look = "None"
        view_settings.exposure = 0.0
        view_settings.gamma = 1.0
        view_settings.use_curve_mapping = False

        for obj in editable:
            obj.color = (0.0, 0.0, 0.0, 1.0)
        for group_id, group in enumerate(groups, start=1):
            # id in 8 bit steps of red and green, read back exactly from the float EXR
            color = ((group_id % 256) / 255.0, (group_id // 256) / 255.0, 0.0, 1.0)
            for obj in group:
                obj.color = color

        bpy.ops.render.render()
        id_file = os.path.join(temp_dir, "ids.exr")
        set_file_format(scene.render.image_settings, "OPEN_EXR")
        scene.render.image_settings.color_mode = "RGBA"
        scene.render.image_settings.color_depth = "32"
        bpy.data.images["Render Result"].save_render(id_file, scene=scene)
    finally:
        for owner, values in saved:
            for name, value in values.items():
                setattr(owner, name, value)
        for obj, color in saved_colors.items():
            obj.color = color

    pixels = oiio.ImageBuf(id_file).get_pixels(oiio.FLOAT)
    ids = np.rint(pixels[:, :, 0] * 255).astype(np.int64) + np.rint(pixels[:, :, 1] * 255).astype(np.int64) * 256
    ids[pixels[:, :, 3] < 0.5] = 0
    return ids


def mask_file(path, file_name, mask):
    """Writes a boolean (height, width) mask as a black and white PNG."""
    height, width = mask.shape
    buffer = oiio.ImageBuf(oiio.ImageSpec(width, height, 1, oiio.UINT8))
    buffer.set_pixels(oiio.ROI(), np.where(mask, 255, 0).astype(np.uint8).reshape(height, width, 1))
    if not buffer.write(os.path.join(path, file_name)):
        raise RuntimeError(f"Cannot write mask {file_name}: {buffer.geterror()}")
    return file_name


def save_masks(scene, instances, path, index, per):
    """Saves black and white mask PNGs of the visible pixels, per instance as
    <path>/<index>_mask_<n>.png and/or per class as <path>/<index>_mask_<class>.png

    Args:
        instances (list): (class name, [objects]) pairs
        per (str): "instance", "class" or "both"

    Returns:
        list: {"class", "objects", "mask"} for every mask
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        ids = render_ids(scene, [group for _, group in instances], temp_dir)

    entries = []
    if per in ("instance", "both"):
        for number, (class_name, group) in enumerate(instances):
            file_name = mask_file(path, f"{index:06d}_mask_{number}.png", ids == number + 1)
            entries.append({"class": class_name, "objects": [obj.name for obj in group], "mask": file_name})
    if per in ("class", "both"):
        for class_name in dict.fromkeys(class_name for class_name, _ in instances):
            numbers = [number for number, (name, _) in enumerate(instances) if name == class_name]
            safe_name = re.sub(r"[^\w.-]", "_", class_name)
            file_name = mask_file(path, f"{index:06d}_mask_{safe_name}.png", np.isin(ids, [n + 1 for n in numbers]))
            objects = [obj.name for number in numbers for obj in instances[number][1]]
            entries.append({"class": class_name, "objects": objects, "mask": file_name})
    return entries


def to_json(value):
    """Converts a blender value to something json can store."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, bpy.types.ID):
        return value.name
    if hasattr(value, "__len__"):
        return [to_json(item) for item in value]
    if hasattr(value, "name"):
        return value.name
    return str(value)


class Frame:
    """One datapoint being generated, shared by the generating steps.
    The beauty render happens at most once, steps that need it reuse the render result.
    """

    def __init__(self, scene, path, index, width, height):
        self.scene = scene
        self.camera = scene.camera
        self.view_layer = bpy.context.view_layer
        self.depsgraph = bpy.context.evaluated_depsgraph_get()
        self.path = path
        self.index = index
        self.width = width
        self.height = height
        self.label = {"resolution": [width, height]}
        self.rendered = False

    def file_name(self, suffix):
        return f"{self.index:06d}{suffix}"

    def add(self, key, entries):
        """Appends entries to a list in the labels, so several steps can add to it."""
        self.label.setdefault(key, []).extend(entries)

    def beauty(self, file_path=None):
        """Renders the scene with its engine, once. With file_path the image is also written."""
        if self.rendered:
            if file_path:
                bpy.data.images["Render Result"].save_render(file_path, scene=self.scene)
            return
        if file_path:
            self.scene.render.filepath = file_path
        bpy.ops.render.render(write_still=bool(file_path))
        self.rendered = True


def bboxes(frame, classes, iou_deconflict):
    """Adds {"class", "objects", "bbox"} of every instance to the labels under "bboxes".

    Returns:
        bool: False if two bboxes overlap over iou_deconflict and the datapoint should be skipped
    """
    entries = []
    for class_name, group in to_instances(classes):
        box = bbox(frame.scene, frame.camera, group, frame.depsgraph, frame.width, frame.height)
        entries.append({"class": class_name, "objects": [obj.name for obj in group], "bbox": box})
    boxes = [entry["bbox"] for entry in entries if entry["bbox"] is not None]
    if iou_deconflict is not None and max_iou(boxes) > iou_deconflict:
        return False
    frame.add("bboxes", entries)
    return True


def check_objects(objects, step_name):
    if not isinstance(objects, (list, tuple)) or not objects:
        raise TypeError(f"{step_name} needs a list of objects")


def rotation_matrices(frame, objects):
    """Adds {"object", "rotation_matrix"} relative to the camera of every object
    to the labels under "rotation_matrices"."""
    frame.add(
        "rotation_matrices",
        [{"object": obj.name, "rotation_matrix": rotation_to_camera(frame.camera, obj)} for obj in objects],
    )


def check_output_field(name, data_path, objects):
    if not bpy_paths.is_absolute(data_path) and not objects:
        raise ValueError(f"Output field '{name}': relative path '{data_path}' needs objects")


def output_field(frame, name, data_path, objects):
    """Adds the value at the data path to the labels under name. Absolute paths are saved once,
    relative paths as {object name: value} for every object."""
    if bpy_paths.is_absolute(data_path):
        frame.label[name] = to_json(bpy_paths.get_value(data_path))
        return
    values = {}
    for obj in objects:
        try:
            values[obj.name] = to_json(bpy_paths.get_value(data_path, obj))
        except (AttributeError, KeyError, IndexError, TypeError) as error:
            raise ValueError(f"Output field '{name}': '{data_path}' does not resolve on '{obj.name}' ({error})")
    frame.label[name] = values


def check_render(file_format):
    if file_format not in RENDER_FORMATS:
        raise ValueError(f"file_format must be one of {list(RENDER_FORMATS)}")


def render_image(frame, file_format):
    """Renders the image to <path>/<index>.<ext>."""
    file_name = frame.file_name(RENDER_FORMATS[file_format])
    set_file_format(frame.scene.render.image_settings, file_format)
    frame.beauty(os.path.join(frame.path, file_name))
    frame.label["image"] = file_name


def aov_images(frame, names, file_format):
    """Saves the AOVs to <path>/<index>_<aov>.<ext>, from the beauty render."""
    frame.beauty()
    frame.label["aovs"] = save_aovs(frame.scene, frame.view_layer, names, file_format, frame.path, frame.index)


def check_segmentation(classes, per):
    if per not in ("instance", "class", "both"):
        raise ValueError('Segmentation per must be "instance", "class" or "both"')
    to_instances(classes)


def segmentation(frame, classes, per):
    """Saves masks of the visible pixels, adds {"class", "objects", "mask"} to the labels under "masks"."""
    frame.add("masks", save_masks(frame.scene, to_instances(classes), frame.path, frame.index, per))


def generate(path, resolution, steps, custom_dict=None):
    """Generates one datapoint: runs the steps in order and writes the labels to <path>/<index>.json

    Args:
        path (str): output directory, "" or relative paths are relative to the .blend file
        resolution (tuple): (width, height) in pixels
        steps (list): generating steps, called with the Frame, a step returning False skips the datapoint
        custom_dict (dict): extra key and values saved in the labels

    Returns:
        bool: True if the datapoint was generated, False if a step skipped it
    """
    scene = bpy.context.scene
    if scene.camera is None:
        raise RuntimeError("Scene has no active camera")
    for step in steps:
        if hasattr(step, "check"):
            step.check()

    path = bpy.path.abspath(path or "//")
    os.makedirs(path, exist_ok=True)

    width, height = int(resolution[0]), int(resolution[1])
    render_settings = scene.render
    image_settings = render_settings.image_settings
    previous = (
        render_settings.resolution_x,
        render_settings.resolution_y,
        render_settings.resolution_percentage,
        render_settings.filepath,
    )
    previous_image = {name: getattr(image_settings, name) for name in IMAGE_SETTINGS if hasattr(image_settings, name)}
    try:
        # set before computing bboxes, the camera frame depends on the resolution
        render_settings.resolution_x = width
        render_settings.resolution_y = height
        render_settings.resolution_percentage = 100
        bpy.context.view_layer.update()

        frame = Frame(scene, path, next_index(path), width, height)
        for step in steps:
            if step(frame) is False:
                return False
    finally:
        (
            render_settings.resolution_x,
            render_settings.resolution_y,
            render_settings.resolution_percentage,
            render_settings.filepath,
        ) = previous
        # format first, the other settings depend on it
        for name, value in previous_image.items():
            setattr(image_settings, name, value)

    label = frame.label
    if custom_dict:
        label.update(custom_dict)
    # an image alone needs no label file
    if set(label) - {"resolution", "image"}:
        with open(os.path.join(path, frame.file_name(".json")), "w") as file:
            json.dump(label, file, indent=2)
    return True
