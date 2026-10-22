import colorsys
import json
import math
import random
from collections import Counter

import bpy
import numpy as np
from mathutils import Euler, Matrix, Quaternion, Vector
from mathutils.bvhtree import BVHTree

from .. import bpy_camera, bpy_paths, bpy_undo
from ..values import is_number, is_point


def sample(value_range):
    """Draws a random offset from an augmentation range.

    Args:
        value_range (float | tuple): a single number ``v`` samples from (-v, v),
            a pair ``(low, high)`` samples from (low, high), a triple
            ``(low, high, step)`` picks one of low, low + step, ... up to high
    """
    if isinstance(value_range, (int, float)):
        return random.uniform(-value_range, value_range)
    if len(value_range) == 3:
        low, high, step = value_range
        # the small tolerance keeps high when (high - low) / step is a whole number
        return low + step * random.randint(0, math.floor((high - low) / step + 1e-9))
    low, high = value_range
    return random.uniform(low, high)


def percent_factor(value_range):
    """Draws a multiplicative factor from a range given in percents."""
    return 1.0 + sample(value_range) / 100.0


def object_at(data_path):
    """The object at an absolute data path, e.g. bpy.data.objects["Camera"]."""
    obj = bpy_paths.get_value(data_path)
    if not isinstance(obj, bpy.types.Object):
        raise TypeError(f"'{data_path}' is not an object, it is {obj!r}")
    return obj


def get_material(obj, material_id):
    """Returns the material of given name from the object's material slots."""
    for slot in obj.material_slots:
        if slot.material is not None and slot.material.name == material_id:
            return slot.material
    raise KeyError(f"Object '{obj.name}' has no material '{material_id}'")


def get_node(node_tree, node_type, owner_name):
    """Returns the first node of given type from the node tree."""
    for node in node_tree.nodes:
        if node.type == node_type:
            return node
    raise ValueError(f"'{owner_name}' has no {node_type} node")


def get_unlinked_input(node, input_name, owner_name):
    """Returns the node input, making sure nothing is connected to it."""
    socket = node.inputs[input_name]
    if socket.is_linked:
        raise ValueError(f"'{input_name}' input of '{owner_name}' is connected, cannot augment it")
    return socket


def translation(obj, x, y, z):
    """Moves the object by random offsets in blender units.

    Returns:
        tuple: applied offsets for x, y and z
    """
    offsets = (sample(x), sample(y), sample(z))
    bpy_undo.record_transforms(obj)
    for axis, offset in enumerate(offsets):
        obj.location[axis] += offset
    return offsets


def rotation(obj, x, y, z):
    """Rotates the object by random angles in degrees, in the object's local euler space.

    Returns:
        tuple: applied angles in degrees for x, y and z
    """
    angles = (sample(x), sample(y), sample(z))
    radians = [math.radians(angle) for angle in angles]
    bpy_undo.record_transforms(obj)

    if obj.rotation_mode == "QUATERNION":
        obj.rotation_quaternion = obj.rotation_quaternion @ Euler(radians).to_quaternion()
    elif obj.rotation_mode == "AXIS_ANGLE":
        angle, *axis = obj.rotation_axis_angle
        rotated = Quaternion(axis, angle) @ Euler(radians).to_quaternion()
        axis, angle = rotated.to_axis_angle()
        obj.rotation_axis_angle = (angle, *axis)
    else:
        for axis, angle in enumerate(radians):
            obj.rotation_euler[axis] += angle
    return angles


def scale(obj, x, y, z):
    """Scales the object by random factors given in percents.

    Returns:
        tuple: applied scale factors for x, y and z
    """
    factors = (percent_factor(x), percent_factor(y), percent_factor(z))
    bpy_undo.record_transforms(obj)
    for axis, factor in enumerate(factors):
        obj.scale[axis] *= factor
    return factors


def range_or_value(value_range, current):
    """None keeps the current value, a number is used as it is, (min, max) is sampled."""
    if value_range is None:
        return current
    if isinstance(value_range, (int, float)):
        return float(value_range)
    low, high = value_range
    return random.uniform(low, high)


def material(obj, material_id, hue, saturation, value, roughness, metallic):
    """Sets base color, roughness and metallic of the material's Principled BSDF
    to random values from (min, max) ranges, or exact values. None leaves the value unchanged.
    Base color is set through hue, saturation and value, each in 0-1.

    Returns:
        dict: values that were set
    """
    mat = get_material(obj, material_id)
    principled = get_node(mat.node_tree, "BSDF_PRINCIPLED", mat.name)
    applied = {}

    hsv = {"hue": range_or_value(hue, None), "saturation": range_or_value(saturation, None),
           "value": range_or_value(value, None)}
    if any(new is not None for new in hsv.values()):
        base_color = get_unlinked_input(principled, "Base Color", mat.name)
        red, green, blue, alpha = base_color.default_value
        current = dict(zip(hsv, colorsys.rgb_to_hsv(red, green, blue)))
        for name, new in hsv.items():
            if new is not None:
                current[name] = new
                applied[name] = new
        bpy_undo.set_attr(base_color, "default_value", (*colorsys.hsv_to_rgb(*current.values()), alpha))

    for name, socket_name, value_range in (("roughness", "Roughness", roughness), ("metallic", "Metallic", metallic)):
        new = range_or_value(value_range, None)
        if new is not None:
            bpy_undo.set_attr(get_unlinked_input(principled, socket_name, mat.name), "default_value", new)
            applied[name] = new

    return applied


def slot_index(obj, slot):
    """Index of a material slot given by index or by its material's name."""
    slots = obj.material_slots
    if isinstance(slot, int):
        if not -len(slots) <= slot < len(slots):
            raise KeyError(f"Object '{obj.name}' has {len(slots)} material slots, no slot {slot}")
        return slot % len(slots)
    for index, material_slot in enumerate(slots):
        if material_slot.material is not None and material_slot.material.name == slot:
            return index
    raise KeyError(f"Object '{obj.name}' has no material '{slot}' in its slots, "
                   f"they hold {[s.material.name if s.material else None for s in slots]}")


def material_slot(obj, slots, weights):
    """Assigns every face of the mesh to one of the object's material slots, picked at
    random, and makes it the active slot, so active_material is the picked material.

    Args:
        slots (list | None): slots to pick from, by index or material name, None = all
        weights (list | None): relative probability of each slot, None = equal

    Returns:
        str | None: name of the picked slot's material, None for an empty slot
    """
    if obj is None or obj.type != "MESH":
        raise TypeError(f"{obj!r} is not a mesh object")
    if not obj.material_slots:
        raise ValueError(f"Object '{obj.name}' has no material slots")
    indices = list(range(len(obj.material_slots))) if slots is None else [slot_index(obj, slot) for slot in slots]
    if weights is not None and len(weights) != len(indices):
        raise ValueError(f"Object '{obj.name}' has {len(indices)} slots to pick from, got {len(weights)} weights")
    index = random.choices(indices, weights=weights)[0]
    mesh = obj.data
    bpy_undo.record_material_indices(mesh)
    mesh.polygons.foreach_set("material_index", [index] * len(mesh.polygons))
    mesh.update()
    bpy_undo.set_attr(obj, "active_material_index", index)
    material = obj.material_slots[index].material
    return None if material is None else material.name


