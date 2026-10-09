# Installation

It is not on PyPI yet, so get it from GitHub: clone the repository, or pip install it
from there (this needs `git`). The `main` branch always holds the
[latest release](https://github.com/mmaciejak/Blendmentation/releases/latest), so both
give you that.

There are four ways to use it: install it in Blender as an add-on, point your script at
a copy of this repository, install it into Blender with pip, or use Blender as a Python
module without the app. They are the same package; inside Blender, pick one of the first
three, since with two copies your scripts import whichever Blender finds first.

## Inside Blender, as an add-on

1. Download `blendmentation-addon-<version>.zip` from the
   [latest release](https://github.com/mmaciejak/Blendmentation/releases/latest), or
   zip the `blendmentation` folder of a clone yourself. (GitHub's "Download ZIP" of the
   repository doesn't work as an add-on: it has the repository folder on top.)
2. In Blender, Edit > Preferences > Add-ons: in Blender 4.2 and newer, the arrow at the
   top right > Install from Disk; in 4.0 and 4.1, Install. Pick the zip.
3. Enable "Blendmentation" in the list.

Blender copies the package to its add-ons folder, which is on `sys.path`, so scripts
import it without any setup:

```python
from blendmentation.augmentations import augmentations
from blendmentation.generating import generating
from blendmentation.state import state
```

It also adds a tool for writing `Node` augmentations: select a node in a node editor (a
smart material's group node, the Principled BSDF...), then Node menu > Copy
Blendmentation Template, or right click > Copy Blendmentation Template, or search for
it with F3. It copies a
[`Node`][blendmentation.augmentations.augmentations.Node] with every input of the node and
the widest range each allows to the clipboard, to paste into a script and narrow down:

```python
augmentations.Node('bpy.data.materials["Master material"].node_tree.nodes["Ferrous metal"]', {
    # "Texture Coordinate": connected
    "Base metal type": augmentations.Input(options=["Shiny metal", "Cast metal"]),  # menu, now "Shiny metal"
    "Rust strength": augmentations.Input((0.0, 1.0)),  # float, now 0.0
    "Paint Color": augmentations.Input((0.0, 1.0)),    # color, now (0.0009, 0.0, 0.1729, 1.0)
    ...
})
```

If your scripts import the module under another name (`... import augmentations as aug`),
set the prefix in the add-on's preferences, e.g. `aug.`. Without the add-on,
[`Node.template`][blendmentation.augmentations.augmentations.Node.template] returns the
same text.

To update, install the new zip the same way and restart Blender.

## Inside Blender, from a copy of the repository

Clone the repository (or download and unpack it as a ZIP from GitHub):

```sh
git clone https://github.com/mmaciejak/Blendmentation
```

Then add the cloned folder, the one that contains `blendmentation/`, to `sys.path` in
your script and import the modules as a package:

```python
import sys
sys.path.append("/path/to/Blendmentation")

from blendmentation.augmentations import augmentations
from blendmentation.generating import generating
from blendmentation.state import state
```

Run the script from Blender's Scripting tab, or headless:

```sh
blender --background scene.blend --python make_dataset.py
```

## Inside Blender, with pip

Install it with Blender's own Python into your Blender user scripts folder, which
Blender adds to `sys.path`. On macOS, for Blender 4.2:

```sh
"/Applications/Blender.app/Contents/Resources/4.2/python/bin/python3.11" -m pip install \
    --no-deps --target "$HOME/Library/Application Support/Blender/4.2/scripts/modules" git+https://github.com/mmaciejak/Blendmentation
```

- **Paths:** use your Blender version in both paths. In Blender,
  `bpy.utils.user_resource("SCRIPTS", path="modules")` prints the folder for your system.
- **`--no-deps` matters:** `--target` doesn't see the numpy bundled with Blender and
  would install a second copy, which Blender would then import instead of its own.
- **Restart Blender** afterwards: it only adds the folder if it existed at startup.

Then import it in any script:

```python
from blendmentation.augmentations import augmentations
from blendmentation.generating import generating
from blendmentation.state import state
```

## As a Python module, without the Blender app

Blender is also published on PyPI as the `bpy` module. Each `bpy` release supports
only one Python version:

| `bpy` | Python |
|---|---|
| 5.1, 5.2 | 3.13 |
| 4.x, 5.0 | 3.11 |

OpenImageIO is bundled with the Blender app but not with the module. The `module` extra
installs both:

```sh
uv venv --python 3.13 .venv                 # or: python3.13 -m venv .venv
uv pip install "blendmentation[module] @ git+https://github.com/mmaciejak/Blendmentation"   # installs bpy and OpenImageIO too
```

Without the extra only `blendmentation.export` works;
the other modules raise an error saying how to get Blender. Don't use the extra inside
the Blender app, which already has both.

The package is installed, so `sys.path` isn't needed. The script is otherwise the same
as inside Blender, except that you open the `.blend` file yourself:

```python
import bpy
from blendmentation.augmentations import augmentations
from blendmentation.generating import generating
from blendmentation.state import state

bpy.ops.wm.open_mainfile(filepath="scene.blend")   # "//" output paths are relative to this file
# ... same augmentations, generating Compose and state.restoring() loop as below ...
```

```sh
.venv/bin/python make_dataset.py
```

For type hints in your editor (Blender objects in the augmentation signatures, `bpy`
completions), install the `bpy` type stubs from the `dev` extra:
`uv pip install "blendmentation[module,dev] @ git+https://github.com/mmaciejak/Blendmentation"`.

All features work the same way in the module. Workbench (used by `Segmentation` outside Cycles, and `max_occlusion`) and
EEVEE need OpenGL: a GPU, or on Linux without one, Mesa's software rendering (as in CI).
It is tested on macOS, and the test suite also runs on Linux.
