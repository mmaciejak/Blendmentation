"""Randomize objects, materials and any other value in the scene, in place.

`Compose` applies a list of augmentations to every object it is called with. Each
augmentation takes `p`, the probability that it runs, drawn per object; a call can
override it, `aug(obj, p=0.5)`. After a
call, `applied` says whether it ran and `actual` (or `actual_x/y/z`) holds the values
it set. `Number`, `Vector`, `Boolean`, `Menu`, `MaterialSlot`, `Visibility`,
`FocalLength` and `DepthOfField` also take `otherwise`, a value to set when they don't run. Each call overwrites them, so after a `Compose` call they describe only the
last object; `results` holds the values for every object, by name. Augment inside
`with state.restoring():`, which sets everything back when the datapoint is done.
`Node` sets the inputs of a node (e.g. the group node of a smart material or of a
geometry nodes setup), each with its own range, `p` and `otherwise`, given as an `Input`;
`Modifier` does the same for the inputs of a geometry nodes modifier, by name.
`Node.template` writes a `Node` with every input of a node and its range, to paste and
edit; the Blender add-on copies it for the selected node.
`MaterialSlot` gives an object one of the materials in its own slots.
`Visibility` shows or hides objects in the render, and the labels follow it.
`KeepAbove`, placed after the transforms, lifts objects out of a floor or terrain;
`PlaceOn` also lowers them, so they rest on it. `Seed` gives every seed in a node
group (e.g. a geometry nodes scatter) its own random value. `PlaceOnCurve` moves a
camera (or any object) to a random point on a curve, optionally facing along it. `OneOf` applies one
augmentation from a list, picked at random by weight, and `Chain` applies a list to one
object as one augmentation, e.g. to pick between whole sequences in a `OneOf`.

`Number`, `Vector`, `Boolean` and `Menu` change any value by its data path. A path
starting with `bpy.` is absolute: right click a value in Blender > Copy Full Data
Path. Any other path is relative to the augmented object, e.g. `data.energy`.
Geometry nodes inputs moved in Blender 5, so their path depends on the version:
`modifiers["GeometryNodes"]["Socket_2"]` in 4.x,
`modifiers["GeometryNodes"].properties.inputs.Socket_2.value` in 5.x. `Modifier`
finds them by name in every version.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any, Optional, Union

from . import bpy_augmentations as bpy_a

if TYPE_CHECKING:
    from bpy.types import Node as BpyNode, NodeTree, Object  # pyright: ignore[reportMissingModuleSource]  (bpy.types only exists at runtime)

#: a number v samples from (-v, v), a pair (low, high) from (low, high), a triple
#: (low, high, step) picks one of low, low + step, ... up to high
Offset = Union[float, tuple[float, float], tuple[float, float, float]]
#: a (min, max) range or an exact number
RangeOrValue = Union[float, tuple[float, float]]
#: an object, objects (the center of their bounding boxes), or a point (x, y, z)
Target = Union["Object", Sequence["Object"], tuple[float, float, float]]
#: a number for all components, or one value per component (None keeps it)
Bound = Union[float, Sequence[Optional[float]]]


def check_p(p: float) -> float:
    if not 0 <= p <= 1:
        raise ValueError("p must be between 0 and 1")
    return p


def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def check_otherwise(otherwise: Any, valid: bool, expected: str) -> Any:
    if otherwise is not None and not valid:
        raise ValueError(f"otherwise must be {expected} or None, got {otherwise!r}")
    return otherwise


def check_offset(offset: Offset, axis: str) -> Offset:
    if is_number(offset):
        return offset
    if not isinstance(offset, Sequence) or len(offset) not in (2, 3) or not all(is_number(v) for v in offset):
        raise ValueError(f"{axis} must be a number, (low, high) or (low, high, step), got {offset!r}")
    if offset[0] > offset[1]:
        raise ValueError(f"{axis}: low must not be above high, got {offset!r}")
    if len(offset) == 3 and offset[2] <= 0:
        raise ValueError(f"{axis}: step must be above 0, got {offset!r}")
    return offset


def check_range(value_range: Any, name: str) -> Any:
    """A (low, high) or (low, high, step) range; check_offset also takes a single number."""
    if is_number(value_range):
        raise ValueError(f"{name} must be (low, high) or (low, high, step), got {value_range!r}")
    return check_offset(value_range, name)


def check_fraction(value: Any, name: str) -> Any:
    """A fraction 0-1, or a (low, high) range of them."""
    values = [value] if is_number(value) else value
    if (not isinstance(values, Sequence) or len(values) not in (1, 2) or not all(is_number(v) for v in values)
            or not all(0 <= v <= 1 for v in values) or values[0] > values[-1]):
        raise ValueError(f"{name} must be a number or (low, high) between 0 and 1, got {value!r}")
    return value


def happens(p: float) -> bool:
    """True with probability p. p = 1 draws no random number, so seeded runs don't change."""
    return p >= 1 or random.random() < p


def flatten(augmentations: Sequence[Any]) -> list[Any]:
    """Each item and, after it, the items in its `augmentations` (a `OneOf`, a `Compose`), depth first."""
    flat = []
    for augmentation in augmentations:
        flat.append(augmentation)
        flat.extend(flatten(getattr(augmentation, "augmentations", ())))
    return flat


class Compose:
    """Applies a list of augmentations to every object it is called with, in place.

    Each augmentation runs once per object, in list order: all of them on the first
    object, then all of them on the next one. Every augmentation draws its own `p` for
    each object, so with `Visibility(p=0.5)` each object is shown or hidden
    independently. Call it inside `with state.restoring():`, so the scene is set back
    afterwards.

    Every call first clears the `results` of its augmentations, also of the ones
    nested in a `OneOf`, so afterwards they hold the values for the objects of this
    call only, also when the call is skipped by `p` or a `OneOf` didn't pick them
    (then they are empty). `actual` and `applied` on an augmentation describe
    only the last object, read `results` for all of them.

    Args:
        augmentations: augmentations to apply, each a callable taking one object.
        p: probability of applying the whole list, drawn once per call. A call can
            override it with its own `p`.

    Attributes:
        applied (bool | None): whether the last call applied the augmentations.

    Example:
        ```python
        objects_aug = augmentations.Compose([
            augmentations.Translation(x=0.5, y=0.5),
            augmentations.Rotation(z=180),
        ])
        objects_aug([car_1, car_2])
        objects_aug([car_1, car_2], p=0.5)  # this call: the whole list half of the time
        ```

        The values each augmentation set, per object:

        ```python
        visibility = augmentations.Visibility(p=0.5)
        boxes_aug = augmentations.Compose([visibility])
        boxes_aug([box_1, box_2, box_3])
        visibility.results   # e.g. {"box_1": False, "box_2": True, "box_3": False}
        visibility.actual    # False, only box_3
        ```
    """

    def __init__(self, augmentations: list[Callable[[Object], Any]], p: float = 1.0):
        self.augmentations = augmentations
        self.p = check_p(p)
        self.applied: Optional[bool] = None

    def __call__(self, blender_objects: Sequence[Object], p: Optional[float] = None) -> None:
        """Applies the augmentations to the objects.

        Args:
            blender_objects: objects to augment.
            p: probability of applying the whole list for this call only, instead of
                the `p` given at construction.

        Raises:
            ValueError: if `p` is not between 0 and 1.
        """
        for augmentation in flatten(self.augmentations):
            results = getattr(augmentation, "results", None)
            if isinstance(results, dict):
                results.clear()
        self.applied = happens(self.p if p is None else check_p(p))
        if not self.applied:
            return
        for blender_object in blender_objects:
            for augmentation in self.augmentations:
                augmentation(blender_object)


