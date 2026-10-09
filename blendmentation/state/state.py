"""Saves the scene state, so it can be restored after augmenting."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Union

from ..augmentations.augmentations import flatten
from . import bpy_states as bpy_s

if TYPE_CHECKING:
    from bpy.types import ID  # pyright: ignore[reportMissingModuleSource]  (bpy.types only exists at runtime)


class State:
    """Saves the scene when created, so it can be restored after every datapoint.

    It saves, for each object:

    - the transforms;
    - the render visibility (`hide_render`);
    - the whole node tree of its materials: every node's settings (e.g. a Math
      node's operation, muting), its input and output values (Value and RGB nodes
      keep theirs on the output), color ramp stops, curve points, images, and the
      nodes inside node groups;
    - the inputs of its geometry nodes modifiers (numbers, booleans, vectors, colors,
      rotations and menus), e.g. those `Modifier` sets;
    - for cameras, the lens and depth of field (`use_dof`, `focus_object`,
      `focus_distance`, `aperture_fstop`).

    `objects` can also hold a World, a node group (e.g. a geometry nodes group whose
    seeds `Seed` changes), or another datablock with a node tree, such as a material or
    light data. For those it saves the whole node tree, and relative
    paths in `fields` resolve on them too.

    It also saves the value at every data path in `fields`. A data path augmentation
    that changes something outside of these (a light's energy, a shape key) is only
    restored when it is passed in `fields`.

    Args:
        objects: objects to save, and worlds, node groups or other datablocks with a
            node tree.
        fields: `Number`, `Vector`, `Boolean` and `Menu` augmentations, or data path
            strings. Absolute paths are saved once, relative paths for every object they
            exist on. Augmentations inside a `OneOf` are saved too, and other entries are
            ignored, so a whole `Compose.augmentations` list can be passed.

    Example:
        ```python
        initial = state.State([car_1, car_2, lamp, camera],
                              fields=objects_aug.augmentations + lamp_aug.augmentations)
        for _ in range(1000):
            objects_aug([car_1, car_2])
            generator()
            initial.restore()

        world = bpy.context.scene.world
        initial = state.State([car_1, world])   # also the world's node values
        ```
    """

    def __init__(self, objects: Sequence[ID], fields: Sequence[Union[Any, str]] = ()):
        self.state_dict = {}
        self.objects = objects
        for object in objects:
            self.state_dict[bpy_s.state_key(object)] = bpy_s.create_state_list(object)
        # other augmentations are covered by the object state, so a whole Compose list can be passed
        data_paths = [field if isinstance(field, str) else field.data_path for field in flatten(fields)
                      if isinstance(field, str) or hasattr(field, "data_path")]
        self.field_state = bpy_s.create_field_state(data_paths, objects)

    def restore(self) -> None:
        """Puts the saved transforms and values back."""

        for object in self.objects:
            bpy_s.load_from_state_dict(object, self.state_dict)
        bpy_s.load_field_state(self.field_state)

    def clear(self) -> None:
        """Forgets the saved state."""

        self.state_dict = {}
        self.field_state = []
