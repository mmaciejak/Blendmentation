# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Blendmentation is a Python library for generating synthetic, augmented training datasets (renders, AOVs, segmentation masks and labels) from Blender scenes. It is modelled after torchvision/albumentations-style transforms: you compose augmentations, apply them to Blender objects, render, then restore the scene and repeat.

## Running

There is no build system, packaging, dependency manifest, linter config, or test suite. The code depends on `bpy`, `mathutils`, `numpy` and `OpenImageIO` and is meant to run inside Blender's bundled Python (e.g. Blender's scripting tab, or `blender --background scene.blend --python example.py`). `example.py` shows the intended usage and adds the repo path to `sys.path` manually (currently a hardcoded Windows path, `E:\blendmentation`). Import the modules as a package (`from blendmentation.augmentations import augmentations`), because they import their `bpy_*` sibling relatively.

To check changes, build a scene in a script and run it headless, e.g. `"/Applications/Blender 4.app/Contents/MacOS/Blender" -b --factory-startup --python-exit-code 1 --python test.py`. On this machine `Blender 4.app` is Blender 5.2 and `Blender.app` is 4.0. The code supports both, so test on both.

The code also runs with `bpy` as a plain Python module (`pip install bpy OpenImageIO`; `bpy` 5.1+ needs Python 3.13, 4.x–5.0 need 3.11). Two things keep that working:
- `blendmentation/__init__.py` imports `bpy` first, because `mathutils` is only importable after `bpy` in module mode.
- The `bpy` wheel ships numpy but not OpenImageIO, so `bpy_generating.py` raises an `ImportError` with install instructions when it's missing.

To test module mode: `uv venv --python 3.13 env && VIRTUAL_ENV=env uv pip install bpy==5.2.2 OpenImageIO`, then `env/bin/python test.py`.

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
- **generating**: `Compose(steps, path, resolution)` generates one datapoint per call (`preview(factor)` at reduced resolution); it returns `False` when a step skips the datapoint. Files are `<path>/<index>...`, where the index is the next free leading number in the folder (`next_index`), so output can be resumed.
  - **Steps:** `BBox(classes, iou_deconflict=None)`, `RotationMatrix(objects)`, `OutputField(name, data_path, objects=None)`, `Render(file_format="PNG")`, `AOVToImage(names, file_format="OPEN_EXR")`, `Segmentation(classes, per="instance"|"class"|"both")`. Each step takes its own objects; `Compose` has none.
  - **Classes:** `classes` is `{class name: [instances]}`, where an instance is an object or a sublist of objects labeled as one (bbox = union of members, one mask). `to_instances` flattens it to `(class, [objects])` pairs and rejects non-dicts, non-list values, empty sublists and objects in two instances.
  - **Stages:** each step class has a `stage`, and `Compose` sorts by it, so list order doesn't matter. Label steps (0) run first, so an `iou_deconflict` skip happens before any render; then `Render` (1), `AOVToImage` (2), `Segmentation` (3).
  - **`check()`:** steps define `check()`, which runs before anything is generated, so bad config (classes format, file format, missing AOV, Workbench + AOVs, relative `OutputField` without objects) fails without wasting a render.
  - **`Frame`:** `bpy_generating.generate` builds a `Frame` and passes it to each step. It holds the scene, index, the `label` dict (`frame.add(key, entries)` appends to a list, so several steps can add to it), and a lazy `beauty()` that renders with the scene engine **at most once**. `Render` writes the image through it (`write_still`), and `AOVToImage` reuses that Render Result, or triggers the one render itself when there is no `Render`. A new step that needs the beauty render must call `frame.beauty()`, not `bpy.ops.render.render()`.
  - **Label JSON:** written only when there is more than the image (e.g. `Render()` alone writes no JSON). It holds:
    - `resolution`, `image`, `aovs: {name: file}`;
    - `bboxes: [{class, objects, bbox}]`;
    - `masks: [{class, objects, mask}]`, with instance masks `<index>_mask_<n>.png` (n counts instances through all classes) first, then class masks `<index>_mask_<class>.png` (name sanitized);
    - `rotation_matrices: [{object, rotation_matrix}]`;
    - `OutputField` values: absolute paths as one value, relative paths as `{object name: value}`;
    - `custom_dict` keys.
  - **Field values:** `OutputField` converts values with `to_json`: IDs become their names, and vectors/matrices become nested lists.
  - **Bboxes** are pixel `[x_min, y_min, x_max, y_max]` with a top-left origin, computed from the evaluated mesh vertices (modifiers included; occlusion ignored; `None` when out of frame).
  - Render resolution and output settings are restored after each datapoint.

  Segmentation (`render_ids` / `save_masks`) is a separate Workbench render:
  - flat `OBJECT` color shading, no anti-aliasing, transparent film, compositor off;
  - every instance colored with its id encoded as `R = id % 256 / 255`, `G = id // 256 / 255`, saved as a float EXR and read back exactly;
  - all other objects, including ones not in `classes` (all of `bpy.data.objects`, to cover instanced collections) black, so they still occlude.

  Class masks are unions of the instance ids, so `per="both"` still needs one render. Masks hold only visible pixels; bboxes don't account for occlusion. All changed settings and object colors are restored afterwards.

  AOVs (`save_aovs`) avoid the compositor. The Render Result is saved via `save_render` as a multilayer EXR to a temp dir, and OpenImageIO splits out the channels named `<view layer>.<aov>.<channel>`, searching every EXR part (5.x writes one part per pass, 4.0 one part total). The engine must be Cycles or EEVEE.

Intended loop (see `example.py`): build `State` once → for N datapoints: apply augmentation `Compose`s → call the generating `Compose` → `State.restore()`.

## Version differences

- Blender 5 moved geometry nodes modifier inputs from id properties to `modifier.properties.inputs.Socket_2.value`, so data paths for them differ by version. On 4.x the path is `modifiers["GeoNodes"]["Socket_2"]`; on 5.x it is `modifiers["GeoNodes"].properties.inputs.Socket_2.value`.
- Geometry nodes menu sockets don't exist in 4.0. On 5.x, modifier menu inputs list their options via RNA.
- Blender 5 needs `image_settings.media_type` set alongside `file_format` (`set_file_format` in `bpy_generating.py`).
- Blender 5.2 EEVEE bug: a VALUE AOV listed before a COLOR AOV in the view layer renders as 0 (Cycles is fine). Listing color AOVs first avoids it.
- Blender 5.2 warns that `Material.use_nodes` and light node trees are going away in Blender 6.

## Known gaps

- Type hints on `Translation`/`Rotation`/`Scale` say `range`, but the values passed are numbers or `(low, high)` tuples.
- `example.py` uses placeholder objects (`bpy.object`, `bpy.lampobject`), so it does not run as-is.