class Augmentation:
    """Base class of the augmentations.

    It runs with probability `p`, drawn on every call, so once per object in a
    `Compose`. A skipped augmentation leaves the object unchanged, unless it has an
    `otherwise` value: then it sets that. `Number`, `Vector`, `Boolean`, `Menu`,
    `MaterialSlot`, `Visibility`, `FocalLength` and `DepthOfField` take `otherwise`; it is None (keep
    the value) by default, except for `Boolean` (False) and `Visibility` (hidden).
    A `Compose` skipped by its own `p` runs none of its augmentations, so their
    `otherwise` isn't set either.

    Subclasses implement `apply(obj)` and store what they sampled in `actual`, and
    `set_otherwise(obj)` when they take `otherwise`.

    `applied` and `actual` (`actual_x/y/z` for `Translation`, `Rotation` and `Scale`)
    describe only the last call. A `Compose` calls each augmentation once per object,
    so after `Compose([aug])([a, b, c])` they hold what happened to `c`; the values for
    `a` and `b` were overwritten. `results` keeps them for every object: each call
    stores its `actual` under the object's name (`None` for a call without an object,
    e.g. an absolute data path), and `Translation`, `Rotation` and `Scale` store
    `(x, y, z)`. A skipped call stores what it set: its `otherwise` value, or `None`
    when it left the value unchanged. A `Compose` call clears
    `results` first; direct calls add to it, so clear it yourself
    (`aug.results.clear()`) when you call an augmentation in your own loop.

    A direct call can override `p` for that call only, like a `Compose` call:
    `rust(p=0.2)`.

    Args:
        p: probability of applying the augmentation, 0-1.

    Attributes:
        applied (bool | None): whether the last call applied it.
        actual (Any): the values set by the last call, None when it was skipped
            and left the value unchanged.
        results (dict[str | None, Any]): the values set for each object, by object
            name, since the last `Compose` call (or since it was cleared).

    Example:
        ```python
        visibility = augmentations.Visibility(p=0.5)
        augmentations.Compose([visibility])([box_1, box_2, box_3])
        visibility.results   # e.g. {"box_1": False, "box_2": True, "box_3": False}
        visibility.actual    # False: only the last object, box_3

        rotation = augmentations.Rotation(z=180)
        for car in [car_1, car_2]:
            rotation(car)
            print(car.name, rotation.actual_z)   # read actual after each call

        rotation(car_1, p=0.5)   # this call only: half of the time
        ```
    """

    def __init__(self, p: float = 1.0, otherwise: Any = None):
        self.p = check_p(p)
        self.otherwise = otherwise
        self.applied: Optional[bool] = None
        self.actual: Any = None
        self.results: dict[Optional[str], Any] = {}

    def __call__(self, obj: Optional[Object] = None, p: Optional[float] = None) -> None:
        """Applies the augmentation with probability `p`, and records it in `results`.

        Args:
            obj: object to augment, not needed for absolute data paths.
            p: probability of applying it for this call only, instead of the `p` given
                at construction.

        Raises:
            ValueError: if `p` is not between 0 and 1.
        """
        self.applied = happens(self.p if p is None else check_p(p))
        if self.applied:
            self.apply(obj)
        else:
            self.skip(obj)
        self.results[None if obj is None else obj.name] = self._result()

    def _result(self) -> Any:
        """The value stored in `results` for the last call."""
        return self.actual

    def apply(self, obj: Optional[Object]) -> None:
        raise NotImplementedError

    def skip(self, obj: Optional[Object]) -> None:
        self.actual = None if self.otherwise is None else self.set_otherwise(obj)

    def set_otherwise(self, obj: Optional[Object]) -> Any:
        """Sets the `otherwise` value, and returns what is stored in `actual`."""
        raise NotImplementedError


class OneOf(Augmentation):
    """Applies one augmentation from a list, picked at random by weight.

    On every call it picks one augmentation and calls it with the object. In a
    `Compose` it picks again for every object. The picked augmentation then draws its
    own `p`; the others don't run, so they don't set their `otherwise` either. A
    `OneOf` can contain another `OneOf`, and a
    [`Chain`][blendmentation.augmentations.augmentations.Chain] to pick a whole sequence.
    `Compose` clears the `results` of the augmentations inside it too.

    Args:
        augmentations: augmentations to pick from, each a callable taking one object.
        weights: relative probability of each augmentation. None = equal.
        p: probability of applying one of them.

    Attributes:
        actual (int | None): index of the augmentation picked by the last call, None
            when it was skipped.
        results (dict[str | None, int | None]): the index picked for each object.

    Raises:
        ValueError: `augmentations` is empty, the number of weights and augmentations
            differ, a weight is negative, or the weights sum to 0.

    Example:
        ```python
        # each part is either moved or turned, turned twice as often
        one_of = augmentations.OneOf(
            [augmentations.Translation(x=0.5, y=0.5), augmentations.Rotation(z=180)],
            weights=[1, 2],
        )
        augmentations.Compose([one_of])([car_1, car_2])
        one_of.results   # e.g. {"car_1": 1, "car_2": 0}
        ```
    """

    def __init__(self, augmentations: list[Callable[[Object], Any]],
                 weights: Optional[Sequence[float]] = None, p: float = 1.0):
        super().__init__(p)
        if not augmentations:
            raise ValueError("Give at least one augmentation")
        if weights is not None:
            if len(weights) != len(augmentations):
                raise ValueError("Give one weight per augmentation")
            if any(weight < 0 for weight in weights) or sum(weights) <= 0:
                raise ValueError(f"weights must not be negative and must not sum to 0, got {weights!r}")
        self.augmentations = augmentations
        self.weights = weights

    def apply(self, obj: Optional[Object]) -> None:
        """Args:
        obj (bpy.object) : object passed to the picked augmentation
        """
        self.actual = random.choices(range(len(self.augmentations)), weights=self.weights)[0]
        self.augmentations[self.actual](obj)


class Chain(Augmentation):
    """Applies a list of augmentations to one object, in order, as one augmentation.

    Where `Compose` is called with a list of objects, a `Chain` is called with one
    object like any other augmentation, so it can go where one augmentation goes: in a
    `OneOf`, to pick between whole sequences, or in a `Compose`. Each augmentation in it
    draws its own `p`; when the `Chain` itself doesn't run, none of them do, so they
    don't set their `otherwise` either. `Compose` clears the `results` of the
    augmentations inside it.

    Args:
        augmentations: augmentations to apply, each a callable taking one object.
        p: probability of applying the whole list.

    Attributes:
        actual (list | None): the `actual` of each augmentation after the last call
            (`None` for those without one), None when it was skipped.

    Raises:
        ValueError: `augmentations` is empty.

    Example:
        ```python
        # a dim, warm light, or just another brightness
        lamp_aug = augmentations.Compose([
            augmentations.OneOf([
                augmentations.Number("data.energy", value_range=(600, 1400)),
                augmentations.Chain([
                    augmentations.Number("data.energy", value_range=(300, 600)),
                    augmentations.Vector("data.color", value_range=((1, 0.7, 0.5), (1, 0.85, 0.7))),
                ]),
            ], weights=[2, 1]),
        ])
        ```

        A material picked from the object's slots, augmented only when it is picked:

        ```python
        material_aug = augmentations.Compose([
            augmentations.OneOf([
                augmentations.Chain([
                    augmentations.MaterialSlot(["Ferrous metal"]),
                    augmentations.Node('active_material.node_tree.nodes["Ferrous metal"]', {
                        "Rust strength": augmentations.Input((0.3, 1)),
                    }),
                ]),
                augmentations.Chain([
                    augmentations.MaterialSlot(["Plastic"]),
                    augmentations.Node('active_material.node_tree.nodes["Principled BSDF"]', {
                        "Roughness": augmentations.Input((0.1, 0.6)),
                    }),
                ]),
            ], weights=[3, 1]),
        ])
        ```
    """

    def __init__(self, augmentations: list[Callable[[Optional[Object]], Any]], p: float = 1.0):
        super().__init__(p)
        if not augmentations:
            raise ValueError("Give at least one augmentation")
        self.augmentations = augmentations

    def apply(self, obj: Optional[Object]) -> None:
        """Args:
        obj (bpy.object) : object passed to every augmentation
        """
        for augmentation in self.augmentations:
            augmentation(obj)
        self.actual = [getattr(augmentation, "actual", None) for augmentation in self.augmentations]


class AxisAugmentation(Augmentation):
    """Base class of `Translation`, `Rotation` and `Scale`, sampled per axis.

    Each axis is a number `v`, sampled from `(-v, v)`, a pair `(low, high)`, or a
    triple `(low, high, step)`, which picks one of `low`, `low + step`, ... up to
    `high`, each equally likely.

    Args:
        x: offset on the X axis.
        y: offset on the Y axis.
        z: offset on the Z axis.
        p: probability of applying the augmentation.

    Raises:
        ValueError: an axis is not a number, a pair or a triple, low is above high, or
            the step is not above 0.

    Attributes:
        actual_x (float | None): value sampled for X in the last call, None when skipped.
        actual_y (float | None): value sampled for Y.
        actual_z (float | None): value sampled for Z.
        results (dict[str | None, tuple[float, float, float] | None]): `(x, y, z)` for
            each object, by name, None when skipped.
    """

    def __init__(self, x: Offset = 0, y: Offset = 0, z: Offset = 0, p: float = 1.0):
        super().__init__(p)
        self.x = check_offset(x, "x")
        self.y = check_offset(y, "y")
        self.z = check_offset(z, "z")
        self.actual_x: Optional[float] = None
        self.actual_y: Optional[float] = None
        self.actual_z: Optional[float] = None

    def skip(self, obj: Optional[Object]) -> None:
        self.actual_x = self.actual_y = self.actual_z = None

    def _result(self) -> Optional[tuple[float, float, float]]:
        if not self.applied:
            return None
        return (self.actual_x, self.actual_y, self.actual_z)  # pyright: ignore[reportReturnType]  (set by apply)


