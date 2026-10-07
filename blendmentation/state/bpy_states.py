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
    # the collection key, node.name is the AOV name on AOV Output nodes in blender 4.0
    for name, node in node_tree.nodes.items():
        inputs = {
            socket.identifier: to_plain(socket.default_value)
            for socket in node.inputs
            if hasattr(socket, "default_value") and not socket.is_linked
        }
        if inputs:
            nodes[name] = inputs
    return nodes


def load_node_tree(node_tree, nodes):
    """Sets node input default values saved by save_node_tree."""
    for node_name, inputs in nodes.items():
        node = node_tree.nodes[node_name]
        for socket in node.inputs:
            if socket.identifier in inputs:
                socket.default_value = inputs[socket.identifier]


def state_key(object):
    """Key of the object in the state dict: its name, and for other datablocks also its type,
    so a world named like an object doesn't overwrite it."""
    return object.name if hasattr(object, "material_slots") else (object.rna_type.identifier, object.name)


def create_state_list(object):
    """Returns a dict of the parameters changed by the object augmentations:
    transforms, render visibility, material node values, and the lens and depth of field of cameras.
    Other datablocks (a world, a material, light data) get only their node tree's values.

    Args:
    object (bpy.object): object to get the parameters from
    """
    if not hasattr(object, "material_slots"):
        node_tree = getattr(object, "node_tree", None)
        return {"node_tree": save_node_tree(node_tree) if node_tree is not None else {}}
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
    state_dict (dict): dict to load the values from, keyed by state_key
    """
    state = state_dict[state_key(object)]
    if "node_tree" in state:
        if state["node_tree"]:
            load_node_tree(object.node_tree, state["node_tree"])
        object.update_tag()
        return

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
