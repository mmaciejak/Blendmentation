# Changelog

All notable changes to Blendmentation. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- `augmentations.OneOf(augmentations, weights=None, p=1.0)` applies one augmentation from a
  list, picked at random by weight, again for every object in a `Compose`. Its `actual` is
  the picked index and `results` the index per object. A `OneOf` can contain another `OneOf`.
  `Compose` clears the `results` of the augmentations inside it, and
  `state.State(fields=compose.augmentations)` saves the data paths of the `Number`, `Vector`,
  `Boolean` and `Menu` inside it.

## [0.7.3] - 2026-10-08

### Added

- `generating.Instances(parent, of=None)` labels the instances that geometry nodes make on
  `parent` (Instance on Points with Object Info or Collection Info, no Realize Instances
  needed): put it in a class's list in `classes`, and every top-level instance becomes an
  instance of the class, with its own box (`BBox`, all skip settings included), pose
  (`Pose`) and masks (`Segmentation`, full masks too). `of` picks the instanced objects
  (an object, a list or a collection). In the labels, an instance's objects are named
  `<parent>/<instance index>/<object>`. Its masks come from a Cycles id render, whatever the
  engine. Geometry nodes instances block `Keypoints` even when the object they instance
  is hidden. `of` also finds instances that geometry nodes turned into copies of the
  object's mesh (Object Info without As Instance, Smooth by Angle after the instancing), and
  raises when it finds none because the instances are meshes of no object.

### Changed

- Mask files have the class in their name: instance masks are
  `<index>_mask_<class>_<n>.png` (was `<index>_mask_<n>.png`), full masks
  `<index>_mask_<class>_<n>_full.png`, and class masks `<index>_class_<class>.png` (was
  `<index>_mask_<class>.png`). The label JSON lists every mask file as before, and the
  exports read them from it.

### Fixed