class Translation(AxisAugmentation):
    """Moves the object by a random offset per axis, in Blender units.

    Each axis is a number `v`, sampled from `(-v, v)`, a pair `(low, high)`, or a
    triple `(low, high, step)` for one of `low`, `low + step`, ... up to `high`.
    The offset is added to the location.

    Args:
        x: offset on X in Blender units.
        y: offset on Y in Blender units.
        z: offset on Z in Blender units.
        p: probability of applying the augmentation.

    Example:
        ```python
        augmentations.Translation(x=0.5, y=0.5, z=(0, 1))
        augmentations.Translation(x=(-1, 1, 0.5))   # -1, -0.5, 0, 0.5 or 1
        ```
    """

    def apply(self, obj: Optional[Object]) -> None:
        """Args:
        obj (bpy.object) : Object to be augmented
        """
        self.actual_x, self.actual_y, self.actual_z = bpy_a.translation(obj, self.x, self.y, self.z)


class Rotation(AxisAugmentation):
    """Rotates the object by a random angle per axis, in degrees.

    Each axis is a number `v`, sampled from `(-v, v)`, a pair `(low, high)`, or a
    triple `(low, high, step)` for one of `low`, `low + step`, ... up to `high`, each
    equally likely. The angle is added to the rotation, in euler, quaternion and
    axis-angle modes.

    For whole turns in steps, leave out the last step: `(0, 270, 90)`, not
    `(0, 360, 90)`, where 360 is the same as 0 and would make it twice as likely.

    Args:
        x: angle around X in degrees.
        y: angle around Y in degrees.
        z: angle around Z in degrees.
        p: probability of applying the augmentation.

    Example:
        ```python
        augmentations.Rotation(z=180)          # any heading
        augmentations.Rotation(z=(0, 270, 90))  # 0, 90, 180 or 270 degrees
        # tilted by -30, -15, 0, 15 or 30 degrees, and turned in 45 degree steps
        augmentations.Rotation(x=(-30, 30, 15), z=(0, 315, 45))
        ```
    """

    def apply(self, obj: Optional[Object]) -> None:
        """Args:
        obj (bpy.object) : Object to be augmented
        """
        self.actual_x, self.actual_y, self.actual_z = bpy_a.rotation(obj, self.x, self.y, self.z)


class Scale(AxisAugmentation):
    """Scales the object by a random percentage per axis.

    Each axis is a number `v`, sampled from `(-v, v)`, a pair `(low, high)`, or a
    triple `(low, high, step)` for one of `low`, `low + step`, ... up to `high`, in
    percent: the scale is multiplied by `1 + sample / 100`.

    Args:
        x: change of the X scale in percent.
        y: change of the Y scale in percent.
        z: change of the Z scale in percent.
        p: probability of applying the augmentation.

    Example:
        ```python
        augmentations.Scale(x=10, y=10, z=10, p=0.5)   # ±10 %, in half of the datapoints
        ```
    """

    def apply(self, obj: Optional[Object]) -> None:
        """Args:
        mesh obj (bpy.object.type == 'MESH') : Object to be augmented
        """
        self.actual_x, self.actual_y, self.actual_z = bpy_a.scale(obj, self.x, self.y, self.z)


class Visibility(Augmentation):
    """Shows the object in the render with probability `p`, otherwise hides it.

    It sets the object's render visibility (`hide_render`) both ways: an object hidden
    in the scene is shown when the draw says visible, and a visible one is hidden when
    it says not. The viewport visibility is left unchanged. Children are not affected,
    pass them to the `Compose` too.

    The labels follow the render: a hidden object has no pixels in the masks, passes
    and AOVs, is left out of its instance's bbox (an instance with every object
    hidden has bbox None and is ignored by the `BBox` skip settings), doesn't hide
    keypoints behind it, and keypoints on it are not visible.

    Args:
        p: probability that the object is visible.
        otherwise: what happens the rest of the time: False hides the object, None
            leaves its visibility unchanged.

    Attributes:
        actual (bool | None): whether the last call made the object visible, None when
            it left it unchanged. In a `Compose` that is the last object; `results` has
            every object.

    Example:
        ```python
        distractors_aug = augmentations.Compose([augmentations.Visibility(p=0.7)])
        distractors_aug([box_1, box_2, box_3])   # each one in about 70 % of the images
        visibility = distractors_aug.augmentations[0]
        visible = [name for name, shown in visibility.results.items() if shown]

        # shown in 70 % of the images, otherwise as it is in the scene
        augmentations.Visibility(p=0.7, otherwise=None)
        ```
    """

    def __init__(self, p: float = 0.5, otherwise: Optional[bool] = False):
        super().__init__(p, check_otherwise(otherwise, isinstance(otherwise, bool), "True or False"))

    def apply(self, obj: Optional[Object]) -> None:
        """Args:
        obj (bpy.object) : Object to be shown
        """
        self.actual = bpy_a.visibility(obj, True)

    def set_otherwise(self, obj: Optional[Object]) -> bool:
        return bpy_a.visibility(obj, self.otherwise)


class KeepAbove(Augmentation):
    """Moves the object up, if needed, so it stays above a surface, e.g. a floor or terrain.

    It checks whether the object's lowest point is at least `margin` above the top of
    the surface where they overlap seen from above, and if not, moves the object up
    along world Z until it is. An object that is already high enough, or not over the
    surface, is left where it is; it is never moved down. Both are compared as
    evaluated meshes (modifiers included) in world space, so uneven surfaces work.
    It sets `matrix_world`, so parented objects work too. Only the object's own
    geometry counts, not its children's.

    !!! warning "Put it after the transforms"
        It checks the object where it is when it runs, so put it in the `Compose`
        after `Translation`, `Rotation`, `Scale` and any other augmentation that moves
        or deforms the object (e.g. a `Number` changing a modifier or shape key).
        Augmentations after it can push the object into the surface again.

    Args:
        surface: object to stay above. It must have faces (a mesh, curve, text...).
        margin: minimum gap between the surface and the object's lowest point, in
            Blender units.
        p: probability of applying the augmentation.

    Attributes:
        actual (float | None): how far the last call moved the object up, 0 when it
            was already above. In a `Compose` that is the last object; `results` has
            every object.

    Raises:
        ValueError: the object is the surface.
        TypeError: the surface has no faces.

    Example:
        ```python
        objects_aug = augmentations.Compose([
            augmentations.Translation(x=0.5, y=0.5, z=(-0.3, 0.3)),
            augmentations.Rotation(x=30, y=30, z=180),
            augmentations.KeepAbove(floor, margin=0.01),   # after the transforms
        ])
        objects_aug([car_1, car_2])
        ```
    """

    def __init__(self, surface: Object, margin: float = 0.0, p: float = 1.0):
        super().__init__(p)
        self.surface = surface
        self.margin = margin

    def apply(self, obj: Optional[Object]) -> None:
        """Args:
        obj (bpy.object) : Object to keep above the surface
        """
        self.actual = bpy_a.keep_above(obj, self.surface, self.margin)


class PlaceOn(Augmentation):
    """Moves the object up or down so it rests on a surface, e.g. a floor or table.

    Like `KeepAbove`, but it also moves the object down: its lowest point ends up
    exactly `margin` above the top of the surface where they overlap seen from above,
    so a floating object drops onto the surface and a sunken one is lifted out. Only
    the height changes, along world Z. An object that is not over the surface is left
    where it is. Both are compared as evaluated meshes (modifiers included) in world
    space, so uneven surfaces work. It sets `matrix_world`, so parented objects work
    too. Only the object's own geometry counts, not its children's.

    Use it when the object must touch the surface, e.g. after a rotation that tips it
    over around an origin that is not at its bottom. Use `KeepAbove` when the object
    may also float above it.

    !!! warning "Put it after the transforms"
        It places the object where it is when it runs, so put it in the `Compose` after
        `Translation`, `Rotation`, `Scale` and any other augmentation that moves or
        deforms the object.

    Args:
        surface: object to rest on. It must have faces (a mesh, curve, text...).
        margin: gap between the surface and the object's lowest point, in Blender
            units.
        p: probability of applying the augmentation.

    Attributes:
        actual (float | None): how far the last call moved the object, positive up and
            negative down, 0 when it was not over the surface. In a `Compose` that is
            the last object; `results` has every object.

    Raises:
        ValueError: the object is the surface.
        TypeError: the surface has no faces.

    Example:
        ```python
        lying_aug = augmentations.Compose([
            augmentations.Rotation(x=(90, 90), y=(0, 270, 90), z=180),
            augmentations.Translation(x=0.5, y=0.5),
            augmentations.PlaceOn(floor),   # after the transforms
        ])
        lying_aug([milk_box])
        ```
    """

    def __init__(self, surface: Object, margin: float = 0.0, p: float = 1.0):
        super().__init__(p)
        self.surface = surface
        self.margin = margin

    def apply(self, obj: Optional[Object]) -> None:
        """Args:
        obj (bpy.object) : Object to place on the surface
        """
        self.actual = bpy_a.place_on(obj, self.surface, self.margin)


