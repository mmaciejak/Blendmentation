import json
import os
import tempfile

import bpy
import numpy as np
import OpenImageIO as oiio

IMAGE_SETTINGS = ("media_type", "file_format", "color_mode", "color_depth", "exr_codec")
AOV_FORMATS = {"OPEN_EXR": ".exr", "PNG": ".png"}


def default_objects(scene):
    """Returns all mesh objects that are visible in the render."""
    return [
        obj
        for obj in scene.objects
        if obj.type == "MESH" and not obj.hide_render and obj.visible_get()
    ]


def next_index(path):
    """Returns the first free image index in the output directory."""
    taken = [
        int(os.path.splitext(name)[0])
        for name in os.listdir(path)
        if os.path.splitext(name)[0].isdigit()
    ]
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


def bbox(scene, camera, obj, depsgraph, width, height):
    """Returns the object's 2D bbox in pixels [x_min, y_min, x_max, y_max]
    with top-left image origin, or None if the object is not in the frame.
    Occlusion by other objects is not taken into account.
    """
    view = camera_view_coords(scene, camera, obj, depsgraph)
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
        raise ValueError(f"aov_format must be one of {list(AOV_FORMATS)}")
    if scene.render.engine == "BLENDER_WORKBENCH":
        raise ValueError("Shader AOVs need Cycles or EEVEE, not Workbench")
    available = [aov.name for aov in view_layer.aovs]
    missing = [name for name in aovs if name not in available]
    if missing:
        raise ValueError(
            f"AOVs {missing} are not in view layer '{view_layer.name}' (has {available}), "
            "add them in View Layer Properties > Passes > Shader AOV"
        )


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


def render(
    path,
    resolution,
    bboxes,
    rotation_matrix,
    iou_deconflict,
    custom_dict=None,
    objects=None,
    aovs=None,
    aov_format="OPEN_EXR",
):
    """Renders the active scene camera to <path>/<index>.png and writes labels to <path>/<index>.json

    Args:
        path (str): output directory, "" or relative paths are relative to the .blend file
        resolution (tuple): (width, height) in pixels
        bboxes (bool): add 2D bboxes to the labels
        rotation_matrix (bool): add object rotation relative to the camera to the labels
        iou_deconflict (float): skip the render if any two bboxes overlap over this IoU, None disables it
        custom_dict (dict): extra key and values saved in the labels
        objects (list): objects to label, None = all visible mesh objects
        aovs (list): names of shader AOVs to save as <path>/<index>_<aov>.<ext>
        aov_format (str): "OPEN_EXR" (32 bit float) or "PNG" (8 bit, values clamped to 0-1)

    Returns:
        bool: True if the image was rendered, False if it was skipped by iou_deconflict
    """
    scene = bpy.context.scene
    camera = scene.camera
    if camera is None:
        raise RuntimeError("Scene has no active camera")

    path = bpy.path.abspath(path or "//")
    os.makedirs(path, exist_ok=True)
    if objects is None:
        objects = default_objects(scene)
    view_layer = bpy.context.view_layer
    if aovs:
        check_aovs(scene, view_layer, aovs, aov_format)

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
    aov_files = {}
    try:
        # set before computing bboxes, the camera frame depends on the resolution
        render_settings.resolution_x = width
        render_settings.resolution_y = height
        render_settings.resolution_percentage = 100
        bpy.context.view_layer.update()
        depsgraph = bpy.context.evaluated_depsgraph_get()

        labels = []
        for obj in objects:
            label = {"name": obj.name}
            if bboxes or iou_deconflict is not None:
                label["bbox"] = bbox(scene, camera, obj, depsgraph, width, height)
            if rotation_matrix:
                label["rotation_matrix"] = rotation_to_camera(camera, obj)
            labels.append(label)

        if iou_deconflict is not None:
            visible_boxes = [label["bbox"] for label in labels if label["bbox"] is not None]
            if max_iou(visible_boxes) > iou_deconflict:
                return False
        if not bboxes:
            for label in labels:
                label.pop("bbox", None)

        index = next_index(path)
        image_name = f"{index:06d}.png"
        set_file_format(image_settings, "PNG")
        render_settings.filepath = os.path.join(path, image_name)
        bpy.ops.render.render(write_still=True)
        if aovs:
            aov_files = save_aovs(scene, view_layer, aovs, aov_format, path, index)
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

    if bboxes or rotation_matrix or custom_dict:
        data = {"image": image_name, "resolution": [width, height], "objects": labels}
        if aov_files:
            data["aovs"] = aov_files
        if custom_dict:
            data.update(custom_dict)
        with open(os.path.join(path, f"{index:06d}.json"), "w") as file:
            json.dump(data, file, indent=2)

    return True
