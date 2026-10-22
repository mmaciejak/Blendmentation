# Stability

What you can rely on when you update Blendmentation, and what may change.

## Versions

From 1.0.0, versions follow [Semantic Versioning](https://semver.org/): `major.minor.patch`.

- **Patch** (1.0.**1**): bug fixes only.
- **Minor** (1.**1**.0): new features. Scripts written for an earlier 1.x keep working.
- **Major** (**2**.0.0): may break scripts. The [changelog](changelog.md) lists every
  breaking change and what to write instead.

Before a name is removed or changes meaning, it is deprecated first: it keeps working,
with a `DeprecationWarning` saying what replaces it, for at least one minor release.

Before 1.0.0 (the 0.x versions), a minor release could break scripts; the changelog
says how.

## What is covered

### The Python API

The public names are the ones in the API reference: each module's `__all__`.

| Module | Public names |
|---|---|
| [`augmentations`](augmentations.md) | `Compose`, `OneOf`, `Chain`, `Augmentation` and every augmentation class, `graph_preview` |
| [`generating`](generating.md) | `Compose`, `Instances` and every step class |
| [`state`](state.md) | `restoring`, `set` |
| [`export`](export.md) | `coco`, `yolo`, `voc`, `bop`, `augmentation_stats`, `skip_stats`, `augmentation_report` |

Import them either way, both stay:

```python
from blendmentation import augmentations
from blendmentation.augmentations import augmentations  # the same names
```

For these, the promise is:

- **Names and arguments.** The main arguments can be passed by position, as in the
  examples (`Translation(x, y, z)`, `Number(data_path, value_range)`,
  `BBox(classes)`). Everything else is a keyword argument (`p=`, `otherwise=`,
  `name=`, `data_path=`, the settings), so new options can be added without breaking
  calls. Defaults don't change in a minor release.
- **Behavior as documented**, e.g. what `p` means, that `state.restoring()` sets every
  change back, which files a step writes.
- **Results.** `results`, `actual` and `applied` keep their documented form; dicts
  returned by `describe`, `augmentation_stats` and `skip_stats` may get new keys.

Also covered: `blendmentation.__version__`.

### The files a dataset is made of

- **File names:** `<prefix><index>.png`, `<index>.json`, `<index>_mask_<class>_<n>.png`
  and the others the [generating](generating.md) steps describe.
- **The label JSON:** its keys (`version`, `resolution`, `image`, `bboxes`, `masks`,
  `full_masks`, `poses`, `camera`, `keypoints`, `aovs`, `passes`, `background`,
  `augmentations`, `bbox_image`, `segmentation_image`, and the ones you name with
  `OutputField` or pass to the generating `Compose`) and what their values mean, as the
  steps document them. New keys may be added in a minor release, so code reading labels
  should ignore keys it doesn't know. Every label starts with `"version"`, the
  Blendmentation version that wrote it.
- **The augmentation records:** the graph file (`augmentations.json`) and
  `skipped.jsonl` that `generating.Augmentations` writes, as documented there; like the
  labels, they may get new keys.
- **Units and conventions:** boxes and keypoints in pixels from the image's top-left
  corner; the camera matrix `K` with OpenCV pixel centers; poses in OpenCV camera axes;
  distances in Blender units, `camera.unit_scale` meters each.
- **Exports:** the files `export.coco`, `yolo`, `voc` and `bop` write follow their
  formats (COCO, Ultralytics YOLO, Pascal VOC, BOP), so the tools of those formats keep
  reading them.

## What is not covered

These may change in any release, with a line in the changelog:

- **Anything not in `__all__`:** the helpers in the public modules (e.g.
  `check_range`, `flatten`, `rle`), and the modules behind them (`bpy_augmentations`,
  `bpy_generating`, `bpy_paths`, `bpy_undo`, `graph`, `polygons`...). They are
  importable, but they are not the API.
- **The HTML pages** of `augmentation_report` and `graph_preview`: their look and
  layout. The functions themselves are covered.
- **The Blender add-on:** its panels and buttons, and the exact text of the templates
  it copies (`Node.template`, `Modifier.template`, `Chain.shape_keys_template`): the
  code they write keeps working, its layout and comments may change.
- **Random draws:** the same `random.seed()` gives the same datapoints within a
  version, not across versions. A new feature or fix can draw random numbers in another
  order.
- **Pixels:** renders depend on the Blender version and its engines, so images and
  masks of the same scene can differ slightly between Blender versions.
- **Supported Blender versions:** the minimum follows Blender's LTS releases. Raising it
  is announced in the changelog.

## When a label value was wrong

A fix that changes values in the labels, because they were wrong, is a bug fix, even
though datasets generated before and after differ. The changelog says what changed,
which datasets it affects and how to correct older ones, e.g. 0.12.1 moving the camera
matrix `K` by half a pixel.
