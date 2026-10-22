# Changelog

All notable changes to Blendmentation. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- `export.yolo(path, task="segment")` writes the instance masks as polygons, in the
  Ultralytics segmentation format, for YOLO segmentation models. Ultralytics draws each
  polygon back exactly as its mask; an object split into pieces by something in front
  of it stays one polygon, and holes are filled. `tolerance` simplifies the polygons
  for shorter files. It needs no OpenCV.

### Fixed

- `export.yolo` now writes `images.txt`, the list of the dataset's images, and points
  `dataset.yaml` at it. Before, it pointed at the whole folder, so Ultralytics also
  trained on the mask PNGs, previews and PNG passes there, as images without objects.
- `augmentations.PlaceOn` and `KeepAbove` left an object whose geometry nodes output
  is an instance (Instance on Points without Realize Instances, Object Info with As
  Instance, Collection Info) where it was, since only its mesh counted; an object with
  some real geometry next to its instances was placed by that geometry only. They now
  count everything the object and the surface show, collection instances too.
- `PlaceOn` and `KeepAbove` could leave an object partly inside the surface where an
  edge of the object crosses an edge of the surface away from any vertex, e.g. an
  object lying across a ridge or on a bumpy low-poly terrain (up to a third of a unit
  in our tests). The contact is now found exactly.
- `PlaceOn` and `KeepAbove` placed an object whose shape depends on where it is
  (geometry nodes reading its position) by its shape before the move. They now measure
  it again after the move until it rests.
- In Cycles, with depth of field (e.g. from `augmentations.DepthOfField`) or motion blur,
  the masks of `generating.Segmentation` had speckled edges as wide as the blur, and so
  did the boxes made from them (`bbox_from="mask"` in `export.coco`, `yolo` and `voc`,
  and `bbox_visib`, `bbox_obj` and `visib_fract` in `export.bop`). The masks, the full
  masks and the masks of `Instances` are now rendered without the blur, sharp like in
  EEVEE and Workbench, while the image keeps it. With blur, Cycles needs one extra
  1 sample render for the masks.

## [0.11.0] - 2026-10-16

### Added

- `generating.Compose(..., prefix="run1_")` starts every file of the dataset with the
  prefix: images, masks, passes, AOVs, previews, label JSONs, and the `Augmentations`
  graph and `skipped.jsonl`. The index counts only the files with the same prefix, so
  runs with different prefixes can write to one folder, or be merged into one later.
  `export.coco`, `yolo`, `voc` and `bop` read the labels of every prefix and, when there
  are prefixes, number the images in order so the ids stay unique.
  `export.augmentation_stats`, `skip_stats` and `augmentation_report` take a `prefix`
  to read one run.
- `augmentations.ShadowCatcher(p=0.5, otherwise=False)` makes an object a shadow catcher
  (Cycles) with probability `p`, and a normal object otherwise, e.g. a floor that renders
  only the shadows on it over a transparent film or a `generating.Background`.
- Shorter imports: `from blendmentation import augmentations as aug, export, generating
  as gen, state`. The longer `from blendmentation.augmentations import augmentations`
  still works. Each module's `__all__` lists its public names.
- Every label JSON and every line of `skipped.jsonl` starts with `"version"`, the
  Blendmentation version that wrote it, and `blendmentation.__version__` gives the
  installed version.

### Changed

- Blendmentation needs Blender 4.2 LTS or newer (it was 4.0), and Python 3.11 or newer
  when used as a Python module or for the export alone.
- Only the main arguments can be passed by position; the options are keyword-only, so
  new ones can be added later without breaking scripts. For example `Translation(x, y,
  z)`, `Number(data_path, value_range)`, `Menu(data_path, options, weights)`,
  `OneOf(augmentations, weights)`, `KeepAbove(surface, margin)`, `LookAt(target)`,
  `Visibility(p)`, `generating.Compose(steps, path, resolution)`, `BBox(classes)`,
  `Render(file_format)`, `export.coco(path, output)`. Write the rest as keywords:
  `Rotation(z=90, p=0.5)`, `BBox(classes, max_truncation=0.3)`,
  `export.yolo(path, bbox_from="mask")`.
- `export.voc` takes `output` instead of `output_dir`, like `coco` and `bop`. The
  `output` of `coco`, `voc` and `bop` can be a `//` path, relative to the .blend file,
  like `path`.

### Removed

- `generating.RotationMatrix` and the `rotation_matrices` label key. `generating.Pose`
  gives each instance's rotation relative to the camera (`R`, in OpenCV camera axes,
  scale removed) with its translation.

## [0.10.2] - 2026-10-15

### Changed

