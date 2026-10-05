# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Blendmentation is a Python library for generating synthetic, augmented training datasets (renders, passes, AOVs, segmentation masks and labels) from Blender scenes. It is modelled after torchvision/albumentations-style transforms: you compose augmentations, apply them to Blender objects, render, then restore the scene and repeat.

## Running

Packaging is in `pyproject.toml`; there is no linter config. The code depends on `bpy`, `mathutils`, `numpy` and `OpenImageIO` and is meant to run inside Blender's bundled Python (e.g. Blender's scripting tab, or `blender --background scene.blend --python example.py`). `example.py` shows the intended usage of every feature; its docstring lists the scene it expects. It adds its own folder to `sys.path`. Import the modules as a package (`from blendmentation.augmentations import augmentations`), because they import their `bpy_*` sibling relatively.

**Tests** are in `tests/` (pytest) and run with `bpy` as a Python module:
```sh
uv venv --python 3.13 .venv && VIRTUAL_ENV=.venv uv pip install -e ".[module,test,dev]"   # bpy 5.x
uv venv --python 3.11 .venv311 && VIRTUAL_ENV=.venv311 uv pip install "bpy==4.5.14" -e ".[module,test]"  # bpy 4.x
.venv/bin/python -m pytest                      # all
.venv/bin/python -m pytest tests/test_generating.py::test_keypoints   # one test
```
Run the suite on both. GitHub Actions (`.github/workflows/tests.yml`) runs it on Linux with Mesa's software EGL (Workbench works without a GPU) for bpy 5.x/3.13 and 4.5.14/3.11, plus a no-`bpy` job on 3.10 and 3.12. `release.yml` builds and creates a GitHub release with the wheel and sdist when a `v<version>` tag matching `pyproject.toml` is pushed. It does not publish to PyPI yet (the user decided against it for now). The `dev` extra is `fake-bpy-module` (stub-only `bpy-stubs`, safe next to the real `bpy`), so Pylance/Pyright know the `bpy` types; check typing with `npx pyright --pythonpath .venv/bin/python <files>`. `conftest.py` resets to an empty factory scene (Workbench, Cycles at 1 CPU sample) with a camera at (0, -10, 0) looking along +Y before every test. The `cube` fixture's default size is 1 (half-extent 0.5), and `renders` records the engine of every render started. Without `bpy`, the conftest still loads and only the pure tests (export) run. Workbench and EEVEE need a GPU.

There are no `bpy` wheels for 4.0/4.1, so 4.0 is only checked in the app. On this machine `Blender 4.app` is Blender 5.2 and `Blender.app` is 4.0, e.g. `"/Applications/Blender.app/Contents/MacOS/Blender" -b --factory-startup --python-exit-code 1 --python script.py`.

