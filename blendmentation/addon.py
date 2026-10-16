"""The Blender add-on: operators that copy a Node template of the active node (node
editor), or a shape keys Chain template of the active object or a Modifier template of
its active modifier (3D viewport) to the clipboard, in a Blendmentation tab in the
sidebar of both. Only imported by register(), so importing the package doesn't register
anything."""

import bpy

from .augmentations import augmentations

DEFAULT_PREFIX = "augmentations."


class BlendmentationPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__ or "blendmentation"

    prefix: bpy.props.StringProperty(  # type: ignore[valid-type]
        name="Prefix",
        description="Written before Node, Modifier, Chain, Number, Vector, Boolean and Menu in the templates, "
                    "e.g. aug. for 'from blendmentation import augmentations as aug'",
        default=DEFAULT_PREFIX,
    )

    def draw(self, context):
        self.layout.prop(self, "prefix")


def template_prefix(context):
    """The prefix set in the add-on preferences, the default when the add-on isn't
    enabled in Blender (e.g. registered by hand in module mode)."""
    addon = context.preferences.addons.get(__package__)
    return addon.preferences.prefix if addon else DEFAULT_PREFIX


def copy_template(node, window_manager, prefix=DEFAULT_PREFIX, obj=None, relative=False):
    """Copies the Node template of the node (its path relative to obj, or absolute) to
    the clipboard and returns it."""
    text = augmentations.Node.template(node, obj, prefix=prefix, relative=relative)
    window_manager.clipboard = text
    return text


class NODE_OT_blendmentation_copy_template(bpy.types.Operator):
    """Copy an augmentations.Node(...) with every input of the active node and its range, to paste into a script"""

    bl_idname = "node.blendmentation_copy_template"
    bl_label = "Copy Blendmentation Template"

    relative: bpy.props.BoolProperty(  # type: ignore[valid-type]
        name="Relative",
        description="Path relative to the active object (its material, data or geometry nodes modifier; "
                    "a world's node: to the world), instead of absolute",
        default=False,
    )

    @classmethod
    def poll(cls, context):
        space = context.space_data
        return space is not None and space.type == "NODE_EDITOR" and context.active_node is not None

    def execute(self, context):  # pyright: ignore[reportIncompatibleMethodOverride]  (the stubs want literal sets)
        node = context.active_node
        if node is None:
            return {"CANCELLED"}
        try:
            copy_template(node, context.window_manager, template_prefix(context), context.active_object,
                          self.relative)
        except (TypeError, ValueError) as error:
            self.report({"WARNING"}, str(error))
            return {"CANCELLED"}
        kind = "relative" if self.relative else "absolute"
        self.report({"INFO"}, f"Copied the Node template of '{node.name}', {kind}")
        return {"FINISHED"}


def copy_shape_keys_template(obj, window_manager, prefix=DEFAULT_PREFIX, relative=True):
    """Copies the shape keys Chain template of the object to the clipboard and returns it."""
    text = augmentations.Chain.shape_keys_template(obj, prefix=prefix, relative=relative)
    window_manager.clipboard = text
    return text


class OBJECT_OT_blendmentation_copy_shape_keys_template(bpy.types.Operator):
    """Copy an augmentations.Chain(...) with a Number for every shape key of the active object, to paste into a
    script"""

    bl_idname = "object.blendmentation_copy_shape_keys_template"
    bl_label = "Copy Shape Keys Template"

    relative: bpy.props.BoolProperty(  # type: ignore[valid-type]
        name="Relative",
        description="Paths relative to the object the Chain is called with, instead of absolute "
                    "paths through the active object",
        default=True,
    )

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and getattr(getattr(obj, "data", None), "shape_keys", None) is not None

    def execute(self, context):  # pyright: ignore[reportIncompatibleMethodOverride]  (the stubs want literal sets)
        obj = context.active_object
        if obj is None:
            return {"CANCELLED"}
        try:
            copy_shape_keys_template(obj, context.window_manager, template_prefix(context), self.relative)
        except ValueError as error:
            self.report({"WARNING"}, str(error))
            return {"CANCELLED"}
        kind = "relative" if self.relative else "absolute"
        self.report({"INFO"}, f"Copied the shape keys template of '{obj.name}', {kind}")
        return {"FINISHED"}


def draw_buttons(layout, label, idname):
    """A label and a Relative and an Absolute button for a template operator."""
    layout.label(text=label)
    row = layout.row(align=True)
    row.operator(idname, text="Relative").relative = True
    row.operator(idname, text="Absolute").relative = False


def copy_modifier_template(modifier, window_manager, prefix=DEFAULT_PREFIX, relative=True):
    """Copies the Modifier template of the modifier to the clipboard and returns it."""
    text = augmentations.Modifier.template(modifier, prefix=prefix, relative=relative)
    window_manager.clipboard = text
    return text


def active_modifier(context):
    """The active modifier of the active object, the one highlighted in the Modifier
    Properties."""
    obj = context.active_object
    return obj.modifiers.active if obj is not None else None


class OBJECT_OT_blendmentation_copy_modifier_template(bpy.types.Operator):
    """Copy an augmentations.Modifier(...) with every input or setting of the active modifier and its range, to paste
    into a script"""

    bl_idname = "object.blendmentation_copy_modifier_template"
    bl_label = "Copy Modifier Template"

    relative: bpy.props.BoolProperty(  # type: ignore[valid-type]
        name="Relative",
        description="Path relative to the object the Modifier is called with, instead of an absolute "
                    "path through the active object",
        default=True,
    )

    @classmethod
    def poll(cls, context):
        return active_modifier(context) is not None

    def execute(self, context):  # pyright: ignore[reportIncompatibleMethodOverride]  (the stubs want literal sets)
        modifier = active_modifier(context)
        if modifier is None:
            return {"CANCELLED"}
        try:
            copy_modifier_template(modifier, context.window_manager, template_prefix(context), self.relative)
        except (TypeError, ValueError) as error:
            self.report({"WARNING"}, str(error))
            return {"CANCELLED"}
        kind = "relative" if self.relative else "absolute"
        self.report({"INFO"}, f"Copied the Modifier template of '{modifier.name}', {kind}")
        return {"FINISHED"}


class NODE_PT_blendmentation(bpy.types.Panel):
    bl_label = "Blendmentation"
    bl_space_type = "NODE_EDITOR"
    bl_region_type = "UI"
    bl_category = "Blendmentation"

    def draw(self, context):
        draw_buttons(self.layout, "Copy Node Template", NODE_OT_blendmentation_copy_template.bl_idname)


class VIEW3D_PT_blendmentation(bpy.types.Panel):
    bl_label = "Blendmentation"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Blendmentation"

    def draw(self, context):
        draw_buttons(self.layout, "Copy Shape Keys Template",
                     OBJECT_OT_blendmentation_copy_shape_keys_template.bl_idname)
        draw_buttons(self.layout, "Copy Modifier Template (active modifier)",
                     OBJECT_OT_blendmentation_copy_modifier_template.bl_idname)


def draw_menu(self, context):
    self.layout.separator()
    idname = NODE_OT_blendmentation_copy_template.bl_idname
    self.layout.operator(idname, text="Copy Blendmentation Template (Relative)").relative = True
    self.layout.operator(idname, text="Copy Blendmentation Template (Absolute)").relative = False


CLASSES = (BlendmentationPreferences, NODE_OT_blendmentation_copy_template,
           OBJECT_OT_blendmentation_copy_shape_keys_template, OBJECT_OT_blendmentation_copy_modifier_template,
           NODE_PT_blendmentation, VIEW3D_PT_blendmentation)
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