- The "skips" bar of `export.augmentation_report` reads like a number line: more images
  skipped when the augmentation runs is red, to the right, with a plus (e.g. +38%), and
  fewer is green, to the left, with a minus. In 0.10.1 red was on the left.

## [0.10.1] - 2026-10-15

### Added

- `generating.Augmentations` writes every datapoint `BBox` skips to `skipped.jsonl` in the
  output folder: the reason (`max_truncation`, `iou_deconflict` or `max_occlusion`), the
  class and objects, the measured value and the limit, and what each augmentation did.
- `export.skip_stats(path)` counts the skipped datapoints by reason and class, and
  `export.augmentation_stats` also counts each augmentation in them (`skipped`).
- `export.augmentation_report` shows the skipped datapoints: their number in the header
  (click it for the reasons and classes) and, on the cards of augmentations that run for
  some objects and not for others, a "skips" bar centered on 0: red to the left when
  images were skipped more often when the augmentation ran than when it didn't (e.g.
  +38%), green to the right when less often, faded when within chance, so the
  augmentations that cause skips stand out.

## [0.10.0] - 2026-10-15

### Added

- `augmentations.Compose.describe()` returns the graph of what a `Compose` can do: every
  augmentation in it, nested ones in `OneOf`, `Chain`, `Node` and `Modifier` included, with
  the probability that it is reached and that it runs per object, its `otherwise` value,
  and for a `Menu`, `MaterialSlot` or `UVMap` with its options listed, a `Boolean` or a
  `Visibility`, the probability of each value. The branches of a `OneOf` have their weight
  and share of its picks.
- `generating.Augmentations({"name": compose, ...})` records in every label what each
  augmentation did for each object (`applied`, whether its `p` let it run, and `value`,
  what it set, the `otherwise` value included), under `augmentations`, and saves the graph
  of the `Compose`s to `augmentations.json` in the output folder.
- `export.augmentation_stats(path)` compares how often each augmentation ran over a
  dataset, and how often it set each value, with the probabilities of its graph. The
  datapoints `BBox` skipped are not in the dataset, so the gap shows what they changed.
- `export.augmentation_report(path)` writes `augmentations.html`, a page that draws the
  graph as a flowchart (the steps of a `Compose` or `Chain` in a row, a `OneOf` as a fork
  to its branches with their weight and share, `Node` and `Modifier` inputs as rows of
  their card), with bars of how often each augmentation runs and sets each value,
  expected and observed, and marks the differences larger than chance. It is one
  file, opened in any browser without internet.
- `augmentations.graph_preview(composes, path, show=False)` draws the graph of augmentation
  `Compose`s on the same HTML page, before generating, with the probability of each
  augmentation and value, to check a pipeline before a long run.
- Every augmentation and `augmentations.Compose` takes an optional `name`, e.g.
  `Translation(x=1, name="slide sideways")`, shown in the graph (`describe()`), in
  `export.augmentation_stats` and as the card title in `export.augmentation_report`.

### Changed

- `results` of an augmentation that doesn't use the object it is called with (an absolute
  data path in `Number`, `Vector`, `Boolean`, `Menu`, `Node` or `Modifier`, `Seed`, or a
  `OneOf` or `Chain` of only those) holds one entry, under `None`, with the last value set.
  In a `Compose` over several objects it runs once per object on the same value, and
  `results` used to list a value per object although only the last one was kept.

### Fixed

- `augmentations.Chain`'s `actual` (and `results`) holds `(x, y, z)` for a `Translation`,
  `Rotation` or `Scale` in it, as their own `results` do. It was `None` for them.
- `augmentations.Menu`, `MaterialSlot` and `UVMap` reject negative weights. Before, they
  were accepted and picked a wrong option every time (`weights=[-1, 2]` always picked the
  second one). Weights that sum to 0, are infinite or aren't numbers now fail when the
  augmentation is created, in `OneOf` too, instead of at the first call. A `Menu` whose
  options are read from Blender says how many options it has when the number of weights
  differs.
- `augmentations.Menu(["A", "B"], [3, 1])` (inside a `Node` or `Modifier`, without a data
  path) explains that the weights must be given by name, `weights=[3, 1]`, instead of
  saying the data path must be a string.
- `generating.Background` rejects infinite weights before generating, instead of failing at
  the first datapoint.

## [0.9.6] - 2026-10-15

### Fixed

- `generating.BBox(..., allow_empty=True)` now ignores every instance out of view, not
  only when all of them are. Before, a datapoint with some instances in frame and others
  out of it (e.g. a geometry nodes scatter) was still skipped by `max_truncation`, and an
  instance partly behind the camera counted as in frame even when it couldn't be seen.