The code also runs with `bpy` as a plain Python module (`pip install bpy OpenImageIO`; `bpy` 5.1+ needs Python 3.13, 4.x–5.0 need 3.11). Two things keep that working:
- `blendmentation/__init__.py` imports `bpy` first (if it's installed), because `mathutils` is only importable after `bpy` in module mode. Without `bpy`, the `augmentations`, `generating` and `state` subpackages call `require_bpy()` in their `__init__`, which raises an `ImportError` explaining how to get Blender; `export` works.
- Packaging: `numpy` is the only hard dependency. Blender bundles it with pip metadata, so installing into Blender's Python downloads nothing. `bpy` and OpenImageIO are bundled too but invisible to pip, so they are in the `module` extra; a hard dependency would install a second copy into Blender. Installing into Blender with `pip --target` (the user `scripts/modules` folder) needs `--no-deps`, or it installs a numpy that shadows Blender's.
- The `bpy` wheel ships numpy but not OpenImageIO, so `bpy_generating.py` raises an `ImportError` with install instructions when it's missing.

## Examples

There are two usage examples: `example.py` and the "Quick start", which is in both the README and `docs/index.md` (keep them identical). **Every new user-facing feature (augmentation, generating step, export, option) must be added to both**, and both must still run. To check them, build a scene matching what each describes (the `example.py` docstring and the paragraph above the quick start), save it as a `.blend`, and run the example with fewer datapoints. Run it in the app and as a module. Keep the scene descriptions up to date when a feature needs something new in the scene.

## Agent skill

`skills/blendmentation/SKILL.md` is an agent skill for using the library through Blender MCP (documented in `docs/blender-mcp.md`). It covers the `sys.path` setup inside Blender, how the MCP code tool runs code (fresh `{"bpy": bpy}` namespace per call, printed output only, a time limit), batching and restoring, and has an API overview table. **Keep its pipeline example and overview in sync** when a user-facing feature is added or changed. Its `python` blocks are runnable in order (setup, header, inspect, pipeline, preview, batch, export); check them by running each from a `bpy.app.timers` callback with `exec(code, {"bpy": bpy})` in the Blender app, as the `blender-mcp` add-on does.

## Documentation

The docs site (MkDocs Material + mkdocstrings, `mkdocs.yml`) is built from `docs/` and the docstrings, and `.github/workflows/docs.yml` deploys it to GitHub Pages (https://mmaciejak.github.io/Blendmentation/) on every push to `main`. The README is only a landing page: intro, installation, quick start and links.

- `docs/<module>.md` is only a title and `::: blendmentation.<module>.<module>`, which renders the API reference; the module docstring is the page's short intro. Don't add guide text above it, it belongs in the docstrings. mkdocstrings reads the source statically, so building needs no `bpy`.
- Public docstrings are Google style: a summary line, details, `Args:` (no types; they come from the annotations), `Returns:`, `Raises:`, `Note:` and an `Example:` with a fenced code block. The label key a generating step writes goes in a `Label:` line. Internals (`apply`, `skip`, `check`, `prepare`, `stage`) are filtered out of the reference.
- **Every new user-facing feature needs** a docstring in that style, with type hints, and, if it changes the big picture, a line in the module docstring.
- Check with `NO_MKDOCS_2_WARNING=1 .venv/bin/mkdocs build --strict` (the `docs` extra). MkDocs is pinned below 2, which drops plugins.

## Architecture

Each subpackage under `blendmentation/` is split into two layers:

- **Public API module** (`augmentations.py`, `generating.py`, `state.py`): user-facing classes that only hold configuration and delegate the actual work.
- **`bpy_*` module** (`bpy_augmentations.py`, `bpy_generating.py`, `bpy_states.py`): plain functions that do the real Blender (`bpy`) manipulation. The public classes call these via a module alias (`bpy_a`, `bpy_g`, `bpy_s`).

Keep this split: Blender-specific logic goes in the `bpy_*` modules, and the public classes stay thin. `blendmentation/bpy_paths.py` is shared by augmentations and state. It parses data paths like `bpy.data.materials["Mat"].node_tree.nodes["X"].inputs[2].default_value` into attribute/item steps without `eval`, then gets or sets the value and calls `update_tag()` on the owning datablock. Paths starting with `bpy.` are absolute; any other path is relative to an object.

The subpackages:

- **augmentations**: `Compose` applies a list of transforms to each object in a list. Every transform is a callable taking a single `bpy` object, and it **mutates in place**.
  - The public classes are type-hinted with the aliases at the top of `augmentations.py` (`Offset`, `RangeOrValue`, `Target`, `Bound`); `bpy.types.Object` is imported under `TYPE_CHECKING` only, with `from __future__ import annotations`, so nothing extra is imported at runtime.
  - Every transform subclasses `Augmentation`, takes `p` (probability it runs, default 1, drawn per call, so per object in a `Compose`) and implements `apply(obj)`; `__call__` draws `p`, sets `applied`, and on a skip calls `skip()`, which sets `actual` (or `actual_x/y/z`) to `None`. `p=1` draws no random number. `Compose` also takes `p`, drawn once per call. `Boolean` overrides `skip()` to set False, so for it `p` is the probability of True.
  - `Translation`, `Rotation`, `Scale` (`AxisAugmentation`): each parameter is a number `v`, sampled from (-v, v), or a `(low, high)` pair. Translation is in blender units and rotation in degrees, both added; scale is in percent, multiplied. The sampled values are stored in `actual_x/y/z`.
  - `LookAt(target, distance, elevation, azimuth, roll, focal_length)`: places the object on a sphere around the target (object, list of objects → mean world bbox center, or point) and aims its -Z at it with Y up (`to_track_quat("-Z", "Y")`), then rolls around local Z. Each parameter is `(min, max)`, an exact number, or `None` (keep current; roll defaults to 0). It sets `matrix_world`, so parented cameras work. `focal_length` is cameras only.
  - `FocalLength(focal_length, target=None, keep_size=False)`: perspective cameras only. `keep_size` scales the camera-to-target vector by `new_lens / old_lens` (a dolly zoom). It is exact for a centered target at its center's depth.
  - `DepthOfField(target=None, f_stop=None, p=1.0)`: enables DoF and sets the f-stop; when skipped by `p`, the DoF settings are untouched. It sets `focus_distance` along the view axis to the target center (clearing `focus_object`), so it must run after `LookAt`/`FocalLength` in a `Compose`.
  - The camera augmentations report the lens and f-stop as read back from Blender (stored as float32), not the sampled double.
  - `Material(material_id, hue, saturation, value, roughness, metallic)`: sets the Principled BSDF Base Color (through HSV), Roughness and Metallic to random values in absolute `(min, max)` ranges, all 0-1. `None` leaves a value unchanged. It changes the **material**, so every object using it is affected.
  - Data-path augmentations change any value by its path (shader node inputs, geometry nodes inputs, shape keys, light settings…). An absolute path ignores the object, so it can be called as `aug()`. Inside a `Compose` it runs once per object.
    - `Number(data_path, value_range)`: int or float, set to a random value in the `(min, max)` range; ints are rounded.
    - `Vector(data_path, value_range)`: the same per component. Each bound is a number for all components or a per-component sequence, and `None` keeps that component, e.g. `((0, 0, 0, None), (1, 1, 1, None))` for RGB that keeps alpha.
    - `Boolean(data_path, p=0.5)`: True with probability `p`, otherwise False.
    - `Menu(data_path, options=None, weights=None)`: enum. With no `options`, it takes them from RNA `enum_items`, or for menu sockets from the Menu Switch node's `enum_items`; otherwise `options` is required.
- **state**: `State(objects, fields=())` snapshots, at construction, each object's transforms, all unlinked node input values of its materials, and the lens and depth of field (`use_dof`, `focus_object`, `focus_distance`, `aperture_fstop`) of cameras (stored as `state_dict[object.name]`). It also saves the value at every data path in `fields` (`Number`/`Vector`/`Boolean`/`Menu` instances or path strings). Entries without a `data_path` are ignored, so a whole `Compose.augmentations` list can be passed. Absolute paths are saved once and relative paths once per object they resolve on. `restore()` reverts everything, which is why augmentations can safely mutate in place. A data-path augmentation that isn't passed to `State` is not restored. A new augmentation must touch only what this snapshot covers, or `bpy_states.py` must be extended.
- **generating**: `Compose(steps, path, resolution)` generates one datapoint per call (`preview(factor)` at reduced resolution); it returns `False` when a step skips the datapoint. Files are `<path>/<index>...`, where the index is the next free leading number in the folder (`next_index`), so output can be resumed.
  - **Steps:** `BBox(classes, iou_deconflict=None, max_truncation=None, max_occlusion=None)`, `RotationMatrix(objects)`, `OutputField(name, data_path, objects=None)`, `CameraData()`, `Keypoints(points)`, `Render(file_format="PNG")`, `BBoxImage(file_format="PNG", line_width=2, show_class=True)`, `AOVToImage(names, file_format="OPEN_EXR", skip_empty=False)`, `Passes(names, file_format="OPEN_EXR", skip_empty=False)`, `Segmentation(classes, per="instance"|"class"|"both", skip_empty=False)`, `SegmentationImage(file_format="PNG", opacity=0.5, line_width=2, show_class=True)`. Each step takes its own objects; `Compose` has none.
  - **Classes:** `classes` is `{class name: [instances]}`, where an instance is an object or a sublist of objects labeled as one (bbox = union of members, one mask). `to_instances` flattens it to `(class, [objects])` pairs and rejects non-dicts, non-list values, empty sublists and objects in two instances. A class value can also be `{"instances": [...], "iou_deconflict": ..., "max_truncation": ..., "max_occlusion": ...}` (`split_class`); `class_settings` merges these over the `BBox` arguments, the class winning even when it sets None. Segmentation ignores them. For IoU, a pair conflicts when its IoU is over the lower of the two classes' limits (`iou_conflict`). `max_occlusion` (`occlusions`) is 1 - an instance's pixels in the normal id render / its pixels in an id render with every object outside the classes hidden (`hide_render`, restored), so only non-class objects occlude; it runs after the cheap checks and only when some class has a limit.
  - **Stages:** each step class has a `stage`, and `Compose` sorts by it, so list order doesn't matter. Label steps (0) run first, so a `BBox` skip happens before the beauty render (`max_occlusion` needs two Workbench id renders); then `Render` (1), `AOVToImage`/`Passes`/`BBoxImage` (2), `Segmentation` (3), `SegmentationImage` (4).
  - **`prepare(frame)`:** runs for all steps after the `Frame` is created and before any step runs. It is for settings that must be in place before the beauty render (e.g. `Passes` enabling `use_pass_*`). Change them with `frame.set(owner, name, value)`, which records the old value; `generate` restores it in a `finally`.
  - **`check()`:** steps define `check()`, which runs before anything is generated, so bad config (classes format, file format, missing AOV, Workbench + AOVs, relative `OutputField` without objects) fails without wasting a render.
  - **`Frame`:** `bpy_generating.generate` builds a `Frame` and passes it to each step. It holds the scene, index, the `label` dict (`frame.add(key, entries)` appends to a list, so several steps can add to it), a lazy `beauty()` that renders with the scene engine **at most once**, and a lazy `multilayer()` that saves that render once as a multilayer EXR in the frame's temp dir, returning the path and each EXR part's channel names. `Render` writes the image through it (`write_still`), and `AOVToImage` reuses that Render Result, or triggers the one render itself when there is no `Render`. A new step that needs the beauty render must call `frame.beauty()`, not `bpy.ops.render.render()`.
  - **Label JSON:** written only when there is more than the image (e.g. `Render()` alone writes no JSON). It holds:
    - `resolution`, `image`, `aovs: {name: file}`, `passes: {name: file}`;
    - `bboxes: [{class, objects, bbox}]`;
    - `masks: [{class, objects, mask, per}]`, with instance masks `<index>_mask_<n>.png` (n counts instances through all classes) first, then class masks `<index>_mask_<class>.png` (name sanitized);
    - `skip_empty=True` (`Segmentation`, `AOVToImage`, `Passes`) doesn't write an all-zero image (masks: no pixel; layers: every non-alpha channel 0, `layer_empty`) and stores `None` as its file. The datapoint is kept. Code reading these files must handle `None` (`segmentation_image` skips them, COCO export skips the instance);
    - `rotation_matrices: [{object, rotation_matrix}]`;
    - `OutputField` values: absolute paths as one value, relative paths as `{object name: value}`;
    - `custom_dict` keys.
  - **Field values:** `OutputField` converts values with `to_json`: IDs become their names, and vectors/matrices become nested lists.
  - **Bboxes** are pixel `[x_min, y_min, x_max, y_max]` with a top-left origin, computed from the evaluated mesh vertices (modifiers included; occlusion ignored; `None` when out of frame). `max_truncation` measures the fraction of the unclipped box (`view_bounds`) outside the frame; any vertex behind the camera counts as fully truncated.
  - **Passes:** `save_layer` extracts one AOV or pass from `frame.multilayer()`. Channels are `<view layer>.<layer>.<channel>`. Index passes are `IndexOB`/`IndexMA` in 4.x and `Object Index`/`Material Index` in 5.x, so `PASSES` lists both names. OpenEXR always stores channels alphabetically (`A,U,V`), so they're sorted by `CHANNEL_ORDER` for PNG output.
  - **Preview images** (`BBoxImage`, `SegmentationImage`): draw the `bboxes` / `masks` of the labels on `frame.beauty_pixels()`, a color-managed 8-bit RGBA copy of the Render Result saved once to the temp dir (each call returns a new array), to `<index>_bboxes.<ext>` / `<index>_segmentation.<ext>`, label keys `bbox_image` / `segmentation_image`. Class colors (`CLASS_COLORS`) follow first appearance in the labels. Class names use the 3x5 `FONT` in numpy, because Blender's OpenImageIO has no FreeType and `blf` can't draw to images in 4.0 / module mode. `Compose` raises if the source step (`BBox` / `Segmentation`) is missing.
  - **`replace_render()`:** The Workbench id render replaces the beauty Render Result, so `frame.ids(groups, hide_others)` calls `frame.replace_render()` before rendering. It caches the id image per groups and `hide_others`, so `Segmentation` reuses the one `BBox(max_occlusion=...)` rendered for the same classes. It caches `beauty_pixels()` when a step set `frame.keep_beauty` in `prepare` (`SegmentationImage`), and marks the beauty as not rendered. A new step with its own render must do the same.
  - **CameraData:** `intrinsics` K follows Blender's sensor fit / shift / pixel-aspect rules; `extrinsics_opencv` flips Blender's camera axes (looks down -Z, Y up) to OpenCV's. `depth_of_field` reports the effective `focus_distance` (with a focus object, the distance along the view axis to it, or to its `focus_subtarget` bone, as Blender computes it).
  - **Keypoints:** they are located on the evaluated mesh (deformations included) and projected with `world_to_camera_view`. Visibility is a `scene.ray_cast` from the camera, with a small tolerance so a point on a surface isn't hidden by that surface.
  - Render resolution and output settings are restored after each datapoint.

  Segmentation (`render_ids` / `save_masks`) is a separate Workbench render:
  - flat `OBJECT` color shading, no anti-aliasing, transparent film, compositor off;
  - every instance colored with its id encoded as `R = id % 256 / 255`, `G = id // 256 / 255`, saved as a float EXR and read back exactly;
  - all other objects, including ones not in `classes` (all of `bpy.data.objects`, to cover instanced collections) black, so they still occlude.

  Class masks are unions of the instance ids, so `per="both"` still needs one render. Masks hold only visible pixels; bboxes don't account for occlusion. All changed settings and object colors are restored afterwards.

  AOVs (`save_aovs`) avoid the compositor. The Render Result is saved via `save_render` as a multilayer EXR to a temp dir, and OpenImageIO splits out the channels named `<view layer>.<aov>.<channel>`, searching every EXR part (5.x writes one part per pass, 4.0 one part total). The engine must be Cycles or EEVEE.

- **export**: `export.coco/yolo/voc(path, ...)` turn a generated folder's `<index>.json` labels into standard formats. It is pure Python with no `bpy` layer: `blendmentation/__init__.py` imports `bpy` only if it's available, and numpy / OpenImageIO are imported lazily, only for COCO masks, so YOLO and VOC run in a plain interpreter. Only labels with `"image"` are exported. Instances are keyed by `(class, objects)`, merging `bboxes` with masks where `"per": "instance"`. COCO segmentation is uncompressed RLE (column-major runs starting with zeros, as pycocotools expects); instances with empty masks are skipped. Class ids follow first appearance unless `classes=` is given.

Intended loop (see `example.py`): build `State` once → for N datapoints: apply augmentation `Compose`s → call the generating `Compose` → `State.restore()`.

## Version differences

- Blender 5 moved geometry nodes modifier inputs from id properties to `modifier.properties.inputs.Socket_2.value`, so data paths for them differ by version. On 4.x the path is `modifiers["GeoNodes"]["Socket_2"]`; on 5.x it is `modifiers["GeoNodes"].properties.inputs.Socket_2.value`.
- Geometry nodes menu sockets don't exist in 4.0. On 5.x, modifier menu inputs list their options via RNA.
- Blender 5 needs `image_settings.media_type` set alongside `file_format` (`set_file_format` in `bpy_generating.py`).
- Blender 5.2 EEVEE bug: a VALUE AOV listed before a COLOR AOV in the view layer renders as 0 (Cycles is fine). Listing color AOVs first avoids it.
- Blender 5.2 warns that `Material.use_nodes` and light node trees are going away in Blender 6.