- Objects in a collection excluded from the view layer or disabled in renders are treated
  as hidden in the labels, like objects hidden with `Visibility`: `BBox` gives them no box
  (and they don't make `max_truncation` skip the datapoint), `Pose` no pose, they don't
  block `Keypoints` and a keypoint on one is not visible.

## [0.7.2] - 2026-10-07

### Changed

- The README and the docs home page explain the approach: the variation lives in your
  Blender scene, in shader and geometry nodes, and Blendmentation changes their values.
  Their quick start is a short example.
- The step-by-step examples and the full example moved to their own
  [Quick start](https://blendmentation.docs.csmx.eu/quick-start/) page, which starts with
  installing in Blender.

## [0.7.1] - 2026-10-07

### Changed

- The README's quick start is shorter: the chained transform setup and the full example.
  The step-by-step setups (a material with shader nodes, an HDRI world) are in the
  [docs](https://blendmentation.docs.csmx.eu/#quick-start).

## [0.7.0] - 2026-10-07

### Added

- `augmentations.Seed(node_group)`: gives every unlinked Seed input in a node group
  (Distribute Points, Random Value, Hash Value..., nested groups included) its own random
  value on every call, e.g. `Seed(bpy.data.node_groups["Geometry Nodes"])` for a new
  scatter in every image. Takes `p` and `otherwise` (one seed for all of them).
- `state.State` takes node groups in `objects` and restores their nodes, e.g. the seeds
  `Seed` changes.
- `augmentations.PlaceOn(surface, margin=0)`: moves the object up or down along world Z
  until its lowest point rests `margin` above the surface, where they overlap seen from
  above. Like `KeepAbove`, which only lifts, but it also lowers floating objects, e.g.
  one tipped over around an origin that is not at its bottom.
- Stepped values in `Translation`, `Rotation` and `Scale`: an axis can be
  `(low, high, step)`, which picks one of `low`, `low + step`, ... up to `high`, e.g.
  `Rotation(z=(0, 270, 90))` for 0, 90, 180 or 270 degrees. An invalid axis (low above
  high, a step of 0 or less) raises a `ValueError` when the augmentation is created.
- `otherwise` on `Number`, `Vector`, `Boolean`, `Menu`, `Visibility`, `FocalLength` and
  `DepthOfField`: the value to set when the augmentation doesn't run because of its `p`,
  e.g. `Number(path, (0.5, 1), p=0.2, otherwise=0)`, `FocalLength((24, 85), p=0.3,
  otherwise=50)` or `DepthOfField(target, p=0.5, otherwise=False)` (depth of field off).
  None keeps the value, the default except for `Boolean` (False) and `Visibility`
  (hidden), which work as before. `Boolean(..., otherwise=None)` and
  `Visibility(..., otherwise=None)` leave the value unchanged instead.
- `state.State` takes a World (or another datablock with a node tree, such as a material)
  in `objects`: it saves and restores its node tree, and relative paths in `fields`
  resolve on it. Pass the world to a `Compose` to augment it with relative data paths,
  e.g. `world_aug([bpy.context.scene.world])`.

### Changed

- `state.State` restores the whole node tree of materials (and of worlds), not only the
  unlinked input values: node settings such as a Math node's operation or muting, the
  values of Value, RGB and Vector nodes, color ramp stops, curve points, images, and
  the nodes inside node groups. Augmentations of these no longer need `fields`.

## [0.6.0] - 2026-10-07

### Added

- `generating.Pose(classes)`: the 6D pose of every instance relative to the camera, `R`
  and `t` in OpenCV camera axes, plus the object's `scale`. The label gets `"poses"`.
- `full_masks=True` on `generating.Segmentation`: also saves every instance's full mask,
  its whole silhouette ignoring occlusion, as `<index>_mask_<n>_full.png`. The label
  gets `"full_masks"`. Instances that don't overlap share one extra render.
- `export.bop()`: writes the dataset as a BOP scene for 6D pose estimation:
  `scene_gt.json`, `scene_camera.json`, `rgb/`, and with masks `mask/`, `mask_visib/`
  and `scene_gt_info.json`, with a `Depth` pass `depth/` (16-bit, mm).
- `CameraData` saves `unit_scale`, the scene's meters per Blender unit.
- `bbox_from="mask"` on `export.yolo` and `export.voc`, as on `export.coco`: boxes of the
  visible pixels of each instance mask instead of the `BBox` boxes around the whole
  object. Needs `Segmentation` with `per="instance"` or `"both"`.

### Changed

- `generating.Segmentation` in Cycles makes the masks from the Object Index pass of
  the beauty render instead of a separate Workbench render, so they match the image:
  alpha-clipped materials (leaf cards, decals, fences) and shader displacement are
  right, and there is no extra render. Volumes get no mask. With EEVEE and Workbench
  it still uses the Workbench render. In Cycles, a `Passes(["ObjectIndex"])` step next
  to `Segmentation` now holds the instance ids instead of the objects' pass indices.

### Fixed

- Several `generating.Segmentation` steps in one `Compose` overwrote each other's masks:
  instance numbers now continue across the steps, and a class mask name that is already
  taken (the same class in two steps, or class names that differ only in characters
  not allowed in file names) gets `_2`, `_3`, ...

## [0.5.0] - 2026-10-07

### Added

- `generating.Background`: puts a random background behind a transparent render, in the
  image and the preview images. Modes: uniform color, white noise, color noise and
  images from a folder, mixed by `weights`, with `noise_size=(min, max)`. Needs
  Film > Transparent and RGBA output. The label gets `"background"`.
- `results` on every augmentation: what it did to each object of the last `Compose`
  call, as `{object name: value}` (`(x, y, z)` for `Translation`, `Rotation` and
  `Scale`). `actual` and `applied` only hold the last object of a `Compose` call, which
  is now documented. Save it in the label with
  `generator({"lift": keep_above.results})`.

## [0.4.0] - 2026-10-06

### Added

- `augmentations.Visibility`: shows or hides an object in the render. Bboxes, masks,
  occlusion and keypoint visibility follow it.
- `augmentations.KeepAbove`: lifts an object until it rests above a surface, also on
  uneven surfaces.

### Fixed

- `State` failed on materials with an AOV Output node in Blender 4.0, where the AOV
  name shadows the node name.

## [0.3.0] - 2026-10-06

### Added

- `BBox(max_occlusion=...)`: skips a datapoint when objects in no class hide too much of
  an instance.
- `skip_empty` for `Segmentation`, `AOVToImage` and `Passes`: empty images aren't
  written, their file in the label is `None`.
- A `Compose` call can override its probability: `aug(objects, p=0.5)`.
- An agent skill for using the library through Blender MCP (`skills/blendmentation`).

### Changed

- The docs moved to https://blendmentation.docs.csmx.eu/.
- `main` holds the latest release, development happens on `dev`.

## [0.2.0] - 2026-10-05

### Added

- `BBox(max_truncation=...)`: skips a datapoint when an instance's box is too far out of
  frame.
- Per-class skip settings: a class can be `{"instances": [...], "iou_deconflict": ...,
  "max_truncation": ...}`, which win over the `BBox` arguments.
- `CameraData` saves the depth of field settings.
- A documentation site, built from `docs/` and the docstrings.

### Changed

- `Number` and `Vector` take a `value_range` (now required), the `percentage` argument
  was removed.

## [0.1.0] - 2026-10-05

### Added

- First release.
- Augmentations: `Compose`, `Translation`, `Rotation`, `Scale`, `LookAt`, `FocalLength`,
  `DepthOfField`, `Material`, and `Number`, `Vector`, `Boolean`, `Menu` for any value
  by data path. Every augmentation takes a probability `p`.
- `State` to save and restore the scene after every datapoint.
- Generating steps: `Render`, `BBox` with `iou_deconflict`, `Segmentation`,
  `AOVToImage`, `Passes`, `RotationMatrix`, `OutputField`, `CameraData`, `Keypoints`, and
  the preview images `BBoxImage` and `SegmentationImage`.
- Export to COCO, YOLO and Pascal VOC.
- Runs inside Blender and with `bpy` as a Python module.

[Unreleased]: https://github.com/mmaciejak/Blendmentation/compare/v0.7.3...dev
[0.7.3]: https://github.com/mmaciejak/Blendmentation/compare/v0.7.2...v0.7.3
[0.7.2]: https://github.com/mmaciejak/Blendmentation/compare/v0.7.1...v0.7.2
[0.7.1]: https://github.com/mmaciejak/Blendmentation/compare/v0.7.0...v0.7.1
[0.7.0]: https://github.com/mmaciejak/Blendmentation/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/mmaciejak/Blendmentation/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/mmaciejak/Blendmentation/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/mmaciejak/Blendmentation/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/mmaciejak/Blendmentation/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/mmaciejak/Blendmentation/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/mmaciejak/Blendmentation/releases/tag/v0.1.0
