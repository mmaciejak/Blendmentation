import json
import os
import re
import tempfile

import bpy
import numpy as np
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Matrix, Vector

try:
    import OpenImageIO as oiio
except ImportError as error:
    # bundled with blender, but not with the bpy python module
    raise ImportError("OpenImageIO is missing, install it with: pip install OpenImageIO") from error

from .. import bpy_paths

IMAGE_SETTINGS = ("media_type", "file_format", "color_mode", "color_depth", "exr_codec")
AOV_FORMATS = {"OPEN_EXR": ".exr", "PNG": ".png"}
RENDER_FORMATS = {"PNG": ".png", "JPEG": ".jpg", "OPEN_EXR": ".exr"}
# built-in passes: view layer property, and the layer names in the multilayer EXR
# (index passes were renamed in blender 5)
PASSES = {
    "Depth": ("use_pass_z", ("Depth",)),
    "Mist": ("use_pass_mist", ("Mist",)),
    "Normal": ("use_pass_normal", ("Normal",)),
    "Position": ("use_pass_position", ("Position",)),
    "Vector": ("use_pass_vector", ("Vector",)),
    "UV": ("use_pass_uv", ("UV",)),
    "ObjectIndex": ("use_pass_object_index", ("Object Index", "IndexOB")),
    "MaterialIndex": ("use_pass_material_index", ("Material Index", "IndexMA")),
}
# EXR stores channels sorted by name (U, V, A becomes A, U, V), this is their natural
# order, which matters when they are written to formats without channel names, like PNG
CHANNEL_ORDER = ("R", "G", "B", "X", "Y", "Z", "W", "U", "V", "A")
PREVIEW_FORMATS = {"PNG": ".png", "JPEG": ".jpg"}
# preview colors, one per class in order of appearance
CLASS_COLORS = (
    (230, 25, 75), (60, 180, 75), (0, 130, 200), (255, 225, 25), (245, 130, 48),
    (145, 30, 180), (70, 240, 240), (240, 50, 230), (210, 245, 60), (250, 190, 212),
)
# 3x5 pixel font for the class names, blender's OpenImageIO cannot render text.
# Rows top to bottom, other characters are drawn as "?"
FONT = {
    "A": "010101111101101", "B": "110101110101110", "C": "011100100100011", "D": "110101101101110",
    "E": "111100110100111", "F": "111100110100100", "G": "011100101101011", "H": "101101111101101",
    "I": "111010010010111", "J": "001001001101010", "K": "101101110101101", "L": "100100100100111",
    "M": "101111111101101", "N": "110101101101101", "O": "010101101101010", "P": "110101110100100",
    "Q": "010101101110011", "R": "110101110101101", "S": "011100010001110", "T": "111010010010010",
    "U": "101101101101111", "V": "101101101101010", "W": "101101111111101", "X": "101101010101101",
    "Y": "101101010010010", "Z": "111001010100111", "0": "111101101101111", "1": "010110010010111",
    "2": "110001010100111", "3": "110001010001110", "4": "101101111001001", "5": "111100110001110",
    "6": "011100111101111", "7": "111001010010010", "8": "111101111101111", "9": "111101111001110",
    " ": "000000000000000", "_": "000000000000111", "-": "000000111000000", ".": "000000000000010",
    ":": "000010000010000", "?": "110001010000010",
}
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


def read_multilayer(file_path):
    """Returns the channel names of every part of a multilayer EXR."""
    image_input = oiio.ImageInput.open(file_path)
    if image_input is None:
        raise RuntimeError(f"Cannot read the render result: {oiio.geterror()}")
    parts = []
    while image_input.seek_subimage(len(parts), 0):
        parts.append(image_input.spec().channelnames)
    image_input.close()
    return parts


