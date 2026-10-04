"""Augmentations, applied in place to blender objects.

Number, Vector, Boolean and Menu take a data path to the value they augment.
Paths starting with "bpy." are absolute (right click > Copy Full Data Path), e.g.
'bpy.data.materials["Mat"].node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value'.
Other paths are relative to the augmented object, e.g. 'data.shape_keys.key_blocks["Key 1"].value'.
"""

import random

from . import bpy_augmentations as bpy_a


def check_p(p):
    if not 0 <= p <= 1:
        raise ValueError("p must be between 0 and 1")
    return p


def happens(p):
    """True with probability p. p = 1 draws no random number, so seeded runs don't change."""
    return p >= 1 or random.random() < p


class Compose:
    """Compose augmentations together. Changes the object in place.
    Save the objects state before applying.

    Args:
        augmentations (list): list of augmentations to compose together
        p (float): probability of applying the whole composition, drawn once per call
    """

    def __init__(self, augmentations, p: float = 1.0):
        self.augmentations = augmentations
        self.p = check_p(p)
        self.applied = None

    def __call__(self, blender_objects):
        """Performs the augmentation on objects.

        Args:
            blender_objects (list) = list of objects to augment
        """
        self.applied = happens(self.p)
        if not self.applied:
            return
        for blender_object in blender_objects:
            for augmentation in self.augmentations:
                augmentation(blender_object)


class Augmentation:
    """Base of the augmentations. Applies the augmentation with probability p, drawn on
    every call (so once per object in a Compose). After a call, ``applied`` says whether
    it ran; a skipped augmentation leaves the object unchanged and ``actual`` None.
    Boolean instead sets the value to False when skipped.

    Args:
        p (float): probability of applying the augmentation
    """

    def __init__(self, p: float = 1.0):
        self.p = check_p(p)
        self.applied = None
        self.actual = None

    def __call__(self, obj=None):
        """Args:
        obj (bpy.object) : Object to be augmented, not needed for absolute data paths
        """
        self.applied = happens(self.p)
        if self.applied:
            self.apply(obj)
        else:
            self.skip(obj)

    def apply(self, obj):
        raise NotImplementedError

    def skip(self, obj):
        self.actual = None


class AxisAugmentation(Augmentation):
    """Base of Translation, Rotation and Scale, sampled per axis into actual_x/y/z."""

    def __init__(self, x: range=(0,0), y: range=(0,0), z: range=(0,0), p: float = 1.0):
        super().__init__(p)
        self.x = x
        self.y = y
        self.z = z
        self.actual_x = None
        self.actual_y = None
        self.actual_z = None

    def skip(self, obj):
        self.actual_x = self.actual_y = self.actual_z = None


class Translation(AxisAugmentation):
    """Augument the object translation, in given ranges for each axis.
    For mesh objects and lamps.

    Args:
        x (range) : range of augmentation in x axis in blender units
        y (range) : range of augmentation in y axis in  blender units
        z (range) : range of augmentation in z axis in  blender units
        p (float) : probability of applying the augmentation
    """

    def apply(self, obj):
        """Args:
        obj (bpy.object) : Object to be augmented
        """
        self.actual_x, self.actual_y, self.actual_z = bpy_a.translation(obj, self.x, self.y, self.z)


class Rotation(AxisAugmentation):
    """Augument the object rotation, in given ranges for each axis.
    For mesh objects and lamps.

    Args:
        x (range) : range of augmentation in x axis in degrees
        y (range) : range of augmentation in y axis in degrees
        z (range) : range of augmentation in z axis in degrees
        p (float) : probability of applying the augmentation
    """

    def apply(self, obj):
        """Args:
        obj (bpy.object) : Object to be augmented
        """
        self.actual_x, self.actual_y, self.actual_z = bpy_a.rotation(obj, self.x, self.y, self.z)


class Scale(AxisAugmentation):
    """Augument the object scale, in given ranges for each axis.
    For mesh objects only.

    Args:
        x (range) : range of augmentation in x
        y (range) : range of augmentation in y
        z (range) : range of augmentation in z
        p (float) : probability of applying the augmentation
    """

    def apply(self, obj):
        """Args:
        mesh obj (bpy.object.type == 'MESH') : Object to be augmented
        """
        self.actual_x, self.actual_y, self.actual_z = bpy_a.scale(obj, self.x, self.y, self.z)


