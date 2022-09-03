import bpy_states as bpy_s


class State:
    """Saves and restors states of blender objects properties for reverting after augmentation

    Args:
        objects (list) : list of bpy objects to save the state from
    """

    def __init__(self, objects):
        self.state_dict = {}
        self.objects = objects
        for object in objects:
            self.state_dict[f"{object.name}"].append(bpy_s.create_state_dict(object))

    def restore(self):
        """Restores transforms and values from previously saved state_dict"""

        for object in self.objects:
            bpy_s.load_from_state_dict(object, self.state_dict)

    def clear(self):
        """Clears the state dict"""

        self.state_dict = {}
