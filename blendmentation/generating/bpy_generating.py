import json
import os
import random
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
# Object.pass_index limit, more instances fall back to the workbench id render
MAX_PASS_INDEX = 32767
BACKGROUND_MODES = ("color", "white_noise", "color_noise", "image")
BACKGROUND_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".tga")
# output files start with the datapoint index: 000012.png, 000012_mask_0.png, 000012_Albedo.exr
INDEX = re.compile(r"^(\d+)(?:[_.]|$)")


CLASS_SETTINGS = ("iou_deconflict", "max_truncation", "max_occlusion")


def check_threshold(name, value, owner):
    if value is not None and not (isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 1):
        raise ValueError(f"{owner} {name} must be None or a number in 0-1, got {value!r}")


def split_class(class_name, value):
    """Splits a class value, a list of instances or {"instances": [...], setting: value},
    into the list of instances and the dict of the settings it sets."""
    if not isinstance(value, dict):
        return value, {}
    unknown = sorted(set(value) - {"instances", *CLASS_SETTINGS})
    if unknown:
        raise ValueError(f"Class '{class_name}' has unknown keys {unknown}, allowed: instances, {', '.join(CLASS_SETTINGS)}")
    if "instances" not in value:
        raise ValueError(f"Class '{class_name}' is a dict, so it needs an 'instances' list")
    settings = {name: value[name] for name in CLASS_SETTINGS if name in value}
    for name, setting in settings.items():
        check_threshold(name, setting, f"Class '{class_name}'")
    return value["instances"], settings


# persistent_id of a depsgraph instance is its path through the nested instances, innermost
# first, padded with this value
INSTANCE_PATH_END = 2**31 - 1
# object types that have no geometry, they are left out of the instances
NO_GEOMETRY_TYPES = {"EMPTY", "LIGHT", "LIGHT_PROBE", "LIGHTPROBE", "CAMERA", "SPEAKER", "ARMATURE", "LATTICE"}
# object types that can have a geometry nodes modifier, needed for the id render of instances
NODES_MODIFIER_TYPES = {"MESH", "CURVE", "FONT", "CURVES", "POINTCLOUD", "VOLUME", "GREASEPENCIL"}
# instance attribute and custom property holding the ids in the id render of instances
ID_ATTRIBUTE = "blendmentation_id"


def is_instances(entry):
    """Whether a classes entry is an `Instances` (the instances of a parent object)."""
    return not isinstance(entry, bpy.types.ID) and hasattr(entry, "parent") and hasattr(entry, "of")


def instance_sources(of):
    """The objects an `Instances` of= selects: None for all, or a set of objects."""
    if of is None:
        return None
    if isinstance(of, bpy.types.Collection):
        return set(of.all_objects)
    objects = list(of) if isinstance(of, (list, tuple)) else [of]
    if not objects or not all(isinstance(obj, bpy.types.Object) for obj in objects):
        raise TypeError(f"Instances of= must be None, an object, a list of objects or a collection, got {of!r}")
    return set(objects)


def check_instances(class_name, spec):
    if not isinstance(spec.parent, bpy.types.Object):
        raise TypeError(f"Class '{class_name}': Instances needs the object that makes the instances, got {spec.parent!r}")
    instance_sources(spec.of)


class InstanceMember:
    """One object of an instance that geometry nodes or collection instancing make, read
    from the depsgraph. It stands in for an object in a group: it has a name, type and
    matrix_world, and the vertices of its evaluated geometry in its local space."""

    def __init__(self, parent, source, top, name, type, matrix_world, coords):
        self.parent = parent
        # the instanced object, the parent for a geometry instance
        self.source = source
        # index of the top-level instance of the parent it belongs to
        self.top = top
        self.name = name
        self.type = type
        self.matrix_world = matrix_world
        self.coords = coords


def mesh_coords(obj_eval):
    """(n, 3) array of the vertices of an evaluated object in its local space."""
    mesh = obj_eval.to_mesh() if obj_eval.type not in NO_GEOMETRY_TYPES else None
    if mesh is None:
        return np.empty((0, 3))
    try:
        coords = np.empty(len(mesh.vertices) * 3, dtype=np.float64)
        mesh.vertices.foreach_get("co", coords)
    finally:
        obj_eval.to_mesh_clear()
    return coords.reshape(-1, 3)


def socket(sockets, name):
    """The socket of a node by name. Blender 4.0 nodes have one socket per data type with
    the same name, only the one of the node's data type is enabled."""
    return next(socket for socket in sockets if socket.name == name and socket.enabled)


def node_group(name):
    """New geometry nodes group with a geometry input and output."""
    tree = bpy.data.node_groups.new(name, "GeometryNodeTree")
    tree.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    return tree


def index_id_nodes():
    """Geometry nodes group that sets the id attribute of the instances to their index."""
    tree = node_group("blendmentation index ids")
    nodes, link = tree.nodes, tree.links.new
    store = nodes.new("GeometryNodeStoreNamedAttribute")
    store.data_type = "INT"
    store.domain = "INSTANCE"
    store.inputs["Name"].default_value = "id"
    link(nodes.new("NodeGroupInput").outputs[0], store.inputs["Geometry"])
    link(nodes.new("GeometryNodeInputIndex").outputs[0], socket(store.inputs, "Value"))
    link(store.outputs["Geometry"], nodes.new("NodeGroupOutput").inputs[0])
    return tree


def instance_members(view_layer, parent):
    """The instances of parent in the depsgraph, by top-level instance index.

    The persistent id of an instance holds its id attribute when it has one (Distribute
    Points on Faces makes it), otherwise its index. For the index, a temporary geometry
    nodes modifier sets the ids to the indices while the instances are read; the renders
    keep the real ids.

    Returns:
        dict: {top-level index: ([InstanceMember], {source objects})}, the source of a
            geometry instance (no object behind it, like Object Info without As Instance)
            is the parent
    """
    modifier = None
    if parent.type in NODES_MODIFIER_TYPES:
        modifier = parent.modifiers.new("blendmentation index ids", "NODES")
        modifier.node_group = index_id_nodes()
    try:
        return read_instance_members(bpy.context.evaluated_depsgraph_get(), parent)
    finally:
        if modifier is not None:
            tree = modifier.node_group
            parent.modifiers.remove(modifier)
            bpy.data.node_groups.remove(tree)
            view_layer.update()


def read_instance_members(depsgraph, parent):
    tops, coords = {}, {}
    for instance in depsgraph.object_instances:
        if not instance.is_instance or instance.parent is None or instance.parent.original != parent:
            continue
        obj = instance.object
        path = [index for index in instance.persistent_id if index != INSTANCE_PATH_END]
        if not path or obj.type in NO_GEOMETRY_TYPES:
            continue
        # the evaluated geometry is only valid while iterating, instances of one source share it
        key = (obj.data.as_pointer() if obj.data else obj.as_pointer(), obj.type)
        if key not in coords:
            coords[key] = mesh_coords(obj)
        source = instance.instance_object.original
        source_name = source.name if source != parent else (obj.data.name if obj.data else obj.name)
        name = f"{parent.name}/{'.'.join(map(str, reversed(path)))}/{source_name}"
        member = InstanceMember(parent, source, path[-1], name, obj.type, instance.matrix_world.copy(), coords[key])
        members, sources = tops.setdefault(path[-1], ([], set()))
        members.append(member)
        sources.add(source)
    # the path inside the top-level instance only tells its objects apart
    for top, (members, _) in tops.items():
        if len(members) == 1:
            members[0].name = f"{parent.name}/{top}/{members[0].name.rsplit('/', 1)[1]}"
    return tops