- `generating.BBox` no longer skips a datapoint for an instance with no geometry, such as
  an object whose geometry nodes output only instances (e.g. a Menu Switch between its own
  mesh and instances). It gets bbox None and never skips, like a hidden one; before,
  `max_truncation` counted it as fully out of frame.

## [0.9.5] - 2026-10-15

### Added

- `generating.BBox(..., allow_empty=True)` keeps datapoints with no instance in frame
  (negative samples), which `max_truncation` used to skip because an instance out of
  frame counts as fully truncated. `max_truncation` still skips when any instance is in
  frame.

## [0.9.4] - 2026-10-14

### Changed

- `generating.Background` no longer requires Render Properties > Film > Transparent, only
  RGBA output. The background shows where the render is transparent and an opaque
  render hides it, so a pipeline can have transparent and opaque datapoints.

## [0.9.3] - 2026-10-14

### Added

- `augmentations.UVMap(uv_maps=None, weights=None, p=1.0, otherwise=None, data_path=None)`
  makes one of a mesh's UV maps, picked at random (by index or name, optionally
  weighted), the one it renders with (and the active one), e.g. to lay a texture out at
  different scales or angles. `actual` holds the UV map's name, and `state.restoring()`
  sets the previous one back.

## [0.9.2] - 2026-10-14

### Added

- The augmentations that change an object (`Translation`, `Rotation`, `Scale`,
  `Visibility`, `KeepAbove`, `PlaceOn`, `LookAt`, `PlaceOnCurve`, `FocalLength`,
  `DepthOfField`, `MaterialSlot`, `SimpleMaterial`) take an optional `data_path`, an
  absolute path to an object, e.g. `Translation(x=0.3, data_path='bpy.data.objects["Light"]')`.
  They then change that object instead of the one they are called with, ignore the
  objects of a `Compose`, and can be called alone: `lamp_shift()`.

## [0.9.1] - 2026-10-13

### Added

- `DepthOfField(..., defocus=...)` moves the focus off the target by a random percent of
  its distance, added like a `Translation` offset: `defocus=10` focuses anywhere from 10 %
  nearer to 10 % farther, `(0, 20)` only farther, `(low, high, step)` in steps. Without a
  target it works from the scene's focus, also a Focus on Object's distance (which is then
  replaced by the distance). `actual` holds the `defocus` percent.
- `FocalLength(..., keep_size=True, size=(80, 120))`: with `keep_size`, the target gets a
  random size in the image instead of keeping it, in percent of its size before the call
  (the camera moves farther or closer). `size` takes a `(min, max)` range or an exact
  number; without it, `keep_size` keeps the size exactly as before. `actual` holds the
  `size` set.
- `augmentations.Modifier` sets the settings of any modifier, not only the inputs of a
  geometry nodes modifier: Subdivision Surface, Bevel, Array, Displace... by their Python
  name, e.g. `Modifier('modifiers["Subdivision"]', {"render_levels": Number((1, 3)),
  "subdivision_type": Menu(["CATMULL_CLARK", "SIMPLE"])})`. On a geometry nodes modifier,
  a key that is no input's name sets that setting, e.g. `"show_render"` to turn it on and
  off.
- `augmentations.Modifier.template(modifier, obj=None, prefix="augmentations.",
  relative=True)` writes a `Modifier` for a modifier: every input of a geometry nodes
  modifier by name, with the Min and Max of the input in the node group's interface, or
  every setting of another modifier by its Python name, with the name shown in the UI and
  the slider's range; `Boolean(p=0.5)`, `Menu` with every option, and the type and
  current value in a comment. Inputs set to read an attribute are comments, and a last
  comment shows how to turn the modifier on and off (`"show_render"`). The path is
  relative to the object by default, or absolute with `relative=False`. The add-on copies
  it for the active modifier of the active object from the 3D viewport's Sidebar (N) >
  Blendmentation > Copy Modifier Template > Relative or Absolute.

## [0.9.0] - 2026-10-12

### Added

- `augmentations.Chain.shape_keys_template(obj, prefix="augmentations.", relative=True)`
  writes a `Chain` with a `Number` for every shape key of an object, over its slider range
  (0 to 1 by default), with the current value in a comment; muted and driven shape keys
  are comments. The paths are relative to the object, or absolute through it with
  `relative=False`. The add-on copies it for the active object from the 3D viewport's
  Sidebar (N) > Blendmentation > Copy Shape Keys Template > Relative or Absolute.