def save_layer(frame, layer_names, file_name, file_format, channel_names=None):
    """Saves one layer of the beauty render (an AOV or a pass) to <path>/<file_name>.
    Channels are named <view layer>.<layer>.<channel>, in one or several EXR parts.

    Args:
        layer_names (tuple): names the layer may have in the EXR, the first found is used
        channel_names (function): number of channels -> new channel names, None keeps them

    Returns:
        str: file_name, or None if the layer is not in the render result
    """
    multilayer, parts = frame.multilayer()
    for layer in layer_names:
        prefix = f"{frame.view_layer.name}.{layer}."
        for part, names in enumerate(parts):
            found = [(i, name[len(prefix):]) for i, name in enumerate(names) if name.startswith(prefix)]
            if found:
                break
        else:
            continue
        break
    else:
        return None

    found.sort(key=lambda item: CHANNEL_ORDER.index(item[1]) if item[1] in CHANNEL_ORDER else len(CHANNEL_ORDER))
    indices = tuple(i for i, _ in found)
    new_names = channel_names(len(indices)) if channel_names else tuple(name for _, name in found)
    buffer = oiio.ImageBufAlgo.channels(oiio.ImageBuf(multilayer, part, 0), indices, new_names)
    if file_format == "OPEN_EXR":
        buffer.set_write_format(oiio.FLOAT)
    if not buffer.write(os.path.join(frame.path, file_name)):
        raise RuntimeError(f"Cannot write {file_name}: {buffer.geterror()}")
    return file_name


def aov_channel_names(count):
    return ("Y",) if count == 1 else tuple("RGBA"[:count])


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
        list: {"class", "objects", "mask", "per"} for every mask
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        ids = render_ids(scene, [group for _, group in instances], temp_dir)

    entries = []
    if per in ("instance", "both"):
        for number, (class_name, group) in enumerate(instances):
            file_name = mask_file(path, f"{index:06d}_mask_{number}.png", ids == number + 1)
            entries.append({"class": class_name, "objects": [obj.name for obj in group], "mask": file_name, "per": "instance"})
    if per in ("class", "both"):
        for class_name in dict.fromkeys(class_name for class_name, _ in instances):
            numbers = [number for number, (name, _) in enumerate(instances) if name == class_name]
            safe_name = re.sub(r"[^\w.-]", "_", class_name)
            file_name = mask_file(path, f"{index:06d}_mask_{safe_name}.png", np.isin(ids, [n + 1 for n in numbers]))
            objects = [obj.name for number in numbers for obj in instances[number][1]]
            entries.append({"class": class_name, "objects": objects, "mask": file_name, "per": "class"})
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

    def __init__(self, scene, path, index, width, height, temp_dir):
        self.scene = scene
        self.camera = scene.camera
        self.view_layer = bpy.context.view_layer
        self.depsgraph = bpy.context.evaluated_depsgraph_get()
        self.path = path
        self.index = index
        self.width = width
        self.height = height
        self.temp_dir = temp_dir
        self.label = {"resolution": [width, height]}
        self.rendered = False
        self.multilayer_file = None
        # set by steps that draw on the beauty render after Segmentation replaced it
        self.keep_beauty = False
        self.beauty_rgba = None
        self.changed = []

    def file_name(self, suffix):
        return f"{self.index:06d}{suffix}"

    def add(self, key, entries):
        """Appends entries to a list in the labels, so several steps can add to it."""
        self.label.setdefault(key, []).extend(entries)

    def set(self, owner, name, value):
        """Changes a setting for this datapoint only, it is restored afterwards."""
        self.changed.append((owner, name, getattr(owner, name)))
        setattr(owner, name, value)

    def restore(self):
        for owner, name, value in reversed(self.changed):
            setattr(owner, name, value)
        self.changed = []

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

    def beauty_pixels(self):
        """Color managed 8 bit RGBA copy of the beauty render, like the saved image, as a
        (height, width, 4) array. Saved once, every call returns a new copy to draw on."""
        if self.beauty_rgba is None:
            self.beauty()
            file_path = os.path.join(self.temp_dir, "beauty.png")
            image_settings = self.scene.render.image_settings
            set_file_format(image_settings, "PNG")
            image_settings.color_mode = "RGBA"
            image_settings.color_depth = "8"
            bpy.data.images["Render Result"].save_render(file_path, scene=self.scene)
            self.beauty_rgba = oiio.ImageBuf(file_path).get_pixels(oiio.UINT8)
        return np.array(self.beauty_rgba)

    def replace_render(self):
        """Call before a render that replaces the beauty Render Result, like the segmentation."""
        if self.rendered and self.keep_beauty:
            self.beauty_pixels()
        self.rendered = False

    def multilayer(self):
        """Saves the beauty render with all its passes as a multilayer EXR, once.

        Returns:
            tuple: path of the EXR and the channel names of each of its parts
        """
        if self.multilayer_file is None:
            self.beauty()
            file_path = os.path.join(self.temp_dir, "render.exr")
            set_file_format(self.scene.render.image_settings, "OPEN_EXR_MULTILAYER")
            self.scene.render.image_settings.color_depth = "32"
            bpy.data.images["Render Result"].save_render(file_path, scene=self.scene)
            self.multilayer_file = (file_path, read_multilayer(file_path))
        return self.multilayer_file


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
    files = {}
    for name in names:
        file_name = frame.file_name(f"_{name}{AOV_FORMATS[file_format]}")
        if save_layer(frame, (name,), file_name, file_format, aov_channel_names) is None:
            raise RuntimeError(f"AOV '{name}' is missing in the render result")
        files[name] = file_name
    frame.label["aovs"] = files