def to_instances(classes, frame=None):
    """Normalizes {class name: [objects or sublists]} to a list of (class name, [objects]).
    An object alone is an instance of one object, the objects of a sublist are one instance.
    An `Instances` is one instance per top-level instance of its parent, with
    `InstanceMember`s for objects; it needs the frame, without it is only checked.
    A class can also be {"instances": [...], setting: value}, see `class_settings`."""
    if not isinstance(classes, dict):
        raise TypeError("classes must be a dict {class name: [objects or sublists of objects]}")
    instances = []
    for class_name, value in classes.items():
        entries, _ = split_class(class_name, value)
        if not isinstance(entries, (list, tuple)):
            raise TypeError(f"Class '{class_name}' must map to a list of objects or sublists of objects")
        for entry in entries:
            if is_instances(entry):
                check_instances(class_name, entry)
                if frame is not None:
                    instances.extend((class_name, group) for group in frame.instance_groups(entry))
                continue
            group = list(entry) if isinstance(entry, (list, tuple)) else [entry]
            if not group:
                raise ValueError(f"Class '{class_name}' has an empty sublist")
            if any(is_instances(obj) for obj in group):
                raise TypeError(f"Class '{class_name}': Instances can't be in a sublist, list it in the class")
            instances.append((class_name, group))
    names = [obj.name for _, group in instances for obj in group]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ValueError(f"Objects {duplicates} are in more than one instance")
    return instances


def class_settings(classes, defaults):
    """{class name: {setting: value}}, where a setting the class sets wins (even None),
    otherwise it is the default (the global BBox argument)."""
    return {name: {**defaults, **split_class(name, value)[1]} for name, value in classes.items()}


def next_index(path):
    """Returns the first free image index in the output directory."""
    taken = [int(match.group(1)) for match in map(INDEX.match, os.listdir(path)) if match]
    return max(taken, default=-1) + 1


def camera_view_coords(scene, camera, obj, depsgraph):
    """Projects the evaluated object's (or InstanceMember's) vertices to normalized camera
    view coordinates.

    Returns:
        tuple: (n, 2) array of x, y in 0-1 (bottom-left origin) of vertices in front of the
            camera, and whether any vertex is behind it
    """
    if isinstance(obj, InstanceMember):
        coords, matrix_world = obj.coords, obj.matrix_world
    else:
        obj_eval = obj.evaluated_get(depsgraph)
        coords, matrix_world = mesh_coords(obj_eval), obj_eval.matrix_world

    to_camera = np.array(camera.matrix_world.inverted() @ matrix_world)
    coords = coords @ to_camera[:3, :3].T + to_camera[:3, 3]
    # the camera looks down its -z axis, drop everything behind it
    in_front = coords[:, 2] < 0
    behind = not in_front.all()
    coords = coords[in_front]

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
    return np.stack([x, y], axis=1), behind


def view_bounds(scene, camera, group, depsgraph):
    """Unclipped normalized bounds of the list of objects, as one object.

    Returns:
        tuple: [x_min, y_min, x_max, y_max] in camera view coordinates (bottom-left origin,
            the frame is 0-1) or None if nothing is in front of the camera, and whether any
            vertex is behind the camera
    """
    projected = [camera_view_coords(scene, camera, obj, depsgraph) for obj in group]
    view = np.concatenate([coords for coords, _ in projected])
    behind = any(obj_behind for _, obj_behind in projected)
    if len(view) == 0:
        return None, behind
    return [view[:, 0].min(), view[:, 1].min(), view[:, 0].max(), view[:, 1].max()], behind


def clip_bounds(bounds):
    """Bounds clipped to the frame, or None if nothing of them is in it."""
    if bounds is None:
        return None
    x_min, y_min, x_max, y_max = np.clip(bounds, 0.0, 1.0)
    if x_min >= x_max or y_min >= y_max:
        return None
    return [x_min, y_min, x_max, y_max]


def truncation(bounds, behind):
    """Fraction of the bounds' area outside the frame. An instance partly behind the
    camera has no meaningful bounds, so it counts as fully truncated (1)."""
    clipped = clip_bounds(bounds)
    if behind or clipped is None:
        return 1.0
    area = (bounds[2] - bounds[0]) * (bounds[3] - bounds[1])
    return float(1.0 - (clipped[2] - clipped[0]) * (clipped[3] - clipped[1]) / area)


def to_pixels(bounds, width, height):
    """Normalized bounds to a pixel bbox [x_min, y_min, x_max, y_max] with top-left origin,
    clipped to the frame, or None if out of it."""
    clipped = clip_bounds(bounds)
    if clipped is None:
        return None
    x_min, y_min, x_max, y_max = clipped
    return [
        float(x_min * width),
        float((1.0 - y_max) * height),
        float(x_max * width),
        float((1.0 - y_min) * height),
    ]


def bbox(scene, camera, group, depsgraph, width, height):
    """Returns the 2D bbox of the list of objects, as one object, in pixels
    [x_min, y_min, x_max, y_max] with top-left image origin, or None if it is not in the frame.
    Occlusion by other objects is not taken into account.
    """
    return to_pixels(view_bounds(scene, camera, group, depsgraph)[0], width, height)


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


def iou_conflict(boxes, limits):
    """Whether two boxes overlap more than their limit, the lower of the two limits
    (a None limit sets none; two None limits mean the pair is never a conflict)."""
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            pair = [limit for limit in (limits[i], limits[j]) if limit is not None]
            if pair and iou(boxes[i], boxes[j]) > min(pair):
                return True
    return False


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


def find_layer(frame, layer_names):
    """Finds a layer of the beauty render (an AOV or a pass) in the multilayer EXR.
    Channels are named <view layer>.<layer>.<channel>, in one or several EXR parts.

    Args:
        layer_names (tuple): names the layer may have in the EXR, the first found is used

    Returns:
        tuple: the EXR path, the part and (channel index, channel name) of every channel
            of the layer, or None when the layer is not in the render result
    """
    multilayer, parts = frame.multilayer()
    return find_layer_in(multilayer, parts, frame.view_layer, layer_names)


def find_layer_in(multilayer, parts, view_layer, layer_names):
    """`find_layer` in any multilayer EXR, parts: the channel names of each of its parts."""
    for layer in layer_names:
        prefix = f"{view_layer.name}.{layer}."
        for part, names in enumerate(parts):
            found = [(i, name[len(prefix):]) for i, name in enumerate(names) if name.startswith(prefix)]
            if found:
                return multilayer, part, found
    return None


def save_layer(frame, layer_names, file_name, file_format, channel_names=None, skip_empty=False):
    """Saves one layer of the beauty render (an AOV or a pass) to <path>/<file_name>.

    Args:
        layer_names (tuple): names the layer may have in the EXR, the first found is used
        channel_names (function): number of channels -> new channel names, None keeps them
        skip_empty (bool): don't write the layer when it is empty (see `layer_empty`)

    Returns:
        tuple: whether the layer is in the render result, and file_name, or None when
            skip_empty skipped it
    """
    layer = find_layer(frame, layer_names)
    if layer is None:
        return False, None
    multilayer, part, found = layer

    found.sort(key=lambda item: CHANNEL_ORDER.index(item[1]) if item[1] in CHANNEL_ORDER else len(CHANNEL_ORDER))
    indices = tuple(i for i, _ in found)
    new_names = channel_names(len(indices)) if channel_names else tuple(name for _, name in found)
    buffer = oiio.ImageBufAlgo.channels(oiio.ImageBuf(multilayer, part, 0), indices, new_names)
    if skip_empty and layer_empty(buffer.get_pixels(oiio.FLOAT), new_names):
        return True, None
    if file_format == "OPEN_EXR":
        buffer.set_write_format(oiio.FLOAT)
    if not buffer.write(os.path.join(frame.path, file_name)):
        raise RuntimeError(f"Cannot write {file_name}: {buffer.geterror()}")
    return True, file_name


