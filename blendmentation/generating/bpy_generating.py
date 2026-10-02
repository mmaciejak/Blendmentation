import json
import os

import bpy
import numpy as np


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


def render(path, resolution, bboxes, rotation_matrix, iou_deconflict, custom_dict=None, objects=None):
    """Renders the active scene camera to <path>/<index>.png and writes labels to <path>/<index>.json

    Args:
        path (str): output directory, "" or relative paths are relative to the .blend file
        resolution (tuple): (width, height) in pixels
        bboxes (bool): add 2D bboxes to the labels
        rotation_matrix (bool): add object rotation relative to the camera to the labels
        iou_deconflict (float): skip the render if any two bboxes overlap over this IoU, None disables it
        custom_dict (dict): extra key and values saved in the labels
        objects (list): objects to label, None = all visible mesh objects

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

    width, height = int(resolution[0]), int(resolution[1])
    render_settings = scene.render
    previous = (
        render_settings.resolution_x,
        render_settings.resolution_y,
        render_settings.resolution_percentage,
        render_settings.filepath,
        render_settings.image_settings.file_format,
    )
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
        render_settings.image_settings.file_format = "PNG"
        render_settings.filepath = os.path.join(path, image_name)
        bpy.ops.render.render(write_still=True)
    finally:
        (
            render_settings.resolution_x,
            render_settings.resolution_y,
            render_settings.resolution_percentage,
            render_settings.filepath,
            render_settings.image_settings.file_format,
        ) = previous

    if bboxes or rotation_matrix or custom_dict:
        data = {"image": image_name, "resolution": [width, height], "objects": labels}
        if custom_dict:
            data.update(custom_dict)
        with open(os.path.join(path, f"{index:06d}.json"), "w") as file:
            json.dump(data, file, indent=2)

    return True