class Seed(Augmentation):
    """Gives every seed in a node group its own random value.

    On every call, each unlinked integer Seed input of the group's nodes (Distribute
    Points on Faces, Distribute Points in Volume, Random Value, Hash Value...) gets a
    different random int, nodes inside nested node groups included. Seeds connected to
    other nodes are left alone. Seeds exposed as modifier inputs are not group nodes;
    set those with `Number`.

    It works on the node group, not on the object passed by `Compose`, so it can be
    called as `seed()`. In a `Compose` it runs once per object, and the last seeds are
    kept.

    Args:
        node_group: the node group, e.g. `bpy.data.node_groups["Geometry Nodes"]`, or
            any other node tree (a material's `node_tree`).
        p: probability of applying the augmentation.
        otherwise: seed to set on all of them when it doesn't run, None keeps them.

    Attributes:
        actual (dict[str, int] | None): the seed set on each node, by node name
            (`"Group node/Node"` for nodes in nested groups).

    Raises:
        TypeError: `node_group` is not a node tree.
        ValueError: the node group has no unlinked Seed inputs, or `otherwise` is not
            an int.

    Example:
        ```python
        scatter = bpy.data.node_groups["Geometry Nodes"]
        seed = augmentations.Seed(scatter)
        for _ in range(100):
            with state.restoring():                 # the seeds are set back too
                seed()                              # a new scatter every time
                generator()
        ```
    """

    def __init__(self, node_group: NodeTree, p: float = 1.0, otherwise: Optional[int] = None):
        valid = isinstance(otherwise, int) and not isinstance(otherwise, bool)
        super().__init__(p, check_otherwise(otherwise, valid, "an int"))
        bpy_a.check_seed_group(node_group)
        self.node_group = node_group

    def apply(self, obj: Optional[Object] = None) -> None:
        """Args:
        obj (bpy.object) : not used, the seeds belong to the node group
        """
        self.actual = bpy_a.seeds(self.node_group)

    def set_otherwise(self, obj: Optional[Object]) -> dict[str, int]:
        return bpy_a.seeds(self.node_group, self.otherwise)


class LookAt(Augmentation):
    """Moves a camera (or a light, or any object) on a sphere around a target and points it
    at the target, upright.

    The target stays in the center of the camera view. It sets `matrix_world`, so
    parented cameras work too. Each parameter is a `(min, max)` range, an exact number,
    or None to keep the current value.

    Args:
        target: an object, a list of objects (the center of their bounding boxes),
            or a point `(x, y, z)`.
        distance: distance from the target in Blender units.
        elevation: angle above the target's horizontal plane in degrees. Avoid
            exactly 90 / -90.
        azimuth: angle around the world Z axis in degrees, 0 = +X.
        roll: rotation around the camera's view axis in degrees. None = upright.
        focal_length: camera lens in mm, cameras only.
        p: probability of applying the augmentation.

    Attributes:
        actual (dict | None): the distance, elevation, azimuth, roll and focal length
            set by the last call.

    Example:
        ```python
        camera_aug = augmentations.Compose([
            augmentations.LookAt([car_1, car_2], distance=(6, 12), elevation=(10, 45),
                                 azimuth=(0, 360), roll=(-5, 5)),
        ])
        camera_aug([bpy.context.scene.camera])
        ```
    """

    def __init__(
        self,
        target: Target,
        distance: Optional[RangeOrValue] = None,
        elevation: Optional[RangeOrValue] = None,
        azimuth: Optional[RangeOrValue] = None,
        roll: Optional[RangeOrValue] = None,
        focal_length: Optional[RangeOrValue] = None,
        p: float = 1.0,
    ):
        super().__init__(p)
        self.target = target
        self.distance = distance
        self.elevation = elevation
        self.azimuth = azimuth
        self.roll = roll
        self.focal_length = focal_length

    def apply(self, obj: Optional[Object]) -> None:
        """Args:
        obj (bpy.object) : camera or other object to move
        """
        self.actual = bpy_a.look_at(
            obj, self.target, self.distance, self.elevation, self.azimuth, self.roll, self.focal_length
        )


class PlaceOnCurve(Augmentation):
    """Moves a camera (or any object) to a random point on a curve, e.g. a camera path.

    The point is picked uniformly along the curve's length (a curve with several splines
    counts them one after another, in their order), so straight and curved parts get
    the same density. The curve is read as Blender evaluates it (modifiers included),
    as the line it draws at its resolution, without bevel or extrusion. It sets
    `matrix_world`, so parented objects work too.

    By default only the location changes: the object keeps its rotation and scale.
    With `align`, it also turns to face along the curve direction at that point, the
    way the curve was drawn (from its first point to its last): a camera's view axis
    (-Z) follows the tangent, upright (its Y axis up towards world Z). The curve's tilt
    is ignored. Avoid vertical parts of the curve with `align`, where "up" is undefined.

    !!! tip "Vary it after"
        Put `Translation` after it in the `Compose` to move the object off the curve
        (in world axes, Blender units), and `Rotation` to turn it from there, e.g.
        `Rotation(z=(90, 90))` after `align=True` makes the camera look to the left
        of the path, and `Rotation(x=5, y=5, z=10)` adds a little shake. To aim at
        something instead, put `LookAt(target)` after it without `distance`,
        `elevation` and `azimuth`: it keeps the point on the curve.

    Args:
        curve: the curve object to place the object on.
        align: also rotate the object to face along the curve direction.
        position: where on the curve, as a fraction of its length from its start, a
            `(min, max)` range (the default is all of it) or an exact number, 0-1.
        p: probability of applying the augmentation.

    Attributes:
        actual (dict | None): the `position` (fraction of the length) and the world
            `location` set by the last call.

    Raises:
        TypeError: `curve` is not a curve object.
        ValueError: `position` is not between 0 and 1, the object is the curve, the
            curve has no length, or it evaluates to faces.

    Example:
        ```python
        camera_path = bpy.data.objects["CameraPath"]
        camera_aug = augmentations.Compose([
            augmentations.PlaceOnCurve(camera_path, align=True),
            augmentations.Translation(z=(-0.2, 0.2)),   # a little up or down
            augmentations.Rotation(x=5, z=(-20, 20)),   # look around a bit
        ])
        camera_aug([bpy.context.scene.camera])
        ```
    """

    def __init__(self, curve: Object, align: bool = False, position: RangeOrValue = (0.0, 1.0), p: float = 1.0):
        super().__init__(p)
        bpy_a.check_curve(curve)
        self.curve = curve
        self.align = align
        self.position = check_fraction(position, "position")

    def apply(self, obj: Optional[Object]) -> None:
        """Args:
        obj (bpy.object) : camera or other object to move
        """
        self.actual = bpy_a.place_on_curve(obj, self.curve, self.align, self.position)


class FocalLength(Augmentation):
    """Sets a random camera lens, optionally as a dolly zoom.

    With `keep_size`, the camera also moves along the line to the target by the same
    ratio as the lens, so the target keeps its size in the image while the perspective
    changes. That is exact for parts at the target's depth when the target is in the
    center of the view (e.g. after `LookAt`); an off-center target also moves in the
    image. Perspective cameras only.

    Args:
        focal_length: lens in mm, a `(min, max)` range or an exact number.
        target: an object, a list of objects (the center of their bounding boxes),
            or a point `(x, y, z)`. Needed for `keep_size`.
        keep_size: move the camera so the target keeps its size in the image.
        p: probability of applying the augmentation.
        otherwise: lens in mm to set when it doesn't run, None keeps the lens. Not
            with `keep_size`.

    Raises:
        ValueError: `keep_size` without a `target`, or `otherwise` with `keep_size`.

    Example:
        ```python
        augmentations.FocalLength((24, 85), target=[car_1, car_2], keep_size=True)
        # a random lens in 30 % of the images, otherwise 50 mm
        augmentations.FocalLength((24, 85), p=0.3, otherwise=50)
        ```
    """

    def __init__(self, focal_length: RangeOrValue, target: Optional[Target] = None, keep_size: bool = False,
                 p: float = 1.0, otherwise: Optional[float] = None):
        super().__init__(p, check_otherwise(otherwise, is_number(otherwise), "a lens in mm"))
        if keep_size and target is None:
            raise ValueError("keep_size needs a target")
        if keep_size and otherwise is not None:
            raise ValueError("otherwise can't be used with keep_size")
        self.focal_length = focal_length
        self.target = target
        self.keep_size = keep_size

    def apply(self, obj: Optional[Object]) -> None:
        """Args:
        camera obj (bpy.object.type == 'CAMERA') : camera to augment
        """
        self.actual = bpy_a.focal_length(obj, self.focal_length, self.target, self.keep_size)

    def set_otherwise(self, obj: Optional[Object]) -> dict[str, float]:
        return bpy_a.focal_length(obj, self.otherwise, None, False)


