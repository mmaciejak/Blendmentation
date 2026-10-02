# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Blendmentation is a Python library for generating synthetic, augmented training datasets (renders + bounding-box labels) from Blender scenes. It is modelled after torchvision/albumentations-style transforms: you compose augmentations, apply them to Blender objects, render, then restore the scene and repeat.

## Running

There is no build system, packaging, dependency manifest, linter config, or test suite. The code depends on `bpy`, `mathutils`, `numpy` and `OpenImageIO` and is meant to run inside Blender's bundled Python (e.g. Blender's scripting tab, or `blender --background scene.blend --python example.py`). `example.py` shows the intended usage and adds the repo path to `sys.path` manually (currently a hardcoded Windows path, `E:\blendmentation`). Import the modules as a package (`from blendmentation.augmentations import augmentations`), because they import their `bpy_*` sibling relatively.

To check changes, build a scene in a script and run it headless, e.g. `"/Applications/Blender 4.app/Contents/MacOS/Blender" -b --factory-startup --python-exit-code 1 --python test.py`. On this machine `Blender 4.app` is Blender 5.2 and `Blender.app` is 4.0. The code supports both, so test on both.

## Architecture

Each subpackage under `blendmentation/` is split into two layers:

- **Public API module** (`augmentations.py`, `generating.py`, `state.py`): user-facing classes that only hold configuration and delegate the actual work.
- **`bpy_*` module** (`bpy_augmentations.py`, `bpy_generating.py`, `bpy_states.py`): plain functions that do the real Blender (`bpy`) manipulation. The public classes call these via a module alias (`bpy_a`, `bpy_g`, `bpy_s`).

Keep this split: Blender-specific logic goes in the `bpy_*` modules, and the public classes stay thin. `blendmentation/bpy_paths.py` is shared by augmentations and state. It parses data paths like `bpy.data.materials["Mat"].node_tree.nodes["X"].inputs[2].default_value` into attribute/item steps without `eval`, then gets or sets the value and calls `update_tag()` on the owning datablock. Paths starting with `bpy.` are absolute; any other path is relative to an object.

The three subpackages:

- **augmentations**: `Compose` applies a list of transforms to each object in a list. Every transform is a callable taking a single `bpy` object, and it **mutates in place**.
  - `Translation`, `Rotation`, `Scale`: each parameter is a number `v`, sampled from (-v, v), or a `(low, high)` pair. Translation is in blender units and rotation in degrees, both added; scale is in percent, multiplied. The sampled values are stored in `actual_x/y/z`.
  - `Material(material_id, hue, saturation, value, roughness, metallic)`: sets the Principled BSDF Base Color (through HSV), Roughness and Metallic to random values in absolute `(min, max)` ranges, all 0-1. `None` leaves a value unchanged. It changes the **material**, so every object using it is affected.
  - Data-path augmentations change any value by its path (shader node inputs, geometry nodes inputs, shape keys, light settings…). An absolute path ignores the object, so it can be called as `aug()`. Inside a `Compose` it runs once per object, which compounds `percent` on absolute paths.
    - `Number(data_path, value_range=None, percent=None)`: int or float. `value_range` sets an absolute value, `percent` scales the current one, and ints are rounded.
    - `Vector(data_path, value_range=None, percent=None)`: the same per component. Each bound is a number for all components or a per-component sequence, and `None` keeps that component, e.g. `((0, 0, 0, None), (1, 1, 1, None))` for RGB that keeps alpha.
    - `Boolean(data_path, probability=0.5)`.
    - `Menu(data_path, options=None, weights=None)`: enum. With no `options`, it takes them from RNA `enum_items`, or for menu sockets from the Menu Switch node's `enum_items`; otherwise `options` is required.
