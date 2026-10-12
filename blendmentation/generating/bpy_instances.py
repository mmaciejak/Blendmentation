"""Reads the instances that geometry nodes or collection instancing make on an object
from the depsgraph, for `Instances` in the classes."""

import bpy
import numpy as np


# persistent_id of a depsgraph instance is its path through the nested instances, innermost
# first, padded with this value
INSTANCE_PATH_END = 2**31 - 1


# object types that have no geometry, they are left out of the instances
NO_GEOMETRY_TYPES = {"EMPTY", "LIGHT", "LIGHT_PROBE", "LIGHTPROBE", "CAMERA", "SPEAKER", "ARMATURE", "LATTICE"}


# object types that can have a geometry nodes modifier, needed for the id render of instances
NODES_MODIFIER_TYPES = {"MESH", "CURVE", "FONT", "CURVES", "POINTCLOUD", "VOLUME", "GREASEPENCIL"}


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
    # a geometry instance is a copy of a mesh, named like it: Object Info without As Instance,
    # or nodes after the instancing that change the instanced meshes (Smooth by Angle)
    mesh_users = {}
    for obj in bpy.data.objects:
        if obj.type == "MESH" and obj.data is not None:
            mesh_users.setdefault(obj.data.name, []).append(obj)
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
        found = [source]
        if source == parent:
            mesh_name = obj.data.name if obj.data else obj.name
            # None: no object behind it
            found = mesh_users.get(mesh_name, [None]) if obj.type == "MESH" else [None]
            source = found[0] or parent
            source_name = found[0].name if found[0] else mesh_name
        else:
            source_name = source.name
        name = f"{parent.name}/{'.'.join(map(str, reversed(path)))}/{source_name}"
        member = InstanceMember(parent, source, path[-1], name, obj.type, instance.matrix_world.copy(), coords[key])
        members, sources = tops.setdefault(path[-1], ([], set()))
        members.append(member)
        sources.update(found)
    # the path inside the top-level instance only tells its objects apart
    for top, (members, _) in tops.items():
        if len(members) == 1:
            members[0].name = f"{parent.name}/{top}/{members[0].name.rsplit('/', 1)[1]}"
    return tops


def has_instances(groups):
    return any(isinstance(obj, InstanceMember) for group in groups for obj in group)
