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
2. In Blender, Edit > Preferences > Add-ons: the arrow at the top right > Install from
   Disk. Pick the zip.
3. Enable "Blendmentation" in the list.

Blender copies the package to its add-ons folder, which is on `sys.path`, so scripts
import it without any setup:

```python
from blendmentation import augmentations
from blendmentation import generating
from blendmentation import state
```

It also adds a tool for writing `Node` augmentations: select a node in a node editor (a
smart material's group node, the Principled BSDF...), then Sidebar (N) > Blendmentation >
Copy Node Template > Relative or Absolute. The same two are in the Node menu, the right
click menu and F3 search as Copy Blendmentation Template (Relative / Absolute). It copies a
[`Node`][blendmentation.augmentations.augmentations.Node] with every input of the node and
the widest range each allows to the clipboard, to paste into a script and narrow down.
A comment on top says which kind of path it has:

- **Absolute** (`bpy.data.materials["Master material"].node_tree...`): it always changes
  that node, so it works with any objects passed to the `Compose`, or called alone.
- **Relative** (`active_material.node_tree...`): it starts from the object it is called
  with, so pass that object to the `Compose`; it also works for other objects set up the
  same way. The path goes from the active object, through its material, its data (a
  light) or its geometry nodes modifier; a world's node starts from the world.

```python
# Absolute paths: they always change the material "Master material", so it works with any
# objects passed to the Compose, or called alone without one.
augmentations.Node('bpy.data.materials["Master material"].node_tree.nodes["Ferrous metal"]', {
    # "Texture Coordinate": connected
    "Base metal type": augmentations.Menu(["Shiny metal", "Cast metal"]),  # menu, now "Shiny metal"
    "Rust strength": augmentations.Number((0.0, 1.0)),  # float, now 0.0
    "Paint Color": augmentations.Vector((0.0, 1.0)),    # color, now (0.0009, 0.0, 0.1729, 1.0)
    ...
})
```

In the 3D viewport, Sidebar (N) > Blendmentation > Copy Shape Keys Template > Relative
or Absolute does the same for the shape keys of the active object: a
[`Chain`][blendmentation.augmentations.augmentations.Chain] with a `Number` for every
shape key, over its slider range (0 to 1 by default). Muted and driven shape keys are
comments.

```python
# Relative paths: they start from the object it is called with, here "Face".
# Pass that object to the Compose, compose([bpy.data.objects["Face"]]), or one set up the same way.
augmentations.Chain([
    augmentations.Number('data.shape_keys.key_blocks["Smile"].value', (0.0, 1.0)),  # now 0.0
    augmentations.Number('data.shape_keys.key_blocks["Blink"].value', (0.0, 1.0)),  # now 0.25
])
```

Below it, Copy Modifier Template > Relative or Absolute does the same for the active
modifier of the active object (the one highlighted in the Modifier Properties): a
[`Modifier`][blendmentation.augmentations.augmentations.Modifier]. For a geometry nodes
modifier, it has every input by name, with the Min and Max of the input in the node
group's interface; inputs set to read an attribute are comments. For any other modifier
(Subdivision Surface, Bevel, Array...), every setting by its Python name, with the name
shown in the UI and the slider's range in the comment:

```python
augmentations.Modifier('modifiers["Subdivision"]', {
    "subdivision_type": augmentations.Menu(["CATMULL_CLARK", "SIMPLE"]),  # "Subdivision Type", menu, now "CATMULL_CLARK"
    "levels": augmentations.Number((0, 6)),                      # "Levels", int, now 1
    "render_levels": augmentations.Number((0, 6)),               # "Render Levels", int, now 2
    ...
    # "show_render": augmentations.Boolean(p=...) turns the modifier on and off in the render, now True
})
```

A geometry nodes modifier's template looks like this:

```python
# Relative paths: they start from the object it is called with, here "Floor".
# Pass that object to the Compose, compose([bpy.data.objects["Floor"]]), or one set up the same way.
augmentations.Modifier('modifiers["Scatter"]', {
    "Density": augmentations.Number((0.0, 50.0)),      # float, now 10.0
    "Mossy": augmentations.Boolean(p=0.5),             # boolean, now False
    "Ground": augmentations.Menu(["Sand", "Gravel"]),  # menu, now "Sand"
})
```

If your scripts import the module under another name (`... import augmentations as aug`),
set the prefix in the add-on's preferences, e.g. `aug.`. Without the add-on,
[`Node.template`][blendmentation.augmentations.augmentations.Node.template],
[`Modifier.template`][blendmentation.augmentations.augmentations.Modifier.template] and
[`Chain.shape_keys_template`][blendmentation.augmentations.augmentations.Chain.shape_keys_template]
return the same text.

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

from blendmentation import augmentations
from blendmentation import generating
from blendmentation import state
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
from blendmentation import augmentations
from blendmentation import generating
from blendmentation import state
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
from blendmentation import augmentations
from blendmentation import generating
from blendmentation import state

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