def uv_map_index(obj, uv_map):
    """Index of a UV map of a mesh object given by index or by name."""
    layers = obj.data.uv_layers
    if isinstance(uv_map, int):
        if not -len(layers) <= uv_map < len(layers):
            raise KeyError(f"Object '{obj.name}' has {len(layers)} UV maps, no UV map {uv_map}")
        return uv_map % len(layers)
    index = layers.find(uv_map)
    if index < 0:
        raise KeyError(f"Object '{obj.name}' has no UV map '{uv_map}', it has {list(layers.keys())}")
    return index


def uv_map(obj, uv_maps, weights):
    """Makes one of the mesh's UV maps, picked at random, the one used for rendering and
    the active one.

    Args:
        uv_maps (list | None): UV maps to pick from, by index or name, None = all
        weights (list | None): relative probability of each UV map, None = equal

    Returns:
        str: name of the picked UV map
    """
    if obj is None or obj.type != "MESH":
        raise TypeError(f"{obj!r} is not a mesh object")
    layers = obj.data.uv_layers
    if not layers:
        raise ValueError(f"Object '{obj.name}' has no UV maps")
    indices = list(range(len(layers))) if uv_maps is None else [uv_map_index(obj, layer) for layer in uv_maps]
    if weights is not None and len(weights) != len(indices):
        raise ValueError(f"Object '{obj.name}' has {len(indices)} UV maps to pick from, got {len(weights)} weights")
    picked = layers[random.choices(indices, weights=weights)[0]]
    # setting a flag to False does nothing, so the old maps are recorded as True first:
    # restoring sets them back last, which clears the picked map's flags
    for name in ("active_render", "active"):
        for layer in layers:
            if getattr(layer, name):
                bpy_undo.set_attr(layer, name, True)
        bpy_undo.set_attr(picked, name, True)
    return picked.name


def target_center(target):
    """World location of a look-at target: an object or a list of objects (the center
    of their bounding boxes), or a point (x, y, z)."""
    if is_point(target):
        return Vector(target)
    objects = [target] if isinstance(target, bpy.types.Object) else list(target)
    if not objects or not all(isinstance(obj, bpy.types.Object) for obj in objects):
        raise TypeError("LookAt target must be an object, a list of objects or a point (x, y, z)")
    centers = [obj.matrix_world @ (sum((Vector(corner) for corner in obj.bound_box), Vector()) / 8) for obj in objects]
    return sum(centers, Vector()) / len(centers)


def look_at(obj, target, distance, elevation, azimuth, roll, focal_length):
    """Moves the object on a sphere around the target and points its -Z axis
    (the view direction of cameras and lights) at it, with Y up.

    Args:
        distance (tuple): distance from the target in blender units
        elevation (tuple): angle above the target's horizontal plane in degrees
        azimuth (tuple): angle around the world Z axis in degrees, 0 = +X
        roll (tuple): rotation around the object's local Z axis (the view axis) in degrees, None = 0
        focal_length (tuple): camera lens in mm, cameras only

    Each is (min, max), an exact number, or None to keep the current value.

    Returns:
        dict: values that were set
    """
    if focal_length is not None and obj.type != "CAMERA":
        raise TypeError(f"focal_length needs a camera, '{obj.name}' is {obj.type}")
    center = target_center(target)
    offset = obj.matrix_world.translation - center
    current_distance = offset.length
    current_elevation = (math.degrees(math.asin(max(-1.0, min(1.0, offset.z / current_distance))))
                         if current_distance else 0.0)
    current_azimuth = math.degrees(math.atan2(offset.y, offset.x))

    applied = {
        "distance": range_or_value(distance, current_distance),
        "elevation": range_or_value(elevation, current_elevation),
        "azimuth": range_or_value(azimuth, current_azimuth),
        "roll": range_or_value(roll, 0.0),
    }
    if applied["distance"] <= 0:
        raise ValueError(f"LookAt distance must be positive, got {applied['distance']}")

    elevation_rad = math.radians(applied["elevation"])
    azimuth_rad = math.radians(applied["azimuth"])
    location = center + applied["distance"] * Vector((
        math.cos(elevation_rad) * math.cos(azimuth_rad),
        math.cos(elevation_rad) * math.sin(azimuth_rad),
        math.sin(elevation_rad),
    ))
    rotation = (center - location).to_track_quat("-Z", "Y") @ Quaternion((0.0, 0.0, 1.0), math.radians(applied["roll"]))
    bpy_undo.record_transforms(obj)
    obj.matrix_world = Matrix.LocRotScale(location, rotation, obj.matrix_world.to_scale())

    if focal_length is not None:
        bpy_undo.set_attr(obj.data, "lens", range_or_value(focal_length, obj.data.lens))
        # blender stores it as 32 bit float, report what was stored
        applied["focal_length"] = obj.data.lens
    return applied


def check_curve(curve):
    if not isinstance(curve, bpy.types.Object) or curve.type != "CURVE":
        kind = curve.type if isinstance(curve, bpy.types.Object) else type(curve).__name__
        raise TypeError(f"PlaceOnCurve needs a curve object, got {kind}")


def curve_segments(curve):
    """World-space segments of the evaluated curve (modifiers included), each a
    (start, end) pair, in the order and direction of its splines. Bevel, extrusion and
    the fill of 2D curves are turned off while it is read, so only the center line counts.

    Returns:
        list: the segments, without zero length ones
    """
    data = curve.data
    flat = {"extrude": 0.0, "bevel_depth": 0.0, "bevel_object": None}
    if data.dimensions == "2D":
        flat["fill_mode"] = "NONE"
    # temporary, not recorded in the restoring() journal
    with bpy_undo.temporary() as changes:
        changes.set_many(data, {name: value for name, value in flat.items() if getattr(data, name) != value})
        curve_eval = curve.evaluated_get(bpy.context.evaluated_depsgraph_get())
        mesh = curve_eval.to_mesh()
        try:
            if mesh.polygons:
                raise ValueError(
                    f"Curve '{curve.name}' evaluates to faces (e.g. a geometry nodes modifier), not a line")
            matrix = curve_eval.matrix_world
            vertices = [matrix @ vertex.co for vertex in mesh.vertices]
            # blender converts each spline to a chain of edges in its direction
            segments = [(vertices[a], vertices[b]) for a, b in (edge.vertices for edge in mesh.edges)]
        finally:
            curve_eval.to_mesh_clear()
    segments = [(start, end) for start, end in segments if (end - start).length > 0]
    if not segments:
        raise ValueError(f"Curve '{curve.name}' has no length")
    return segments


