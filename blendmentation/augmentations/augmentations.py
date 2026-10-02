"""Augmentations, applied in place to blender objects.

Number, Vector, Boolean and Menu take a data path to the value they augment.
Paths starting with "bpy." are absolute (right click > Copy Full Data Path), e.g.
'bpy.data.materials["Mat"].node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value'.
Other paths are relative to the augmented object, e.g. 'data.shape_keys.key_blocks["Key 1"].value'.
"""

from . import bpy_augmentations as bpy_a


class Compose:
    """Compose augmentations together. Changes the object in place.
    Save the objects state before applying.

    Args:
        augmentations (list): list of augmentations to compose together
    """

    def __init__(self, augmentations):
        self.augmentations = augmentations

    def __call__(self, blender_objects):
        """Performs the augmentation on objects.

        Args:
            blender_objects (list) = list of objects to augment
        """
        for blender_object in blender_objects:
            for augmentation in self.augmentations:
                augmentation(blender_object)


class Translation:
    """Augument the object translation, in given ranges for each axis.
    For mesh objects and lamps.

    Args:
        x (range) : range of augmentation in x axis in blender units
        y (range) : range of augmentation in y axis in  blender units
        z (range) : range of augmentation in z axis in  blender units
    """

    def __init__(self, x: range=(0,0), y: range=(0,0), z: range=(0,0)):
        self.x = x
        self.y = y
        self.z = z
        self.actual_x = None
        self.actual_y = None 
        self.actual_z = None

    def __call__(self, obj):
        """Args:
        obj (bpy.object) : Object to be augmented
        """
        self.actual_x, self.actual_y, self.actual_z = bpy_a.translation(obj, self.x, self.y, self.z)


class Rotation:
    """Augument the object rotation, in given ranges for each axis.
    For mesh objects and lamps.

    Args:
        x (range) : range of augmentation in x axis in degrees
        y (range) : range of augmentation in y axis in degrees
        z (range) : range of augmentation in z axis in degrees
    """

    def __init__(self,  x: range=(0,0), y: range=(0,0), z: range=(0,0)):
        self.x = x
        self.y = y
        self.z = z
        self.actual_x = None
        self.actual_y = None
        self.actual_z = None

    def __call__(self, obj):
        """Args:
        obj (bpy.object) : Object to be augmented
        """
        self.actual_x, self.actual_y, self.actual_z = bpy_a.rotation(obj, self.x, self.y, self.z)


class Scale:
    """Augument the object scale, in given ranges for each axis.
    For mesh objects only.

    Args:
        x (range) : range of augmentation in x
        y (range) : range of augmentation in y
        z (range) : range of augmentation in z
    """

    def __init__(self,  x: range=(0,0), y: range=(0,0), z: range=(0,0)):
        self.x = x
        self.y = y
        self.z = z
        self.actual_x = None
        self.actual_y = None
        self.actual_z = None

    def __call__(self, obj):
        """Args:
        mesh obj (bpy.object.type == 'MESH') : Object to be augmented
        """
        self.actual_x, self.actual_y, self.actual_z = bpy_a.scale(obj, self.x, self.y, self.z)


class LookAt:
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

    Each is a (min, max) range, an exact number, or None to keep the current value.

    .. note::
        Pass the camera to State, it restores the transform and the lens.
    """

    def __init__(self, target, distance=None, elevation=None, azimuth=None, roll=None, focal_length=None):
        self.target = target
        self.distance = distance
        self.elevation = elevation
        self.azimuth = azimuth
        self.roll = roll
        self.focal_length = focal_length
        self.actual = None

    def __call__(self, obj):
        """Args:
        obj (bpy.object) : camera or other object to move
        """
        self.actual = bpy_a.look_at(
            obj, self.target, self.distance, self.elevation, self.azimuth, self.roll, self.focal_length
        )


class Material:
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
    """

    def __init__(
        self,
        material_id: str,
        hue: tuple = None,
        saturation: tuple = None,
        value: tuple = None,
        roughness: tuple = None,
        metallic: tuple = None,
    ):
        self.material_id = material_id
        self.hue = hue
        self.saturation = saturation
        self.value = value
        self.roughness = roughness
        self.metallic = metallic
        self.actual = None

    def __call__(self, obj):
        """Args:
        mesh obj (bpy.object.type == 'MESH') : Object to be augmented
        """
        self.actual = bpy_a.material(
            obj, self.material_id, self.hue, self.saturation, self.value, self.roughness, self.metallic
        )


