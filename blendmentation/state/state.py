"""Puts the scene back after every datapoint.

Augmentations change the scene in place. Run them, and the generating step, inside
`with state.restoring():`, and every value they changed is set back when the block
ends, also when something in it raises. Nothing has to be listed or saved first: each
augmentation records the values it changes, including those of augmentations inside a
`OneOf`, `Chain`, `Node` or `Modifier`.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from .. import bpy_undo


@contextmanager
def restoring() -> Iterator[None]:
    """Sets every value changed inside the block back when it ends.

    The augmentations record each value the first time they change it, and the block's
    end sets them back, the last change first. That covers transforms, render
    visibility, node and modifier inputs, material slots, camera settings and any
    value changed by a data path. Changes made by your own code are not recorded:
    make them with [`set`][blendmentation.state.state.set] to have them set back too.
    Blocks can be nested; an inner block sets its changes back when it ends.

    Augmentations called outside a `restoring()` block change the scene for good.

    Example:
        ```python
        for _ in range(100):
            with state.restoring():
                objects_aug([car_1, car_2])
                lamp_aug([lamp])
                generator()
        ```
    """
    journal = bpy_undo.open_journal()
    try:
        yield
    finally:
        bpy_undo.close_journal(journal)


def set(owner: Any, name: str, value: Any) -> None:
    """Sets `owner.name` to `value`, and inside a `restoring()` block sets it back when
    the block ends.

    For changes your own code makes between augmentations, e.g. a value that depends on
    what an augmentation did.

    Args:
        owner: the Blender struct, e.g. `lamp.data`.
        name: the attribute to set.
        value: the new value.

    Example:
        ```python
        with state.restoring():
            objects_aug([car])
            if car.location.z > 1:
                state.set(lamp.data, "energy", 2000)
            generator()
        ```
    """
    bpy_undo.set_attr(owner, name, value)
    bpy_undo.tag(owner)