def layer_empty(pixels, channel_names):
    """Whether every pixel of a (height, width, channels) layer is 0 (black), ignoring
    alpha unless it is the only channel."""
    channels = [i for i, name in enumerate(channel_names) if name != "A"] or list(range(len(channel_names)))
    return not pixels[:, :, channels].any()


def aov_channel_names(count):
    return ("Y",) if count == 1 else tuple("RGBA"[:count])


def render_ids(scene, groups, file_path, hide_others=False):
    """Renders every group of objects in a flat color encoding its id (1, 2, ...)
    with workbench, other objects in black so they still occlude, or hidden with hide_others.
    The id image is saved to file_path (an EXR).

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
    in_groups = {obj for group in groups for obj in group}
    hidden = [obj for obj in editable if hide_others and obj not in in_groups and not obj.hide_render]
    try:
        for obj in hidden:
            obj.hide_render = True
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
        set_file_format(scene.render.image_settings, "OPEN_EXR")
        scene.render.image_settings.color_mode = "RGBA"
        scene.render.image_settings.color_depth = "32"
        bpy.data.images["Render Result"].save_render(file_path, scene=scene)
    finally:
        for obj in hidden:
            obj.hide_render = False
        for owner, values in saved:
            for name, value in values.items():
                setattr(owner, name, value)
        for obj, color in saved_colors.items():
            obj.color = color

    pixels = oiio.ImageBuf(file_path).get_pixels(oiio.FLOAT)
    ids = np.rint(pixels[:, :, 0] * 255).astype(np.int64) + np.rint(pixels[:, :, 1] * 255).astype(np.int64) * 256
    ids[pixels[:, :, 3] < 0.5] = 0
    return ids


def render_index_ids(scene, view_layer, groups, file_path):
    """Renders the groups with Cycles at 1 sample, every other object hidden, and reads
    the group ids (1, 2, ...) from the Object Index pass, like `index_pass_ids`. The
    multilayer render result is saved to file_path (an EXR).

    Returns:
        np.ndarray: (height, width) array of group ids, 0 = background
    """
    saved = [
        (scene.render, ("use_compositing", "use_sequencer")),
        (scene.cycles, ("samples", "use_denoising", "use_adaptive_sampling")),
        (view_layer, ("use_pass_object_index",)),
    ]
    saved = [(owner, {name: getattr(owner, name) for name in names}) for owner, names in saved]
    editable = [obj for obj in bpy.data.objects if obj.library is None]
    saved_indices = {obj: obj.pass_index for obj in editable}
    in_groups = {obj for group in groups for obj in group}
    hidden = [obj for obj in editable if obj not in in_groups and not obj.hide_render]
    try:
        for obj in hidden:
            obj.hide_render = True
        scene.render.use_compositing = False
        scene.render.use_sequencer = False
        # the pass is written by the first sample, it is not anti-aliased
        scene.cycles.samples = 1
        scene.cycles.use_denoising = False
        scene.cycles.use_adaptive_sampling = False
        view_layer.use_pass_object_index = True
        for obj in editable:
            obj.pass_index = 0
        for group_id, group in enumerate(groups, start=1):
            for obj in group:
                obj.pass_index = group_id

        bpy.ops.render.render()
        set_file_format(scene.render.image_settings, "OPEN_EXR_MULTILAYER")
        scene.render.image_settings.color_depth = "32"
        bpy.data.images["Render Result"].save_render(file_path, scene=scene)
    finally:
        for obj in hidden:
            obj.hide_render = False
        for obj, pass_index in saved_indices.items():
            obj.pass_index = pass_index
        for owner, values in saved:
            for name, value in values.items():
                setattr(owner, name, value)

    layer = find_layer_in(file_path, read_multilayer(file_path), view_layer, PASSES["ObjectIndex"][1])
    if layer is None:
        raise RuntimeError("The Object Index pass is missing in the render result")
    _, part, found = layer
    pixels = oiio.ImageBuf(file_path, part, 0).get_pixels(oiio.FLOAT)
    return np.rint(pixels[:, :, found[0][0]]).astype(np.int64)


def has_instances(groups):
    return any(isinstance(obj, InstanceMember) for group in groups for obj in group)


def instance_id_nodes(lut, keep_own_geometry, hide_others):
    """Geometry nodes group that stores the id of every top-level instance, read from the
    point attribute of the lut object at the instance index, as an instance attribute.
    With hide_others, instances with no id are deleted, and so is the parent's own
    geometry unless keep_own_geometry."""
    tree = node_group("blendmentation ids")
    nodes, link = tree.nodes, tree.links.new
    group_input, group_output = nodes.new("NodeGroupInput"), nodes.new("NodeGroupOutput")
    info = nodes.new("GeometryNodeObjectInfo")
    info.inputs["Object"].default_value = lut
    attribute = nodes.new("GeometryNodeInputNamedAttribute")
    attribute.data_type = "INT"
    attribute.inputs["Name"].default_value = ID_ATTRIBUTE
    sample = nodes.new("GeometryNodeSampleIndex")
    sample.data_type = "INT"
    sample.domain = "POINT"
    # instances past the lut get its last value, -1
    sample.clamp = True
    link(info.outputs["Geometry"], sample.inputs["Geometry"])
    link(socket(attribute.outputs, "Attribute"), socket(sample.inputs, "Value"))
    link(nodes.new("GeometryNodeInputIndex").outputs[0], sample.inputs["Index"])
    store = nodes.new("GeometryNodeStoreNamedAttribute")
    store.data_type = "INT"
    store.domain = "INSTANCE"
    store.inputs["Name"].default_value = ID_ATTRIBUTE
    link(socket(sample.outputs, "Value"), socket(store.inputs, "Value"))
    geometry = group_input.outputs[0]
    if hide_others and not keep_own_geometry:
        separate = nodes.new("GeometryNodeSeparateComponents")
        link(geometry, separate.inputs[0])
        geometry = separate.outputs["Instances"]
    link(geometry, store.inputs["Geometry"])
    geometry = store.outputs["Geometry"]
    if hide_others:
        compare = nodes.new("FunctionNodeCompare")
        compare.data_type = "FLOAT"
        compare.operation = "LESS_THAN"
        link(socket(sample.outputs, "Value"), compare.inputs[0])
        compare.inputs[1].default_value = 0.5
        delete = nodes.new("GeometryNodeDeleteGeometry")
        delete.domain = "INSTANCE"
        link(geometry, delete.inputs["Geometry"])
        link(compare.outputs[0], delete.inputs["Selection"])
        geometry = delete.outputs["Geometry"]
    link(geometry, group_output.inputs[0])
    return tree


def instance_id_material(hide_others):
    """Emission material showing the id: the instance attribute of an instance that has one
    (-1, an instance of no group, is 0), otherwise the object's custom property. With
    hide_others, what has no id is transparent."""
    material = bpy.data.materials.new("blendmentation ids")
    # blender 5 materials always have nodes, use_nodes is deprecated
    if material.node_tree is None:
        material.use_nodes = True
    nodes, link = material.node_tree.nodes, material.node_tree.links.new
    nodes.clear()

    def math(operation, a, b=None):
        node = nodes.new("ShaderNodeMath")
        node.operation = operation
        for socket, value in zip(node.inputs, (a, b)):
            if isinstance(value, (int, float)):
                socket.default_value = value
            elif value is not None:
                link(value, socket)
        return node.outputs[0]

    attributes = {}
    for kind in ("INSTANCER", "OBJECT"):
        node = nodes.new("ShaderNodeAttribute")
        node.attribute_type = kind
        node.attribute_name = ID_ATTRIBUTE
        attributes[kind] = node.outputs["Fac"]
    instance_id, object_id = attributes["INSTANCER"], attributes["OBJECT"]
    is_instance = math("GREATER_THAN", math("ABSOLUTE", instance_id), 0.5)
    # object_id + is_instance * (max(instance_id, 0) - object_id)
    difference = math("SUBTRACT", math("MAXIMUM", instance_id, 0.0), object_id)
    group_id = math("ADD", object_id, math("MULTIPLY", is_instance, difference))
    emission = nodes.new("ShaderNodeEmission")
    link(group_id, emission.inputs["Color"])
    surface = emission.outputs[0]
    if hide_others:
        mix = nodes.new("ShaderNodeMixShader")
        link(math("GREATER_THAN", group_id, 0.5), mix.inputs[0])
        link(nodes.new("ShaderNodeBsdfTransparent").outputs[0], mix.inputs[1])
        link(surface, mix.inputs[2])
        surface = mix.outputs[0]
    output = nodes.new("ShaderNodeOutputMaterial")
    link(surface, output.inputs["Surface"])
    return material


def render_instance_ids(scene, view_layer, groups, file_path, hide_others=False):
    """Renders the group ids of groups with instances (`InstanceMember`s) with Cycles at
    1 sample and an emission material override, whatever the engine. A temporary geometry
    nodes modifier on each parent stores the ids of its top-level instances as an instance
    attribute; objects get theirs as a custom property. Everything is restored afterwards.
    Without hide_others, all other objects and instances are 0 and still occlude;
    with it, they are hidden.

    Returns:
        np.ndarray: (height, width) array of group ids, 0 = background or other objects
    """
    names = [
        (scene.render, ("engine", "film_transparent", "use_compositing", "use_sequencer", "use_motion_blur")),
        (scene.cycles, ("samples", "use_denoising", "use_adaptive_sampling", "pixel_filter_type", "filter_width",
                        "film_exposure", "sample_clamp_direct", "sample_clamp_indirect", "transparent_max_bounces")),
        (view_layer, ("material_override", "samples")),
        (scene.view_settings, ("view_transform", "look", "exposure", "gamma", "use_curve_mapping")),
    ]
    saved = [(owner, {name: getattr(owner, name) for name in names if hasattr(owner, name)}) for owner, names in names]
    editable = [obj for obj in bpy.data.objects if obj.library is None]
    objects, parents, sources = {}, {}, set()
    for group_id, group in enumerate(groups, start=1):
        for obj in group:
            if isinstance(obj, InstanceMember):
                parents.setdefault(obj.parent, {})[obj.top] = group_id
                sources.add(obj.source)
            else:
                objects[obj] = group_id
    # hiding a source hides its instances in blender 4.0, the material hides what has no id
    hidden = [obj for obj in editable if hide_others and obj not in objects and obj not in parents
              and obj not in sources and not obj.hide_render]
    saved_properties = {obj: obj.get(ID_ATTRIBUTE) for obj in objects}
    created, modifiers = [], []
    try:
        for obj in hidden:
            obj.hide_render = True
        for obj, group_id in objects.items():
            obj[ID_ATTRIBUTE] = float(group_id)
        for parent, tops in parents.items():
            lut_ids = [-1] * (max(tops) + 2)
            for top, group_id in tops.items():
                lut_ids[top] = group_id
            mesh = bpy.data.meshes.new("blendmentation ids")
            mesh.vertices.add(len(lut_ids))
            mesh.attributes.new(ID_ATTRIBUTE, "INT", "POINT").data.foreach_set("value", lut_ids)
            lut = bpy.data.objects.new("blendmentation ids", mesh)
            tree = instance_id_nodes(lut, parent in objects, hide_others)
            created += [lut, mesh, tree]
            modifier = parent.modifiers.new("blendmentation ids", "NODES")
            modifier.node_group = tree
            modifiers.append((parent, modifier))
        material = instance_id_material(hide_others)
        created.append(material)

        scene.render.engine = "CYCLES"
        scene.render.film_transparent = True
        scene.render.use_compositing = False
        scene.render.use_sequencer = False
        scene.render.use_motion_blur = False
        cycles = scene.cycles
        cycles.samples = 1
        cycles.use_denoising = False
        cycles.use_adaptive_sampling = False
        # one sample near the pixel center, the ids are not blended
        cycles.pixel_filter_type = "BOX"
        cycles.filter_width = 0.01
        cycles.film_exposure = 1.0
        cycles.sample_clamp_direct = 0.0
        cycles.sample_clamp_indirect = 0.0
        cycles.transparent_max_bounces = 1024
        view_layer.material_override = material
        if hasattr(view_layer, "samples"):
            view_layer.samples = 0
        view_settings = scene.view_settings
        view_settings.view_transform = "Standard"
        view_settings.look = "None"
        view_settings.exposure = 0.0
        view_settings.gamma = 1.0
        view_settings.use_curve_mapping = False

        bpy.ops.render.render()
        set_file_format(scene.render.image_settings, "OPEN_EXR")
        scene.render.image_settings.color_mode = "RGBA"
        scene.render.image_settings.color_depth = "32"
        bpy.data.images["Render Result"].save_render(file_path, scene=scene)
    finally:
        for parent, modifier in modifiers:
            parent.modifiers.remove(modifier)
        for owner, values in saved:
            for name, value in values.items():
                setattr(owner, name, value)
        for datablock in created:
            collection = {bpy.types.Object: bpy.data.objects, bpy.types.Mesh: bpy.data.meshes,
                          bpy.types.Material: bpy.data.materials}.get(type(datablock), bpy.data.node_groups)
            collection.remove(datablock)
        for obj, value in saved_properties.items():
            if value is None:
                del obj[ID_ATTRIBUTE]
            else:
                obj[ID_ATTRIBUTE] = value
        for obj in hidden:
            obj.hide_render = False

    pixels = oiio.ImageBuf(file_path).get_pixels(oiio.FLOAT)
    ids = np.rint(pixels[:, :, 0]).astype(np.int64)
    ids[pixels[:, :, 3] < 0.5] = 0
    return ids


def write_mask(path, file_name, mask):
    """Writes a boolean (height, width) mask as a black and white PNG."""
    height, width = mask.shape
    buffer = oiio.ImageBuf(oiio.ImageSpec(width, height, 1, oiio.UINT8))
    buffer.set_pixels(oiio.ROI(), np.where(mask, 255, 0).astype(np.uint8).reshape(height, width, 1))
    if not buffer.write(os.path.join(path, file_name)):
        raise RuntimeError(f"Cannot write mask {file_name}: {buffer.geterror()}")
    return file_name


def save_masks(ids, instances, path, index, per, skip_empty=False, previous=(), full=None, previous_full=()):
    """Saves black and white mask PNGs of the visible pixels, per instance as
    <path>/<index>_mask_<n>.png and/or per class as <path>/<index>_mask_<class>.png,
    and the full masks as <path>/<index>_mask_<n>_full.png. A name that is already taken
    gets _2, _3, ... before the extension.

    Args:
        ids (np.ndarray): id image of the instances, from `render_ids`
        instances (list): (class name, [objects]) pairs
        per (str): "instance", "class" or "both"
        skip_empty (bool): don't write empty masks, their "mask" is None
        previous (list): mask entries of earlier Segmentation steps of the datapoint,
            n continues after their instances and their files are not overwritten
        full (list): full mask (np.ndarray) of every instance, None for no full masks
        previous_full (list): full mask entries of earlier Segmentation steps

    Returns:
        tuple: {"class", "objects", "mask", "per"} for every mask, and
            {"class", "objects", "mask"} for every full mask
    """
    taken = {entry["mask"] for entry in list(previous) + list(previous_full)}
    start = sum(entry["per"] == "instance" for entry in previous)

    def mask_file(name, mask):
        if skip_empty and not mask.any():
            return None
        file_name, copy = f"{index:06d}_mask_{name}.png", 2
        while file_name in taken:
            file_name, copy = f"{index:06d}_mask_{name}_{copy}.png", copy + 1
        taken.add(file_name)
        return write_mask(path, file_name, mask)

    entries = []
    if per in ("instance", "both"):
        for number, (class_name, group) in enumerate(instances):
            file_name = mask_file(start + number, ids == number + 1)
            entries.append({"class": class_name, "objects": [obj.name for obj in group], "mask": file_name, "per": "instance"})
    if per in ("class", "both"):
        for class_name in dict.fromkeys(class_name for class_name, _ in instances):
            numbers = [number for number, (name, _) in enumerate(instances) if name == class_name]
            safe_name = re.sub(r"[^\w.-]", "_", class_name)
            file_name = mask_file(safe_name, np.isin(ids, [n + 1 for n in numbers]))
            objects = [obj.name for number in numbers for obj in instances[number][1]]
            entries.append({"class": class_name, "objects": objects, "mask": file_name, "per": "class"})
    full_entries = []
    for number, ((class_name, group), mask) in enumerate(zip(instances, full or ())):
        file_name = mask_file(f"{len(previous_full) + number}_full", mask)
        full_entries.append({"class": class_name, "objects": [obj.name for obj in group], "mask": file_name})
    return entries, full_entries


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


def collection_objects(layer_collection):
    """Objects in the render of a view layer's collections: in a collection that is not
    excluded from the view layer or disabled in renders, and neither is any of its parents."""
    if layer_collection.exclude or layer_collection.collection.hide_render:
        return set()
    objects = set(layer_collection.collection.objects)
    for child in layer_collection.children:
        objects |= collection_objects(child)
    return objects


def group_key(groups):
    return tuple(tuple(obj.name for obj in group) for group in groups)


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
        # (height, width, 3) 8 bit sRGB image under the beauty render, set by Background
        self.background = None
        self.id_images = {}
        # groups whose ids are the pass indices of the beauty render, set by Segmentation in Cycles
        self.index_groups = None
        self.changed = []
        self.collection_objects = collection_objects(self.view_layer.layer_collection)
        self.instances = {}

    def in_render(self, obj):
        """Whether obj is in the render: not hidden itself (e.g. by Visibility) or by its
        collections. An InstanceMember is when its parent is."""
        if isinstance(obj, InstanceMember):
            obj = obj.parent
        return not obj.hide_render and obj in self.collection_objects

    def instance_groups(self, spec):
        """The groups of InstanceMembers of an `Instances`: one per top-level instance of its
        parent with an object of `of`, none when the parent is not in the render."""
        parent = spec.parent
        if not self.in_render(parent):
            return []
        if parent not in self.instances:
            self.instances[parent] = instance_members(self.view_layer, parent)
        sources = instance_sources(spec.of)
        return [members for _, (members, found) in sorted(self.instances[parent].items())
                if sources is None or found & sources]

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
        (height, width, 4) array, over the background when there is one. Saved once, every
        call returns a new copy to draw on."""
        if self.beauty_rgba is None:
            self.beauty()
            file_path = os.path.join(self.temp_dir, "beauty.png")
            image_settings = self.scene.render.image_settings
            set_file_format(image_settings, "PNG")
            image_settings.color_mode = "RGBA"
            image_settings.color_depth = "8"
            bpy.data.images["Render Result"].save_render(file_path, scene=self.scene)
            # OpenImageIO premultiplies the colors by alpha when reading a PNG
            pixels = oiio.ImageBuf(file_path).get_pixels(oiio.UINT8)
            if self.background is not None:
                pixels = over_background(pixels, self.background)
            self.beauty_rgba = pixels
        return np.array(self.beauty_rgba)

    def beauty_float(self):
        """Linear 32 bit float RGBA beauty render (premultiplied), as a (height, width, 4) array."""
        self.beauty()
        file_path = os.path.join(self.temp_dir, "beauty.exr")
        image_settings = self.scene.render.image_settings
        set_file_format(image_settings, "OPEN_EXR")
        image_settings.color_mode = "RGBA"
        image_settings.color_depth = "32"
        bpy.data.images["Render Result"].save_render(file_path, scene=self.scene)
        return oiio.ImageBuf(file_path).get_pixels(oiio.FLOAT)

    def replace_render(self):
        """Call before a render that replaces the beauty Render Result, like the segmentation."""
        if self.rendered and self.keep_beauty:
            self.beauty_pixels()
        self.rendered = False

    def ids(self, groups, hide_others=False):
        """Id image of the groups of objects (see `render_ids`), rendered once per groups
        and hide_others, so BBox occlusion and Segmentation share it."""
        key = (group_key(groups), hide_others)
        if key not in self.id_images:
            self.replace_render()
            file_path = os.path.join(self.temp_dir, f"ids_{len(self.id_images)}.exr")
            if has_instances(groups):
                self.id_images[key] = render_instance_ids(self.scene, self.view_layer, groups, file_path, hide_others)
            else:
                self.id_images[key] = render_ids(self.scene, groups, file_path, hide_others)
        return self.id_images[key]

    def full_ids(self, groups):
        """Id image of the groups with every other object hidden, so nothing occludes
        them: in Cycles from the Object Index pass of a 1 sample render (see
        `render_index_ids`), otherwise from the workbench (`ids`, shared with BBox occlusion)."""
        if self.scene.render.engine != "CYCLES" or has_instances(groups):
            return self.ids(groups, hide_others=True)
        key = (group_key(groups), "index")
        if key not in self.id_images:
            self.replace_render()
            file_path = os.path.join(self.temp_dir, f"ids_{len(self.id_images)}.exr")
            self.id_images[key] = render_index_ids(self.scene, self.view_layer, groups, file_path)
        return self.id_images[key]

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


