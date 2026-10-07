"""Randomize objects, materials and any other value in the scene, in place.

`Compose` applies a list of augmentations to every object it is called with. Each
augmentation takes `p`, the probability that it runs, drawn per object. After a
call, `applied` says whether it ran and `actual` (or `actual_x/y/z`) holds the values
it set. `Number`, `Vector`, `Boolean`, `Menu`, `Visibility`, `FocalLength` and
`DepthOfField` also take `otherwise`, a value to set when they don't run. Each call overwrites them, so after a `Compose` call they describe only the
last object; `results` holds the values for every object, by name. Save the scene
with `State` first, and restore it after every datapoint.
`Visibility` shows or hides objects in the render, and the labels follow it.
`KeepAbove`, placed after the transforms, lifts objects out of a floor or terrain;
`PlaceOn` also lowers them, so they rest on it.

`Number`, `Vector`, `Boolean` and `Menu` change any value by its data path. A path
starting with `bpy.` is absolute: right click a value in Blender > Copy Full Data
Path. Any other path is relative to the augmented object, e.g. `data.energy`.
Geometry nodes inputs moved in Blender 5, so their path depends on the version:
`modifiers["GeometryNodes"]["Socket_2"]` in 4.x,
`modifiers["GeometryNodes"].properties.inputs.Socket_2.value` in 5.x.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any, Optional, Union

from . import bpy_augmentations as bpy_a

if TYPE_CHECKING:
    from bpy.types import Object  # pyright: ignore[reportMissingModuleSource]  (bpy.types only exists at runtime)

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


def happens(p: float) -> bool:
    """True with probability p. p = 1 draws no random number, so seeded runs don't change."""
    return p >= 1 or random.random() < p


class Compose:
    """Applies a list of augmentations to every object it is called with, in place.

    Each augmentation runs once per object, in list order: all of them on the first
    object, then all of them on the next one. Every augmentation draws its own `p` for
    each object, so with `Visibility(p=0.5)` each object is shown or hidden
    independently. Save the scene with `State` before augmenting, so it can be
    restored.

    Every call first clears the `results` of its augmentations, so afterwards they
    hold the values for the objects of this call only, also when the call is skipped
    by `p` (then they are empty). `actual` and `applied` on an augmentation describe
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
        for augmentation in self.augmentations:
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
    `Visibility`, `FocalLength` and `DepthOfField` take `otherwise`; it is None (keep
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
        ```
    """

    def __init__(self, p: float = 1.0, otherwise: Any = None):
        self.p = check_p(p)
        self.otherwise = otherwise
        self.applied: Optional[bool] = None
        self.actual: Any = None
        self.results: dict[Optional[str], Any] = {}

    def __call__(self, obj: Optional[Object] = None) -> None:
        """Applies the augmentation with probability `p`, and records it in `results`.

        Args:
            obj: object to augment, not needed for absolute data paths.
        """
        self.applied = happens(self.p)
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

    Note:
        Pass the camera to `State`, it restores the transform and the lens.

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


class Material(Augmentation):
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
        Use `Material` for negative data, secondary objects, or to make a model
        generalize over shape while ignoring the material. For finer control over the
        materials of hero objects, use [`Number`][blendmentation.augmentations.augmentations.Number]
        to set individual shader node inputs.

    Example:
        ```python
        augmentations.Material("CarPaint", hue=(0, 1), saturation=(0.5, 1), roughness=(0.1, 0.6))
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
    on. Ints are rounded.

    Args:
        data_path: path to the value, absolute (starting with `bpy.`) or relative to
            the object (or the World, material… passed in its place). Use `[index]` for one vector component, e.g. `'location[2]'`.
        value_range: `(min, max)` range of the new value.
        p: probability of applying the augmentation.
        otherwise: value to set when it doesn't run, None keeps the value.

    !!! info "Use it inside a Compose"
        With an absolute path (starting with `bpy.`) it doesn't use the object passed
        by `Compose`, but it still belongs in one: it runs with the rest of the list,
        and `State(fields=compose.augmentations)` restores it. It runs once per object
        the `Compose` is called with, and the last value set is kept.

    Example:
        ```python
        augmentations.Number("data.energy", value_range=(600, 1400))
        augmentations.Number('data.shape_keys.key_blocks["Smile"].value', value_range=(0, 1))
        # rust in 20 % of the images, none in the others
        augmentations.Number('node_tree.nodes["Rust"].outputs[0].default_value',
                             value_range=(0.5, 1), p=0.2, otherwise=0)
        ```
    """

    def __init__(self, data_path: str, value_range: tuple[float, float], p: float = 1.0,
                 otherwise: Optional[float] = None):
        super().__init__(p, check_otherwise(otherwise, is_number(otherwise), "a number"))
        self.data_path = data_path
        self.value_range = value_range

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

    !!! info "Use it inside a Compose"
        With an absolute path (starting with `bpy.`) it doesn't use the object passed
        by `Compose`, but it still belongs in one: it runs with the rest of the list,
        and `State(fields=compose.augmentations)` restores it. It runs once per object
        the `Compose` is called with, and the last value set is kept.

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

    !!! info "Use it inside a Compose"
        With an absolute path (starting with `bpy.`) it doesn't use the object passed
        by `Compose`, but it still belongs in one: it runs with the rest of the list,
        and `State(fields=compose.augmentations)` restores it.

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

    !!! info "Use it inside a Compose"
        With an absolute path (starting with `bpy.`) it doesn't use the object passed
        by `Compose`, but it still belongs in one: it runs with the rest of the list,
        and `State(fields=compose.augmentations)` restores it.

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
