"""Randomize objects, materials and any other value in the scene, in place.

`Compose` applies a list of augmentations to every object it is called with. Each
augmentation takes `p`, the probability that it runs, drawn per object. After a
call, `applied` says whether it ran and `actual` (or `actual_x/y/z`) holds the values
it set. Save the scene with `State` first, and restore it after every datapoint.

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

#: a number v samples from (-v, v), a pair (low, high) from (low, high)
Offset = Union[float, tuple[float, float]]
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


def happens(p: float) -> bool:
    """True with probability p. p = 1 draws no random number, so seeded runs don't change."""
    return p >= 1 or random.random() < p


class Compose:
    """Applies a list of augmentations to every object it is called with, in place.

    Each augmentation runs once per object, in list order. Save the scene with `State`
    before augmenting, so it can be restored.

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
        self.applied = happens(self.p if p is None else check_p(p))
        if not self.applied:
            return
        for blender_object in blender_objects:
            for augmentation in self.augmentations:
                augmentation(blender_object)


class Augmentation:
    """Base class of the augmentations.

    It runs with probability `p`, drawn on every call, so once per object in a
    `Compose`. A skipped augmentation leaves the object unchanged. `Boolean` is the
    exception, it sets False when skipped.

    Subclasses implement `apply(obj)` and store what they sampled in `actual`.

    Args:
        p: probability of applying the augmentation, 0-1.

    Attributes:
        applied (bool | None): whether the last call applied it.
        actual (Any): the values set by the last call, None when it was skipped.
    """

    def __init__(self, p: float = 1.0):
        self.p = check_p(p)
        self.applied: Optional[bool] = None
        self.actual: Any = None

    def __call__(self, obj: Optional[Object] = None) -> None:
        """Applies the augmentation with probability `p`.

        Args:
            obj: object to augment, not needed for absolute data paths.
        """
        self.applied = happens(self.p)
        if self.applied:
            self.apply(obj)
        else:
            self.skip(obj)

    def apply(self, obj: Optional[Object]) -> None:
        raise NotImplementedError

    def skip(self, obj: Optional[Object]) -> None:
        self.actual = None


class AxisAugmentation(Augmentation):
    """Base class of `Translation`, `Rotation` and `Scale`, sampled per axis.

    Each axis is a number `v`, sampled from `(-v, v)`, or a pair `(low, high)`.

    Args:
        x: offset on the X axis.
        y: offset on the Y axis.
        z: offset on the Z axis.
        p: probability of applying the augmentation.

    Attributes:
        actual_x (float | None): value sampled for X in the last call, None when skipped.
        actual_y (float | None): value sampled for Y.
        actual_z (float | None): value sampled for Z.
    """

    def __init__(self, x: Offset = 0, y: Offset = 0, z: Offset = 0, p: float = 1.0):
        super().__init__(p)
        self.x = x
        self.y = y
        self.z = z
        self.actual_x: Optional[float] = None
        self.actual_y: Optional[float] = None
        self.actual_z: Optional[float] = None

    def skip(self, obj: Optional[Object]) -> None:
        self.actual_x = self.actual_y = self.actual_z = None


class Translation(AxisAugmentation):
    """Moves the object by a random offset per axis, in Blender units.

    Each axis is a number `v`, sampled from `(-v, v)`, or a pair `(low, high)`.
    The offset is added to the location.

    Args:
        x: offset on X in Blender units.
        y: offset on Y in Blender units.
        z: offset on Z in Blender units.
        p: probability of applying the augmentation.

    Example:
        ```python
        augmentations.Translation(x=0.5, y=0.5, z=(0, 1))
        ```
    """

    def apply(self, obj: Optional[Object]) -> None:
        """Args:
        obj (bpy.object) : Object to be augmented
        """
        self.actual_x, self.actual_y, self.actual_z = bpy_a.translation(obj, self.x, self.y, self.z)


class Rotation(AxisAugmentation):
    """Rotates the object by a random angle per axis, in degrees.

    Each axis is a number `v`, sampled from `(-v, v)`, or a pair `(low, high)`. The
    angle is added to the rotation, in euler, quaternion and axis-angle modes.

    Args:
        x: angle around X in degrees.
        y: angle around Y in degrees.
        z: angle around Z in degrees.
        p: probability of applying the augmentation.

    Example:
        ```python
        augmentations.Rotation(z=180)          # any heading
        ```
    """

    def apply(self, obj: Optional[Object]) -> None:
        """Args:
        obj (bpy.object) : Object to be augmented
        """
        self.actual_x, self.actual_y, self.actual_z = bpy_a.rotation(obj, self.x, self.y, self.z)


class Scale(AxisAugmentation):
    """Scales the object by a random percentage per axis.

    Each axis is a number `v`, sampled from `(-v, v)`, or a pair `(low, high)`, in
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

    Raises:
        ValueError: `keep_size` without a `target`.

    Example:
        ```python
        augmentations.FocalLength((24, 85), target=[car_1, car_2], keep_size=True)
        ```
    """

    def __init__(self, focal_length: RangeOrValue, target: Optional[Target] = None, keep_size: bool = False,
                 p: float = 1.0):
        super().__init__(p)
        if keep_size and target is None:
            raise ValueError("keep_size needs a target")
        self.focal_length = focal_length
        self.target = target
        self.keep_size = keep_size

    def apply(self, obj: Optional[Object]) -> None:
        """Args:
        camera obj (bpy.object.type == 'CAMERA') : camera to augment
        """
        self.actual = bpy_a.focal_length(obj, self.focal_length, self.target, self.keep_size)


class DepthOfField(Augmentation):
    """Turns on depth of field, focused on a target, with a random f-stop.

    Focus is measured along the view axis from where the camera is when this runs, so
    put it after `LookAt` / `FocalLength` in a `Compose`.

    Args:
        target: an object, a list of objects (the center of their bounding boxes), or a
            point `(x, y, z)` to focus on. None keeps the current focus.
        f_stop: aperture f-stop, a `(min, max)` range or an exact number, lower gives
            more blur. None keeps the current one.
        p: probability of applying the augmentation. When skipped, the depth of field
            settings are left unchanged, so with depth of field off in the scene only
            some images are blurred.

    Example:
        ```python
        augmentations.DepthOfField(car_1, f_stop=(1.4, 5.6), p=0.5)
        ```
    """

    def __init__(self, target: Optional[Target] = None, f_stop: Optional[RangeOrValue] = None, p: float = 1.0):
        super().__init__(p)
        self.target = target
        self.f_stop = f_stop

    def apply(self, obj: Optional[Object]) -> None:
        """Args:
        camera obj (bpy.object.type == 'CAMERA') : camera to augment
        """
        self.actual = bpy_a.depth_of_field(obj, self.target, self.f_stop)


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
            the object. Use `[index]` for one vector component, e.g. `'location[2]'`.
        value_range: `(min, max)` range of the new value.
        p: probability of applying the augmentation.

    !!! info "Use it inside a Compose"
        With an absolute path (starting with `bpy.`) it doesn't use the object passed
        by `Compose`, but it still belongs in one: it runs with the rest of the list,
        and `State(fields=compose.augmentations)` restores it. It runs once per object
        the `Compose` is called with, and the last value set is kept.

    Example:
        ```python
        augmentations.Number("data.energy", value_range=(600, 1400))
        augmentations.Number('data.shape_keys.key_blocks["Smile"].value', value_range=(0, 1))
        ```
    """

    def __init__(self, data_path: str, value_range: tuple[float, float], p: float = 1.0):
        super().__init__(p)
        self.data_path = data_path
        self.value_range = value_range

    def apply(self, obj: Optional[Object]) -> None:
        """Args:
        obj (bpy.object) : Object relative paths start from, not needed for absolute paths
        """
        self.actual = bpy_a.number(obj, self.data_path, self.value_range)