def check_bboxes(classes, settings):
    to_instances(classes)
    for name, value in settings.items():
        check_threshold(name, value, "BBox")
    limits = class_settings(classes, settings)
    check_instance_renders({name: value for name, value in classes.items()
                            if limits[name]["max_occlusion"] is not None}, "BBox max_occlusion")


def check_instance_renders(classes, step_name):
    """Raises if the instances of a class can't be in an id render: their parent needs a
    temporary geometry nodes modifier."""
    for class_name, value in classes.items():
        for entry in split_class(class_name, value)[0]:
            if is_instances(entry) and entry.parent.type not in NODES_MODIFIER_TYPES:
                raise TypeError(
                    f"{step_name} of the instances of '{entry.parent.name}' (class '{class_name}') needs geometry "
                    f"nodes on it, a {entry.parent.type.lower()} can't have them: make the instances with a "
                    "geometry nodes modifier (Collection Info, Object Info) on a mesh instead"
                )


def occlusions(frame, groups):
    """Fraction of each group's pixels that objects in no group cover: 1 - its pixels with
    everything rendered / its pixels with only the groups rendered. Groups covering each
    other count in both, so only other objects occlude. A group with no pixels without the
    other objects (out of frame, or behind another group) is not occluded by them: 0."""
    counts = [np.bincount(frame.ids(groups, hide_others).ravel(), minlength=len(groups) + 1)[1:]
              for hide_others in (True, False)]
    alone, visible = counts
    return [max(0.0, 1.0 - float(visible[i] / alone[i])) if alone[i] else 0.0 for i in range(len(groups))]


