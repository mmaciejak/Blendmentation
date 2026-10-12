"""The Blender add-on: operators that copy a Node template of the active node (node
editor) or a shape keys Chain template of the active object (3D viewport) to the
clipboard, in a Blendmentation tab in the sidebar of both. Only imported by register(),
so importing the package doesn't register anything."""

import bpy

from .augmentations import augmentations

DEFAULT_PREFIX = "augmentations."


class BlendmentationPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__ or "blendmentation"

    prefix: bpy.props.StringProperty(  # type: ignore[valid-type]
        name="Prefix",
        description="Written before Node, Input, Chain and Number in the templates, e.g. aug. for "
                    "'from blendmentation.augmentations import augmentations as aug'",
        default=DEFAULT_PREFIX,
    )

    def draw(self, context):
        self.layout.prop(self, "prefix")


def template_prefix(context):
    """The prefix set in the add-on preferences, the default when the add-on isn't
    enabled in Blender (e.g. registered by hand in module mode)."""
    addon = context.preferences.addons.get(__package__)
    return addon.preferences.prefix if addon else DEFAULT_PREFIX


def copy_template(node, window_manager, prefix=DEFAULT_PREFIX):
    """Copies the Node template of the node to the clipboard and returns it."""
    text = augmentations.Node.template(node, prefix=prefix)
    window_manager.clipboard = text
    return text


class NODE_OT_blendmentation_copy_template(bpy.types.Operator):
    """Copy an augmentations.Node(...) with every input of the active node and its range, to paste into a script"""

    bl_idname = "node.blendmentation_copy_template"
    bl_label = "Copy Blendmentation Template"

    @classmethod
    def poll(cls, context):
        space = context.space_data
        return space is not None and space.type == "NODE_EDITOR" and context.active_node is not None

    def execute(self, context):  # pyright: ignore[reportIncompatibleMethodOverride]  (the stubs want literal sets)
        node = context.active_node
        if node is None:
            return {"CANCELLED"}
        try:
            copy_template(node, context.window_manager, template_prefix(context))
        except (TypeError, ValueError) as error:
            self.report({"WARNING"}, str(error))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Copied the Node template of '{node.name}'")
        return {"FINISHED"}


def copy_shape_keys_template(obj, window_manager, prefix=DEFAULT_PREFIX):
    """Copies the shape keys Chain template of the object to the clipboard and returns it."""
    text = augmentations.Chain.shape_keys_template(obj, prefix=prefix)
    window_manager.clipboard = text
    return text


class OBJECT_OT_blendmentation_copy_shape_keys_template(bpy.types.Operator):
    """Copy an augmentations.Chain(...) with a Number for every shape key of the active object, to paste into a script"""

    bl_idname = "object.blendmentation_copy_shape_keys_template"
    bl_label = "Copy Shape Keys Template"

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and getattr(getattr(obj, "data", None), "shape_keys", None) is not None

    def execute(self, context):  # pyright: ignore[reportIncompatibleMethodOverride]  (the stubs want literal sets)
        obj = context.active_object
        if obj is None:
            return {"CANCELLED"}
        try:
            copy_shape_keys_template(obj, context.window_manager, template_prefix(context))
        except ValueError as error:
            self.report({"WARNING"}, str(error))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Copied the shape keys template of '{obj.name}'")
        return {"FINISHED"}


class NODE_PT_blendmentation(bpy.types.Panel):
    bl_label = "Blendmentation"
    bl_space_type = "NODE_EDITOR"
    bl_region_type = "UI"
    bl_category = "Blendmentation"

    def draw(self, context):
        self.layout.operator(NODE_OT_blendmentation_copy_template.bl_idname)  # pyright: ignore[reportOptionalMemberAccess]  (a drawn panel has a layout)


class VIEW3D_PT_blendmentation(bpy.types.Panel):
    bl_label = "Blendmentation"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Blendmentation"

    def draw(self, context):
        self.layout.operator(OBJECT_OT_blendmentation_copy_shape_keys_template.bl_idname)  # pyright: ignore[reportOptionalMemberAccess]  (a drawn panel has a layout)


def draw_menu(self, context):
    self.layout.separator()
    self.layout.operator(NODE_OT_blendmentation_copy_template.bl_idname)


CLASSES = (BlendmentationPreferences, NODE_OT_blendmentation_copy_template,
           OBJECT_OT_blendmentation_copy_shape_keys_template, NODE_PT_blendmentation, VIEW3D_PT_blendmentation)
# the Node menu in the header makes it searchable with F3, the context menu is a right click
MENUS = ("NODE_MT_node", "NODE_MT_context_menu")


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    for menu in MENUS:
        getattr(bpy.types, menu).append(draw_menu)


def unregister():
    for menu in MENUS:
        getattr(bpy.types, menu).remove(draw_menu)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