class Vector(Augmentation):
    """Sets any vector value, given by its data path, to random values per component.

    For locations, colors, vector node inputs and so on. Each bound of `value_range` is
    a number for all components, or a sequence with one value per component, where
    None keeps that component. Int components are rounded.

    Args:
        data_path: path to the value, absolute (starting with `bpy.`) or relative to
            the object.
        value_range: `(min, max)` bounds of the new values.
        p: probability of applying the augmentation.

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
        ```
    """

    def __init__(self, data_path: str, value_range: tuple[Bound, Bound], p: float = 1.0):
        super().__init__(p)
        self.data_path = data_path
        self.value_range = value_range

    def apply(self, obj: Optional[Object]) -> None:
        """Args:
        obj (bpy.object) : Object relative paths start from, not needed for absolute paths
        """
        self.actual = bpy_a.vector(obj, self.data_path, self.value_range)


class Boolean(Augmentation):
    """Sets any boolean value, given by its data path, to True with probability `p`,
    otherwise to False.

    For geometry nodes switches, object visibility, modifier toggles and so on.

    Args:
        data_path: path to the value, absolute (starting with `bpy.`) or relative to
            the object.
        p: probability of True.

    !!! info "Use it inside a Compose"
        With an absolute path (starting with `bpy.`) it doesn't use the object passed
        by `Compose`, but it still belongs in one: it runs with the rest of the list,
        and `State(fields=compose.augmentations)` restores it.

    Example:
        ```python
        augmentations.Boolean("data.use_shadow", p=0.8)
        ```
    """

    def __init__(self, data_path: str, p: float = 0.5):
        super().__init__(p)
        self.data_path = data_path

    def apply(self, obj: Optional[Object]) -> None:
        """Args:
        obj (bpy.object) : Object relative paths start from, not needed for absolute paths
        """
        self.actual = bpy_a.boolean(obj, self.data_path, True)

    def skip(self, obj: Optional[Object]) -> None:
        self.actual = bpy_a.boolean(obj, self.data_path, False)


class Menu(Augmentation):
    """Sets any menu (enum) value, given by its data path, to a random option.

    For geometry nodes menu inputs, node settings like the Principled BSDF
    distribution, the light type and so on.

    Args:
        data_path: path to the value, absolute (starting with `bpy.`) or relative to
            the object.
        options: options to choose from. None = all options of the menu; required when
            Blender doesn't list them, e.g. for menu sockets not on a Menu Switch node.
        weights: relative probability of each option. None = equal.
        p: probability of applying the augmentation.

    Raises:
        ValueError: the number of weights and options differ.

    !!! info "Use it inside a Compose"
        With an absolute path (starting with `bpy.`) it doesn't use the object passed
        by `Compose`, but it still belongs in one: it runs with the rest of the list,
        and `State(fields=compose.augmentations)` restores it.

    Example:
        ```python
        augmentations.Menu("data.type", options=["POINT", "SPOT", "AREA"], weights=[2, 1, 1])
        ```
    """

    def __init__(self, data_path: str, options: Optional[Sequence[Any]] = None,
                 weights: Optional[Sequence[float]] = None, p: float = 1.0):
        super().__init__(p)
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