def check_passes(names, file_format):
    unknown = [name for name in names if name not in PASSES]
    if unknown:
        raise ValueError(f"Unknown passes {unknown}, available: {list(PASSES)}")
    if file_format not in AOV_FORMATS:
        raise ValueError(f"Passes file_format must be one of {list(AOV_FORMATS)}")


def prepare_passes(frame, names):
    """Enables the passes in the view layer for this datapoint."""
    for name in names:
        frame.set(frame.view_layer, PASSES[name][0], True)


def render_passes(frame, names, file_format):
    """Saves the built-in passes to <path>/<index>_<pass>.<ext>, from the beauty render."""
    files = {}
    for name in names:
        file_name = frame.file_name(f"_{name}{AOV_FORMATS[file_format]}")
        if save_layer(frame, PASSES[name][1], file_name, file_format) is None:
            hint = ", Vector also needs motion blur off" if name == "Vector" else ""
            raise RuntimeError(f"Pass '{name}' was not rendered, {frame.scene.render.engine} may not support it{hint}")
        files[name] = file_name
    frame.label["passes"] = files


def check_preview(step_name, file_format, line_width, min_line_width, opacity=None):
    if file_format not in PREVIEW_FORMATS:
        raise ValueError(f"{step_name} file_format must be one of {list(PREVIEW_FORMATS)}")
    if not isinstance(line_width, int) or line_width < min_line_width:
        raise ValueError(f"{step_name} line_width must be a whole number of pixels, at least {min_line_width}")
    if opacity is not None and not 0 <= opacity <= 1:
        raise ValueError(f"{step_name} opacity must be between 0 and 1")


def text_pixels(text, scale):
    """Boolean (height, width) array of the text in the 3x5 pixel font, scaled up."""
    glyphs = [np.array([int(bit) for bit in FONT.get(char, FONT["?"])], dtype=bool).reshape(5, 3)
              for char in text.upper()]
    spaced = [np.pad(glyph, ((0, 0), (0, 1))) for glyph in glyphs]
    pixels = np.concatenate(spaced, axis=1)[:, :-1] if spaced else np.zeros((5, 0), dtype=bool)
    return np.kron(pixels, np.ones((scale, scale), dtype=bool))


def paint(pixels, rows, columns, color):
    """Paints the clipped area with an RGB color, opaque when the image has alpha."""
    rows, columns = slice(max(rows.start, 0), max(rows.stop, 0)), slice(max(columns.start, 0), max(columns.stop, 0))
    pixels[rows, columns, :3] = color
    if pixels.shape[2] == 4:
        pixels[rows, columns, 3] = 255


