"""Reads and writes images with OpenImageIO: the Render Result, the multilayer EXR of the
beauty render and its layers, the id renders, masks and plain pixel arrays."""

import os

import bpy
import numpy as np

try:
    import OpenImageIO as oiio
except ImportError as error:
    # bundled with blender, but not with the bpy python module
    raise ImportError("OpenImageIO is missing, install it with: pip install OpenImageIO") from error


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


def set_file_format(image_settings, file_format):
    """Sets the output file format, blender 5 also needs the matching media type."""
    if hasattr(image_settings, "media_type"):
        image_settings.media_type = "MULTI_LAYER_IMAGE" if file_format == "OPEN_EXR_MULTILAYER" else "IMAGE"
    image_settings.file_format = file_format


def save_render_result(scene, file_path, file_format, color_depth, color_mode=None):
    """Saves the Render Result to file_path. The output settings it changes are set back
    by generate."""
    image_settings = scene.render.image_settings
    set_file_format(image_settings, file_format)
    if color_mode is not None:
        image_settings.color_mode = color_mode
    image_settings.color_depth = color_depth
    bpy.data.images["Render Result"].save_render(file_path, scene=scene)


def read_ids(file_path, encoded=False):
    """(height, width) array of the group ids of an RGBA id render EXR: the red channel,
    or with encoded the 8 bit steps of red and green of `render_ids`. Transparent pixels are 0."""
    pixels = oiio.ImageBuf(file_path).get_pixels(oiio.FLOAT)
    if encoded:
        ids = np.rint(pixels[:, :, 0] * 255).astype(np.int64) + np.rint(pixels[:, :, 1] * 255).astype(np.int64) * 256
    else:
        ids = np.rint(pixels[:, :, 0]).astype(np.int64)
    ids[pixels[:, :, 3] < 0.5] = 0
    return ids


def object_index_ids(file_path, parts, view_layer):
    """(height, width) array of the Object Index pass of a multilayer EXR, parts: the
    channel names of each of its parts."""
    layer = find_layer_in(file_path, parts, view_layer, PASSES["ObjectIndex"][1])
    if layer is None:
        raise RuntimeError("The Object Index pass is missing in the render result")
    _, part, found = layer
    pixels = oiio.ImageBuf(file_path, part, 0).get_pixels(oiio.FLOAT)
    # not anti-aliased, the pass holds whole numbers
    return np.rint(pixels[:, :, found[0][0]]).astype(np.int64)


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


def find_layer_in(multilayer, parts, view_layer, layer_names):
    """`find_layer` in any multilayer EXR, parts: the channel names of each of its parts."""
    for layer in layer_names:
        prefix = f"{view_layer.name}.{layer}."
        for part, names in enumerate(parts):
            found = [(i, name[len(prefix):]) for i, name in enumerate(names) if name.startswith(prefix)]
            if found:
                return multilayer, part, found
    return None


def layer_empty(pixels, channel_names):
    """Whether every pixel of a (height, width, channels) layer is 0 (black), ignoring
    alpha unless it is the only channel."""
    channels = [i for i, name in enumerate(channel_names) if name != "A"] or list(range(len(channel_names)))
    return not pixels[:, :, channels].any()


def write_mask(path, file_name, mask):
    """Writes a boolean (height, width) mask as a black and white PNG."""
    height, width = mask.shape
    buffer = oiio.ImageBuf(oiio.ImageSpec(width, height, 1, oiio.UINT8))
    buffer.set_pixels(oiio.ROI(), np.where(mask, 255, 0).astype(np.uint8).reshape(height, width, 1))
    if not buffer.write(os.path.join(path, file_name)):
        raise RuntimeError(f"Cannot write mask {file_name}: {buffer.geterror()}")
    return file_name


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
