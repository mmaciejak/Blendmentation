# Changelog

All notable changes to Blendmentation. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

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

[Unreleased]: https://github.com/mmaciejak/Blendmentation/compare/v0.5.0...dev
[0.5.0]: https://github.com/mmaciejak/Blendmentation/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/mmaciejak/Blendmentation/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/mmaciejak/Blendmentation/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/mmaciejak/Blendmentation/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/mmaciejak/Blendmentation/releases/tag/v0.1.0