def bboxes(frame, classes, settings):
    """Adds {"class", "objects", "bbox"} of every instance to the labels under "bboxes".

    Args:
        settings (dict): the BBox arguments iou_deconflict, max_truncation and max_occlusion,
            the defaults for the classes

    Returns:
        bool: False if two bboxes overlap over iou_deconflict, an instance is more than
            max_truncation out of frame or more than max_occlusion covered by objects in no
            class, and the datapoint should be skipped. Objects hidden in the render are left
            out, an instance with all of them hidden has bbox None and never skips.
    """
    settings = class_settings(classes, settings)
    instances = to_instances(classes, frame)
    entries, boxes, limits = [], [], []
    for class_name, group in instances:
        entry = {"class": class_name, "objects": [obj.name for obj in group], "bbox": None}
        entries.append(entry)
        # objects hidden in the render are not in the image, an instance of only them has no bbox
        rendered = [obj for obj in group if frame.in_render(obj)]
        if not rendered:
            continue
        bounds, behind = view_bounds(frame.scene, frame.camera, rendered, frame.depsgraph)
        limit = settings[class_name]["max_truncation"]
        if limit is not None and truncation(bounds, behind) > limit:
            return False
        box = entry["bbox"] = to_pixels(bounds, frame.width, frame.height)
        if box is not None:
            boxes.append(box)
            limits.append(settings[class_name]["iou_deconflict"])
    if iou_conflict(boxes, limits):
        return False
    # last, it needs two workbench renders
    occlusion_limits = [settings[class_name]["max_occlusion"] for class_name, _ in instances]
    if any(limit is not None for limit in occlusion_limits):
        occluded = occlusions(frame, [group for _, group in instances])
        if any(limit is not None and value > limit for value, limit in zip(occluded, occlusion_limits)):
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


