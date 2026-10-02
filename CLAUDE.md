# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Blendmentation is an early-stage Python library for generating synthetic, augmented training datasets (renders + bounding-box labels) from Blender scenes. It is modelled after torchvision/albumentations-style transforms: you compose augmentations, apply them to Blender objects, render, then restore the scene and repeat.

## Running

There is no build system, packaging, dependency manifest, linter config, or test suite. The code depends on `bpy` and is meant to run inside Blender's bundled Python (e.g. Blender's scripting tab, or `blender --background scene.blend --python example.py`). `example.py` shows the intended usage and adds the repo path to `sys.path` manually (currently a hardcoded Windows path, `E:\blendmentation`).

## Architecture

Each subpackage under `blendmentation/` is split into two layers:

- **Public API module** (`augmentations.py`, `generating.py`, `state.py`): user-facing classes that only hold configuration and delegate the actual work.
- **`bpy_*` module** (`bpy_augmentations.py`, `bpy_generating.py`, `bpy_states.py`): plain functions that do the real Blender (`bpy`) manipulation. The public classes call these via a module alias (`bpy_a`, `bpy_g`, `bpy_s`).

Keep this split: Blender-specific logic goes in the `bpy_*` modules, and the public classes stay thin.

The three subpackages:

- **augmentations**: `Compose` applies a list of transforms to each object in a list. Every transform (`Translation`, `Rotation`, `Scale`, `Color`, `Shader`, `Lamp`, `GeoNode`) is a callable taking a single `bpy` object, and it **mutates the object in place**. Parameters are randomization ranges: blender units for translation, degrees for rotation, percentages for scale, color, shader and lamp. Each transform has requirements on the object it targets (e.g. `Color`/`Shader` need a Principled BSDF with unconnected sockets; `Lamp` needs an emission shader with a blackbody node; `GeoNode` needs a Geometry Nodes modifier). These are documented in the class docstrings. The `actual_x/y/z` attributes are meant to record the random values that were actually sampled.
- **state**: `State` snapshots the augmentable properties of the objects at construction. `restore()` reverts them after each augmentation pass, which is why augmentations can safely mutate in place.
- **generating**: `Generator` renders images and optionally produces bbox labels (with optional rotation matrix and custom label fields). `iou_deconflict` skips frames whose bboxes overlap above that IoU threshold. `preview()` renders at a reduced resolution.

Intended loop (see `example.py`): build `State` once → for N datapoints: apply `Compose` transforms → `Generator.generate()` → `State.restore()`.

## Current state / known gaps

The project is a skeleton. Watch for these when implementing:

- `bpy_augmentations.py` and `bpy_generating.py` are empty, and `bpy_states.py` contains only stub signatures. The public classes already call `bpy_a.translation/rotation/scale/color/shader/lamp/geonode`, `bpy_g.render(path, resolution, bboxes, rotation_matrix, iou_deconflict, custom_dict=None)` and `bpy_s.create_state_list` / `load_from_state_dict`; implement them with those names and signatures.
- Public modules import their `bpy_*` sibling relatively (`from . import bpy_augmentations as bpy_a`), so import them as a package (`from blendmentation.augmentations import augmentations`).
- `State` stores `state_dict[object.name] = create_state_list(object)`, but `restore()` passes the whole `state_dict` to `load_from_state_dict`. The state format isn't settled yet.
- Type hints say `range`, but defaults and `example.py` use tuples or single floats. The parameter convention is not settled yet.
