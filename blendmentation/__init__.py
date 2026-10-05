# when bpy is used as a python module (pip install bpy), mathutils and the other
# blender modules can only be imported after bpy, so import it before any submodule
try:
    import bpy  # noqa: F401

    HAS_BPY = True
except ImportError:
    # export works without blender
    HAS_BPY = False


def require_bpy():
    """Raises a helpful error in the subpackages that need blender, when bpy is missing."""
    if not HAS_BPY:
        raise ImportError(
            "blendmentation needs Blender: run your script inside Blender, or install Blender as a "
            "Python module with: pip install 'blendmentation[module]' (bpy 5.1+ needs Python 3.13, "
            "bpy 4.2-5.0 Python 3.11). Only blendmentation.export works without Blender."
        )