class DepthOfField(Augmentation):
    """Turns on depth of field, focused on a target, with a random f-stop.

    Focus is measured along the view axis from where the camera is when this runs, so
    put it after `LookAt` / `FocalLength` in a `Compose`.

    Args:
        target: an object, a list of objects (the center of their bounding boxes), or a
            point `(x, y, z)` to focus on. None keeps the current focus.
        f_stop: aperture f-stop, a `(min, max)` range or an exact number, lower gives
            more blur. None keeps the current one.
        p: probability of applying the augmentation.
        otherwise: what happens the rest of the time: False turns depth of field off,
            None leaves the depth of field settings unchanged.

    Attributes:
        actual (dict | bool | None): `{"f_stop", "focus_distance"}` set by the last
            call, False when it turned depth of field off, None when it left it
            unchanged.

    Example:
        ```python
        # blurred in half of the images, sharp in the others
        augmentations.DepthOfField(car_1, f_stop=(1.4, 5.6), p=0.5, otherwise=False)
        ```
    """

    def __init__(self, target: Optional[Target] = None, f_stop: Optional[RangeOrValue] = None, p: float = 1.0,
                 otherwise: Optional[bool] = None):
        super().__init__(p, check_otherwise(otherwise, otherwise is False, "False (depth of field off)"))
        self.target = target
        self.f_stop = f_stop

    def apply(self, obj: Optional[Object]) -> None:
        """Args:
        camera obj (bpy.object.type == 'CAMERA') : camera to augment
        """
        self.actual = bpy_a.depth_of_field(obj, self.target, self.f_stop)

    def set_otherwise(self, obj: Optional[Object]) -> bool:
        return bpy_a.disable_depth_of_field(obj)


class MaterialSlot(Augmentation):
    """Gives the object one of the materials in its own material slots, picked at random.

    Add the materials to pick from to the object's slots in Blender (Material
    Properties, `+`). On every call it assigns every face of the mesh to the picked
    slot, and makes it the active slot, so later augmentations in the same `Compose`
    reach the picked material through a relative path,
    `active_material.node_tree.nodes[...]`. `state.restoring()` sets the faces' slots
    and the active slot back.

    It doesn't copy materials: an object shows a material that other objects may use
    too, and augmenting that material changes it for all of them. Which objects share
    a material, and a mesh (with its slots, by default), is up to how the scene is set
    up: give objects their own copies to augment them separately.

    Args:
        slots: slots to pick from, by index or material name. None = all of them.
        weights: relative probability of each slot. None = equal.
        p: probability of applying the augmentation.
        otherwise: slot to use when it doesn't run, by index or material name. None
            keeps the faces' slots.

    Attributes:
        actual (str | None): name of the picked slot's material (None for an empty
            slot, or when skipped without `otherwise`).

    Raises:
        ValueError: the number of weights and slots differ, or `otherwise` is not an
            index or a name. At a call: the object has no material slots.
        TypeError: at a call, the object is not a mesh.
        KeyError: at a call, a slot in `slots` doesn't exist.

    Note:
        A geometry nodes modifier that sets the material (Set Material) wins over the
        faces' slots.

    Example:
        ```python
        # the cube's slots: "Ferrous metal", "Plastic", "Rubber"
        cube_aug = augmentations.Compose([
            augmentations.MaterialSlot(weights=[3, 1, 1]),
            # the picked material, whichever it is
            augmentations.Number(
                'active_material.node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value',
                value_range=(0.1, 0.6),
            ),
        ])
        cube_aug([cube])
        ```

        Each material augmented its own way: a `Node` with an absolute path per
        material. The ones that weren't picked are changed too, but don't show; to
        augment only the picked one, see the example of
        [`Chain`][blendmentation.augmentations.augmentations.Chain].

        ```python
        cube_aug = augmentations.Compose([
            augmentations.MaterialSlot(["Ferrous metal", "Plastic"]),
            augmentations.Node(
                'bpy.data.materials["Ferrous metal"].node_tree.nodes["Ferrous metal"]',
                {"Rust strength": augmentations.Input((0.3, 1))},
            ),
            augmentations.Node(
                'bpy.data.materials["Plastic"].node_tree.nodes["Principled BSDF"]',
                {"Base Color": augmentations.Input((0, 1))},
            ),
        ])
        ```
    """

    def __init__(self, slots: Optional[Sequence[Union[int, str]]] = None, weights: Optional[Sequence[float]] = None,
                 p: float = 1.0, otherwise: Optional[Union[int, str]] = None):
        valid = isinstance(otherwise, str) or (isinstance(otherwise, int) and not isinstance(otherwise, bool))
        super().__init__(p, check_otherwise(otherwise, valid, "a slot index or a material name"))
        if slots is not None and weights is not None and len(slots) != len(weights):
            raise ValueError("Give one weight per slot")
        self.slots = slots
        self.weights = weights

    def apply(self, obj: Optional[Object]) -> None:
        """Args:
        obj (bpy.object) : mesh object whose slots to pick from
        """
        self.actual = bpy_a.material_slot(obj, self.slots, self.weights)

    def set_otherwise(self, obj: Optional[Object]) -> Optional[str]:
        return bpy_a.material_slot(obj, [self.otherwise], None)


class SimpleMaterial(Augmentation):
    """Sets random Principled BSDF base color, roughness and metallic values.

    Each parameter is an absolute `(min, max)` range, 0-1, and None leaves the value
    unchanged. The base color is set through hue, saturation and value. The sockets
    must not be connected to other nodes.

    It changes the **material**, so every object using it is affected.

    Args:
        material_id: name of the material.
        hue: range of the base color hue.
        saturation: range of the base color saturation.
        value: range of the base color value (brightness).
        roughness: range of the roughness.
        metallic: range of the metallic.
        p: probability of applying the augmentation.

    Tip:
        Use `SimpleMaterial` for negative data, secondary objects, or to make a model
        generalize over shape while ignoring the material. For finer control over the
        materials of hero objects, use [`Node`][blendmentation.augmentations.augmentations.Node]
        to set the inputs of a node group, or [`Number`][blendmentation.augmentations.augmentations.Number]
        to set individual shader node inputs.

    Example:
        ```python
        augmentations.SimpleMaterial("CarPaint", hue=(0, 1), saturation=(0.5, 1), roughness=(0.1, 0.6))
        ```
    """

    def __init__(
        self,
        material_id: str,
        hue: Optional[tuple[float, float]] = None,
        saturation: Optional[tuple[float, float]] = None,
        value: Optional[tuple[float, float]] = None,
        roughness: Optional[tuple[float, float]] = None,
        metallic: Optional[tuple[float, float]] = None,
        p: float = 1.0,
    ):
        super().__init__(p)
        self.material_id = material_id
        self.hue = hue
        self.saturation = saturation
        self.value = value
        self.roughness = roughness
        self.metallic = metallic

    def apply(self, obj: Optional[Object]) -> None:
        """Args:
        mesh obj (bpy.object.type == 'MESH') : Object to be augmented
        """
        self.actual = bpy_a.material(
            obj, self.material_id, self.hue, self.saturation, self.value, self.roughness, self.metallic
        )


class Number(Augmentation):
    """Sets any int or float value, given by its data path, to a random value.

    For shader node inputs, geometry nodes inputs, shape keys, light settings and so
    on. Ints are rounded. With a step, the value is one of min, min + step, ... max.

    Args:
        data_path: path to the value, absolute (starting with `bpy.`) or relative to
            the object (or the World, material… passed in its place). Use `[index]` for one vector component, e.g. `'location[2]'`.
        value_range: `(min, max)` range of the new value, or `(min, max, step)`.
        p: probability of applying the augmentation.
        otherwise: value to set when it doesn't run, None keeps the value.

    Raises:
        ValueError: `value_range` is not `(min, max)` or `(min, max, step)`, min is
            above max, or step is not above 0.

    !!! info "With an absolute path"
        A path starting with `bpy.` doesn't use the object, so it can be called alone,
        `aug()`. In a `Compose` it runs once per object the `Compose` is called with,
        and the last value set is kept.

    Example:
        ```python
        augmentations.Number("data.energy", value_range=(600, 1400))
        augmentations.Number("data.energy", value_range=(600, 1400, 200))  # 600, 800, ... 1400
        augmentations.Number('data.shape_keys.key_blocks["Smile"].value', value_range=(0, 1))
        # rust in 20 % of the images, none in the others
        augmentations.Number('node_tree.nodes["Rust"].outputs[0].default_value',
                             value_range=(0.5, 1), p=0.2, otherwise=0)
        ```
    """

    def __init__(self, data_path: str, value_range: Union[tuple[float, float], tuple[float, float, float]],
                 p: float = 1.0, otherwise: Optional[float] = None):
        super().__init__(p, check_otherwise(otherwise, is_number(otherwise), "a number"))
        self.data_path = data_path
        self.value_range = check_range(value_range, "value_range")

    def apply(self, obj: Optional[Object]) -> None:
        """Args:
        obj (bpy.object) : Object relative paths start from, not needed for absolute paths
        """
        self.actual = bpy_a.number(obj, self.data_path, self.value_range)

    def set_otherwise(self, obj: Optional[Object]) -> float:
        return bpy_a.number(obj, self.data_path, (self.otherwise, self.otherwise))