- **state**: `State(objects, fields=())` snapshots, at construction, each object's transforms and all unlinked node input values of its materials (stored as `state_dict[object.name]`). It also saves the value at every data path in `fields` (`Number`/`Vector`/`Boolean`/`Menu` instances or path strings). Entries without a `data_path` are ignored, so a whole `Compose.augmentations` list can be passed. Absolute paths are saved once and relative paths once per object they resolve on. `restore()` reverts everything, which is why augmentations can safely mutate in place. A data-path augmentation that isn't passed to `State` is not restored, and with `percent` it compounds every iteration. A new augmentation must touch only what this snapshot covers, or `bpy_states.py` must be extended.
- **generating**: `Generator` renders the scene camera to `<path>/<index>.png` with a matching `<index>.json` label. The index is the next free number in the folder, so output can be resumed. `objects` entries are objects or sublists of objects. Each sublist is one label ("group"), and its bbox is the union of its members. Labels hold, per group, `"objects": [names]` and:
  - a pixel bbox `[x_min, y_min, x_max, y_max]` with a top-left origin, computed from the evaluated mesh vertices (modifiers included; occlusion ignored; `None` when the object is out of frame);
  - optionally, the 3x3 rotation relative to the camera (for a group, its first object's);
  - with `segmentation=True`, `"mask": "<index>_mask_<n>.png"`, where `n` is the position in `objects`;
  - any `custom_dict` keys.

  `objects` defaults to all render-visible meshes, which includes floors and backgrounds, so pass the objects to label explicitly. `iou_deconflict` skips the render (and `generate()` returns `False`) if any two bboxes overlap above that IoU. Render resolution and output settings are restored after each render. `preview()` renders at a reduced resolution.

  Segmentation (`render_ids` / `save_masks`) is a second render with Workbench:
  - flat `OBJECT` color shading, no anti-aliasing, transparent film, compositor off;
  - every group colored with its id encoded as `R = id % 256 / 255`, `G = id // 256 / 255`, saved as a float EXR and read back exactly;
  - all other objects (all of `bpy.data.objects`, to cover instanced collections) black, so they still occlude.

  Masks hold only visible pixels; bboxes are geometric and ignore occlusion. All changed scene/shading/color-management settings and object colors are restored afterwards.

  `aovs=[...]` also saves shader AOVs (they must exist in the view layer, and the engine must be Cycles or EEVEE) as `<index>_<aov>.exr`, or `.png` with `aov_format="PNG"`. The label lists them under `"aovs"`. This avoids the compositor: after the normal PNG `write_still`, the Render Result is saved via `save_render` as a multilayer EXR to a temp dir. OpenImageIO then splits out the channels named `<view layer>.<aov>.<channel>`, searching every EXR part (5.x writes one part per pass, 4.0 one part total).

Intended loop (see `example.py`): build `State` once → for N datapoints: apply `Compose` transforms → `Generator.generate()` → `State.restore()`.

## Version differences

- Blender 5 moved geometry nodes modifier inputs from id properties to `modifier.properties.inputs.Socket_2.value`, so data paths for them differ by version. On 4.x the path is `modifiers["GeoNodes"]["Socket_2"]`; on 5.x it is `modifiers["GeoNodes"].properties.inputs.Socket_2.value`.
- Geometry nodes menu sockets don't exist in 4.0. On 5.x, modifier menu inputs list their options via RNA.
- Blender 5 needs `image_settings.media_type` set alongside `file_format` (`set_file_format` in `bpy_generating.py`).
- Blender 5.2 EEVEE bug: a VALUE AOV listed before a COLOR AOV in the view layer renders as 0 (Cycles is fine). Listing color AOVs first avoids it.
- Blender 5.2 warns that `Material.use_nodes` and light node trees are going away in Blender 6.

## Known gaps

- Type hints on `Translation`/`Rotation`/`Scale` say `range`, but the values passed are numbers or `(low, high)` tuples.
- `example.py` uses placeholder objects (`bpy.object`, `bpy.lampobject`), so it does not run as-is.
