"""Records the values the augmentations change, so state.restoring() can set them back.

Every write to Blender made by an augmentation goes through set_attr, set_item or one of
the record_* functions. While a journal is open (state.restoring()), the first change of
each value records how to set it back; undo() does that in reverse order. Without an open
journal the values are only set.
"""

from array import array

import bpy

# the open journals, innermost last; a change is recorded in the innermost one only, an
# inner journal sets its values back before the outer one ends
journals = []

# transforms in the order they are set back: the rotation mode first, so the rotation
# values are set in the right mode
TRANSFORM_PROPERTIES = (
    "rotation_mode",
    "location",
    "rotation_euler",
    "rotation_quaternion",
    "rotation_axis_angle",
    "scale",
)


class Journal:
    """The values changed since it was opened, and how to set each back."""

    def __init__(self):
        self.keys = set()
        self.restores = []

    def undo(self):
        """Sets every recorded value back, the last change first. A value whose owner
        was removed in the meantime is skipped. Then the view layer is updated, so
        matrix_world and the evaluated meshes are back too."""
        if not self.restores:
            return
        for restore in reversed(self.restores):
            try:
                restore()
            except ReferenceError:
                continue
        self.keys.clear()
        self.restores.clear()
        if bpy.context.view_layer is not None:
            bpy.context.view_layer.update()


def open_journal():
    journal = Journal()
    journals.append(journal)
    return journal


def close_journal(journal):
    """Sets the journal's values back and closes it."""
    try:
        journal.undo()
    finally:
        journals.remove(journal)


def to_plain(value):
    """Copies blender arrays to tuples so the saved value doesn't follow later changes."""
    if hasattr(value, "__len__") and not isinstance(value, str):
        return tuple(value)
    return value


def pointer(owner):
    return owner.as_pointer() if hasattr(owner, "as_pointer") else id(owner)


def tag(owner):
    """Tags the datablock that owns a struct for update, so the depsgraph sees the change."""
    id_data = getattr(owner, "id_data", None)
    if id_data is not None:
        id_data.update_tag()


def record(key, restore):
    """Records how to set a value back, on its first change in the innermost journal."""
    if not journals:
        return
    journal = journals[-1]
    if key not in journal.keys:
        journal.keys.add(key)
        journal.restores.append(restore)


def set_attr(owner, name, value):
    """Sets owner.name, recording the old value."""
    if journals:
        old = to_plain(getattr(owner, name))

        def restore():
            setattr(owner, name, old)
            tag(owner)

        record((pointer(owner), "attr", name), restore)
    setattr(owner, name, value)


def set_item(owner, key, value):
    """Sets owner[key] (an id property), recording the old value."""
    if journals:
        old = to_plain(owner[key])

        def restore():
            owner[key] = old
            tag(owner)

        record((pointer(owner), "item", key), restore)
    owner[key] = value


def record_transforms(obj):
    """Records the object's transforms before they change in place or through matrix_world."""
    if not journals:
        return
    old = [(name, to_plain(getattr(obj, name))) for name in TRANSFORM_PROPERTIES]

    def restore():
        for name, value in old:
            setattr(obj, name, value)
        obj.update_tag()

    record((pointer(obj), "transforms"), restore)


def record_material_indices(mesh):
    """Records the material slot of every face of the mesh before it changes."""
    if not journals:
        return
    old = array("i", bytes(4 * len(mesh.polygons)))
    mesh.polygons.foreach_get("material_index", old)

    def restore():
        if len(mesh.polygons) == len(old):
            mesh.polygons.foreach_set("material_index", old)
            mesh.update()

    record((pointer(mesh), "material_index"), restore)