# blender cameras look down -z with y up, OpenCV cameras look down +z with y down
BLENDER_TO_OPENCV = Matrix(((1, 0, 0), (0, -1, 0), (0, 0, -1)))


def check_poses(classes):
    to_instances(classes)


def poses(frame, classes):
    """Adds {"class", "objects", "R", "t", "scale"} of every instance to the labels under
    "poses": the pose of its first object in the OpenCV camera frame, R row by row and t in
    blender units, with the scale removed and saved separately. An instance with all its
    objects hidden in the render has R, t and scale None."""
    camera_location, camera_rotation, _ = frame.camera.matrix_world.decompose()
    world_to_camera = BLENDER_TO_OPENCV @ camera_rotation.to_matrix().transposed()
    entries = []
    for class_name, group in to_instances(classes, frame):
        entry = {"class": class_name, "objects": [obj.name for obj in group], "R": None, "t": None, "scale": None}
        if any(frame.in_render(obj) for obj in group):
            location, rotation, scale = group[0].matrix_world.decompose()
            entry.update(
                R=[list(row) for row in world_to_camera @ rotation.to_matrix()],
                t=list(world_to_camera @ (location - camera_location)),
                scale=list(scale),
            )
        entries.append(entry)
    frame.add("poses", entries)


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
    """Renders the image to <path>/<index>.<ext>. Over a background it is written as RGB,
    8 bit for PNG and JPEG, 32 bit float for EXR."""
    file_name = frame.file_name(RENDER_FORMATS[file_format])
    file_path = os.path.join(frame.path, file_name)
    if frame.background is None:
        set_file_format(frame.scene.render.image_settings, file_format)
        frame.beauty(file_path)
    elif file_format == "OPEN_EXR":
        pixels = frame.beauty_float()
        # the background is sRGB, the EXR linear
        rgb = pixels[:, :, :3] + srgb_to_linear(frame.background) * (1.0 - pixels[:, :, 3:])
        write_pixels(file_path, rgb.astype(np.float32), oiio.FLOAT)
    else:
        quality = frame.scene.render.image_settings.quality if file_format == "JPEG" else None
        write_pixels(file_path, frame.beauty_pixels()[:, :, :3], oiio.UINT8, quality)
    frame.label["image"] = file_name


def check_background(weights, noise_size, images_path):
    """Raises if the background can't be used."""
    render = bpy.context.scene.render
    if not render.film_transparent:
        raise ValueError("Background needs a transparent render, enable Render Properties > Film > Transparent")
    if render.image_settings.color_mode != "RGBA":
        raise ValueError("Background needs RGBA output, set Output Properties > Output > Color to RGBA")
    if not (isinstance(noise_size, (list, tuple)) and len(noise_size) == 2
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in noise_size)
            and 0 < noise_size[0] <= noise_size[1]):
        raise ValueError(f"Background noise_size must be (min, max) pixels with 0 < min <= max, got {noise_size!r}")
    if background_weights(weights, images_path)["image"]:
        if not images_path:
            raise ValueError("Background weights use the image mode, give images_path, a folder of images")
        if not os.path.isdir(bpy.path.abspath(images_path)):
            raise ValueError(f"Background images_path '{images_path}' is not a folder")


def background_images(weights, images_path):
    """The images in images_path and its subfolders, an empty list when the weights never
    pick the image mode."""
    if not background_weights(weights, images_path)["image"]:
        return []
    images = sorted(
        os.path.join(root, name)
        for root, _, names in os.walk(bpy.path.abspath(images_path))
        for name in names
        if name.lower().endswith(BACKGROUND_IMAGE_EXTENSIONS)
    )
    if not images:
        raise ValueError(f"Background images_path '{images_path}' has no {', '.join(BACKGROUND_IMAGE_EXTENSIONS)} images")
    return images


def background_weights(weights, images_path):
    """{mode: weight} for every mode. None = equal weights, the image mode only with
    images_path; modes missing from the weights get 0."""
    if weights is None:
        return {mode: 0.0 if mode == "image" and not images_path else 1.0 for mode in BACKGROUND_MODES}
    if not isinstance(weights, dict):
        raise TypeError(f"Background weights must be a dict {{mode: weight}}, modes: {', '.join(BACKGROUND_MODES)}")
    unknown = sorted(set(weights) - set(BACKGROUND_MODES))
    if unknown:
        raise ValueError(f"Unknown background modes {unknown}, available: {', '.join(BACKGROUND_MODES)}")
    if not all(isinstance(w, (int, float)) and not isinstance(w, bool) and w >= 0 for w in weights.values()):
        raise ValueError("Background weights must be numbers >= 0")
    if not sum(weights.values()):
        raise ValueError("Background weights are all 0, at least one mode needs a weight")
    return {mode: float(weights.get(mode, 0)) for mode in BACKGROUND_MODES}