def place_on_curve(obj, curve, align, position):
    """Moves the object to a point on the curve, a fraction `position` of its length from
    its start (splines one after another, in their order). With align, its -Z axis (the
    view direction of cameras and lights) points along the curve direction there, with Y
    up; otherwise its rotation and scale are kept.

    Args:
        position (tuple | float): (min, max) fraction of the curve length, or an exact one

    Returns:
        dict: the position and the world location that were set
    """
    if obj == curve:
        raise ValueError(f"'{obj.name}' cannot be placed on itself")
    # earlier augmentations changed location / rotation / scale, matrix_world is stale until then
    bpy.context.view_layer.update()
    segments = curve_segments(curve)
    lengths = [(end - start).length for start, end in segments]
    fraction = range_or_value(position, 0.0)
    distance = fraction * sum(lengths)
    index = 0
    while index < len(segments) - 1 and distance > lengths[index]:
        distance -= lengths[index]
        index += 1
    start, end = segments[index]
    direction = (end - start) / lengths[index]
    location = start + direction * min(distance, lengths[index])

    matrix = obj.matrix_world.copy()
    if align:
        matrix = Matrix.LocRotScale(location, direction.to_track_quat("-Z", "Y"), matrix.to_scale())
    else:
        matrix.translation = location
    bpy_undo.record_transforms(obj)
    obj.matrix_world = matrix
    return {"position": fraction, "location": tuple(location)}


def check_camera(obj, perspective=False):
    if obj.type != "CAMERA":
        raise TypeError(f"'{obj.name}' is {obj.type}, not a camera")
    if perspective and obj.data.type != "PERSP":
        raise TypeError(f"Camera '{obj.name}' is {obj.data.type}, focal length needs a perspective camera")


def focal_length(obj, focal_length_range, target, keep_size, size=None):
    """Sets the camera lens. With keep_size the camera moves along the line to the target
    by the same ratio as the lens, so the target keeps its size in the image (a dolly zoom).
    A target's size in the image is about lens / distance, so with size the distance is
    also divided by the size factor.

    Args:
        focal_length_range (tuple): lens in mm, (min, max) or an exact number
        target: object, list of objects or point (x, y, z), needed for keep_size
        size (tuple): with keep_size, the target's new size in percent of its size
            before, (min, max) or an exact number; None keeps it

    Returns:
        dict: focal_length that was set, and with keep_size the new distance to the
        target and the size in percent
    """
    check_camera(obj, perspective=True)
    old_lens = obj.data.lens
    bpy_undo.set_attr(obj.data, "lens", range_or_value(focal_length_range, old_lens))
    # blender stores it as 32 bit float, report and use what was stored
    applied = {"focal_length": obj.data.lens}
    if keep_size:
        percent = range_or_value(size, 100.0)
        center = target_center(target)
        matrix = obj.matrix_world.copy()
        matrix.translation = center + (matrix.translation - center) * (obj.data.lens / old_lens) / (percent / 100)
        bpy_undo.record_transforms(obj)
        obj.matrix_world = matrix
        applied["distance"] = (matrix.translation - center).length
        applied["size"] = percent
    return applied


def depth_of_field(obj, target, f_stop, defocus=None):
    """Enables depth of field, focused at the target with a random f-stop.

    Args:
        target: object, list of objects or point (x, y, z) to focus on, None keeps the focus
        f_stop (tuple): aperture f-stop, (min, max), an exact number, or None to keep it
        defocus (float | tuple): percent added to the focus distance, sampled like a
            Translation offset (v: (-v, v)); None focuses exactly

    Returns:
        dict: f_stop, focus_distance, and defocus (the percent added) with defocus
    """
    check_camera(obj)
    dof = obj.data.dof
    bpy_undo.set_attr(dof, "use_dof", True)
    distance = None
    if target is not None:
        # focus distance along the view axis to the target center, from where the camera is now
        forward = obj.matrix_world.to_3x3().normalized() @ Vector((0.0, 0.0, -1.0))
        distance = max((target_center(target) - obj.matrix_world.translation).dot(forward), 0.0)
    applied = {}
    if defocus is not None:
        if distance is None:
            # the scene's focus, also when a focus object sets it
            distance = bpy_camera.focus_distance(obj)
        percent = sample(defocus)
        distance = max(distance * (1.0 + percent / 100.0), 0.0)
        applied["defocus"] = percent
    if distance is not None:
        bpy_undo.set_attr(dof, "focus_object", None)
        bpy_undo.set_attr(dof, "focus_distance", distance)
    bpy_undo.set_attr(dof, "aperture_fstop", range_or_value(f_stop, dof.aperture_fstop))
    return {"f_stop": dof.aperture_fstop, "focus_distance": dof.focus_distance, **applied}


def disable_depth_of_field(obj):
    """Turns depth of field off, the focus and f-stop settings are kept.

    Returns:
        bool: False, depth of field is off
    """
    check_camera(obj)
    bpy_undo.set_attr(obj.data.dof, "use_dof", False)
    return False


def number(obj, data_path, value_range):
    """Sets the int or float value at the data path to a random value from
    value_range (min, max), or one of min, min + step, ... max for (min, max, step).

    Returns:
        float | int: value that was set
    """
    current = bpy_paths.get_value(data_path, obj)
    if not is_number(current):
        raise TypeError(f"'{data_path}' is not an int or float value")

    if len(value_range) == 3:
        new = sample(value_range)
        new = round(new) if isinstance(current, int) else new
    else:
        low, high = value_range
        new = random.randint(round(low), round(high)) if isinstance(current, int) else random.uniform(low, high)

    bpy_paths.set_value(data_path, new, obj)
    return new


def visibility(obj, visible):
    """Shows or hides the object in renders, the viewport visibility is left unchanged.

    Returns:
        bool: whether the object is visible in renders
    """
    bpy_undo.set_attr(obj, "hide_render", not visible)
    return visible


def shadow_catcher(obj, enabled):
    """Makes the object a shadow catcher (Cycles) or a normal object.

    Returns:
        bool: whether the object is a shadow catcher
    """
    bpy_undo.set_attr(obj, "is_shadow_catcher", enabled)
    return enabled


# object types with an evaluated mesh (to_mesh)
GEOMETRY_TYPES = {"MESH", "CURVE", "SURFACE", "META", "FONT", "CURVES", "POINTCLOUD"}


def local_geometry(obj_eval):
    """(n, 3) vertices of an evaluated object's mesh in its local space, and its faces as
    the triangles Blender splits them into (as it renders them: a face that isn't flat
    bends along a diagonal, which is an edge too)."""
    mesh = obj_eval.to_mesh() if obj_eval.type in GEOMETRY_TYPES else None
    if mesh is None:
        return np.empty((0, 3)), []
    try:
        coords = np.empty(len(mesh.vertices) * 3, dtype=np.float64)
        mesh.vertices.foreach_get("co", coords)
        mesh.calc_loop_triangles()
        triangles = np.empty(len(mesh.loop_triangles) * 3, dtype=np.int64)
        mesh.loop_triangles.foreach_get("vertices", triangles)
    finally:
        obj_eval.to_mesh_clear()
    return coords.reshape(-1, 3), [tuple(triangle) for triangle in triangles.reshape(-1, 3).tolist()]


def world_mesh(obj, depsgraph):
    """World coordinates of the vertices of everything the evaluated object shows, as an
    (n, 3) array, and its faces (triangles, `local_geometry`): its mesh (modifiers included) and the instances it makes
    (geometry nodes instances, collection instancing), which aren't in its mesh. An object
    without either (an empty, a camera) is a point at its origin, without faces."""
    parts, faces, count, local = [], [], 0, {}
    for instance in depsgraph.object_instances:
        if instance.is_instance:
            if instance.parent is None or instance.parent.original != obj:
                continue
        elif instance.object.original != obj:
            continue
        source = instance.object
        # the evaluated geometry is only valid while iterating; instances of one mesh share it
        key = (source.data.as_pointer() if source.data else source.as_pointer(), source.type)
        if key not in local:
            local[key] = local_geometry(source)
        coords, local_faces = local[key]
        if not len(coords):
            continue
        matrix = np.array(instance.matrix_world)
        parts.append(coords @ matrix[:3, :3].T + matrix[:3, 3])
        faces += [tuple(index + count for index in face) for face in local_faces]
        count += len(coords)
    if not parts:
        return np.array([obj.evaluated_get(depsgraph).matrix_world.translation]), []
    return np.concatenate(parts), faces