class LookAt(Augmentation):
    """Moves a camera (or a light, or any object) on a sphere around a target and points
    it at the target, upright. The target stays in the center of the camera view.

    Args:
        target : object, list of objects (the center of their bounding boxes),
            or point (x, y, z)
        distance (tuple) : distance from the target in blender units
        elevation (tuple) : angle above the target's horizontal plane in degrees,
            avoid exactly 90 / -90
        azimuth (tuple) : angle around the world Z axis in degrees, 0 = +X
        roll (tuple) : rotation around the camera's local Z axis (the view axis) in degrees,
            like a local Z rotation in blender, None = upright
        focal_length (tuple) : camera lens in mm, cameras only
        p (float) : probability of applying the augmentation

    Each is a (min, max) range, an exact number, or None to keep the current value.

    .. note::
        Pass the camera to State, it restores the transform and the lens.
    """

    def __init__(self, target, distance=None, elevation=None, azimuth=None, roll=None, focal_length=None,
                 p: float = 1.0):
        super().__init__(p)
        self.target = target
        self.distance = distance
        self.elevation = elevation
        self.azimuth = azimuth
        self.roll = roll
        self.focal_length = focal_length

    def apply(self, obj):
        """Args:
        obj (bpy.object) : camera or other object to move
        """
        self.actual = bpy_a.look_at(
            obj, self.target, self.distance, self.elevation, self.azimuth, self.roll, self.focal_length
        )


class FocalLength(Augmentation):
    """Augument the camera focal length. Optionally moves the camera along the line to a
    target by the same ratio as the lens (a dolly zoom), so the target keeps its size in
    the image. That is exact for objects at the target's depth when the target is in the
    center of the view, e.g. after LookAt; an off-center target also moves in the image.
    For perspective cameras.

    Args:
        focal_length (tuple) : lens in mm, (min, max) or an exact number
        target : object, list of objects (the center of their bounding boxes),
            or point (x, y, z), needed for keep_size
        keep_size (bool) : move the camera so the target keeps its size in the image
        p (float) : probability of applying the augmentation
    """

    def __init__(self, focal_length, target=None, keep_size: bool = False, p: float = 1.0):
        super().__init__(p)
        if keep_size and target is None:
            raise ValueError("keep_size needs a target")
        self.focal_length = focal_length
        self.target = target
        self.keep_size = keep_size

    def apply(self, obj):
        """Args:
        camera obj (bpy.object.type == 'CAMERA') : camera to augment
        """
        self.actual = bpy_a.focal_length(obj, self.focal_length, self.target, self.keep_size)


class DepthOfField(Augmentation):
    """Augument the camera depth of field: enable it and focus on a target with a random f-stop.
    The focus distance is measured from where the camera is when this runs, so put it
    after LookAt / FocalLength in a Compose.

    Args:
        target : object, list of objects (the center of their bounding boxes),
            or point (x, y, z) to focus on, None keeps the current focus
        f_stop (tuple) : aperture f-stop, lower is more blur, (min, max), an exact
            number, or None to keep the current one
        p (float) : probability of applying the augmentation, when skipped the depth of
            field settings are left unchanged
    """

    def __init__(self, target=None, f_stop=None, p: float = 1.0):
        super().__init__(p)
        self.target = target
        self.f_stop = f_stop

    def apply(self, obj):
        """Args:
        camera obj (bpy.object.type == 'CAMERA') : camera to augment
        """
        self.actual = bpy_a.depth_of_field(obj, self.target, self.f_stop)


class Material(Augmentation):
    """Augument the basic material values: base color, roughness and metallic,
    set to random values in given (min, max) ranges. None leaves the value unchanged.
    For mesh objects only with principle shader and unconnected sockets
    for the augmented values. Changes the material, so all objects using it are affected.

    Args:
        material_id (str) : name of material to augment
        hue (tuple) : range of base color hue, 0-1
        saturation (tuple) : range of base color saturation, 0-1
        value (tuple) : range of base color value, 0-1
        roughness (tuple) : range of roughness, 0-1
        metallic (tuple) : range of metallic, 0-1
        p (float) : probability of applying the augmentation
    """

    def __init__(
        self,
        material_id: str,
        hue: tuple = None,
        saturation: tuple = None,
        value: tuple = None,
        roughness: tuple = None,
        metallic: tuple = None,
        p: float = 1.0,
    ):
        super().__init__(p)
        self.material_id = material_id
        self.hue = hue
        self.saturation = saturation
        self.value = value
        self.roughness = roughness
        self.metallic = metallic

    def apply(self, obj):
        """Args:
        mesh obj (bpy.object.type == 'MESH') : Object to be augmented
        """
        self.actual = bpy_a.material(
            obj, self.material_id, self.hue, self.saturation, self.value, self.roughness, self.metallic
        )