- `Node.template` takes `relative=True`, which writes the node's path relative to `obj`
  (through its active material, another material slot, its data or a geometry nodes
  modifier; a world's node relative to the world) instead of absolute.
- The add-on has a Blendmentation tab in the node editor's sidebar (N): Copy Node Template
  > Relative (from the active object) or Absolute. The Node menu, right click menu and F3
  search have both too.

### Changed

- The inputs of a `Node` or `Modifier` are set with `Number`, `Vector`, `Boolean` and
  `Menu` written without a data path, e.g. `"Rust strength": augmentations.Number((0,
  1))`: the `Node` or `Modifier` gives each one the path of its input (for a `Node`,
  `<node path>.inputs["Rust strength"].default_value`, relative or absolute as the node's
  path is). Use `Vector` for vector, rotation and color inputs; on a color input it keeps
  alpha. An augmentation that doesn't fit the socket raises a `TypeError` naming the
  right one. `Node.template` and the add-on write inputs this way.
- The templates (`Node.template`, `Chain.shape_keys_template` and the add-on's) start
  with a comment that says whether the paths are relative or absolute, with the name of
  the object they start from or of what they change, and how to call the augmentation.

### Fixed

- `Node.template` (and the add-on's Copy Node Template) no longer writes an input that
  fails when run for a built-in node's menu input, e.g. Transform Geometry's Mode, whose
  options Python can't list. It is now a comment starting with `Warning!`, with the
  current option, to add `Menu([...])` yourself.

### Removed

- `augmentations.Input`, and the dict form of a `Node` or `Modifier` input
  (`{"value_range": ..., "p": ...}`). Set each input with `Number`, `Vector`, `Boolean`
  or `Menu` written without a data path instead, e.g. `Input((0.5, 1), p=0.2,
  otherwise=0)` becomes `Number((0.5, 1), p=0.2, otherwise=0)`, `Input(p=0.3)` becomes
  `Boolean(p=0.3)` and `Input(options=["A", "B"])` becomes `Menu(["A", "B"])`. A plain
  range or option list (`"Rust": (0, 1)`) still works.

## [0.8.2] - 2026-10-12

### Changed

- `SimpleMaterial` also takes an exact value for `hue`, `saturation`, `value`,
  `roughness` and `metallic`, e.g. `metallic=1`, next to a `(min, max)` range.
- `generating.Compose` raises a `TypeError` naming the item when `steps` holds something
  that is not a generating step, e.g. an augmentation.
- `export.coco(bbox_from="mask")` with an instance that has no instance mask raises the
  same error as `yolo` and `voc`, which says to add `Segmentation(classes, per="instance")`.

### Fixed

- A `Keypoints` point and the `target` of `LookAt`, `FocalLength` and `DepthOfField` no
  longer accept booleans as coordinates: `(True, 0, 1)` raises a `TypeError` instead of
  being taken as the point (1, 0, 1).

## [0.8.1] - 2026-10-09

### Added

- `augmentations.PlaceOnCurve(curve, align=False, position=(0, 1), p=1.0)` moves a camera
  (or any object) to a random point on a curve object, e.g. a camera path, picked evenly
  along its length. By default the object keeps its rotation; with `align=True` it faces
  along the curve direction there, upright. `position` limits it to part of the curve, as a
  fraction of its length. Put `Translation` and `Rotation` after it to move or turn the
  object from there, or `LookAt(target)` to aim it at something. `actual` holds the
  `position` and the world `location`.
- Blendmentation is also a Blender add-on: install `blendmentation-addon-<version>.zip`
  from the GitHub release in Preferences > Add-ons, and scripts can import it without
  `sys.path` setup. It adds Node > Copy Blendmentation Template to the node editor (also
  in the right click menu and F3 search), which copies an `augmentations.Node(...)` for
  the selected node to the clipboard: every input by its exact name, with the widest range
  the node allows (a group input's Min and Max), all menu options, and the type and
  current value in a comment. The prefix (e.g. `aug.`) is set in the add-on's preferences.
- `augmentations.Node.template(node, obj=None, prefix="augmentations.")` returns the same
  text without the add-on, for a node or its data path.

## [0.8.0] - 2026-10-09

### Added

- `with state.restoring():` sets back every value the augmentations changed inside the
  block when it ends, also when something in it raises. Nothing has to be listed or saved
  first: every augmentation records what it changes (transforms, render visibility, node
  and modifier inputs, material slots, camera settings, any data path), also inside a
  `OneOf`, `Chain`, `Node` or `Modifier`. `state.set(owner, name, value)` makes a change of
  your own code that is set back too. Blocks can be nested.
- `augmentations.OneOf(augmentations, weights=None, p=1.0)` applies one augmentation from a
  list, picked at random by weight, again for every object in a `Compose`. Its `actual` is
  the picked index and `results` the index per object. A `OneOf` can contain another `OneOf`.
  `Compose` clears the `results` of the augmentations inside it.
- `augmentations.Node(node, inputs, p=1.0)` sets many inputs of one node, e.g. the
  group node of a smart material or of a geometry nodes setup, in one augmentation. `inputs` maps each input name (or
  index) to a range, or to an `augmentations.Input(value_range, options, weights, p,
  otherwise)` (a dict with these keys works too); each input is set like a `Number`,
  `Vector` (vectors, colors and rotations, in radians), `Boolean` or `Menu`, picked by the
  socket type. A color range sets red, green and blue and keeps alpha. `actual` holds the
  value set to each input.
- `augmentations.Modifier(modifier, inputs, p=1.0)` sets the inputs of a geometry nodes
  modifier like `Node` does for a node, e.g. `Modifier('modifiers["Scatter"]',
  {"Density": Input((5, 20))})`. It finds the inputs by their name in the node group, in
  every Blender version, so you don't need the `Socket_2` data paths that changed in
  Blender 5.
- `augmentations.Menu` works on the menu inputs of a geometry nodes modifier in Blender 4.x,
  which Blender stores as numbers: it sets and reports the option names.
- `augmentations.MaterialSlot(slots=None, weights=None, p=1.0, otherwise=None)` gives an
  object one of the materials in its own material slots, picked at random: every face
  gets the picked slot, and it becomes the active slot, so `active_material` paths in the
  same `Compose` reach it. `actual` is the material's name. Materials aren't copied, so
  objects that share a material or a mesh keep sharing it.
- `augmentations.Chain(augmentations, p=1.0)` applies a list of augmentations to one
  object as one augmentation, so a `OneOf` can pick between whole sequences, e.g. a
  `MaterialSlot` together with a `Node` that augments only that material. `Compose` clears
  the `results` inside it.
- Every augmentation call takes `p`, which overrides the augmentation's `p` for that call,
  like a `Compose` call does: `rust(p=0.2)`, `rotation(car, p=0.5)`.
- `augmentations.Number` takes `value_range=(min, max, step)`, which sets one of min,
  min + step, ... max.
- `augmentations.Menu` without `options` reads them on a group node's menu input too, from
  the Menu Switch the group passes it to.

### Changed

- The loop restores the scene with `with state.restoring():` instead of
  `initial = state.State(objects, fields=...)` and `initial.restore()`. Put the
  augmentations and the generating `Compose` inside the block.
- `augmentations.Material` is renamed to `augmentations.SimpleMaterial`.
- `augmentations.Number` raises a `ValueError` for a `value_range` that is not
  `(min, max)` or `(min, max, step)`, or whose min is above its max.

### Removed

- `state.State`, with its `objects` and `fields`: use `state.restoring()`. It only set back
  what was saved when it was built; `restoring()` sets back exactly what the augmentations
  changed.

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

[Unreleased]: https://github.com/mmaciejak/Blendmentation/compare/v0.11.0...dev
[0.11.0]: https://github.com/mmaciejak/Blendmentation/compare/v0.10.2...v0.11.0
[0.10.2]: https://github.com/mmaciejak/Blendmentation/compare/v0.10.1...v0.10.2
[0.10.1]: https://github.com/mmaciejak/Blendmentation/compare/v0.10.0...v0.10.1
[0.10.0]: https://github.com/mmaciejak/Blendmentation/compare/v0.9.6...v0.10.0
[0.9.6]: https://github.com/mmaciejak/Blendmentation/compare/v0.9.5...v0.9.6
[0.9.5]: https://github.com/mmaciejak/Blendmentation/compare/v0.9.4...v0.9.5
[0.9.4]: https://github.com/mmaciejak/Blendmentation/compare/v0.9.3...v0.9.4
[0.9.3]: https://github.com/mmaciejak/Blendmentation/compare/v0.9.2...v0.9.3
[0.9.2]: https://github.com/mmaciejak/Blendmentation/compare/v0.9.1...v0.9.2
[0.9.1]: https://github.com/mmaciejak/Blendmentation/compare/v0.9.0...v0.9.1
[0.9.0]: https://github.com/mmaciejak/Blendmentation/compare/v0.8.2...v0.9.0
[0.8.2]: https://github.com/mmaciejak/Blendmentation/compare/v0.8.1...v0.8.2
[0.8.1]: https://github.com/mmaciejak/Blendmentation/compare/v0.8.0...v0.8.1
[0.8.0]: https://github.com/mmaciejak/Blendmentation/compare/v0.7.3...v0.8.0
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