def noise(rng, height, width, cell_size, channels):
    """Random 8 bit (height, width, channels) noise in square cells of cell_size pixels."""
    rows = (np.arange(height) / cell_size).astype(np.int64)
    columns = (np.arange(width) / cell_size).astype(np.int64)
    cells = rng.integers(0, 256, (rows[-1] + 1, columns[-1] + 1, channels), dtype=np.uint8)
    return cells[rows][:, columns]


def cover_image(file_path, width, height, rng):
    """An image scaled to cover width x height and cropped there at a random position,
    as 8 bit (height, width, 3) RGB. Gray images become RGB, alpha is dropped."""
    source = oiio.ImageBuf(file_path)
    if source.has_error:
        raise RuntimeError(f"Cannot read background image {file_path}: {source.geterror()}")
    spec = source.spec()
    source = oiio.ImageBufAlgo.channels(source, (0, 0, 0) if spec.nchannels < 3 else (0, 1, 2))
    scale = max(width / spec.width, height / spec.height)
    cover_width, cover_height = max(width, round(spec.width * scale)), max(height, round(spec.height * scale))
    resized = oiio.ImageBufAlgo.resize(source, roi=oiio.ROI(0, cover_width, 0, cover_height, 0, 1, 0, 3))
    pixels = resized.get_pixels(oiio.UINT8)
    top = int(rng.integers(0, cover_height - height + 1))
    left = int(rng.integers(0, cover_width - width + 1))
    return pixels[top:top + height, left:left + width]


def background(frame, weights, noise_size, images_path, images):
    """Picks a background mode by the weights, makes the background image for the beauty
    render and adds {"mode", ...} to the labels under "background"."""
    weights = background_weights(weights, images_path)
    mode = random.choices(BACKGROUND_MODES, weights=[weights[mode] for mode in BACKGROUND_MODES])[0]
    # seeded from random, so random.seed() makes the backgrounds reproducible too
    rng = np.random.default_rng(random.getrandbits(64))
    height, width = frame.height, frame.width
    info: dict = {"mode": mode}
    if mode == "color":
        color = rng.integers(0, 256, 3, dtype=np.uint8)
        pixels = np.broadcast_to(color, (height, width, 3))
        info["color"] = color.tolist()
    elif mode in ("white_noise", "color_noise"):
        size = random.uniform(*noise_size)
        pixels = noise(rng, height, width, size, 1 if mode == "white_noise" else 3)
        pixels = np.broadcast_to(pixels, (height, width, 3))
        info["noise_size"] = size
    else:
        file_path = random.choice(images)
        pixels = cover_image(file_path, width, height, rng)
        info["image"] = os.path.relpath(file_path, bpy.path.abspath(images_path))
    frame.background = np.array(pixels)
    frame.label["background"] = info


def srgb_to_linear(pixels):
    """8 bit sRGB to linear float."""
    values = pixels / 255.0
    return np.where(values <= 0.04045, values / 12.92, ((values + 0.055) / 1.055) ** 2.4)


def over_background(pixels, background):
    """Premultiplied 8 bit RGBA pixels over an 8 bit RGB background, opaque 8 bit RGBA."""
    alpha = pixels[:, :, 3:] / 255.0
    rgb = np.clip(np.rint(pixels[:, :, :3] + background * (1.0 - alpha)), 0, 255).astype(np.uint8)
    return np.concatenate([rgb, np.full(alpha.shape, 255, dtype=np.uint8)], axis=2)


def write_pixels(file_path, pixels, pixel_type, quality=None):
    """Writes a (height, width, channels) array, with the JPEG quality when given."""
    height, width, channels = pixels.shape
    spec = oiio.ImageSpec(width, height, channels, pixel_type)
    if quality is not None:
        spec.attribute("Compression", f"jpeg:{quality}")
    buffer = oiio.ImageBuf(spec)
    buffer.set_pixels(oiio.ROI(), np.ascontiguousarray(pixels))
    if not buffer.write(file_path):
        raise RuntimeError(f"Cannot write {os.path.basename(file_path)}: {buffer.geterror()}")


def aov_images(frame, names, file_format, skip_empty=False):
    """Saves the AOVs to <path>/<index>_<aov>.<ext>, from the beauty render.
    With skip_empty, an empty AOV isn't written and its file is None."""
    files = {}
    for name in names:
        file_name = frame.file_name(f"_{name}{AOV_FORMATS[file_format]}")
        found, files[name] = save_layer(frame, (name,), file_name, file_format, aov_channel_names, skip_empty)
        if not found:
            raise RuntimeError(f"AOV '{name}' is missing in the render result")
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


def render_passes(frame, names, file_format, skip_empty=False):
    """Saves the built-in passes to <path>/<index>_<pass>.<ext>, from the beauty render.
    With skip_empty, an empty pass isn't written and its file is None."""
    files = {}
    for name in names:
        file_name = frame.file_name(f"_{name}{AOV_FORMATS[file_format]}")
        found, files[name] = save_layer(frame, PASSES[name][1], file_name, file_format, skip_empty=skip_empty)
        if not found:
            hint = ", Vector also needs motion blur off" if name == "Vector" else ""
            raise RuntimeError(f"Pass '{name}' was not rendered, {frame.scene.render.engine} may not support it{hint}")
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
    write_pixels(os.path.join(frame.path, file_name), pixels, oiio.UINT8)
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
        # a mask skipped by skip_empty has no pixels to draw
        if entry["per"] == per and entry["mask"] is not None:
            mask = oiio.ImageBuf(os.path.join(frame.path, entry["mask"])).get_pixels(oiio.UINT8)[:, :, 0] > 127
            masks.append((class_color(class_names, entry["class"]), mask, entry["class"]))

    rgb = pixels[:, :, :3].astype(np.float64)
    for color, mask, _ in masks:
        rgb[mask] = (1 - opacity) * rgb[mask] + opacity * np.array(color)
    pixels[:, :, :3] = np.rint(rgb).astype(np.uint8)
    if pixels.shape[2] == 4 and masks:
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


def focus_distance(camera):
    """Distance from the camera to its focal plane, as Blender computes it: along the view
    axis to the focus object (or its bone), otherwise the focus distance setting."""
    dof = camera.data.dof
    target = dof.focus_object
    if target is None:
        return dof.focus_distance
    location = target.matrix_world.translation
    bone = getattr(dof, "focus_subtarget", "")
    if bone and target.pose and bone in target.pose.bones:
        location = target.matrix_world @ target.pose.bones[bone].head
    forward = camera.matrix_world.col[2].xyz.normalized()
    return max(abs(forward.dot(camera.matrix_world.translation - location)), 1e-5)


def depth_of_field(camera):
    dof = camera.data.dof
    return {
        "use_dof": dof.use_dof,
        "focus_object": to_json(dof.focus_object),
        "focus_distance": focus_distance(camera),
        "f_stop": dof.aperture_fstop,
        "aperture_blades": dof.aperture_blades,
        "aperture_rotation": dof.aperture_rotation,
        "aperture_ratio": dof.aperture_ratio,
    }


