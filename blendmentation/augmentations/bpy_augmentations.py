import colorsys
import math
import random

import bpy
from mathutils import Euler, Matrix, Quaternion, Vector
from mathutils.bvhtree import BVHTree

from .. import bpy_paths, bpy_undo


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


def sample_absolute(value_range):
    """Draws a value from a (min, max) range, None means no change."""
    if value_range is None:
        return None
    low, high = value_range
    return random.uniform(low, high)


def material(obj, material_id, hue, saturation, value, roughness, metallic):
    """Sets base color, roughness and metallic of the material's Principled BSDF
    to random values from (min, max) ranges. None leaves the value unchanged.
    Base color is set through hue, saturation and value, each in 0-1.

    Returns:
        dict: values that were set
    """
    mat = get_material(obj, material_id)
    principled = get_node(mat.node_tree, "BSDF_PRINCIPLED", mat.name)
    applied = {}

    hsv = {"hue": sample_absolute(hue), "saturation": sample_absolute(saturation), "value": sample_absolute(value)}
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
        new = sample_absolute(value_range)
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


def target_center(target):
    """World location of a look-at target: an object or a list of objects (the center
    of their bounding boxes), or a point (x, y, z)."""
    if isinstance(target, (list, tuple)) and len(target) == 3 and all(isinstance(v, (int, float)) for v in target):
        return Vector(target)
    objects = [target] if isinstance(target, bpy.types.Object) else list(target)
    if not objects or not all(isinstance(obj, bpy.types.Object) for obj in objects):
        raise TypeError("LookAt target must be an object, a list of objects or a point (x, y, z)")
    centers = [obj.matrix_world @ (sum((Vector(corner) for corner in obj.bound_box), Vector()) / 8) for obj in objects]
    return sum(centers, Vector()) / len(centers)


def range_or_value(value_range, current):
    """None keeps the current value, a number is used as it is, (min, max) is sampled."""
    if value_range is None:
        return current
    if isinstance(value_range, (int, float)):
        return float(value_range)
    low, high = value_range
    return random.uniform(low, high)


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
    current_elevation = math.degrees(math.asin(max(-1.0, min(1.0, offset.z / current_distance)))) if current_distance else 0.0
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


def check_camera(obj, perspective=False):
    if obj.type != "CAMERA":
        raise TypeError(f"'{obj.name}' is {obj.type}, not a camera")
    if perspective and obj.data.type != "PERSP":
        raise TypeError(f"Camera '{obj.name}' is {obj.data.type}, focal length needs a perspective camera")


def focal_length(obj, focal_length_range, target, keep_size):
    """Sets the camera lens. With keep_size the camera moves along the line to the target
    by the same ratio as the lens, so the target keeps its size in the image (a dolly zoom).

    Args:
        focal_length_range (tuple): lens in mm, (min, max) or an exact number
        target: object, list of objects or point (x, y, z), needed for keep_size

    Returns:
        dict: focal_length that was set, and the new distance to the target with keep_size
    """
    check_camera(obj, perspective=True)
    old_lens = obj.data.lens
    bpy_undo.set_attr(obj.data, "lens", range_or_value(focal_length_range, old_lens))
    # blender stores it as 32 bit float, report and use what was stored
    applied = {"focal_length": obj.data.lens}
    if keep_size:
        center = target_center(target)
        matrix = obj.matrix_world.copy()
        matrix.translation = center + (matrix.translation - center) * (obj.data.lens / old_lens)
        bpy_undo.record_transforms(obj)
        obj.matrix_world = matrix
        applied["distance"] = (matrix.translation - center).length
    return applied


def depth_of_field(obj, target, f_stop):
    """Enables depth of field, focused at the target with a random f-stop.

    Args:
        target: object, list of objects or point (x, y, z) to focus on, None keeps the focus
        f_stop (tuple): aperture f-stop, (min, max), an exact number, or None to keep it

    Returns:
        dict: f_stop and focus_distance
    """
    check_camera(obj)
    dof = obj.data.dof
    bpy_undo.set_attr(dof, "use_dof", True)
    if target is not None:
        # focus distance along the view axis to the target center, from where the camera is now
        forward = obj.matrix_world.to_3x3().normalized() @ Vector((0.0, 0.0, -1.0))
        bpy_undo.set_attr(dof, "focus_object", None)
        bpy_undo.set_attr(dof, "focus_distance",
                          max((target_center(target) - obj.matrix_world.translation).dot(forward), 0.0))
    bpy_undo.set_attr(dof, "aperture_fstop", range_or_value(f_stop, dof.aperture_fstop))
    return {"f_stop": dof.aperture_fstop, "focus_distance": dof.focus_distance}