class Number(Augmentation):
    """Augument any int or float value given by its data path: shader node inputs,
    geometry nodes inputs, shape keys, light settings etc.
    Either sets the value to a random value in value_range, or scales it by a random percent.
    Int values are rounded.

    Args:
        data_path (str) : path to the value, see the module docstring. Use [index] for
            a single vector component, e.g. 'location[2]'
        value_range (tuple) : (min, max) range to set the value in
        percent (float | tuple) : range of augmentation in percents, v = (-v, v)
        p (float) : probability of applying the augmentation

    .. note::
        Give either value_range or percent. Pass it to State(fields=...)
        so the value is restored.
    """

    def __init__(self, data_path: str, value_range: tuple = None, percent=None, p: float = 1.0):
        super().__init__(p)
        if (value_range is None) == (percent is None):
            raise ValueError("Give either value_range or percent")
        self.data_path = data_path
        self.value_range = value_range
        self.percent = percent

    def apply(self, obj):
        """Args:
        obj (bpy.object) : Object relative paths start from, not needed for absolute paths
        """
        self.actual = bpy_a.number(obj, self.data_path, self.value_range, self.percent)


class Vector(Augmentation):
    """Augument any vector value given by its data path: locations, colors,
    vector node inputs etc. Every component is augmented independently.
    Either sets the components to random values in value_range, or scales them by random percents.

    Args:
        data_path (str) : path to the value, see the module docstring
        value_range (tuple) : (min, max), each a number for all components or a
            sequence with a value per component, e.g. ((0, 0, 0, None), (1, 1, 1, None))
            for a random RGB color that keeps alpha. None keeps the component.
        percent (float | tuple) : v = (-v, v) for all components, or (low, high)
            with numbers or per component sequences like value_range
        p (float) : probability of applying the augmentation

    .. note::
        Give either value_range or percent. Pass it to State(fields=...)
        so the value is restored.
    """

    def __init__(self, data_path: str, value_range: tuple = None, percent=None, p: float = 1.0):
        super().__init__(p)
        if (value_range is None) == (percent is None):
            raise ValueError("Give either value_range or percent")
        self.data_path = data_path
        self.value_range = value_range
        self.percent = percent

    def apply(self, obj):
        """Args:
        obj (bpy.object) : Object relative paths start from, not needed for absolute paths
        """
        self.actual = bpy_a.vector(obj, self.data_path, self.value_range, self.percent)


class Boolean(Augmentation):
    """Augument any boolean value given by its data path: geometry nodes switches,
    object visibility, modifier toggles etc.

    Args:
        data_path (str) : path to the value, see the module docstring
        p (float) : probability of setting the value to True, otherwise it is set to False

    .. note::
        Pass it to State(fields=...) so the value is restored.
    """

    def __init__(self, data_path: str, p: float = 0.5):
        super().__init__(p)
        self.data_path = data_path

    def apply(self, obj):
        """Args:
        obj (bpy.object) : Object relative paths start from, not needed for absolute paths
        """
        self.actual = bpy_a.boolean(obj, self.data_path, True)

    def skip(self, obj):
        self.actual = bpy_a.boolean(obj, self.data_path, False)


class Menu(Augmentation):
    """Augument any menu (enum) value given by its data path: geometry nodes menu inputs,
    node settings like the Principled BSDF distribution, light type etc.
    Sets the value to a random option.

    Args:
        data_path (str) : path to the value, see the module docstring
        options (list) : options to choose from, None = all options of the menu.
            Required when blender doesn't list the options, e.g. for menu
            sockets not on a menu switch node.
        weights (list) : relative probability of each option, None = equal
        p (float) : probability of applying the augmentation

    .. note::
        Pass it to State(fields=...) so the value is restored.
    """

    def __init__(self, data_path: str, options: list = None, weights: list = None, p: float = 1.0):
        super().__init__(p)
        if options is not None and weights is not None and len(options) != len(weights):
            raise ValueError("Give one weight per option")
        self.data_path = data_path
        self.options = options
        self.weights = weights

    def apply(self, obj):
        """Args:
        obj (bpy.object) : Object relative paths start from, not needed for absolute paths
        """
        self.actual = bpy_a.menu(obj, self.data_path, self.options, self.weights)