# the edges of a triangle, as index pairs into its corners
TRIANGLE_EDGES = np.array([[0, 1], [1, 2], [2, 0]])


def edge_crossings(triangles, others):
    """Where the edges of paired triangles cross seen from above (in XY): for triangle
    pairs as (p, 3, 3) arrays of corners, the z on each triangle's edge at every crossing
    of an edge of one with an edge of the other, as two arrays."""
    a = triangles[:, TRIANGLE_EDGES][:, :, None]  # (p, 3, 1, 2, 3): every edge of one...
    b = others[:, TRIANGLE_EDGES][:, None]        # (p, 1, 3, 2, 3): ...against every edge of the other
    r, s = a[..., 1, :] - a[..., 0, :], b[..., 1, :] - b[..., 0, :]
    q = b[..., 0, :] - a[..., 0, :]
    denominator = r[..., 0] * s[..., 1] - r[..., 1] * s[..., 0]
    # parallel in XY: no single crossing (their end points are vertices), masked out
    with np.errstate(divide="ignore", invalid="ignore"):
        t = (q[..., 0] * s[..., 1] - q[..., 1] * s[..., 0]) / denominator
        u = (q[..., 0] * r[..., 1] - q[..., 1] * r[..., 0]) / denominator
        crossing = (denominator != 0) & (t >= 0) & (t <= 1) & (u >= 0) & (u <= 1)
        return (a[..., 0, 2] + t * r[..., 2])[crossing], (b[..., 0, 2] + u * s[..., 2])[crossing]


# rounds of lifting the object off the surface triangles it still cuts through
CROSSING_ROUNDS = 8
# how much lower than the object would rest it looks for triangles cutting through
CROSSING_TOLERANCE = 1e-6


def column_hit(tree, x, y, z, direction):
    """Z of the first hit of a vertical ray from (x, y, z), None when it misses."""
    location = tree.ray_cast(Vector((x, y, z)), Vector((0.0, 0.0, direction)))[0]
    return None if location is None else location.z


def rest_offset(obj, surface, margin):
    """How far to move the object along world Z so its lowest point is margin above the
    top of the surface, where they overlap seen from above: positive up, negative down.

    Both are compared as everything they show (`world_mesh`: modifiers and instances
    included, as triangles) in world space, where the gap between them can be smallest:
    under the object's vertices, over the surface's vertices (a peak between the
    object's vertices), and where their edges cross seen from above (an object lying
    across a ridge). The gap is linear in between, so that is exact. The crossings are
    only looked for between triangles that cut through each other where the vertices
    would put the object (a BVH overlap), the only place a crossing can be too low.

    Returns:
        float | None: the move, None when the object is not over the surface
    """
    if obj == surface:
        raise ValueError(f"'{obj.name}' cannot be placed on itself")
    # earlier augmentations changed location / rotation / scale, matrix_world is stale until then
    bpy.context.view_layer.update()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    surface_vertices, surface_faces = world_mesh(surface, depsgraph)
    if not surface_faces:
        raise TypeError(f"Surface '{surface.name}' has no faces")
    vertices, faces = world_mesh(obj, depsgraph)

    surface_tree = BVHTree.FromPolygons(surface_vertices.tolist(), surface_faces)
    above_surface = max(surface_vertices[:, 2].max(), vertices[:, 2].max()) + 1.0
    lifts = []
    for x, y, z in vertices.tolist():
        top = column_hit(surface_tree, x, y, above_surface, -1.0)
        if top is not None:
            lifts.append(top + margin - z)
    if faces:
        tree = BVHTree.FromPolygons(vertices.tolist(), faces)
        below_object = min(surface_vertices[:, 2].min(), vertices[:, 2].min()) - 1.0
        for x, y, z in surface_vertices.tolist():
            bottom = column_hit(tree, x, y, below_object, 1.0)
            if bottom is None:
                continue
            top = column_hit(surface_tree, x, y, above_surface, -1.0)
            lifts.append((z if top is None else top) + margin - bottom)
    if not lifts or not faces:
        return max(lifts) if lifts else None
    # the vertices miss where an edge of the object crosses an edge of the surface seen
    # from above, e.g. lying across a ridge: there the object cuts through the surface,
    # so lift it off the triangles it cuts through, by what their edges need
    offset = max(lifts)
    faces, surface_faces = np.array(faces), np.array(surface_faces)
    for _ in range(CROSSING_ROUNDS):
        resting = vertices + (0.0, 0.0, offset - margin - CROSSING_TOLERANCE)
        pairs = BVHTree.FromPolygons(resting.tolist(), faces.tolist()).overlap(surface_tree)
        if not pairs:
            break
        pairs = np.array(pairs)
        object_z, surface_z = edge_crossings(vertices[faces[pairs[:, 0]]],
                                             surface_vertices[surface_faces[pairs[:, 1]]])
        needed = float((surface_z + margin - object_z).max()) if len(object_z) else -math.inf
        if needed <= offset + CROSSING_TOLERANCE:
            break
        offset = needed
    return offset


def move_z(obj, offset):
    """Moves the object along world Z, parented objects included."""
    matrix = obj.matrix_world.copy()
    matrix.translation.z += offset
    bpy_undo.record_transforms(obj)
    obj.matrix_world = matrix


# measuring again after a move, for objects whose shape depends on where they are
SETTLE_STEPS = 8
SETTLE_TOLERANCE = 1e-5


def has_drivers(owner):
    animation = getattr(owner, "animation_data", None) if owner is not None else None
    return bool(animation and animation.drivers)


def shape_may_follow(obj):
    """Whether the object's evaluated shape can change when it moves: modifiers (e.g.
    geometry nodes reading its position), instancing, or drivers on it, its data or its
    shape keys (e.g. a shape key driven by its height). A plain mesh, shape keys
    included, keeps its shape."""
    shape_keys = getattr(obj.data, "shape_keys", None) if obj.data is not None else None
    return (bool(obj.modifiers) or obj.instance_type != "NONE"
            or any(has_drivers(owner) for owner in (obj, obj.data, shape_keys)))


def settle(obj, surface, margin, lift_only):
    """Moves the object along world Z by `rest_offset`. Its shape can depend on where it
    is (`shape_may_follow`: geometry nodes reading its position, a driven shape key), so
    for such an object it measures again after the move and corrects, with secant
    steps, until it rests within SETTLE_TOLERANCE. With lift_only, an object high enough
    isn't moved.

    Returns:
        float: how far the object was moved, positive up, 0 when it was not moved
    """
    offset = rest_offset(obj, surface, margin)
    if offset is None or (lift_only and offset <= 0):
        return 0.0
    move_z(obj, offset)
    if not shape_may_follow(obj):
        return offset
    # the residual offset after moving by `moved`: goes down 1 for 1 for a fixed shape
    previous, moved = (0.0, offset), offset
    for _ in range(SETTLE_STEPS):
        residual = rest_offset(obj, surface, margin)
        if residual is None or abs(residual) <= SETTLE_TOLERANCE:
            break
        slope = (residual - previous[1]) / (moved - previous[0])
        step = -residual / slope if abs(slope) > 1e-9 else residual
        previous = (moved, residual)
        move_z(obj, step)
        moved += step
    return moved