def disable_depth_of_field(obj):
    """Turns depth of field off, the focus and f-stop settings are kept.

    Returns:
        bool: False, depth of field is off
    """
    check_camera(obj)
    bpy_undo.set_attr(obj.data.dof, "use_dof", False)
    return False


def is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


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


def world_mesh(obj, depsgraph):
    """World coordinates of the evaluated object's vertices, and its faces. Objects
    without geometry (empties, cameras...) are a point at their origin, without faces."""
    obj_eval = obj.evaluated_get(depsgraph)
    mesh = obj_eval.to_mesh() if obj.type in {"MESH", "CURVE", "SURFACE", "META", "FONT", "CURVES", "POINTCLOUD"} else None
    if mesh is None:
        return [obj_eval.matrix_world.translation.copy()], []
    try:
        matrix = obj_eval.matrix_world
        vertices = [matrix @ vertex.co for vertex in mesh.vertices]
        faces = [tuple(polygon.vertices) for polygon in mesh.polygons]
    finally:
        obj_eval.to_mesh_clear()
    return vertices, faces


def column_hit(tree, x, y, z, direction):
    """Z of the first hit of a vertical ray from (x, y, z), None when it misses."""
    location = tree.ray_cast(Vector((x, y, z)), Vector((0.0, 0.0, direction)))[0]
    return None if location is None else location.z


def rest_offset(obj, surface, margin):
    """How far to move the object along world Z so its lowest point is margin above the
    top of the surface, where they overlap seen from above: positive up, negative down.

    Both are compared as evaluated meshes (modifiers included) in world space: the
    object's vertices against the surface top under them, and the surface's vertices
    against the object's bottom over them, so a surface peak between the object's
    vertices also counts.

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

    surface_tree = BVHTree.FromPolygons(surface_vertices, surface_faces)
    above_surface = max(vertex.z for vertex in surface_vertices + vertices) + 1.0
    offset = None
    for vertex in vertices:
        top = column_hit(surface_tree, vertex.x, vertex.y, above_surface, -1.0)
        if top is not None:
            offset = max(offset if offset is not None else -math.inf, top + margin - vertex.z)
    if faces:
        tree = BVHTree.FromPolygons(vertices, faces)
        below_object = min(vertex.z for vertex in vertices + surface_vertices) - 1.0
        for vertex in surface_vertices:
            bottom = column_hit(tree, vertex.x, vertex.y, below_object, 1.0)
            if bottom is None:
                continue
            top = column_hit(surface_tree, vertex.x, vertex.y, above_surface, -1.0)
            offset = max(offset if offset is not None else -math.inf, (vertex.z if top is None else top) + margin - bottom)
    return offset


def move_z(obj, offset):
    """Moves the object along world Z, parented objects included."""
    matrix = obj.matrix_world.copy()
    matrix.translation.z += offset
    bpy_undo.record_transforms(obj)
    obj.matrix_world = matrix


def keep_above(obj, surface, margin):
    """Moves the object up along world Z until its lowest point is at least margin
    above the top of the surface, where they overlap seen from above (rest_offset). An
    object that is already high enough, or not over the surface, is not moved.

    Returns:
        float: how far the object was moved up, 0 when it was not moved
    """
    offset = rest_offset(obj, surface, margin)
    if offset is None or offset <= 0:
        return 0.0
    move_z(obj, offset)
    return offset


def place_on(obj, surface, margin):
    """Moves the object up or down along world Z until its lowest point is margin above
    the top of the surface, where they overlap seen from above (rest_offset). An object
    that is not over the surface is not moved.

    Returns:
        float: how far the object was moved, positive up, 0 when it was not moved
    """
    offset = rest_offset(obj, surface, margin)
    if offset is None:
        return 0.0
    move_z(obj, offset)
    return offset


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


def modifier_input(obj, modifier_path, key):
    """The data path of a geometry nodes modifier input's value, and its kind (VALUE,
    INT, VECTOR, ... as bpy_paths.interface_kind).

    Args:
        modifier_path (str): data path of the modifier, absolute or relative to obj
        key (str | int): input name, or index among the inputs (Geometry included)
    """
    modifier = bpy_paths.get_value(modifier_path, obj)
    inputs = bpy_paths.modifier_inputs(modifier)
    if isinstance(key, int):
        if not -len(inputs) <= key < len(inputs):
            raise KeyError(f"Modifier '{modifier.name}' has {len(inputs)} inputs, no input {key}")
        _, identifier, kind = inputs[key]
    else:
        matches = [(identifier, kind) for name, identifier, kind in inputs if name == key]
        if not matches:
            raise KeyError(f"Modifier '{modifier.name}' has no input '{key}', "
                           f"its inputs are {[name for name, _, _ in inputs]}")
        identifier, kind = matches[0]
    return modifier_path + bpy_paths.modifier_input_path(modifier, identifier), kind
