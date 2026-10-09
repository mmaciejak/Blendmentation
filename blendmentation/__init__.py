# also a Blender add-on: Blender reads bl_info from the source and calls register() when
# the add-on is enabled. Imported as a package (scripts, bpy as a module) none of it runs.
# The version matches pyproject.toml (tests/test_addon.py checks it)
bl_info = {
    "name": "Blendmentation",
    "author": "Maciej Maciejak",
    "version": (0, 8, 1),
    "blender": (4, 0, 0),
    "location": "Node Editor > Node > Copy Blendmentation Template",
    "description": "Synthetic datasets from Blender scenes. Copies a Node augmentation with the inputs of the active node",
    "doc_url": "https://blendmentation.docs.csmx.eu/",
    "tracker_url": "https://github.com/mmaciejak/Blendmentation/issues",
    "category": "Node",
}

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


def register():
    """Registers the add-on's operator and menu entries; Blender calls it when the add-on
    is enabled."""
    from . import addon

    addon.register()


def unregister():
    from . import addon

    addon.unregister()