class Vector(Augmentation):
    """Sets any vector value, given by its data path, to random values per component.

    For locations, colors, vector node inputs and so on. Each bound of `value_range` is
    a number for all components, or a sequence with one value per component, where
    None keeps that component. Int components are rounded.

    Args:
        data_path: path to the value, absolute (starting with `bpy.`) or relative to
            the object (or the World, material… passed in its place).
        value_range: `(min, max)` bounds of the new values.
        p: probability of applying the augmentation.
        otherwise: vector to set when it doesn't run, a number for all components or
            one value per component (None keeps that component). None keeps the vector.

    !!! info "With an absolute path"
        A path starting with `bpy.` doesn't use the object, so it can be called alone,
        `aug()`. In a `Compose` it runs once per object the `Compose` is called with,
        and the last value set is kept.

    Example:
        ```python
        augmentations.Vector("data.color", value_range=(0.8, 1.0))
        # a random RGB color that keeps alpha
        augmentations.Vector(
            'bpy.data.materials["Mat"].node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value',
            value_range=((0, 0, 0, None), (1, 1, 1, None)),
        )
        # a random offset in half of the images, no offset in the others
        augmentations.Vector('node_tree.nodes["Offset"].vector', value_range=(-1, 1), p=0.5, otherwise=0)
        ```
    """

    def __init__(self, data_path: str, value_range: tuple[Bound, Bound], p: float = 1.0,
                 otherwise: Optional[Bound] = None):
        valid = is_number(otherwise) or (
            isinstance(otherwise, Sequence) and not isinstance(otherwise, str)
            and all(value is None or is_number(value) for value in otherwise))
        super().__init__(p, check_otherwise(otherwise, valid, "a number or one value per component"))
        self.data_path = data_path
        self.value_range = value_range

    def apply(self, obj: Optional[Object]) -> None:
        """Args:
        obj (bpy.object) : Object relative paths start from, not needed for absolute paths
        """
        self.actual = bpy_a.vector(obj, self.data_path, self.value_range)

    def set_otherwise(self, obj: Optional[Object]) -> tuple:
        return bpy_a.vector(obj, self.data_path, (self.otherwise, self.otherwise))


class Boolean(Augmentation):
    """Sets any boolean value, given by its data path, to True with probability `p`,
    otherwise to `otherwise` (False by default).

    For geometry nodes switches, object visibility, modifier toggles and so on.

    Args:
        data_path: path to the value, absolute (starting with `bpy.`) or relative to
            the object (or the World, material… passed in its place).
        p: probability of True.
        otherwise: value to set the rest of the time, None keeps the value.

    !!! info "With an absolute path"
        A path starting with `bpy.` doesn't use the object, so it can be called alone,
        `aug()`. In a `Compose` it runs once per object the `Compose` is called with.

    Example:
        ```python
        augmentations.Boolean("data.use_shadow", p=0.8)
        # turned on in 30 % of the images, otherwise as it is in the scene
        augmentations.Boolean("data.use_shadow", p=0.3, otherwise=None)
        ```
    """

    def __init__(self, data_path: str, p: float = 0.5, otherwise: Optional[bool] = False):
        super().__init__(p, check_otherwise(otherwise, isinstance(otherwise, bool), "True or False"))
        self.data_path = data_path

    def apply(self, obj: Optional[Object]) -> None:
        """Args:
        obj (bpy.object) : Object relative paths start from, not needed for absolute paths
        """
        self.actual = bpy_a.boolean(obj, self.data_path, True)

    def set_otherwise(self, obj: Optional[Object]) -> bool:
        return bpy_a.boolean(obj, self.data_path, self.otherwise)


class Menu(Augmentation):
    """Sets any menu (enum) value, given by its data path, to a random option.

    For geometry nodes menu inputs, node settings like the Principled BSDF
    distribution, the light type and so on.

    Args:
        data_path: path to the value, absolute (starting with `bpy.`) or relative to
            the object (or the World, material… passed in its place).
        options: options to choose from. None = all options of the menu; required when
            Blender doesn't list them, e.g. for menu sockets not on a Menu Switch node.
        weights: relative probability of each option. None = equal.
        p: probability of applying the augmentation.
        otherwise: option to set when it doesn't run, None keeps the value. It doesn't
            have to be one of `options`.

    Raises:
        ValueError: the number of weights and options differ.

    !!! info "With an absolute path"
        A path starting with `bpy.` doesn't use the object, so it can be called alone,
        `aug()`. In a `Compose` it runs once per object the `Compose` is called with.

    Example:
        ```python
        augmentations.Menu("data.type", options=["POINT", "SPOT", "AREA"], weights=[2, 1, 1])
        # a spot or area light in 30 % of the images, a point light in the others
        augmentations.Menu("data.type", options=["SPOT", "AREA"], p=0.3, otherwise="POINT")
        ```
    """

    def __init__(self, data_path: str, options: Optional[Sequence[Any]] = None,
                 weights: Optional[Sequence[float]] = None, p: float = 1.0, otherwise: Any = None):
        super().__init__(p, otherwise)
        if options is not None and weights is not None and len(options) != len(weights):
            raise ValueError("Give one weight per option")
        self.data_path = data_path
        self.options = options
        self.weights = weights

    def apply(self, obj: Optional[Object]) -> None:
        """Args:
        obj (bpy.object) : Object relative paths start from, not needed for absolute paths
        """
        self.actual = bpy_a.menu(obj, self.data_path, self.options, self.weights)

    def set_otherwise(self, obj: Optional[Object]) -> Any:
        return bpy_a.menu(obj, self.data_path, [self.otherwise], None)


class Unset:
    """The default of `Input(otherwise=...)`: the default of the augmentation it becomes."""

    def __repr__(self) -> str:
        return "UNSET"


UNSET = Unset()


class Input:
    """How a `Node` sets one input: its range or options, `p` and `otherwise`.

    The arguments are those of the augmentation the input becomes, picked by its
    socket type: `value_range`, `p` and `otherwise` for numbers, vectors and colors
    (as in [`Number`][blendmentation.augmentations.augmentations.Number] and
    [`Vector`][blendmentation.augmentations.augmentations.Vector]), `p` and `otherwise`
    for booleans ([`Boolean`][blendmentation.augmentations.augmentations.Boolean]), and
    `options`, `weights`, `p` and `otherwise` for menus
    ([`Menu`][blendmentation.augmentations.augmentations.Menu]). Arguments that don't
    fit the socket raise a `ValueError` at the first call.

    Args:
        value_range: `(min, max)`, or `(min, max, step)` for numbers. For a color, a
            number or 3 values per bound set red, green and blue and keep alpha.
        options: menu options to choose from. None = all of them.
        weights: relative probability of each menu option. None = equal.
        p: probability of setting the input. None = the augmentation's default: 1,
            or 0.5 for a boolean, which is then the probability of True.
        otherwise: value to set when it doesn't run because of `p`. None keeps the
            value; left out, it is the augmentation's default: None, or False for a
            boolean.

    Raises:
        ValueError: both `value_range` and `options` are given, `value_range` is not
            a pair or a triple, `p` is not between 0 and 1, or the number of weights
            and options differ.

    Example:
        ```python
        augmentations.Input((0.5, 1), p=0.2, otherwise=0)    # a number or a color
        augmentations.Input((0, 1, 0.25))                     # 0, 0.25, ... 1
        augmentations.Input(options=["Shiny metal", "Cast metal"], weights=[3, 1])
        augmentations.Input(p=0.3, otherwise=None)            # a boolean: True 30 %, else kept
        ```
    """

    def __init__(self, value_range: Optional[Sequence[Any]] = None, options: Optional[Sequence[Any]] = None,
                 weights: Optional[Sequence[float]] = None, p: Optional[float] = None, otherwise: Any = UNSET):
        if value_range is not None and options is not None:
            raise ValueError("Give value_range or options, not both")
        if value_range is not None and (isinstance(value_range, str) or not isinstance(value_range, Sequence)
                                        or len(value_range) not in (2, 3)):
            raise ValueError(f"value_range must be (min, max) or (min, max, step), got {value_range!r}")
        if options is not None and weights is not None and len(options) != len(weights):
            raise ValueError("Give one weight per option")
        self.value_range = value_range
        self.options = options
        self.weights = weights
        self.p = None if p is None else check_p(p)
        self.otherwise = otherwise

    def arguments(self) -> dict[str, Any]:
        """The arguments that were given, for the augmentation the input becomes."""
        arguments = {name: getattr(self, name) for name in ("value_range", "options", "weights", "p")
                     if getattr(self, name) is not None}
        if self.otherwise is not UNSET:
            arguments["otherwise"] = self.otherwise
        return arguments

    def __repr__(self) -> str:
        return f"Input({', '.join(f'{name}={value!r}' for name, value in self.arguments().items())})"