def draw_tab(pixels, x, y, text, color, scale):
    """Draws the text on a filled tab above the point (below it when at the top edge)."""
    height, width = pixels.shape[:2]
    glyphs = text_pixels(text, scale)
    padding = scale
    tab_height, tab_width = glyphs.shape[0] + 2 * padding, glyphs.shape[1] + 2 * padding
    top = y - tab_height if y - tab_height >= 0 else y
    left = min(x, width - tab_width)
    paint(pixels, slice(top, top + tab_height), slice(left, left + tab_width), color)
    # dark text on bright colors
    text_color = (0, 0, 0) if 0.299 * color[0] + 0.587 * color[1] + 0.114 * color[2] > 150 else (255, 255, 255)
    rows, columns = np.nonzero(glyphs)
    rows, columns = rows + top + padding, columns + left + padding
    inside = (rows >= 0) & (rows < height) & (columns >= 0) & (columns < width)
    pixels[rows[inside], columns[inside], :3] = text_color


def draw_bbox(pixels, box, color, line_width):
    """Draws a bbox outline."""
    height, width = pixels.shape[:2]
    x_min, y_min = int(np.floor(box[0])), int(np.floor(box[1]))
    x_max, y_max = min(int(np.ceil(box[2])), width), min(int(np.ceil(box[3])), height)
    paint(pixels, slice(y_min, y_max), slice(x_min, x_min + line_width), color)
    paint(pixels, slice(y_min, y_max), slice(x_max - line_width, x_max), color)
    paint(pixels, slice(y_min, y_min + line_width), slice(x_min, x_max), color)
    paint(pixels, slice(y_max - line_width, y_max), slice(x_min, x_max), color)


def erode(mask, steps):
    """Shrinks a boolean mask by steps pixels (4-neighborhood)."""
    for _ in range(steps):
        padded = np.pad(mask, 1)
        mask = mask & padded[:-2, 1:-1] & padded[2:, 1:-1] & padded[1:-1, :-2] & padded[1:-1, 2:]
    return mask


