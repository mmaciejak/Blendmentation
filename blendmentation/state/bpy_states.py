from .. import bpy_paths

TRANSFORM_PROPERTIES = (
    "location",
    "rotation_mode",
    "rotation_euler",
    "rotation_quaternion",
    "rotation_axis_angle",
    "scale",
)
DOF_PROPERTIES = ("use_dof", "focus_object", "focus_distance", "aperture_fstop")


def to_plain(value):
    """Copies blender arrays to tuples so the saved value doesn't follow later changes."""
    if hasattr(value, "__len__") and not isinstance(value, str):
        return tuple(value)
    return value


def save_node_tree(node_tree):
    """Returns default values of all unconnected node inputs, by node name and input identifier."""
    nodes = {}
    for node in node_tree.nodes:
        inputs = {
            socket.identifier: to_plain(socket.default_value)
            for socket in node.inputs
            if hasattr(socket, "default_value") and not socket.is_linked
        }
        if inputs:
            nodes[node.name] = inputs
    return nodes


def load_node_tree(node_tree, nodes):
    """Sets node input default values saved by save_node_tree."""
    for node_name, inputs in nodes.items():
        node = node_tree.nodes[node_name]
        for socket in node.inputs:
            if socket.identifier in inputs:
                socket.default_value = inputs[socket.identifier]


def create_state_list(object):
    """Returns a dict of the parameters changed by the object augmentations:
    transforms, render visibility, material node values, and the lens and depth of field of cameras.

    Args:
    object (bpy.object): object to get the parameters from
    """
    state = {
        "transforms": {name: to_plain(getattr(object, name)) for name in TRANSFORM_PROPERTIES},
        "hide_render": object.hide_render,
        "materials": {},
    }
    for slot in object.material_slots:
        material = slot.material
        if material is not None and material.node_tree is not None:
            state["materials"][material.name] = save_node_tree(material.node_tree)
    if object.type == "CAMERA":
        dof = object.data.dof
        state["camera"] = {
            "lens": object.data.lens,
            "dof": {name: getattr(dof, name) for name in DOF_PROPERTIES},
        }
    return state


def load_from_state_dict(object, state_dict: dict):
    """Set all parameters of blender object to values in state dict

    Args:
    object (bpy.object): object to set the parameters from state dict
    state_dict (dict): dict to load the values from, keyed by object name
    """
    state = state_dict[object.name]

    # rotation mode first, so the rotation values are restored in the right mode
    for name, value in state["transforms"].items():
        setattr(object, name, value)
    object.hide_render = state["hide_render"]

    for slot in object.material_slots:
        material = slot.material
        if material is not None and material.name in state["materials"]:
            load_node_tree(material.node_tree, state["materials"][material.name])

    if "camera" in state:
        object.data.lens = state["camera"]["lens"]
        for name, value in state["camera"]["dof"].items():
            setattr(object.data.dof, name, value)

    object.update_tag()


def create_field_state(data_paths, objects):
    """Returns (object, data_path, value) for every data path value. Absolute paths are saved once,
    relative paths for every object they resolve on.

    Args:
    data_paths (list): data paths of the fields
    objects (list): objects relative paths start from
    """
    saved = []
    for data_path in data_paths:
        if bpy_paths.is_absolute(data_path):
            saved.append((None, data_path, to_plain(bpy_paths.get_value(data_path))))
            continue
        found = False
        for object in objects:
            try:
                value = bpy_paths.get_value(data_path, object)
            except (AttributeError, KeyError, IndexError, TypeError):
                continue
            saved.append((object, data_path, to_plain(value)))
            found = True
        if not found:
            raise ValueError(f"Relative data path '{data_path}' does not resolve on any of the objects")
    return saved


def load_field_state(saved):
    """Sets field values saved by create_field_state."""
    for object, data_path, value in saved:
        bpy_paths.set_value(data_path, value, object)