def keep_above(obj, surface, margin):
    """Moves the object up along world Z until its lowest point is at least margin
    above the top of the surface, where they overlap seen from above (`settle`). An
    object that is already high enough, or not over the surface, is not moved.

    Returns:
        float: how far the object was moved up, 0 when it was not moved
    """
    return settle(obj, surface, margin, lift_only=True)


def place_on(obj, surface, margin):
    """Moves the object up or down along world Z until its lowest point is margin above
    the top of the surface, where they overlap seen from above (`settle`). An object
    that is not over the surface is not moved.

    Returns:
        float: how far the object was moved, positive up, 0 when it was not moved
    """
    return settle(obj, surface, margin, lift_only=False)


# the largest seed, Blender's seed inputs are 32 bit ints
MAX_SEED = 2**31 - 1


def seed_inputs(node_tree, prefix="", seen=None):
    """Unlinked integer Seed inputs of the nodes (Distribute Points, Random Value, Hash
    Value...), nested node groups included, each group once.

    Returns:
        list: (key, socket) pairs, the key is the node name, "Group node/Node" in groups
    """
    seen = set() if seen is None else seen
    seen.add(node_tree.name)
    found = []
    for name, node in node_tree.nodes.items():
        for socket in node.inputs:
            if socket.identifier == "Seed" and socket.type == "INT" and not socket.is_linked:
                found.append((prefix + name, socket))
        group = getattr(node, "node_tree", None)
        if group is not None and group.name not in seen:
            found += seed_inputs(group, f"{prefix}{name}/", seen)
    return found


def check_seed_group(node_tree):
    if not hasattr(node_tree, "nodes"):
        raise TypeError(f"{node_tree!r} is not a node group, pass e.g. bpy.data.node_groups[\"Geometry Nodes\"]")
    if not seed_inputs(node_tree):
        raise ValueError(f"Node group '{node_tree.name}' has no unlinked Seed inputs")


def seeds(node_tree, value=None):
    """Sets every Seed input of the node group (seed_inputs) to its own random int, or
    all of them to value.

    Returns:
        dict: seed that was set, by node (seed_inputs keys)
    """
    applied = {}
    for key, socket in seed_inputs(node_tree):
        bpy_undo.set_attr(socket, "default_value", random.randint(0, MAX_SEED) if value is None else value)
        applied[key] = socket.default_value
    return applied


def boolean(obj, data_path, value):
    """Sets the boolean value at the data path.

    Returns:
        bool: value that was set
    """
    current = bpy_paths.get_value(data_path, obj)
    if not isinstance(current, bool) and not (isinstance(current, int) and current in (0, 1)):
        raise TypeError(f"'{data_path}' is not a boolean value")
    new = bool(value)
    # old blender versions store some booleans as 0/1 ints
    bpy_paths.set_value(data_path, new if isinstance(current, bool) else int(new), obj)
    return new


def per_component(bound, size, data_path):
    """Expands a bound to one value per vector component. A number is used for all
    components, a sequence gives one value per component, None keeps the component."""
    if bound is None or is_number(bound):
        return [bound] * size
    if len(bound) != size:
        raise ValueError(f"'{data_path}' has {size} components, got bound {bound}")
    return list(bound)


def vector(obj, data_path, value_range):
    """Sets every component of the vector value at the data path to a random value
    from value_range (min, max). min and max are numbers for all components, or
    sequences with a value per component, None in both keeps that component.

    Returns:
        tuple: vector that was set
    """
    current = bpy_paths.get_value(data_path, obj)
    if not hasattr(current, "__len__") or isinstance(current, str) or not all(is_number(c) for c in current):
        raise TypeError(f"'{data_path}' is not a vector of numbers")
    current = tuple(current)
    size = len(current)

    low, high = value_range
    lows = per_component(low, size, data_path)
    highs = per_component(high, size, data_path)

    new = []
    for component, component_low, component_high in zip(current, lows, highs):
        if component_low is None or component_high is None:
            new.append(component)
            continue
        value = random.uniform(component_low, component_high)
        new.append(round(value) if isinstance(component, int) else value)

    new = tuple(new)
    bpy_paths.set_value(data_path, new, obj)
    return new


def menu_options(owner, token, data_path):
    """Returns all options of the menu at the data path."""
    kind, key = token
    if kind == "attr" and key in owner.bl_rna.properties:
        prop = owner.bl_rna.properties[key]
        if prop.type == "ENUM":
            if prop.is_enum_flag:
                raise TypeError(f"'{data_path}' is a multiple choice enum, not supported")
            if len(prop.enum_items):
                return [item.identifier for item in prop.enum_items]
    # menu sockets have dynamic items, defined by the menu switch node they belong to
    if getattr(owner, "type", None) == "MENU":
        options = socket_menu_options(owner)
        if options:
            return options
    raise ValueError(f"Cannot list the options of '{data_path}', pass them as options")


def socket_menu_options(socket):
    """Options of a menu input socket: those of its Menu Switch node, or for a group
    node, of the Menu Switch the group passes the input to (through reroutes and
    nested groups). None when there is none."""
    node = socket.node
    if hasattr(node, "enum_items"):
        return [item.name for item in node.enum_items]
    group = getattr(node, "node_tree", None)
    if group is None:
        return None
    outputs = [output for inner in group.nodes if inner.type == "GROUP_INPUT"
               for output in inner.outputs if output.identifier == socket.identifier]
    while outputs:
        output = outputs.pop()
        for link in output.links:
            if link.to_node.type == "REROUTE":
                outputs.extend(link.to_node.outputs)
            elif link.to_socket.type == "MENU":
                options = socket_menu_options(link.to_socket)
                if options:
                    return options
    return None


def id_property_menu(owner, token):
    """For a menu stored as an int id property (geometry nodes modifier menu inputs in
    4.x), the int of each option by name, from its UI data; otherwise None."""
    kind, key = token
    if kind != "item" or not isinstance(key, str) or not hasattr(owner, "id_properties_ui"):
        return None
    current = owner.get(key)
    if not isinstance(current, int) or isinstance(current, bool):
        return None
    items = owner.id_properties_ui(key).as_dict().get("items")
    return {item[0]: item[4] for item in items} if items else None


def menu(obj, data_path, options, weights):
    """Sets the menu (enum) value at the data path to a random option.

    Args:
        options (list): options to choose from, None = all options of the menu
        weights (list): relative probability of each option, None = equal

    Returns:
        str: option that was set
    """
    owner, token = bpy_paths.resolve(data_path, obj)
    values = id_property_menu(owner, token)
    if options is None:
        options = list(values) if values else menu_options(owner, token, data_path)
    if weights is not None and len(weights) != len(options):
        raise ValueError(f"'{data_path}' has {len(options)} options {options}, got {len(weights)} weights")
    new = random.choices(options, weights=weights)[0]
    if values:
        if new not in values:
            raise ValueError(f"'{new}' is not an option of '{data_path}', the options are {list(values)}")
        bpy_paths.set_value(data_path, values[new], obj)
    else:
        bpy_paths.set_value(data_path, new, obj)
    return new


