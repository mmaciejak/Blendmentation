"""Id renders: images where every pixel holds the number of the group of objects it
shows, for masks and occlusion. Every setting they change is set back."""

from functools import partial

import bpy

from .. import bpy_undo
from . import bpy_images, bpy_instances

# instance attribute and custom property holding the ids in the id render of instances
ID_ATTRIBUTE = "blendmentation_id"


# color management that leaves the rendered values unchanged
STANDARD_VIEW = {"view_transform": "Standard", "look": "None", "exposure": 0.0, "gamma": 1.0,
                 "use_curve_mapping": False}


def editable_objects():
    """Objects not linked from a library. All of bpy.data, since instanced collections
    may use objects that are not in the scene."""
    return [obj for obj in bpy.data.objects if obj.library is None]


def hide_in_render(changes, keep):
    """Hides every editable object but those in keep in the render, recorded in changes."""
    for obj in editable_objects():
        if obj not in keep and not obj.hide_render:
            changes.set(obj, "hide_render", True)


def render_ids(scene, groups, file_path, hide_others=False):
    """Renders every group of objects in a flat color encoding its id (1, 2, ...)
    with workbench, other objects in black so they still occlude, or hidden with hide_others.
    The id image is saved to file_path (an EXR).

    Returns:
        np.ndarray: (height, width) array of group ids, 0 = background or other objects
    """
    with bpy_undo.temporary() as changes:
        if hide_others:
            hide_in_render(changes, {obj for group in groups for obj in group})
        changes.set_many(scene.render, {"engine": "BLENDER_WORKBENCH", "film_transparent": True,
                                        "use_compositing": False, "use_sequencer": False})
        changes.set(scene.display, "render_aa", "OFF")
        changes.set_many(scene.display.shading, {
            "type": "SOLID", "light": "FLAT", "color_type": "OBJECT", "show_cavity": False,
            "show_object_outline": False, "show_shadows": False, "show_specular_highlight": False,
            "show_xray": False, "use_dof": False, "show_backface_culling": False,
        })
        changes.set_many(scene.view_settings, STANDARD_VIEW)
        for obj in editable_objects():
            changes.set(obj, "color", (0.0, 0.0, 0.0, 1.0))
        for group_id, group in enumerate(groups, start=1):
            # id in 8 bit steps of red and green, read back exactly from the float EXR
            color = ((group_id % 256) / 255.0, (group_id // 256) / 255.0, 0.0, 1.0)
            for obj in group:
                obj.color = color

        bpy.ops.render.render()
        bpy_images.save_render_result(scene, file_path, "OPEN_EXR", "32", "RGBA")
    return bpy_images.read_ids(file_path, encoded=True)


def render_index_ids(scene, view_layer, groups, file_path):
    """Renders the groups with Cycles at 1 sample, every other object hidden, and reads
    the group ids (1, 2, ...) from the Object Index pass, like `index_pass_ids`. The
    multilayer render result is saved to file_path (an EXR).

    Returns:
        np.ndarray: (height, width) array of group ids, 0 = background
    """
    with bpy_undo.temporary() as changes:
        hide_in_render(changes, {obj for group in groups for obj in group})
        changes.set_many(scene.render, {"use_compositing": False, "use_sequencer": False})
        # the pass is written by the first sample, it is not anti-aliased
        changes.set_many(scene.cycles, {"samples": 1, "use_denoising": False, "use_adaptive_sampling": False})
        changes.set(view_layer, "use_pass_object_index", True)
        for obj in editable_objects():
            changes.set(obj, "pass_index", 0)
        for group_id, group in enumerate(groups, start=1):
            for obj in group:
                obj.pass_index = group_id

        bpy.ops.render.render()
        bpy_images.save_render_result(scene, file_path, "OPEN_EXR_MULTILAYER", "32")
    return bpy_images.object_index_ids(file_path, bpy_images.read_multilayer(file_path), view_layer)


def instance_id_nodes(lut, keep_own_geometry, hide_others):
    """Geometry nodes group that stores the id of every top-level instance, read from the
    point attribute of the lut object at the instance index, as an instance attribute.
    With hide_others, instances with no id are deleted, and so is the parent's own
    geometry unless keep_own_geometry."""
    tree = bpy_instances.node_group("blendmentation ids")
    nodes, link = tree.nodes, tree.links.new
    group_input, group_output = nodes.new("NodeGroupInput"), nodes.new("NodeGroupOutput")
    info = nodes.new("GeometryNodeObjectInfo")
    info.inputs["Object"].default_value = lut
    attribute = nodes.new("GeometryNodeInputNamedAttribute")
    attribute.data_type = "INT"
    attribute.inputs["Name"].default_value = ID_ATTRIBUTE
    sample = nodes.new("GeometryNodeSampleIndex")
    sample.data_type = "INT"
    sample.domain = "POINT"
    # instances past the lut get its last value, -1
    sample.clamp = True
    link(info.outputs["Geometry"], sample.inputs["Geometry"])
    link(attribute.outputs["Attribute"], sample.inputs["Value"])
    link(nodes.new("GeometryNodeInputIndex").outputs[0], sample.inputs["Index"])
    store = nodes.new("GeometryNodeStoreNamedAttribute")
    store.data_type = "INT"
    store.domain = "INSTANCE"
    store.inputs["Name"].default_value = ID_ATTRIBUTE
    link(sample.outputs["Value"], store.inputs["Value"])
    geometry = group_input.outputs[0]
    if hide_others and not keep_own_geometry:
        separate = nodes.new("GeometryNodeSeparateComponents")
        link(geometry, separate.inputs[0])
        geometry = separate.outputs["Instances"]
    link(geometry, store.inputs["Geometry"])
    geometry = store.outputs["Geometry"]
    if hide_others:
        compare = nodes.new("FunctionNodeCompare")
        compare.data_type = "FLOAT"
        compare.operation = "LESS_THAN"
        link(sample.outputs["Value"], compare.inputs[0])
        compare.inputs[1].default_value = 0.5
        delete = nodes.new("GeometryNodeDeleteGeometry")
        delete.domain = "INSTANCE"
        link(geometry, delete.inputs["Geometry"])
        link(compare.outputs[0], delete.inputs["Selection"])
        geometry = delete.outputs["Geometry"]
    link(geometry, group_output.inputs[0])
    return tree


def instance_id_material(hide_others):
    """Emission material showing the id: the instance attribute of an instance that has one
    (-1, an instance of no group, is 0), otherwise the object's custom property. With
    hide_others, what has no id is transparent."""
    material = bpy.data.materials.new("blendmentation ids")
    # blender 5 materials always have nodes, use_nodes is deprecated
    if material.node_tree is None:
        material.use_nodes = True
    nodes, link = material.node_tree.nodes, material.node_tree.links.new
    nodes.clear()

    def math(operation, a, b=None):
        node = nodes.new("ShaderNodeMath")
        node.operation = operation
        for socket, value in zip(node.inputs, (a, b)):
            if isinstance(value, (int, float)):
                socket.default_value = value
            elif value is not None:
                link(value, socket)
        return node.outputs[0]

    attributes = {}
    for kind in ("INSTANCER", "OBJECT"):
        node = nodes.new("ShaderNodeAttribute")
        node.attribute_type = kind
        node.attribute_name = ID_ATTRIBUTE
        attributes[kind] = node.outputs["Fac"]
    instance_id, object_id = attributes["INSTANCER"], attributes["OBJECT"]
    is_instance = math("GREATER_THAN", math("ABSOLUTE", instance_id), 0.5)
    # object_id + is_instance * (max(instance_id, 0) - object_id)
    difference = math("SUBTRACT", math("MAXIMUM", instance_id, 0.0), object_id)
    group_id = math("ADD", object_id, math("MULTIPLY", is_instance, difference))
    emission = nodes.new("ShaderNodeEmission")
    link(group_id, emission.inputs["Color"])
    surface = emission.outputs[0]
    if hide_others:
        mix = nodes.new("ShaderNodeMixShader")
        link(math("GREATER_THAN", group_id, 0.5), mix.inputs[0])
        link(nodes.new("ShaderNodeBsdfTransparent").outputs[0], mix.inputs[1])
        link(surface, mix.inputs[2])
        surface = mix.outputs[0]
    output = nodes.new("ShaderNodeOutputMaterial")
    link(surface, output.inputs["Surface"])
    return material


def render_instance_ids(scene, view_layer, groups, file_path, hide_others=False):
    """Renders the group ids of groups with instances (`InstanceMember`s) with Cycles at
    1 sample and an emission material override, whatever the engine. A temporary geometry
    nodes modifier on each parent stores the ids of its top-level instances as an instance
    attribute; objects get theirs as a custom property. Everything is restored afterwards.
    Without hide_others, all other objects and instances are 0 and still occlude;
    with it, they are hidden.

    Returns:
        np.ndarray: (height, width) array of group ids, 0 = background or other objects
    """
    objects, parents, sources = {}, {}, set()
    for group_id, group in enumerate(groups, start=1):
        for obj in group:
            if isinstance(obj, bpy_instances.InstanceMember):
                parents.setdefault(obj.parent, {})[obj.top] = group_id
                sources.add(obj.source)
            else:
                objects[obj] = group_id
    with bpy_undo.temporary() as changes:
        if hide_others:
            # hiding a source can hide its instances too, the material hides what has no id
            hide_in_render(changes, set(objects) | set(parents) | sources)
        for obj, group_id in objects.items():
            changes.set_item(obj, ID_ATTRIBUTE, float(group_id))
        for parent, tops in parents.items():
            lut_ids = [-1] * (max(tops) + 2)
            for top, group_id in tops.items():
                lut_ids[top] = group_id
            mesh = bpy.data.meshes.new("blendmentation ids")
            changes.add(partial(bpy.data.meshes.remove, mesh))
            mesh.vertices.add(len(lut_ids))
            mesh.attributes.new(ID_ATTRIBUTE, "INT", "POINT").data.foreach_set("value", lut_ids)
            lut = bpy.data.objects.new("blendmentation ids", mesh)
            changes.add(partial(bpy.data.objects.remove, lut))
            tree = instance_id_nodes(lut, parent in objects, hide_others)
            changes.add(partial(bpy.data.node_groups.remove, tree))
            modifier = parent.modifiers.new("blendmentation ids", "NODES")
            changes.add(partial(parent.modifiers.remove, modifier))
            modifier.node_group = tree
        material = instance_id_material(hide_others)
        changes.add(partial(bpy.data.materials.remove, material))

        changes.set_many(scene.render, {"engine": "CYCLES", "film_transparent": True, "use_compositing": False,
                                        "use_sequencer": False, "use_motion_blur": False})
        changes.set_many(scene.cycles, {
            "samples": 1, "use_denoising": False, "use_adaptive_sampling": False,
            # one sample near the pixel center, the ids are not blended
            "pixel_filter_type": "BOX", "filter_width": 0.01,
            "film_exposure": 1.0, "sample_clamp_direct": 0.0, "sample_clamp_indirect": 0.0,
            "transparent_max_bounces": 1024,
        })
        changes.set(view_layer, "material_override", material)
        if hasattr(view_layer, "samples"):
            changes.set(view_layer, "samples", 0)
        changes.set_many(scene.view_settings, STANDARD_VIEW)

        bpy.ops.render.render()
        bpy_images.save_render_result(scene, file_path, "OPEN_EXR", "32", "RGBA")
    return bpy_images.read_ids(file_path)
