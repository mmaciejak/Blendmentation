# when bpy is used as a python module (pip install bpy), mathutils and the other
# blender modules can only be imported after bpy, so import it before any submodule
try:
    import bpy  # noqa: F401
except ImportError:
    # export works without blender
    pass