def socket_type(obj, data_path):
    """Type of the node socket whose default_value the data path points at, e.g.
    VALUE, INT, BOOLEAN, VECTOR, RGBA, MENU."""
    owner, _ = bpy_paths.resolve(data_path, obj)
    return owner.type


# modifier properties about the modifier's UI, not its result, and show_render (a hint
# in the template instead): not written as rows of Modifier templates
MODIFIER_UI = ("name", "type", "rna_type", "show_expanded", "show_in_editmode", "show_on_cage", "show_viewport",
               "show_render",
               "is_active", "use_pin_to_last", "is_override_data", "use_apply_on_spline", "persistent_uid",
               "execution_time")


def property_kind(prop):
    """Kind of an RNA property, named like a node socket type: VALUE, INT, VECTOR (a
    number array), RGBA (a 4 component color), BOOLEAN, MENU (a single choice enum), or
    the property type itself (POINTER, STRING...) for the others."""
    length = getattr(prop, "array_length", 0)
    if prop.type in ("FLOAT", "INT"):
        if length:
            return "RGBA" if length == 4 and prop.subtype in ("COLOR", "COLOR_GAMMA") else "VECTOR"
        return "VALUE" if prop.type == "FLOAT" else "INT"
    if prop.type == "BOOLEAN" and not length:
        return "BOOLEAN"
    if prop.type == "ENUM" and not prop.is_enum_flag:
        return "MENU"
    return prop.type


def modifier_properties(modifier):
    """The settings of a modifier a Modifier can set: (identifier, property, kind) for
    every writable number, vector, boolean and menu property, in the order Blender lists
    them, without those about its UI (MODIFIER_UI, open_... panels)."""
    settings = []
    for prop in modifier.bl_rna.properties:
        if prop.is_readonly or prop.identifier in MODIFIER_UI or prop.identifier.startswith("open_"):
            continue
        kind = property_kind(prop)
        if kind in INPUT_KINDS:
            settings.append((prop.identifier, prop, kind))
    return settings


def modifier_input(obj, modifier_path, key):
    """The data path of a modifier input's value, and its kind (VALUE, INT, VECTOR, ...
    as bpy_paths.interface_kind).

    Args:
        modifier_path (str): data path of the modifier, absolute or relative to obj
        key (str | int): for a geometry nodes modifier, an input name, or index among
            the inputs (Geometry included); for any modifier, a property name, e.g.
            levels (a geometry nodes input of that name comes first)
    """
    modifier = bpy_paths.get_value(modifier_path, obj)
    if not isinstance(modifier, bpy.types.Modifier):
        raise TypeError(f"'{modifier_path}' is not a modifier")
    if modifier.type == "NODES":
        inputs = bpy_paths.modifier_inputs(modifier)
        if isinstance(key, int):
            if not -len(inputs) <= key < len(inputs):
                raise KeyError(f"Modifier '{modifier.name}' has {len(inputs)} inputs, no input {key}")
            _, identifier, kind = inputs[key]
            return modifier_path + bpy_paths.modifier_input_path(modifier, identifier), kind
        matches = [(identifier, kind) for name, identifier, kind in inputs if name == key]
        if matches:
            identifier, kind = matches[0]
            return modifier_path + bpy_paths.modifier_input_path(modifier, identifier), kind
    prop = modifier.bl_rna.properties.get(key) if isinstance(key, str) else None
    if prop is None or prop.is_readonly or key in ("name", "type", "rna_type"):
        names = [identifier for identifier, _, _ in modifier_properties(modifier)]
        if modifier.type == "NODES":
            raise KeyError(f"Modifier '{modifier.name}' has no input or setting {key!r}, its inputs are "
                           f"{[name for name, _, _ in bpy_paths.modifier_inputs(modifier)]}")
        raise KeyError(f"Modifier '{modifier.name}' has no setting {key!r}: give a setting by its "
                       f"Python name, its settings are {names}")
    return f"{modifier_path}.{key}", property_kind(prop)


# a socket limit this far from 0 is the "no limit" default (about 3.4e38 for floats, 2**31 for ints)
UNBOUNDED = 1e9
# Node template comments are aligned after the inputs up to this long
TEMPLATE_ALIGN = 60


def socket_limits(socket):
    """(min, max) of a number or vector input socket: for a group node, its group
    interface's min and max; otherwise those of the socket type (e.g. 0 to 1 for a
    factor). None when it has no finite limits."""
    group = getattr(socket.node, "node_tree", None)
    item = None
    if group is not None:
        item = next((item for item in group.interface.items_tree
                     if item.item_type == "SOCKET" and item.in_out == "INPUT"
                     and item.identifier == socket.identifier), None)
    if item is not None and hasattr(item, "min_value"):
        low, high = item.min_value, item.max_value
    else:
        prop = socket.bl_rna.properties.get("default_value")
        if prop is None or not hasattr(prop, "hard_min"):
            return None
        low, high = prop.hard_min, prop.hard_max
    if abs(low) >= UNBOUNDED or abs(high) >= UNBOUNDED:
        return None
    return low, high


def template_number(value):
    """A number as source text, floats rounded to 4 decimals."""
    if isinstance(value, (bool, int)):
        return repr(value)
    return repr(round(value, 4) + 0.0)  # + 0.0 turns -0.0 into 0.0


def template_value(value, short=False):
    """A number, or a vector as a tuple of numbers, as source text. With short, a
    vector with equal components is written as one number."""
    if isinstance(value, (bool, int, float)):
        return template_number(value)
    texts = [template_number(v) for v in value]
    if short and len(set(texts)) == 1:
        return texts[0]
    return "(" + ", ".join(texts) + ")"


def template_string(text):
    """A string as source text, in double quotes."""
    return json.dumps(text, ensure_ascii=False)


def path_text(steps):
    """Parsed data path steps as text, with double quoted names."""
    path = ""
    for kind, key in steps:
        if kind == "attr":
            path += "." + key
        else:
            path += f"[{key}]" if isinstance(key, int) else f"[{template_string(key)}]"
    return path


def node_path(node):
    """The absolute data path of a node (or any datablock), with double quoted names."""
    full = repr(node)
    if not full.startswith("bpy."):
        raise ValueError(f"Cannot find the data path of {full}, pass it as a string")
    return "bpy" + path_text(bpy_paths.parse(full)[1:])


# the datablock kinds a template names, by their bpy.data collection
ID_KINDS = {"objects": "object", "materials": "material", "worlds": "world", "lights": "light",
            "node_groups": "node group", "meshes": "mesh", "shape_keys": "shape keys"}


def path_owner(path):
    """The datablock an absolute path starts from (bpy.data.<collection>[name]), its
    collection, and the rest of the path as steps."""
    steps = bpy_paths.parse(path)[1:]
    collection, name = steps[1][1], steps[2][1]
    return getattr(bpy.data, collection)[name], collection, steps[3:]


