from . import bpy_states as bpy_s


class State:
    """Saves and restors states of blender objects properties for reverting after augmentation

    Args:
        objects (list) : list of bpy objects to save the state from
        fields (list) : Number, Vector, Boolean and Menu augmentations or data paths to save, absolute paths
            are saved once, relative paths for every object they exist on.
            Other augmentations in the list are ignored.
    """

    def __init__(self, objects, fields=()):
        self.state_dict = {}
        self.objects = objects
        for object in objects:
            self.state_dict[object.name] = bpy_s.create_state_list(object)
        # other augmentations are covered by the object state, so a whole Compose list can be passed
        data_paths = [field if isinstance(field, str) else field.data_path for field in fields
                      if isinstance(field, str) or hasattr(field, "data_path")]
        self.field_state = bpy_s.create_field_state(data_paths, objects)

    def restore(self):
        """Restores transforms and values from previously saved state_dict"""

        for object in self.objects:
            bpy_s.load_from_state_dict(object, self.state_dict)
        bpy_s.load_field_state(self.field_state)

    def clear(self):
        """Clears the state dict"""

        self.state_dict = {}
        self.field_state = []
