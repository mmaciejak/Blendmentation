from .. import require_bpy

require_bpy()

# the public names, so `from blendmentation import augmentations` is enough; the module itself
# stays importable as `from blendmentation.augmentations import augmentations`
from .augmentations import *  # noqa: E402, F403
from .augmentations import __all__ as __all__  # noqa: E402
