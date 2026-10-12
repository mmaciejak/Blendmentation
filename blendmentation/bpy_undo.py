"""Records the values the augmentations change, so state.restoring() can set them back.

Every write to Blender made by an augmentation goes through set_attr, set_item or one of
the record_* functions. While a journal is open (state.restoring()), the first change of
each value records how to set it back; undo() does that in reverse order. Without an open
journal the values are only set.

Temporary records settings changed for a while by Blendmentation itself (render settings
of an id render), which are set back right after, whatever journal is open.
"""

from array import array
from contextlib import contextmanager

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


class Temporary:
    """Settings changed for a while, outside the journals: e.g. the render settings of an
    id render, or a datapoint's. restore() sets them back, the last change first."""

    def __init__(self):
        self.restores = []

    def save(self, owner, *names):
        """Records the values of owner's names, set back in this order by one restore."""
        old = [(name, to_plain(getattr(owner, name))) for name in names]

        def restore():
            for name, value in old:
                setattr(owner, name, value)

        self.restores.append(restore)

    def set(self, owner, name, value):
        """Sets owner.name, recording the old value."""
        self.save(owner, name)
        setattr(owner, name, value)

    def set_many(self, owner, values):
        """Sets owner's attributes from a {name: value} dict, recording the old values."""
        for name, value in values.items():
            self.set(owner, name, value)

    def set_item(self, owner, key, value):
        """Sets owner[key] (an id property), recording the old value or that it had none."""
        old = owner.get(key)

        def restore():
            if old is None:
                del owner[key]
            else:
                owner[key] = old

        self.restores.append(restore)
        owner[key] = value

    def add(self, restore):
        """Records a function to call on restore, e.g. to remove a datablock created for a while."""
        self.restores.append(restore)

    def restore(self):
        while self.restores:
            self.restores.pop()()


@contextmanager
def temporary():
    """A Temporary that is restored when the block ends, also on an exception."""
    changes = Temporary()
    try:
        yield changes
    finally:
        changes.restore()


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