def relative_node_path(node, obj):
    """The data path of a node relative to obj: through the object itself (a world),
    its data (a light), its active material or another material slot, or a geometry
    nodes modifier using the node's group."""
    owner, _, rest = path_owner(node_path(node))
    rest = path_text(rest)
    if owner == obj:
        return rest.lstrip(".")
    if owner == getattr(obj, "data", None):
        return "data" + rest
    if owner == getattr(obj, "active_material", None):
        return "active_material" + rest
    for index, slot in enumerate(getattr(obj, "material_slots", ())):
        if slot.material == owner:
            return f"material_slots[{index}].material" + rest
    for modifier in getattr(obj, "modifiers", ()):
        if modifier.type == "NODES" and modifier.node_group == owner:
            return f"modifiers[{template_string(modifier.name)}].node_group" + rest
    raise ValueError(f"Node '{node.name}' is not in a material, modifier or data of '{obj.name}', "
                     f"so it has no path relative to it: use an absolute path")


def template_header(relative_to, owner, collection):
    """The comment lines on top of a template that say which kind of paths it has: for
    relative paths the object they start from, for absolute ones what they change."""
    if relative_to is not None:
        return [f"# Relative paths: they start from the object it is called with, here "
                f"{template_string(relative_to.name)}.",
                f"# Pass that object to the Compose, compose([{node_path(relative_to)}]), "
                f"or one set up the same way."]
    kind = ID_KINDS.get(collection, "datablock")
    return [f"# Absolute paths: they always change the {kind} {template_string(owner.name)}, "
            f"so it works with any",
            "# objects passed to the Compose, or called alone without one."]


def template_text(header, opening, rows, closing, align_all=False):
    """A template: the header comments, the opening line, one indented line per row
    (code, comment), code None for a row that is only a comment, and the closing line.
    Comments are aligned after the code, past lines longer than TEMPLATE_ALIGN only by
    two spaces unless align_all."""
    width = max((len(code) for code, _ in rows if code and (align_all or len(code) <= TEMPLATE_ALIGN)), default=0)
    lines = [*header, opening]
    for code, comment in rows:
        lines.append(f"    # {comment}" if code is None else f"    {code.ljust(width)}  # {comment}")
    lines.append(closing)
    return "\n".join(lines) + "\n"


def template_entry(kind, value, limits, options):
    """The augmentation (Number, Vector, Boolean or Menu), its arguments and the comment
    of one input in a Node or Modifier template, from its kind (VALUE, INT, VECTOR...),
    current value, (min, max) limits (None: none) and menu options (None: not
    readable). None for a kind that can't be set; None for the augmentation when a menu's
    options can't be read, so it is only a comment."""
    if kind in ("VALUE", "INT", "VECTOR"):
        word = {"VALUE": "float", "INT": "int", "VECTOR": "vector"}[kind]
        augmentation = "Vector" if kind == "VECTOR" else "Number"
        now = template_value(value)
        if limits is None:
            bound = template_value(value, short=True)
            return augmentation, f"({bound}, {bound})", f"{word}, now {now}, no min/max"
        return augmentation, f"({template_number(limits[0])}, {template_number(limits[1])})", f"{word}, now {now}"
    if kind == "ROTATION":
        now = template_value(value)
        return "Vector", f"({now}, {now})", f"rotation in radians, now {now}"
    if kind == "RGBA":
        return "Vector", "(0.0, 1.0)", f"color, now {template_value(value)}"
    if kind == "BOOLEAN":
        return "Boolean", "p=0.5", f"boolean, now {bool(value)}"
    if kind == "MENU":
        now = template_string(value)
        if not options:
            return None, None, f"Warning! menu, now {now}, options not readable: add Menu([...]) yourself"
        return "Menu", "[" + ", ".join(template_string(o) for o in options) + "]", f"menu, now {now}"
    return None


def template_input(socket):
    """The augmentation (Number, Vector, Boolean or Menu), its arguments and the
    comment of one unlinked input socket in a Node template, None for a socket type
    Node can't set. Numbers and vectors get the socket's min and max, or the current
    value when it has none. A menu whose options can't be found (a built-in node's
    menu, e.g. Transform Geometry's Mode: Python can't list them) gets None for the
    augmentation, so it is only a comment."""
    kind = socket.type
    if kind not in INPUT_KINDS:
        return None
    limits = socket_limits(socket) if kind in ("VALUE", "INT", "VECTOR") else None
    options = socket_menu_options(socket) if kind == "MENU" else None
    return template_entry(kind, socket.default_value, limits, options)


def node_template(node, obj=None, prefix="augmentations.", relative=False):
    """Source text of a Node augmentation that sets every input of the node, see
    augmentations.Node.template.

    Args:
        node (str | bpy.types.Node): the node, or its data path (absolute, or relative
            to obj), which is then kept in the text
        obj (bpy.types.Object): object a relative path starts from
        prefix (str): written before Node and the input augmentations
        relative (bool): for a node, write its path relative to obj (a world's node:
            to the world)
    """
    if isinstance(node, str):
        path, node = node, bpy_paths.get_value(node, obj)
        relative = not bpy_paths.is_absolute(path)
    else:
        path = None
    if not isinstance(getattr(node, "inputs", None), bpy.types.bpy_prop_collection) or not hasattr(node, "bl_idname"):
        raise TypeError(f"{node!r} is not a node")
    owner, collection, _ = path_owner(node_path(node))
    if relative and isinstance(owner, bpy.types.World):
        obj = owner  # a world is augmented itself
    if path is None:
        if relative and obj is None:
            raise ValueError("A relative template needs the object its path starts from")
        path = relative_node_path(node, obj) if relative else node_path(node)
    sockets = [(index, socket) for index, socket in enumerate(node.inputs)
               if socket.enabled and socket.bl_idname != "NodeSocketVirtual"]
    if not sockets:
        raise ValueError(f"Node '{node.name}' has no inputs")
    names = Counter(socket.name for _, socket in sockets)
    rows = []  # (code, comment), code None for an input that is only a comment
    for index, socket in sockets:
        # inputs that share a name are keyed by index, the name goes in the comment
        if names[socket.name] == 1:
            key, named = template_string(socket.name), ""
        else:
            key, named = str(index), f"{template_string(socket.name)}, "
        entry = None if socket.is_linked or socket.hide_value else template_input(socket)
        if socket.is_linked:
            rows.append((None, f"{key}: {named}connected"))
        elif socket.hide_value:
            # e.g. Normal: only a link sets it, the node shows no value
            rows.append((None, f"{key}: {named}no value on the node"))
        elif entry is None:
            rows.append((None, f"{key}: {named}{socket.type.lower()} socket, not supported"))
        elif entry[0] is None:
            rows.append((None, f"{key}: {named}{entry[2]}"))
        else:
            augmentation, arguments, comment = entry
            rows.append((f"{key}: {prefix}{augmentation}({arguments}),", named + comment))
    return template_text(template_header(obj if relative else None, owner, collection),
                         f"{prefix}Node({path!r}, {{", rows, "})")