#: the arguments an input of a `Node` can set, in a dict
INPUT_ARGUMENTS = ("value_range", "options", "weights", "p", "otherwise")


def color_bound(bound: Any) -> Any:
    """A bound or otherwise for a color socket: a number or 3 values set R, G, B and keep alpha."""
    if is_number(bound):
        return (bound, bound, bound, None)
    if isinstance(bound, Sequence) and len(bound) == 3:
        return (*bound, None)
    return bound


def input_augmentation(kind: str, key: Union[str, int], data_path: str, spec: Any) -> Augmentation:
    """The `Number`, `Vector`, `Boolean` or `Menu` for an input of a `Node`, by socket type."""
    augmentations = {"VALUE": (Number, "value_range"), "INT": (Number, "value_range"),
                     "VECTOR": (Vector, "value_range"), "ROTATION": (Vector, "value_range"),
                     "RGBA": (Vector, "value_range"),
                     "BOOLEAN": (Boolean, "p"), "MENU": (Menu, "options")}
    if kind not in augmentations:
        raise TypeError(f"Input {key!r} is a {kind} socket, only number, vector, rotation, color, boolean and menu inputs can be augmented")
    augmentation, main = augmentations[kind]
    if isinstance(spec, Input):
        arguments = spec.arguments()
    else:
        arguments = dict(spec) if isinstance(spec, dict) else {} if spec is None else {main: spec}
    if kind == "RGBA":
        if "value_range" in arguments:
            arguments["value_range"] = tuple(color_bound(bound) for bound in arguments["value_range"])
        if arguments.get("otherwise") is not None:
            arguments["otherwise"] = color_bound(arguments["otherwise"])
    if augmentation in (Number, Vector) and arguments.get("value_range") is None:
        raise ValueError(f"Input {key!r} is a {kind} socket, give it a value_range")
    try:
        return augmentation(data_path, **arguments)
    except TypeError as error:
        raise ValueError(f"Input {key!r} is a {kind} socket: {error}") from None


class InputAugmentation:
    """One input of a `Node` or `Modifier`. On every call it runs a `Number`, `Vector`,
    `Boolean` or `Menu`, picked by the type of the socket, with the input's arguments."""

    def __init__(self, key: Union[str, int], spec: Any):
        if isinstance(spec, dict):
            unknown = set(spec) - set(INPUT_ARGUMENTS)
            if unknown:
                raise ValueError(f"Input {key!r}: unknown arguments {sorted(unknown)}, use {', '.join(INPUT_ARGUMENTS)}")
        self.key = key
        self.spec = spec
        self.by_path: dict[tuple[str, str], Augmentation] = {}
        self.applied: Optional[bool] = None
        self.actual: Any = None
        self.results: dict[Optional[str], Any] = {}

    def resolve(self, obj: Optional[Object]) -> tuple[str, str]:
        """The data path of the input's value for this object, and its socket type."""
        raise NotImplementedError

    def __call__(self, obj: Optional[Object] = None) -> None:
        data_path, kind = self.resolve(obj)
        if (data_path, kind) not in self.by_path:
            self.by_path[data_path, kind] = input_augmentation(kind, self.key, data_path, self.spec)
        augmentation = self.by_path[data_path, kind]
        augmentation(obj)
        self.applied, self.actual = augmentation.applied, augmentation.actual
        self.results[None if obj is None else obj.name] = self.actual


class NodeInput(InputAugmentation):
    """One input of a `Node`, at a fixed data path."""

    def __init__(self, key: Union[str, int], data_path: str, spec: Any):
        super().__init__(key, spec)
        self.data_path = data_path

    def resolve(self, obj: Optional[Object]) -> tuple[str, str]:
        return self.data_path, bpy_a.socket_type(obj, self.data_path)


class ModifierInput(InputAugmentation):
    """One input of a `Modifier`. Its data path depends on the Blender version and the
    node group's socket identifier, so it is found on every call."""

    def __init__(self, key: Union[str, int], modifier: str, spec: Any):
        input_key(key)
        super().__init__(key, spec)
        self.modifier = modifier

    def resolve(self, obj: Optional[Object]) -> tuple[str, str]:
        return bpy_a.modifier_input(obj, self.modifier, self.key)


