import re

import bpy

from . import bpy_undo

TOKEN = re.compile(
    r"""\.?(?P<attr>[A-Za-z_]\w*)"""
    r"""|\[\s*"(?P<dkey>(?:[^"\\]|\\.)*)"\s*\]"""
    r"""|\[\s*'(?P<skey>(?:[^'\\]|\\.)*)'\s*\]"""
    r"""|\[\s*(?P<index>-?\d+)\s*\]"""
)


def parse(data_path):
    """Splits a data path into tokens: ("attr", name) for .name, ("item", key) for ["key"] or [index]."""
    tokens = []
    position = 0
    while position < len(data_path):
        match = TOKEN.match(data_path, position)
        if match is None or (match.group("attr") and position > 0 and data_path[position] != "."):
            raise ValueError(f"Cannot parse data path '{data_path}' at position {position}")
        if match.group("attr") is not None:
            tokens.append(("attr", match.group("attr")))
        elif match.group("index") is not None:
            tokens.append(("item", int(match.group("index"))))
        else:
            key = match.group("dkey") if match.group("dkey") is not None else match.group("skey")
            tokens.append(("item", re.sub(r"\\(.)", r"\1", key)))
        position = match.end()
    if not tokens:
        raise ValueError("Empty data path")
    return tokens


def is_absolute(data_path):
    """Absolute paths start at the bpy module, e.g. bpy.data.materials["Mat"]..."""
    return data_path.startswith("bpy.") or data_path.startswith("bpy[")


def step(owner, token):
    kind, key = token
    return getattr(owner, key) if kind == "attr" else owner[key]


def resolve(data_path, obj=None):
    """Returns the owner of the last path element and the last token.

    Args:
        data_path (str): absolute path starting with "bpy." (as from "Copy Full Data Path"),
            or a path relative to obj, e.g. 'data.shape_keys.key_blocks["Key 1"].value'
        obj (bpy.object): object relative paths start from
    """
    tokens = parse(data_path)
    if is_absolute(data_path):
        owner, tokens = bpy, tokens[1:]
    elif obj is None:
        raise ValueError(f"Relative data path '{data_path}' needs an object")
    else:
        owner = obj
    for token in tokens[:-1]:
        owner = step(owner, token)
    return owner, tokens[-1]


def get_value(data_path, obj=None):
    """Returns the value at the data path."""
    owner, token = resolve(data_path, obj)
    return step(owner, token)


def set_value(data_path, value, obj=None):
    """Sets the value at the data path and tags its datablock for update."""
    owner, token = resolve(data_path, obj)
    kind, key = token
    if kind == "attr":
        bpy_undo.set_attr(owner, key, value)
    else:
        bpy_undo.set_item(owner, key, value)
    bpy_undo.tag(owner)


# the kind of value an input socket holds, by the start of its interface socket_type,
# named like node socket types
SOCKET_KINDS = (
    ("NodeSocketFloat", "VALUE"),
    ("NodeSocketInt", "INT"),
    ("NodeSocketBool", "BOOLEAN"),
    ("NodeSocketVector", "VECTOR"),
    ("NodeSocketColor", "RGBA"),
    ("NodeSocketRotation", "ROTATION"),
    ("NodeSocketMenu", "MENU"),
)


def interface_kind(socket_type):
    """Kind of a node group interface socket (socket_type e.g. NodeSocketFloatFactor),
    like a node socket's type: VALUE, INT, BOOLEAN, VECTOR, RGBA, ROTATION, MENU, or the
    socket_type itself for the others."""
    for prefix, kind in SOCKET_KINDS:
        if socket_type.startswith(prefix):
            return kind
    return socket_type


def modifier_inputs(modifier):
    """The inputs of a geometry nodes modifier, in the order of its node group's
    interface: (name, identifier, kind) for every input socket, Geometry included."""
    if getattr(modifier, "type", None) != "NODES":
        raise TypeError(f"{modifier!r} is not a geometry nodes modifier")
    if modifier.node_group is None:
        raise ValueError(f"Modifier '{modifier.name}' has no node group")
    return [(item.name, item.identifier, interface_kind(item.socket_type))
            for item in modifier.node_group.interface.items_tree
            if item.item_type == "SOCKET" and item.in_out == "INPUT"]


def modifier_input_path(modifier, identifier):
    """Path of a geometry nodes modifier input's value, relative to the modifier. Blender
    5 moved them from id properties to modifier.properties.inputs."""
    if hasattr(modifier, "properties"):
        return f".properties.inputs.{identifier}.value"
    return f'["{identifier}"]'
