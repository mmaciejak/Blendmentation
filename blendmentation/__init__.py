# when bpy is used as a python module (pip install bpy), mathutils and the other
# blender modules can only be imported after bpy, so import it before any submodule
import bpy  # noqa: F401