class Node(Augmentation):
    """Sets the inputs of a node to random values, each with its own range, probability
    and `otherwise`.

    For the group node of a smart material, of a geometry nodes setup, or any other
    node in a shader, world or geometry nodes tree. The inputs of a geometry nodes
    *modifier* are not a node: set them with
    [`Modifier`][blendmentation.augmentations.augmentations.Modifier].

    `inputs` maps each input to augment, by name (or index, for inputs that share a
    name), to how it is set: an [`Input`][blendmentation.augmentations.augmentations.Input],
    or for short its main argument alone. Inputs that are left out keep their values.
    Each input is set by a [`Number`][blendmentation.augmentations.augmentations.Number],
    [`Vector`][blendmentation.augmentations.augmentations.Vector],
    [`Boolean`][blendmentation.augmentations.augmentations.Boolean] or
    [`Menu`][blendmentation.augmentations.augmentations.Menu], picked by the type of
    the socket, and takes the arguments of that augmentation. A dict with the same
    keys as the `Input` arguments works too.

    | Socket | Main argument alone | `Input` arguments |
    | --- | --- | --- |
    | Float, Int | `value_range`: `(min, max)` or `(min, max, step)` | `value_range`, `p`, `otherwise` |
    | Vector | `value_range`: `(min, max)`, as in `Vector` | `value_range`, `p`, `otherwise` |
    | Rotation | `value_range`: `(min, max)` of the X, Y, Z angles in radians, as in `Vector` | `value_range`, `p`, `otherwise` |
    | Color | `value_range`: `(min, max)`, as in `Vector` | `value_range`, `p`, `otherwise` |
    | Boolean | `p`, the probability of True | `p`, `otherwise` |
    | Menu | `options`, None = all of them | `options`, `weights`, `p`, `otherwise` |

    For a color, a number or 3 values set red, green and blue and keep alpha, so
    `(0, 1)` is any color; give 4 values to set alpha too. Each input draws its own
    `p`, and `Node`'s own `p` applies the whole node: when it doesn't run, no
    input changes, also not to its `otherwise`.

    The node is given by its data path, with the node's name: Sidebar (N) > Node >
    Name, not the title on the node (a group node shows its group's name), or right
    click an input > Copy Full Data Path. The path is absolute (starting with `bpy.`) or
    relative to the object, e.g. `active_material.node_tree.nodes["Ferrous metal"]` to augment the
    material of each object in a `Compose`. The inputs must not be connected to other
    nodes. It changes the **node tree** (the material, the node group…), so everything
    using it is affected.

    Args:
        node: data path of the node.
        inputs: what to set each input to, by input name or index.
        p: probability of applying the augmentation.

    Attributes:
        actual (dict | None): the value set to each input by the last call, by its
            key in `inputs` (None for an input skipped by its `p` without
            `otherwise`), None when it was skipped.
        augmentations (list): one entry per input, with the `data_path` of its value;
            `Compose` clears their `results`.

    Raises:
        ValueError: `inputs` is empty, an input's key is not a name or index, or its
            dict has other keys. At the first call: an input's arguments don't fit
            its socket type, e.g. a Float without a range.
        TypeError: at a call, an input is not a number, vector, rotation, color,
            boolean or menu socket.

    !!! info "With an absolute path"
        A path starting with `bpy.` doesn't use the object, so it can be called alone,
        `node()`. In a `Compose` it runs once per object the `Compose` is called with,
        and the last values set are kept.

    Tip:
        Each input can also be set on its own, with `Number`, `Vector`, `Boolean` or
        `Menu` and the data path of its value,
        `'node_tree.nodes["Ferrous metal"].inputs["Rust strength"].default_value'`.
        `Node` is shorter for several inputs of one node, and picks the augmentation
        for each input by itself.

    Example:
        An input takes one of three forms, and they can be mixed:

        - an `Input`, which takes every argument, checked when it is built:
          `Input((0.5, 1), p=0.2, otherwise=0)`;
        - the main argument alone, short for an `Input` with only that: `(0, 1)` is
          `Input((0, 1))`, and `None` is `Input()` (a menu: any option);
        - a dict with the `Input` arguments as keys,
          `{"value_range": (0.5, 1), "p": 0.2}`, checked only at the first call.

        ```python
        ferrous_metal = augmentations.Node(
            'bpy.data.materials["Master material"].node_tree.nodes["Ferrous metal"]',
            {
                "Texture ofset": (-100, 100),             # Vector: every axis
                "Base metal type": None,                  # Menu: any option
                "Surface effect": augmentations.Input(options=["Weathered", "Grinded"], weights=[3, 1]),
                "Base metal color": ((0.3, 0.3, 0.3), (0.6, 0.6, 0.6)),  # Color, alpha kept
                "Rust strength": (0, 1, 0.25),            # Float: 0, 0.25, ... 1
                # rust in 20 % of the images, none in the others
                "Rust spread": augmentations.Input((0.5, 1), p=0.2, otherwise=0),
                "Paint Color": (0, 1),                    # Color: any color
            },
        )
        with state.restoring():
            ferrous_metal()   # absolute path: no object needed
            ferrous_metal.actual   # e.g. {"Texture ofset": (12.0, -40.3, 77.1), "Base metal type": "Cast metal", ...}
            generator()
        ```

        A group node in a geometry nodes tree:

        ```python
        rocks = augmentations.Node(
            'bpy.data.node_groups["Scatter"].nodes["Rock generator"]',
            {"Count": (10, 50), "Size": augmentations.Input((0.5, 2), p=0.5, otherwise=1)},
        )
        ```
    """

    def __init__(self, node: str, inputs: dict[Union[str, int], Any], p: float = 1.0):
        super().__init__(p)
        if not inputs:
            raise ValueError("Give at least one input")
        self.node = node
        self.inputs = inputs
        self.augmentations = [NodeInput(key, f"{node}.inputs[{input_key(key)}].default_value", spec)
                              for key, spec in inputs.items()]

    def apply(self, obj: Optional[Object]) -> None:
        """Args:
        obj (bpy.object) : Object relative paths start from, not needed for absolute paths
        """
        self.actual = {}
        for augmentation in self.augmentations:
            augmentation(obj)
            self.actual[augmentation.key] = augmentation.actual

    @staticmethod
    def template(node: Union[str, BpyNode], obj: Optional[Object] = None, prefix: str = "augmentations.") -> str:
        """Writes the source of a `Node` that sets every input of a node, to paste into a
        script and edit.

        Each input gets an `Input` with the widest range the node allows, so you narrow
        the ranges you want and delete the inputs to leave alone:

        | Socket | Written as |
        | --- | --- |
        | Float, Int, Vector | `(min, max)` of the socket: for a group node, Min and Max of the input in the group's interface; for other nodes, the socket type's (0 to 1 for a factor). Without one, `(v, v)` with the current value |
        | Rotation | `(v, v)` with the current X, Y, Z angles in radians |
        | Color | `(0.0, 1.0)`: any color, alpha kept |
        | Boolean | `p=0.5` |
        | Menu | every option, when they can be found |

        A comment after each input gives its type and current value. Connected inputs
        and sockets `Node` can't set (shader, geometry, string, object...) are only
        comments, and hidden ones (unavailable in the node's current mode) are left out.
        Inputs that share a name are keyed by index, with the name in the comment. The
        node's name and the input names are written exactly, trailing spaces included.

        In the Blender app, the add-on does the same for the active node in the node
        editor and copies it to the clipboard: Node menu (or right click a node) > Copy
        Blendmentation Template, or search for it with F3. See
        [Installation](installation.md#inside-blender-as-an-add-on).

        Args:
            node: the node, or its data path. A path is kept in the text as it is, so a
                path relative to `obj` makes a relative template; a node gets its
                absolute path.
            obj: object a relative path starts from.
            prefix: written before `Node` and `Input`, e.g. `"aug."` after
                `from blendmentation.augmentations import augmentations as aug`.

        Returns:
            The source of the `Node`, ending in a newline.

        Raises:
            TypeError: `node` is not a node.
            ValueError: the node has no inputs.

        Example:
            ```python
            print(augmentations.Node.template(
                'bpy.data.materials["Master material"].node_tree.nodes["Ferrous metal"]'
            ))
            ```

            prints

            ```python
            augmentations.Node('bpy.data.materials["Master material"].node_tree.nodes["Ferrous metal"]', {
                # "Texture Coordinate": connected
                "Base metal color": augmentations.Input((0.0, 1.0)),   # color, now (0.1385, 0.1385, 0.1385, 1.0)
                "Base metal type": augmentations.Input(options=["Shiny metal", "Cast metal"]),  # menu, now "Shiny metal"
                "Texture scale": augmentations.Input((-10000.0, 10000.0)),  # float, now 1.0
                "Rust strength": augmentations.Input((0.0, 1.0)),      # float, now 0.0
                ...
            })
            ```
        """
        return bpy_a.node_template(node, obj, prefix)


def input_key(key: Union[str, int]) -> str:
    """`key` as a data path subscript: an index, or a quoted name."""
    if isinstance(key, int) and not isinstance(key, bool):
        return str(key)
    if not isinstance(key, str):
        raise ValueError(f"Inputs are given by name or index, got {key!r}")
    return '"' + key.replace("\\", "\\\\").replace('"', '\\"') + '"'


class Modifier(Augmentation):
    """Sets the inputs of a geometry nodes modifier to random values, each with its own
    range, probability and `otherwise`.

    It works like [`Node`][blendmentation.augmentations.augmentations.Node], for the
    inputs shown on the modifier: `inputs` maps each input, by its name in the node
    group (or its index among the group's inputs, Geometry included), to an
    [`Input`][blendmentation.augmentations.augmentations.Input], or its main argument
    alone (see the table in `Node`). A rotation is X, Y, Z angles in radians. An
    input set to be read from an attribute keeps reading it.

    The modifier is given by its data path, relative to the object (e.g.
    `modifiers["Scatter"]`, to augment that modifier on every object in a `Compose`),
    or absolute (`bpy.data.objects["Floor"].modifiers["Scatter"]`). It changes the
    modifier only, not its node group, so objects with the same group keep their
    own values.

    Args:
        modifier: data path of the modifier.
        inputs: what to set each input to, by input name or index.
        p: probability of applying the augmentation.

    Attributes:
        actual (dict | None): the value set to each input by the last call, by its
            key in `inputs` (None for an input skipped by its `p` without
            `otherwise`), None when it was skipped.

    Raises:
        ValueError: `inputs` is empty, an input's key is not a name or index, or its
            dict has other keys. At the first call: an input's arguments don't fit
            its socket type, e.g. a Float without a range.
        KeyError: at a call, the modifier has no such input.
        TypeError: at a call, the path is not a geometry nodes modifier, or an input
            is not a number, vector, rotation, color, boolean or menu socket.

    Tip:
        Each input can also be set on its own, with `Number`, `Vector`, `Boolean` or
        `Menu` and the data path of its value. That path uses the socket's identifier,
        not its name, and changed in Blender 5:
        `modifiers["Scatter"]["Socket_2"]` in 4.x,
        `modifiers["Scatter"].properties.inputs.Socket_2.value` in 5.x. `Modifier`
        finds the inputs by name and builds the path for the running version.

    Example:
        ```python
        floor = bpy.data.objects["Floor"]
        scatter = augmentations.Modifier('modifiers["Scatter"]', {
            "Density": augmentations.Input((5, 20)),
            "Rock size": augmentations.Input((0.5, 2), p=0.5, otherwise=1),
            "Mossy": augmentations.Input(p=0.3),          # Boolean: True 30 %
            "Ground": augmentations.Input(options=["Sand", "Gravel"]),
        })
        with state.restoring():
            augmentations.Compose([scatter])([floor])
            scatter.actual   # e.g. {"Density": 12.7, "Rock size": 1, "Mossy": False, "Ground": "Sand"}
            generator()
        ```
    """

    def __init__(self, modifier: str, inputs: dict[Union[str, int], Any], p: float = 1.0):
        super().__init__(p)
        if not inputs:
            raise ValueError("Give at least one input")
        self.modifier = modifier
        self.inputs = inputs
        self.augmentations = [ModifierInput(key, modifier, spec) for key, spec in inputs.items()]

    def apply(self, obj: Optional[Object]) -> None:
        """Args:
        obj (bpy.object) : Object relative paths start from, not needed for absolute paths
        """
        self.actual = {}
        for augmentation in self.augmentations:
            augmentation(obj)
            self.actual[augmentation.key] = augmentation.actual
