import bpy_augmentations as bpy_a


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
            x (float) : range of augmentation in x axis in blender units
            y (float) : range of augmentation in y axis in  blender units
            z (float) : range of augmentation in z axis in  blender units
    """

    def __init__(self, x: float, y: float, z: float):
        self.x = x
        self.y = y
        self.z = z

    def __call__(self, obj):
        """Args:
        obj (bpy.object) : Object to be augmented
        """
        bpy_a.translation(obj, self.x, self.y, self.z)


class Rotation:
    """Augument the object rotation, in given ranges for each axis.
    For mesh objects and lamps.

        Args:
            x (float) : range of augmentation in x axis in degrees
            y (float) : range of augmentation in y axis in degrees
            z (float) : range of augmentation in z axis in degrees
    """

    def __init__(self, x: float, y: float, z: float):
        self.x = x
        self.y = y
        self.z = z

    def __call__(self, obj):
        """Args:
        obj (bpy.object) : Object to be augmented
        """
        bpy_a.rotation(obj, self.x, self.y, self.z)


class Scale:
    """Augument the object scale, in given ranges for each axis.
    For mesh objects only.

        Args:
            x (float) : range of augmentation in x axis in percents
            y (float) : range of augmentation in y axis in percents
            z (float) : range of augmentation in z axis in percents
    """

    def __init__(self, x: float, y: float, z: float):
        self.x = x
        self.y = y
        self.z = z

    def __call__(self, obj):
        """Args:
        mesh obj (bpy.object.type == 'MESH') : Object to be augmented
        """
        bpy_a.scale(obj, self.x, self.y, self.z)


class Color:
    """Augument the object color, in given ranges for H,S and V.
    For mesh objects only with principle shader and unconnected socket
    for base color.

        Args:
            material_id (str) : name of material to augment
            H (float) : range of augmentation of hue in percents
            S (float) : range of augmentation of saturation in percents
            V (float) : range of augmentation of value in percents
    """

    def __init__(self, material_id: str, h: float, s: float, v: float):
        self.material_id = material_id
        self.h = h
        self.s = s
        self.v = v

    def __call__(self, obj):
        """Args:
        mesh obj (bpy.object.type == 'MESH') : Object to be augmented
        """
        bpy_a.color(obj, self.material_id, self.h, self.s, self.v)


class Shader:
    """Augument the shader values, for specifed material in roughness and normals strength .
    For mesh objects only with principle shader and unconnected socket for roughness and normals node.

        Args:
            material_id (str) : name of material to augment
            roughness (float) : range of augmentation of roughness in percents
            normals (float) : range of augmentation of normals strength in percents
    """

    def __init__(self, material_id: str, roughness: float, normals: float):
        self.material_id = material_id
        self.roughness = roughness
        self.normals = normals

    def __call__(self, obj):
        """Args:
        mesh obj (bpy.object.type == 'MESH') : Object to be augmented
        """
        bpy_a.shader(obj, self.material_id, self.roughness, self.normals)


class Lamp:
    """Augument the lamp values, strength, size and color temperature .
    For lamp objects only with emission shader and blackbody converter connected to color.

        Args:
            strength (float) : range of augmentation of strength in percents
            size (float) : range of augmentation of size in percents
            temp (float) : range of augmentation of color temperature in percents
    """

    def __init__(self, strength: float, size: float, temp: float):
        self.strength = strength
        self.size = size
        self.temp = temp

    def __call__(self, obj):
        """Args:
        light obj (bpy.object.type == ‘LIGHT’) : Object to be augmented
        """
        bpy_a.shader(obj, self.strength, self.size, self.temp)