class Number:
    """Augument any int or float value given by its data path: shader node inputs,
    geometry nodes inputs, shape keys, light settings etc.
    Either sets the value to a random value in value_range, or scales it by a random percent.
    Int values are rounded.

    Args:
        data_path (str) : path to the value, see the module docstring. Use [index] for
            a single vector component, e.g. 'location[2]'
        value_range (tuple) : (min, max) range to set the value in
        percent (float | tuple) : range of augmentation in percents, v = (-v, v)

    .. note::
        Give either value_range or percent. Pass it to State(fields=...)
        so the value is restored.
    """

    def __init__(self, data_path: str, value_range: tuple = None, percent=None):
        if (value_range is None) == (percent is None):
            raise ValueError("Give either value_range or percent")
        self.data_path = data_path
        self.value_range = value_range
        self.percent = percent
        self.actual = None

    def __call__(self, obj=None):
        """Args:
        obj (bpy.object) : Object relative paths start from, not needed for absolute paths
        """
        self.actual = bpy_a.number(obj, self.data_path, self.value_range, self.percent)


class Vector:
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

    .. note::
        Give either value_range or percent. Pass it to State(fields=...)
        so the value is restored.
    """

    def __init__(self, data_path: str, value_range: tuple = None, percent=None):
        if (value_range is None) == (percent is None):
            raise ValueError("Give either value_range or percent")
        self.data_path = data_path
        self.value_range = value_range
        self.percent = percent
        self.actual = None

    def __call__(self, obj=None):
        """Args:
        obj (bpy.object) : Object relative paths start from, not needed for absolute paths
        """
        self.actual = bpy_a.vector(obj, self.data_path, self.value_range, self.percent)


class Boolean:
    """Augument any boolean value given by its data path: geometry nodes switches,
    object visibility, modifier toggles etc.

    Args:
        data_path (str) : path to the value, see the module docstring
        probability (float) : probability of setting the value to True

    .. note::
        Pass it to State(fields=...) so the value is restored.
    """

    def __init__(self, data_path: str, probability: float = 0.5):
        self.data_path = data_path
        self.probability = probability
        self.actual = None

    def __call__(self, obj=None):
        """Args:
        obj (bpy.object) : Object relative paths start from, not needed for absolute paths
        """
        self.actual = bpy_a.boolean(obj, self.data_path, self.probability)


class Menu:
    """Augument any menu (enum) value given by its data path: geometry nodes menu inputs,
    node settings like the Principled BSDF distribution, light type etc.
    Sets the value to a random option.

    Args:
        data_path (str) : path to the value, see the module docstring
        options (list) : options to choose from, None = all options of the menu.
            Required when blender doesn't list the options, e.g. for menu
            sockets not on a menu switch node.
        weights (list) : relative probability of each option, None = equal

    .. note::
        Pass it to State(fields=...) so the value is restored.
    """

    def __init__(self, data_path: str, options: list = None, weights: list = None):
        if options is not None and weights is not None and len(options) != len(weights):
            raise ValueError("Give one weight per option")
        self.data_path = data_path
        self.options = options
        self.weights = weights
        self.actual = None

    def __call__(self, obj=None):
        """Args:
        obj (bpy.object) : Object relative paths start from, not needed for absolute paths
        """
        self.actual = bpy_a.menu(obj, self.data_path, self.options, self.weights)