def text_scale(frame):
    return max(2, frame.height // 160)


def class_color(class_names, class_name):
    """Color of a class, by its position among the classes in the labels."""
    return CLASS_COLORS[class_names.index(class_name) % len(CLASS_COLORS)]


def write_preview(frame, pixels, suffix, file_format, label_key):
    """Writes 8 bit pixels to <path>/<index><suffix>.<ext> and adds the file to the labels."""
    if file_format == "JPEG":
        pixels = pixels[:, :, :3]
    file_name = frame.file_name(f"{suffix}{PREVIEW_FORMATS[file_format]}")
    height, width, channels = pixels.shape
    buffer = oiio.ImageBuf(oiio.ImageSpec(width, height, channels, oiio.UINT8))
    buffer.set_pixels(oiio.ROI(), np.ascontiguousarray(pixels))
    if not buffer.write(os.path.join(frame.path, file_name)):
        raise RuntimeError(f"Cannot write {file_name}: {buffer.geterror()}")
    frame.label[label_key] = file_name


def bbox_image(frame, file_format, line_width, show_class):
    """Saves a copy of the beauty render with the bboxes of the labels drawn on it, to
    <path>/<index>_bboxes.<ext>. Reuses the render, the main image stays clean."""
    if "bboxes" not in frame.label:
        raise RuntimeError("BBoxImage draws the bboxes of a BBox step, add BBox to the steps")
    pixels = frame.beauty_pixels()
    entries = frame.label["bboxes"]
    class_names = list(dict.fromkeys(entry["class"] for entry in entries))
    for entry in entries:
        if entry["bbox"] is not None:
            color = class_color(class_names, entry["class"])
            draw_bbox(pixels, entry["bbox"], color, line_width)
            if show_class:
                draw_tab(pixels, int(entry["bbox"][0]), int(entry["bbox"][1]), entry["class"], color, text_scale(frame))
    write_preview(frame, pixels, "_bboxes", file_format, "bbox_image")


def segmentation_image(frame, file_format, opacity, line_width, show_class):
    """Saves a copy of the beauty render with the masks of the labels drawn over it in
    their class color, each instance outlined, to <path>/<index>_segmentation.<ext>."""
    if "masks" not in frame.label:
        raise RuntimeError("SegmentationImage draws the masks of a Segmentation step, add Segmentation to the steps")
    pixels = frame.beauty_pixels()
    entries = frame.label["masks"]
    class_names = list(dict.fromkeys(entry["class"] for entry in entries))
    # instance masks separate touching instances, class masks are used when there are none
    per = "instance" if any(entry["per"] == "instance" for entry in entries) else "class"
    masks = []
    for entry in entries:
        if entry["per"] == per:
            mask = oiio.ImageBuf(os.path.join(frame.path, entry["mask"])).get_pixels(oiio.UINT8)[:, :, 0] > 127
            masks.append((class_color(class_names, entry["class"]), mask, entry["class"]))

    rgb = pixels[:, :, :3].astype(np.float64)
    for color, mask, _ in masks:
        rgb[mask] = (1 - opacity) * rgb[mask] + opacity * np.array(color)
    pixels[:, :, :3] = np.rint(rgb).astype(np.uint8)
    if pixels.shape[2] == 4:
        # opaque where a mask is drawn, also over a transparent background
        pixels[np.any([mask for _, mask, _ in masks], axis=0), 3] = 255
    for color, mask, class_name in masks:
        if line_width:
            pixels[mask & ~erode(mask, line_width), :3] = color
        if show_class and mask.any():
            rows, columns = np.nonzero(mask)
            draw_tab(pixels, int(columns.min()), int(rows.min()), class_name, color, text_scale(frame))
    write_preview(frame, pixels, "_segmentation", file_format, "segmentation_image")


def camera_intrinsics(scene, camera_data, width, height):
    """3x3 intrinsic matrix K of a perspective camera, in pixels, for the OpenCV
    camera convention (x right, y down, z forward) and top-left image origin."""
    render = scene.render
    pixel_aspect = render.pixel_aspect_y / render.pixel_aspect_x
    sensor_fit = camera_data.sensor_fit
    if sensor_fit == "AUTO":
        sensor_fit = "HORIZONTAL" if render.pixel_aspect_x * width >= render.pixel_aspect_y * height else "VERTICAL"
    sensor_size = camera_data.sensor_height if camera_data.sensor_fit == "VERTICAL" else camera_data.sensor_width
    view_factor = width if sensor_fit == "HORIZONTAL" else pixel_aspect * height
    pixels_per_mm = camera_data.lens * view_factor / sensor_size
    return [
        [pixels_per_mm, 0.0, width / 2 - camera_data.shift_x * view_factor],
        [0.0, pixels_per_mm / pixel_aspect, height / 2 + camera_data.shift_y * view_factor / pixel_aspect],
        [0.0, 0.0, 1.0],
    ]


def camera_data(frame):
    """Adds the camera settings, intrinsics and extrinsics to the labels under "camera"."""
    camera = frame.camera
    data = camera.data
    location, rotation, _ = camera.matrix_world.decompose()
    # blender cameras look down -z with y up, OpenCV cameras look down +z with y down
    world_to_camera = Matrix(((1, 0, 0), (0, -1, 0), (0, 0, -1))) @ rotation.to_matrix().transposed()
    translation = -(world_to_camera @ location)
    info = {
        "name": camera.name,
        "type": data.type,
        "matrix_world": to_json(camera.matrix_world),
        "extrinsics_opencv": [list(row) + [translation[i]] for i, row in enumerate(world_to_camera)],
        "clip_start": data.clip_start,
        "clip_end": data.clip_end,
    }
    if data.type == "PERSP":
        info.update(
            lens=data.lens,
            sensor_width=data.sensor_width,
            sensor_height=data.sensor_height,
            sensor_fit=data.sensor_fit,
            intrinsics=camera_intrinsics(frame.scene, data, frame.width, frame.height),
        )
    elif data.type == "ORTHO":
        info["ortho_scale"] = data.ortho_scale
    frame.label["camera"] = info


def check_keypoints(points):
    if not isinstance(points, dict) or not points:
        raise TypeError("Keypoints needs a dict {name: object | (object, vertex index | vertex group | bone) | (x, y, z)}")


def keypoint_location(source, depsgraph):
    """World location of a keypoint source: an object (its origin), (mesh object, vertex index),
    (mesh object, vertex group name) for the group's center, (armature, bone name) for the
    bone head, or a point (x, y, z)."""
    if isinstance(source, bpy.types.Object):
        return source.matrix_world.translation.copy()
    if isinstance(source, (list, tuple)) and len(source) == 3 and all(isinstance(v, (int, float)) for v in source):
        return Vector(source)
    if not (isinstance(source, (list, tuple)) and len(source) == 2 and isinstance(source[0], bpy.types.Object)):
        raise TypeError(f"Unknown keypoint source {source}")

    obj, key = source
    if obj.type == "ARMATURE":
        return obj.matrix_world @ obj.pose.bones[key].head
    # evaluated mesh, so deformations like armatures are included
    obj_eval = obj.evaluated_get(depsgraph)
    mesh = obj_eval.to_mesh()
    try:
        if isinstance(key, int):
            co = mesh.vertices[key].co.copy()
        else:
            group = obj.vertex_groups[key].index
            coords = [v.co for v in mesh.vertices if any(g.group == group and g.weight > 0 for g in v.groups)]
            if not coords:
                raise ValueError(f"Vertex group '{key}' of '{obj.name}' has no vertices")
            co = sum(coords, Vector()) / len(coords)
    finally:
        obj_eval.to_mesh_clear()
    return obj_eval.matrix_world @ co


def keypoint_visible(frame, location):
    """True if nothing is between the camera and the location."""
    camera_matrix = frame.camera.matrix_world
    if frame.camera.data.type == "ORTHO":
        forward = (camera_matrix.to_3x3() @ Vector((0, 0, -1))).normalized()
        origin = location - forward * (location - camera_matrix.translation).dot(forward)
    else:
        origin = camera_matrix.translation
    direction = location - origin
    distance = direction.length
    if distance == 0:
        return True
    hit, hit_location, *_ = frame.scene.ray_cast(frame.depsgraph, origin, direction.normalized(), distance=distance)
    # a point on a surface hits that surface itself, at its own distance
    return not hit or (hit_location - origin).length >= distance - max(1e-4, distance * 1e-4)


def keypoints(frame, points):
    """Adds {"name", "position", "depth", "in_frame", "visible"} of every keypoint to the labels
    under "keypoints". position is in pixels from the top left corner."""
    entries = []
    for name, source in points.items():
        location = keypoint_location(source, frame.depsgraph)
        view = world_to_camera_view(frame.scene, frame.camera, location)
        in_frame = 0.0 <= view.x <= 1.0 and 0.0 <= view.y <= 1.0 and view.z > 0
        entries.append({
            "name": name,
            "position": [view.x * frame.width, (1.0 - view.y) * frame.height],
            "depth": view.z,
            "in_frame": in_frame,
            "visible": in_frame and keypoint_visible(frame, location),
        })
    frame.add("keypoints", entries)


def check_segmentation(classes, per):
    if per not in ("instance", "class", "both"):
        raise ValueError('Segmentation per must be "instance", "class" or "both"')
    to_instances(classes)


def segmentation(frame, classes, per):
    """Saves masks of the visible pixels, adds {"class", "objects", "mask"} to the labels under "masks"."""
    frame.replace_render()
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

        with tempfile.TemporaryDirectory() as temp_dir:
            frame = Frame(scene, path, next_index(path), width, height, temp_dir)
            try:
                for step in steps:
                    if hasattr(step, "prepare"):
                        step.prepare(frame)
                for step in steps:
                    if step(frame) is False:
                        return False
            finally:
                frame.restore()
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
