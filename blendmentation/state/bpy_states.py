import bpy

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
# node properties that only change the node editor, or are saved separately (sockets)
NODE_UI_PROPERTIES = {
    "name", "label", "location", "location_absolute", "width", "height", "select", "hide",
    "show_options", "show_preview", "show_texture", "use_custom_color", "color", "parent",
    "warning_propagation", "active_index", "active_item", "bytecode", "bytecode_hash",
    "inputs", "outputs", "internal_links",
}
# how deep save_struct goes into nested structs, a node's color ramp stops are at depth 2
MAX_DEPTH = 3


def to_plain(value):
    """Copies blender arrays to tuples so the saved value doesn't follow later changes."""
    if hasattr(value, "__len__") and not isinstance(value, str):
        return tuple(value)
    return value


def is_id_type(rna):
    """Whether the RNA struct is an ID (image, node group, object...) or a subtype of one."""
    while rna is not None:
        if rna.identifier == "ID":
            return True
        rna = rna.base
    return False


def save_struct(struct, skip=(), depth=0):
    """Returns the writable values of a struct by property name: numbers, enums, strings and IDs
    (an image, a node group), nested structs (a color ramp, a curve mapping) as dicts and their
    collections (color ramp stops, curve points) as lists of dicts.

    Args:
    struct (bpy_struct): struct to save, e.g. a node
    skip (set): property names to leave out
    """
    values = {}
    for prop in struct.bl_rna.properties:
        name = prop.identifier
        if name in skip or name == "rna_type" or name.startswith("bl_"):
            continue
        if prop.type == "POINTER":
            if not prop.is_readonly:
                if is_id_type(prop.fixed_type):
                    values[name] = getattr(struct, name)
            elif depth < MAX_DEPTH and not is_id_type(prop.fixed_type):
                value = getattr(struct, name)
                if value is not None:
                    values[name] = save_struct(value, depth=depth + 1)
        elif prop.type == "COLLECTION":
            if depth < MAX_DEPTH:
                values[name] = [save_struct(item, depth=depth + 1) for item in getattr(struct, name)]
        elif not prop.is_readonly:
            values[name] = to_plain(getattr(struct, name))
    return values


def load_struct(struct, values):
    """Sets the values saved by save_struct. Only changed values are set, since every set
    triggers an update. Collections are restored only when they still have as many items."""
    changed = False
    for name, value in values.items():
        current = getattr(struct, name)
        if isinstance(value, dict):
            changed |= load_struct(current, value)
        elif isinstance(value, list):
            if len(current) == len(value):
                for item, item_values in zip(current, value):
                    changed |= load_struct(item, item_values)
        elif to_plain(current) != value:
            setattr(struct, name, value)
            changed = True
    # curve points only take effect after an update
    if changed and isinstance(struct, bpy.types.CurveMapping):
        struct.update()
    return changed


def save_node_tree(node_tree):
    """Returns the state of every node by node name: its settings (save_struct), the values of
    all its input and output sockets (Value and RGB nodes keep theirs on the output) by
    identifier, and for group nodes the group's nodes."""
    nodes = {}
    # the collection key, node.name is the AOV name on AOV Output nodes in blender 4.0
    for name, node in node_tree.nodes.items():
        state = {
            "settings": save_struct(node, NODE_UI_PROPERTIES),
            "inputs": save_sockets(node.inputs),
            "outputs": save_sockets(node.outputs),
        }
        if getattr(node, "node_tree", None) is not None:
            state["group"] = save_node_tree(node.node_tree)
        nodes[name] = state
    return nodes


def save_sockets(sockets):
    return {socket.identifier: to_plain(socket.default_value)
            for socket in sockets if hasattr(socket, "default_value")}


def load_sockets(sockets, values):
    for socket in sockets:
        if socket.identifier in values and to_plain(socket.default_value) != values[socket.identifier]:
            socket.default_value = values[socket.identifier]


def load_node_tree(node_tree, nodes):
    """Sets the node states saved by save_node_tree. Settings go first, since some (a data
    type) change the sockets."""
    for node_name, state in nodes.items():
        node = node_tree.nodes.get(node_name)
        if node is None:
            continue
        load_struct(node, state["settings"])
        load_sockets(node.inputs, state["inputs"])
        load_sockets(node.outputs, state["outputs"])
        if "group" in state and node.node_tree is not None:
            load_node_tree(node.node_tree, state["group"])


def state_key(object):
    """Key of the object in the state dict: its name, and for other datablocks also its type,
    so a world named like an object doesn't overwrite it."""
    return object.name if hasattr(object, "material_slots") else (object.rna_type.identifier, object.name)


def own_node_tree(datablock):
    """The node tree of a world, material or light, or the datablock itself for a node group."""
    return datablock if isinstance(datablock, bpy.types.NodeTree) else getattr(datablock, "node_tree", None)


def create_state_list(object):
    """Returns a dict of the parameters changed by the object augmentations:
    transforms, render visibility, material node values, and the lens and depth of field of cameras.
    Other datablocks (a world, a material, light data) get only their node tree's values.

    Args:
    object (bpy.object): object to get the parameters from
    """
    if not hasattr(object, "material_slots"):
        node_tree = own_node_tree(object)
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
            load_node_tree(own_node_tree(object), state["node_tree"])
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