def shape_keys_template(obj, prefix="augmentations.", relative=True):
    """Source text of a Chain with a Number for the value of every shape key of the
    object, see augmentations.Chain.shape_keys_template.

    Args:
        obj (bpy.types.Object): object with shape keys (mesh, curve, lattice)
        prefix (str): written before Chain and Number
        relative (bool): paths relative to the object, otherwise absolute through it
    """
    key = getattr(getattr(obj, "data", None), "shape_keys", None)
    name = getattr(obj, "name", repr(obj))
    if key is None or len(key.key_blocks) < 2:
        raise ValueError(f"'{name}' has no shape keys besides the basis")
    if not key.use_relative:
        raise ValueError(f"The shape keys of '{name}' are not relative: Evaluation Time sets them, not their values")
    drivers = {curve.data_path for curve in key.animation_data.drivers} if key.animation_data else set()
    rows = []  # (code, comment), code None for a key that is only a comment
    for block in key.key_blocks:
        if block == key.reference_key:
            continue
        path = f"data.shape_keys.key_blocks[{template_string(block.name)}].value"
        if not relative:
            path = f"{node_path(obj)}.{path}"
        now = template_number(block.value)
        if block.path_from_id("value") in drivers:
            rows.append((None, f"{path!r}: driven, now {now}"))
        elif block.mute:
            rows.append((None, f"{path!r}: muted, now {now}"))
        else:
            value_range = f"({template_number(block.slider_min)}, {template_number(block.slider_max)})"
            rows.append((f"{prefix}Number({path!r}, {value_range}),", f"now {now}"))
    # the lines differ only by the key names, so all comments are aligned
    return template_text(template_header(obj if relative else None, obj, "objects"),
                         f"{prefix}Chain([", rows, "])", align_all=True)


def modifier_input_used_attribute(modifier, identifier):
    """The attribute name a geometry nodes modifier input reads, None when it uses its
    value."""
    if hasattr(modifier, "properties"):
        item = getattr(modifier.properties.inputs, identifier)
        return item.attribute_name if item.type == "ATTRIBUTE" else None
    if modifier.get(f"{identifier}_use_attribute"):
        return modifier.get(f"{identifier}_attribute_name", "")
    return None


def interface_limits(item):
    """(min, max) of a node group interface input, None without finite ones."""
    if not hasattr(item, "min_value"):
        return None
    low, high = item.min_value, item.max_value
    if abs(low) >= UNBOUNDED or abs(high) >= UNBOUNDED:
        return None
    return low, high


def modifier_template(modifier, obj=None, prefix="augmentations.", relative=True):
    """Source text of a Modifier augmentation that sets every input of a geometry
    nodes modifier, or every setting of another modifier, see
    augmentations.Modifier.template.

    Args:
        modifier (str | bpy.types.Modifier): the modifier, or its data path (absolute,
            or relative to obj), which is then kept in the text
        obj (bpy.types.Object): object a relative path starts from
        prefix (str): written before Modifier and the input augmentations
        relative (bool): for a modifier, write its path relative to its object
    """
    if isinstance(modifier, str):
        path, modifier = modifier, bpy_paths.get_value(modifier, obj)
        relative = not bpy_paths.is_absolute(path)
    else:
        path = None
    if not isinstance(modifier, bpy.types.Modifier):
        raise TypeError(f"{modifier!r} is not a modifier")
    owner = modifier.id_data
    if path is None:
        path = f"modifiers[{template_string(modifier.name)}]" if relative else node_path(modifier)
    if relative and obj is None:
        obj = owner
    if modifier.type == "NODES":
        rows = node_modifier_rows(modifier, prefix)
    else:
        rows = modifier_property_rows(modifier, prefix)
    if not rows:
        raise ValueError(f"Modifier '{modifier.name}' has no inputs or settings to set")
    # the modifier on and off: a hint, randomizing it in every template would be a surprise
    rows.append((None, f'"show_render": {prefix}Boolean(p=...) turns the modifier on and off in the render, '
                       f"now {modifier.show_render}"))
    return template_text(template_header(obj if relative else None, owner, "objects"),
                         f"{prefix}Modifier({path!r}, {{", rows, "})")


def template_row(key, named, entry, kind, prefix):
    """One row (code, comment) of a template from template_entry's result."""
    if entry is None:
        word = kind.removeprefix("NodeSocket").lower()
        return None, f"{key}: {named}{word} socket, not supported"
    if entry[0] is None:
        return None, f"{key}: {named}{entry[2]}"
    augmentation, arguments, comment = entry
    return f"{key}: {prefix}{augmentation}({arguments}),", named + comment


def modifier_property_rows(modifier, prefix):
    """Template rows for the settings of a modifier other than geometry nodes, by their
    Python name, with the name shown in the UI in the comment. Numbers get the soft
    (slider) min and max."""
    rows = []
    for identifier, prop, kind in modifier_properties(modifier):
        value = getattr(modifier, identifier)
        limits = None
        if kind in ("VALUE", "INT", "VECTOR") and abs(prop.soft_min) < UNBOUNDED and abs(prop.soft_max) < UNBOUNDED:
            limits = prop.soft_min, prop.soft_max
        options = [item.identifier for item in prop.enum_items] if kind == "MENU" else None
        entry = template_entry(kind, value, limits, options)
        rows.append(template_row(template_string(identifier), f"{template_string(prop.name)}, ", entry, kind, prefix))
    return rows


def node_modifier_rows(modifier, prefix):
    """Template rows for the inputs of a geometry nodes modifier, by name. Numbers get
    the Min and Max of the input in the node group's interface."""
    inputs = bpy_paths.modifier_inputs(modifier)
    items = {item.identifier: item for item in modifier.node_group.interface.items_tree
             if item.item_type == "SOCKET" and item.in_out == "INPUT"}
    names = Counter(name for name, _, _ in inputs)
    rows = []
    for index, (name, identifier, kind) in enumerate(inputs):
        if names[name] == 1:
            key, named = template_string(name), ""
        else:
            key, named = str(index), f"{template_string(name)}, "
        if kind == "GEOMETRY" or kind.startswith("NodeSocketGeometry"):
            continue  # the modifier's geometry, not an input to set
        value_path = bpy_paths.modifier_input_path(modifier, identifier)
        attribute = modifier_input_used_attribute(modifier, identifier) if kind in INPUT_KINDS else None
        if attribute is not None:
            rows.append((None, f"{key}: {named}reads the attribute {template_string(attribute)}"))
            continue
        entry = None
        if kind in INPUT_KINDS:
            value = bpy_paths.get_value(value_path, modifier)
            options = None
            if kind == "MENU":
                owner_struct, token = bpy_paths.resolve(value_path, modifier)
                values = id_property_menu(owner_struct, token)
                if values:  # 4.x: an int per option
                    options = list(values)
                    value = next((name for name, number in values.items() if number == value), value)
                else:
                    try:
                        options = menu_options(owner_struct, token, value_path)
                    except ValueError:
                        options = None
            limits = interface_limits(items[identifier]) if kind in ("VALUE", "INT", "VECTOR") else None
            entry = template_entry(kind, value, limits, options)
        rows.append(template_row(key, named, entry, kind, prefix))
    return rows


# the kinds of inputs Number, Vector, Boolean and Menu set
INPUT_KINDS = ("VALUE", "INT", "VECTOR", "ROTATION", "RGBA", "BOOLEAN", "MENU")