def camera_data(frame):
    """Adds the camera settings, depth of field, intrinsics and extrinsics to the labels under "camera"."""
    camera = frame.camera
    data = camera.data
    location, rotation, _ = camera.matrix_world.decompose()
    world_to_camera = BLENDER_TO_OPENCV @ rotation.to_matrix().transposed()
    translation = -(world_to_camera @ location)
    info = {
        "name": camera.name,
        "type": data.type,
        "matrix_world": to_json(camera.matrix_world),
        "extrinsics_opencv": [list(row) + [translation[i]] for i, row in enumerate(world_to_camera)],
        "clip_start": data.clip_start,
        "clip_end": data.clip_end,
        "depth_of_field": depth_of_field(camera),
        "unit_scale": frame.scene.unit_settings.scale_length,
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


def keypoint_object(source):
    """Object a keypoint source is on, None for a point."""
    if isinstance(source, bpy.types.Object):
        return source
    if isinstance(source, (list, tuple)) and source and isinstance(source[0], bpy.types.Object):
        return source[0]
    return None


def keypoint_visible(frame, location):
    """True if nothing rendered is between the camera and the location. The depsgraph also
    has the objects hidden in the render, rays pass through them."""
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
    direction = direction.normalized()
    # a point on a surface hits that surface itself, at its own distance
    tolerance = max(1e-4, distance * 1e-4)
    start = origin
    while True:
        remaining = distance - (start - origin).length
        if remaining <= 0:
            return True
        hit, hit_location, _, _, hit_object, hit_matrix = frame.scene.ray_cast(
            frame.depsgraph, start, direction, distance=remaining)
        if not hit or (hit_location - origin).length >= distance - tolerance:
            return True
        # an instance (geometry nodes, collection) renders whether or not its source object does
        if hit_matrix != hit_object.matrix_world or frame.in_render(hit_object.original):
            return False
        start = hit_location + direction * max(1e-5, distance * 1e-6)


def keypoints(frame, points):
    """Adds {"name", "position", "depth", "in_frame", "visible"} of every keypoint to the labels
    under "keypoints". position is in pixels from the top left corner."""
    entries = []
    for name, source in points.items():
        location = keypoint_location(source, frame.depsgraph)
        view = world_to_camera_view(frame.scene, frame.camera, location)
        in_frame = 0.0 <= view.x <= 1.0 and 0.0 <= view.y <= 1.0 and view.z > 0
        obj = keypoint_object(source)
        # a keypoint on an object hidden in the render is not in the image
        rendered = obj is None or frame.in_render(obj)
        entries.append({
            "name": name,
            "position": [view.x * frame.width, (1.0 - view.y) * frame.height],
            "depth": view.z,
            "in_frame": in_frame,
            "visible": in_frame and rendered and keypoint_visible(frame, location),
        })
    frame.add("keypoints", entries)


def check_segmentation(classes, per):
    if per not in ("instance", "class", "both"):
        raise ValueError('Segmentation per must be "instance", "class" or "both"')
    to_instances(classes)
    check_instance_renders(classes, "Segmentation")


def prepare_segmentation(frame, classes):
    """In Cycles, the masks come from the Object Index pass of the beauty render, which
    respects alpha (pass_alpha_threshold), displacement and hair: every instance gets its
    id as pass index, all other objects 0. Other engines, a second Segmentation with other
    classes, or more instances than pass indices use the workbench id render, and
    geometry nodes instances the Cycles one of `render_instance_ids`."""
    groups = [group for _, group in to_instances(classes, frame)]
    if (frame.scene.render.engine != "CYCLES" or frame.index_groups is not None or len(groups) > MAX_PASS_INDEX
            or has_instances(groups)):
        return
    frame.set(frame.view_layer, "use_pass_object_index", True)
    # instanced collections may use objects that are not in the scene, so all objects are set
    for obj in bpy.data.objects:
        if obj.library is None and obj.pass_index:
            frame.set(obj, "pass_index", 0)
    for group_id, group in enumerate(groups, start=1):
        for obj in group:
            frame.set(obj, "pass_index", group_id)
    frame.index_groups = group_key(groups)


def index_pass_ids(frame):
    """Id image of the instances from the Object Index pass of the beauty render, like `render_ids`."""
    layer = find_layer(frame, PASSES["ObjectIndex"][1])
    if layer is None:
        raise RuntimeError("The Object Index pass is missing in the render result")
    multilayer, part, found = layer
    pixels = oiio.ImageBuf(multilayer, part, 0).get_pixels(oiio.FLOAT)
    # not anti-aliased, the pass holds whole numbers
    return np.rint(pixels[:, :, found[0][0]]).astype(np.int64)


# object types whose evaluated mesh bounds their silhouette, for packing full mask renders
PACKABLE_TYPES = {"MESH", "CURVE", "SURFACE", "META", "FONT"}
# pixels around the projected boxes, the pixel filter spreads a silhouette a little
PACK_MARGIN = 2


def full_mask_packs(frame, groups):
    """Splits the groups into packs that can be rendered together for their full masks:
    groups whose projected boxes (plus a margin) don't overlap can't hide each other.
    Groups that are hidden in the render or out of frame are in no pack (empty mask),
    groups whose box is unknown (other object types, partly behind the camera) are
    alone in their pack.

    Returns:
        list: lists of group numbers
    """
    packs, pack_boxes = [], []
    for number, group in enumerate(groups):
        rendered = [obj for obj in group if frame.in_render(obj)]
        if not rendered:
            continue
        if any(obj.type not in PACKABLE_TYPES for obj in rendered):
            packs.append([number])
            pack_boxes.append(None)
            continue
        bounds, behind = view_bounds(frame.scene, frame.camera, rendered, frame.depsgraph)
        if behind:
            packs.append([number])
            pack_boxes.append(None)
            continue
        box = to_pixels(bounds, frame.width, frame.height)
        if box is None:
            continue
        box = [box[0] - PACK_MARGIN, box[1] - PACK_MARGIN, box[2] + PACK_MARGIN, box[3] + PACK_MARGIN]
        for pack, boxes in zip(packs, pack_boxes):
            if boxes is not None and all(iou(box, other) == 0 for other in boxes):
                pack.append(number)
                boxes.append(box)
                break
        else:
            packs.append([number])
            pack_boxes.append([box])
    return packs


def full_masks(frame, groups):
    """Mask of every group as if no other object were in the scene."""
    masks = [np.zeros((frame.height, frame.width), dtype=bool) for _ in groups]
    for pack in full_mask_packs(frame, groups):
        ids = frame.full_ids([groups[number] for number in pack])
        for position, number in enumerate(pack, start=1):
            masks[number] = ids == position
    return masks


def segmentation(frame, classes, per, skip_empty=False, full=False):
    """Saves masks of the visible pixels, adds {"class", "objects", "mask", "per"} to the
    labels under "masks", and with full, the masks ignoring occlusion under "full_masks"."""
    instances = to_instances(classes, frame)
    groups = [group for _, group in instances]
    # the visible masks first, they may read the beauty render that the full masks replace
    ids = index_pass_ids(frame) if frame.index_groups == group_key(groups) else frame.ids(groups)
    full = full_masks(frame, groups) if full else None
    entries, full_entries = save_masks(ids, instances, frame.path, frame.index, per, skip_empty,
                                       frame.label.get("masks", []), full, frame.label.get("full_masks", []))
    frame.add("masks", entries)
    if full is not None:
        frame.add("full_masks", full_entries)


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
