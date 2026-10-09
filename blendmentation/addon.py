"""The Blender add-on: an operator in the node editor that copies a Node template of the
active node to the clipboard. Only imported by register(), so importing the package
doesn't register anything."""

import bpy

from .augmentations import augmentations

DEFAULT_PREFIX = "augmentations."


class BlendmentationPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__ or "blendmentation"

    prefix: bpy.props.StringProperty(  # type: ignore[valid-type]
        name="Prefix",
        description="Written before Node and Input in the template, e.g. aug. for "
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


def draw_menu(self, context):
    self.layout.separator()
    self.layout.operator(NODE_OT_blendmentation_copy_template.bl_idname)


CLASSES = (BlendmentationPreferences, NODE_OT_blendmentation_copy_template)
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
