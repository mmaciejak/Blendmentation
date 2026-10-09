---
name: blendmentation
description: Generate synthetic, augmented training datasets (renders, passes, AOVs, segmentation masks, bounding boxes, keypoints, 6D poses, camera data, COCO/YOLO/VOC/BOP export) from a live Blender scene with the Blendmentation library, driving Blender through a Blender MCP server (a tool that executes Python in Blender, such as execute_blender_code). Use when the user wants a dataset, augmentations, labels or masks from the scene open in Blender, or mentions Blendmentation together with Blender MCP.
---

# Blendmentation through Blender MCP

Blendmentation is a Python library that runs inside Blender. You compose
augmentations, apply them to objects, generate a datapoint (render plus a JSON label),
restore the scene, and repeat. Through Blender MCP you run that code in the user's open
Blender with the MCP tool that executes Python (`execute_blender_code` in the
`blender-mcp` server; other servers name it differently).

Full API reference: https://blendmentation.docs.csmx.eu/

## How the MCP code tool behaves

These rules shape every call you make:

- **Fresh namespace per call.** Only `bpy` is predefined. Variables, imported names,
  and augmentations do not survive to the next call, so every call
  re-imports and looks up its objects again. `sys.path` and `sys.modules` do persist,
  because they live in Blender's Python.
- **Only `print` output comes back.** Print what you need to see (counts, label JSON,
  file paths, errors). Nothing is returned otherwise.
- **The call blocks Blender** on its main thread, and the server gives up waiting after
  a time limit (180 s in `blender-mcp`). Renders are slow, so generate in batches that
  finish well within it (see "Generate in batches").
- **The scene is the user's live scene.** Augmentations mutate it in place. Always
  augment inside `with state.restoring():`, which sets it back when the block ends, also
  on an error.

## 1. Make the library importable

Run this once per Blender session (or in every call; it is cheap). `REPO` is the
folder that **contains** the `blendmentation/` package, i.e. the repository root.

```python
import sys

REPO = "/absolute/path/to/Blendmentation"
if REPO not in sys.path:
    sys.path.insert(0, REPO)
# drop cached modules, so changes to the library files are picked up
for name in [name for name in sys.modules if name == "blendmentation" or name.startswith("blendmentation.")]:
    del sys.modules[name]

import blendmentation
from blendmentation.generating import generating
print("blendmentation:", blendmentation.__file__, "| Blender", bpy.app.version_string, "| file:", bpy.data.filepath or "(unsaved)")
```

Finding `REPO`:
1. First try `import blendmentation` without changing `sys.path`. If the user installed
   it into Blender with pip, it works and you can skip the path.
2. Otherwise, use the repository the user is working in, if it has a `blendmentation/`
   folder with `__init__.py`. If this skill file lives inside the repository
   (`<repo>/skills/blendmentation/SKILL.md`), the repository root is two folders up.
3. Otherwise ask the user for the path, or clone it:
   `git clone https://github.com/mmaciejak/Blendmentation`.

Don't `pip install` into Blender's Python as a side effect. If the user wants it
installed, follow https://blendmentation.docs.csmx.eu/installation/ (it needs
`--no-deps`, or a second numpy breaks Blender's). The library needs Blender 4.0 or newer,
and only uses numpy and OpenImageIO, which ship with Blender.

## 2. Look at the scene before writing a pipeline

Every later call starts with this header (adjust the names):

```python
import sys

REPO = "/absolute/path/to/Blendmentation"
if REPO not in sys.path:
    sys.path.insert(0, REPO)
from blendmentation.augmentations import augmentations
from blendmentation.generating import generating
from blendmentation.state import state
from blendmentation.export import export
```

Inspect what the dataset can use:

```python
scene = bpy.context.scene
print("engine:", scene.render.engine, "| camera:", scene.camera and scene.camera.name,
      "| resolution:", scene.render.resolution_x, scene.render.resolution_y)
print("AOVs:", [(aov.name, aov.type) for aov in bpy.context.view_layer.aovs])
for obj in scene.objects:
    print(obj.name, obj.type, "| hidden" if obj.hide_render else "",
          "| materials:", [slot.material.name for slot in obj.material_slots if slot.material],
          "| collections:", [c.name for c in obj.users_collection],
          "| modifiers:", [(m.name, m.type) for m in obj.modifiers])
```

Then agree with the user on, before generating anything:
- **classes**: which objects are labeled, and as what. An instance is one object, or a
  sublist of objects labeled as one (`{"table": [[top, legs]]}`). Objects scattered by
  geometry nodes (a `NODES` modifier) are not objects in the scene: label them with
  `generating.Instances(parent, of=...)` in the class list, e.g.
  `{"rock": [generating.Instances(ground, of=rocks_collection)]}`, one instance per
  scattered object, no Realize Instances needed.
- **augmentations**: what varies (objects, camera, lights, materials, any value by data path).
- **outputs**: render, passes, AOVs, masks, bboxes, keypoints, 6D poses, camera data, preview images,
  export format.
- **output folder, resolution and count.**

## 3. The pipeline

The intended loop: per datapoint, inside `with state.restoring():`, apply the
augmentations and call the generating `Compose`; the block's end sets every value they
changed back. Nothing has to be listed or saved first.

```python
import bpy
from blendmentation.augmentations import augmentations
from blendmentation.generating import generating
from blendmentation.state import state

car_1, car_2 = bpy.data.objects["Car.001"], bpy.data.objects["Car.002"]
plant = bpy.data.objects["Plant"]                         # in no class
floor = bpy.data.objects["Floor"]
lamp = bpy.data.objects["Light"]
camera = bpy.context.scene.camera
world = bpy.context.scene.world

keep_above = augmentations.KeepAbove(floor, margin=0.01)
objects_aug = augmentations.Compose([
    augmentations.Translation(x=0.5, y=0.5),              # number v: sampled from (-v, v), blender units
    augmentations.Rotation(z=180),                        # degrees, added
    augmentations.SimpleMaterial("CarPaint", hue=(0, 1), roughness=(0.1, 0.6)),  # changes the material
    augmentations.Node('active_material.node_tree.nodes["Principled BSDF"]', {  # inputs of one node,
        "Coat Roughness": augmentations.Input((0, 0.5, 0.1)),  # by socket type: Number (+ step), Vector, color,
        "Coat Weight": augmentations.Input((0.5, 1), p=0.3, otherwise=0),  # Boolean, Menu
    }),
    keep_above,                                           # after the transforms: lifts out of the floor
])
lamp_aug = augmentations.Compose([
    augmentations.OneOf([                                 # one of them per call, picked by weight
        augmentations.Number("data.energy", value_range=(600, 1400)),     # any value by data path
        augmentations.Chain([                             # a whole list as one augmentation: dim and warm
            augmentations.Number("data.energy", value_range=(300, 600)),
            augmentations.Vector("data.color", value_range=((1, 0.7, 0.5), (1, 0.85, 0.7))),
        ]),
    ], weights=[2, 1]),
])
plant_aug = augmentations.Compose([
    augmentations.Visibility(p=0.7),      # in the render 70% of the time, else hidden; labels follow
    augmentations.MaterialSlot(),         # one of the materials in its own slots (no copies: shared stays shared)
    augmentations.Scale(x=10, y=10, z=10, p=0.5),         # percent; p = probability it runs
    augmentations.Rotation(z=(0, 315, 45)),               # (low, high, step): one of 8 headings
])                                        # (not on posed objects: BOP has one model size per class)
world_aug = augmentations.Compose([                        # call it with [world], paths relative to it
    augmentations.Number('node_tree.nodes["Background"].inputs[1].default_value', value_range=(0.5, 1.5)),
])
floor_aug = augmentations.Compose([                        # the inputs shown on a geometry nodes modifier,
    augmentations.Modifier('modifiers["Scatter"]', {      # by name, in every Blender version
        "Density": augmentations.Input((5, 20)),
    }),
])
camera_aug = augmentations.Compose([, distance=(6, 12), elevation=(10, 45), azimuth=(0, 360)),
    augmentations.FocalLength((24, 85), target=[car_1, car_2], keep_size=True),
    augmentations.DepthOfField(car_1, f_stop=(1.4, 5.6), p=0.5, otherwise=False),  # after LookAt/FocalLength; sharp otherwise
])

scene = bpy.context.scene
scene.render.film_transparent = True                      # Background needs a transparent RGBA render;
scene.render.image_settings.color_mode = "RGBA"           # these change the scene, tell the user

classes = {"car": [car_1, car_2]}   # scattered by geometry nodes: [generating.Instances(floor, of=rocks)]
generator = generating.Compose(
    [
        generating.Render(),
        generating.Background(weights={"color": 1, "color_noise": 1}),  # + "image": w with images_path=folder
        generating.BBox(classes, iou_deconflict=0.5, max_truncation=0.3),
        generating.BBoxImage(),                 # preview copy with the boxes drawn
        generating.Segmentation(classes, per="both", skip_empty=True,   # no file for empty masks
                                full_masks=True),               # + masks ignoring occlusion (BOP)
        generating.Pose(classes),               # 6D pose per instance, OpenCV camera axes
        generating.Passes(["Depth"]),           # depth for BOP
        generating.CameraData(),
    ],
    path="/absolute/output/folder",
    resolution=(640, 480),
)
```

## 4. Check with one preview first

Put the pipeline from step 3 in the same call (nothing survives between calls), then:

```python
import json, os, glob
with state.restoring():                                   # the scene is set back afterwards, also on an error
    objects_aug([car_1, car_2]); lamp_aug([lamp], p=0.7); plant_aug([plant]); camera_aug([camera]); world_aug([world]); floor_aug([floor])
    print("generated:", generator.preview(4, {"lift": keep_above.results}))   # resolution / 4; False = skipped
labels = sorted(glob.glob(os.path.join(generator.path, "[0-9]*.json")))
print(open(labels[-1]).read() if labels else "no label yet")
print(sorted(os.listdir(generator.path))[-10:])
```

- A step's `check()` raises before anything renders when the config is wrong (missing
  AOV, AOVs with Workbench, bad classes). Read the error and fix the pipeline.
- Look at the preview images (`<index>_bboxes.png`, `<index>_segmentation.png`) with
  your file tools if you have them, or describe the label to the user. The MCP viewport
  screenshot shows the viewport, not the render.
- Preview datapoints are written to the output folder like any other datapoint. Delete
  them (ask first) or use a scratch folder for previews.

## 5. Generate in batches

Time one datapoint, then pick a batch size that ends well within the MCP time limit
(e.g. under 120 s for a 180 s limit). Each call builds the pipeline again and runs one batch:

```python
import time
BATCH = 10
done = skipped = 0
start = time.perf_counter()
for _ in range(BATCH):
    with state.restoring():                               # every datapoint starts from the user's scene
        objects_aug([car_1, car_2]); lamp_aug([lamp], p=0.7); plant_aug([plant]); camera_aug([camera]); world_aug([world]); floor_aug([floor])
        if generator({"lift": keep_above.results}):
            done += 1
        else:
            skipped += 1
print(f"done {done}, skipped {skipped}, {(time.perf_counter() - start) / BATCH:.1f} s per datapoint")
```

- File names continue from the highest index already in the folder, so batches (and
  interrupted runs) append instead of overwriting.
- `generator()` returns `False` when a `BBox` setting skips the datapoint (before the
  beauty render). If most are skipped, loosen the settings or the augmentations, and tell
  the user.
- Every call builds the pipeline again; that's fine, since each datapoint's
  `restoring()` block leaves the scene as it found it.

For large datasets (hundreds of renders or more), a headless run is faster and doesn't
freeze the user's Blender. If you have a shell: save a copy of the scene, without
replacing the user's file, with
`bpy.ops.wm.save_as_mainfile(filepath="/path/dataset_scene.blend", copy=True)`, write the
pipeline and loop to a script (with the `sys.path` lines), and run
`blender --background /path/dataset_scene.blend --python make_dataset.py`.

## 6. Export

Export reads the labels, so run it once at the end, over the whole folder:

```python
from blendmentation.export import export
export.coco("/absolute/output/folder")   # coco.json, masks as RLE if Segmentation ran
export.yolo("/absolute/output/folder")   # <index>.txt next to the images, classes.txt, dataset.yaml
# coco/yolo/voc: bbox_from="label" (default) = BBox boxes around the whole object, hidden parts
# included; bbox_from="mask" = boxes of the visible pixels, needs Segmentation per="instance"/"both"
export.voc("/absolute/output/folder")    # Annotations/*.xml
export.bop("/absolute/output/folder")    # bop/train_pbr/000000/: needs Pose + CameraData; masks, depth if generated
```

## Steps and options at a glance

Generating steps (list order doesn't matter, they are sorted by stage):

| Step | Writes |
|---|---|
| `Render(file_format="PNG")` | `<index>.png` |
| `Background(weights=None, noise_size=(1, 8), images_path=None)` | random color, white/color noise or image behind the render and previews; `background` |
| `BBox(classes, iou_deconflict=None, max_truncation=None, max_occlusion=None)` | `bboxes`; the three settings skip the datapoint |
| `BBoxImage()` / `SegmentationImage()` | preview copies with boxes / masks drawn |
| `Segmentation(classes, per="instance" \| "class" \| "both", skip_empty=False, full_masks=False)` | mask PNGs, `masks`; `full_masks`: unoccluded masks, `full_masks` |
| `AOVToImage(["Albedo"], skip_empty=False)` | shader AOVs from the view layer (Cycles or EEVEE) |
| `Passes(["Depth", "Normal"], skip_empty=False)` | render passes, enabled for you |
| `RotationMatrix(objects)` | rotation of each object relative to the camera (Blender camera axes) |
| `Pose(classes)` | 6D pose of each instance: `R`, `t` (OpenCV camera axes, Blender units), `scale`; `poses` |
| `CameraData()` | camera, intrinsics, OpenCV extrinsics, depth of field |
| `Keypoints({"name": obj \| (obj, vertex index or group) \| (armature, bone) \| (x, y, z)})` | projected points, visibility |
| `OutputField(name, data_path, objects=None)` | any value by data path |

- **BBox skip settings**: `iou_deconflict` (two boxes overlap more than this IoU),
  `max_truncation` (fraction of a box out of frame), `max_occlusion` (fraction of an
  instance hidden by objects that are in no class; costs two Workbench renders). Set them
  per class with `{"car": {"instances": [car_1, car_2], "max_occlusion": 0.3}}`; a class's
  own value wins over the `BBox` argument, even `None`. `Segmentation` ignores them.
- **`Instances(parent, of=None)`** in a class list: every top-level geometry nodes
  instance of `parent` (of the objects in `of`: object, list or collection) is one
  instance; label names `<parent>/<index>/<object>`. Boxes, poses and all skip settings
  work; masks and `max_occlusion` use one Cycles render with a material override (no
  alpha, no displacement), whatever the engine, and need geometry nodes on `parent`.
- **`Background`** needs Film > Transparent and RGBA output, or it raises. `weights` is
  `{"color", "white_noise", "color_noise", "image": weight}` (left out = never);
  `images_path` (a folder) is only needed when `"image"` has a weight above 0; with
  `"image": 0` or `"image"` left out, leave it None. Labels, AOVs and
  passes don't change; the image is saved opaque RGB.
- **`skip_empty=True`** (`Segmentation`, `AOVToImage`, `Passes`) doesn't write an image
  that is fully black; its file name in the label is `None`, and the datapoint is kept.
- **Augmentations**: `Translation`, `Rotation`, `Scale`, `Visibility`, `KeepAbove`, `LookAt`,
  `FocalLength`, `DepthOfField`, `SimpleMaterial`, the data-path ones `Number`, `Vector`,
  `Boolean`, `Menu`, `Node` (many inputs of one node, e.g. a smart material's or a
  geometry nodes group node, not a modifier's inputs; `{input name: range or Input(value_range, options=, weights=, p=, otherwise=)}`,
  the augmentation picked by socket type; a color range keeps alpha), `Modifier` (the same
  for the inputs of a geometry nodes modifier, by name, in every version),
  `MaterialSlot` (one of the materials in the object's own slots, every face; then
  `active_material...` paths reach it), `OneOf` to pick
  one of several, and `Chain` to apply a list to one object as one augmentation (e.g. a
  `OneOf` option). Every one takes `p`, drawn per object; a call can override it, `aug(obj, p=0.5)` or `aug(p=0.5)` for an absolute path (a `Compose` call: `compose(objects, p=0.5)`). Ranges are `(low, high)` or a single number;
  `Translation`/`Rotation`/`Scale` and `Number` also take `(low, high, step)` for one of low, low + step, ... high
  (for whole turns `(0, 270, 90)`: 360 would repeat 0).
- **`otherwise`** (`Number`, `Vector`, `Boolean`, `Menu`, `MaterialSlot`, `Visibility`,
  `FocalLength`, `DepthOfField`): the value set when the augmentation doesn't run because of `p`, e.g.
  `Number(path, (0.5, 1), p=0.2, otherwise=0)`, `DepthOfField(car, p=0.5, otherwise=False)`
  (sharp in the other half). None keeps the value; `Boolean` and `Visibility` default to
  False (hidden). Not with `FocalLength(keep_size=True)`. A skipped `Compose` sets nothing.
- **What an augmentation did**: `aug.actual` (`actual_x/y/z`) and `aug.applied` describe
  only the last call, so after a `Compose` call only its last object. `aug.results` is
  `{object name: actual}` for every object of the last `Compose` call (`(x, y, z)` for
  `Translation`/`Rotation`/`Scale`). Save it in the label with `generator({"key": aug.results})`.
- **`Visibility(p=0.5)`** shows the object in the render with probability `p` and hides
  it otherwise (`hide_render`, the viewport is untouched). Bboxes leave hidden objects
  out (an instance with all of them hidden gets `None`), masks have no pixels for them,
  and they don't block keypoints.
- **`KeepAbove(surface, margin=0)`** moves the object up along world Z, only when needed,
  until its lowest point is `margin` above the surface (evaluated meshes, uneven surfaces
  work). It checks the object where it is, so put it after every augmentation that moves
  or deforms the object. **`PlaceOn(surface, margin=0)`** is the same but also moves it
  down, so it rests on the surface (e.g. an object tipped over around a centered origin).
- **`Seed(node_group)`** gives every seed input in a node group (Distribute Points, Random
  Value...) its own random value per call: `seed = augmentations.Seed(bpy.data.node_groups["Geometry Nodes"]); seed()`.
- **`OneOf(augmentations, weights=None, p=1)`** applies one augmentation from the list,
  picked by weight, again for every object in a `Compose`; its `results` hold the picked
  index. The others don't run, so they don't set their `otherwise`. It can be nested, and
  `Compose`'s clearing of `results` reaches inside it.
  To pick a whole sequence, put a `Chain([...])` in it; e.g. a material and its own `Node`:
  `OneOf([Chain([MaterialSlot(["Metal"]), Node('active_material.node_tree.nodes["Metal"]', {...})]), ...])`.
- **Data paths** starting with `bpy.` are absolute (`'bpy.data.materials["Mat"].node_tree.nodes["X"].inputs[2].default_value'`),
  others are relative to each object (`"data.energy"`). A world can be passed in place of
  an object (`world_aug([bpy.context.scene.world])`, paths like `'node_tree.nodes["Background"].inputs[1].default_value'`).

## Pitfalls

- **Restore what you change.** `state.restoring()` sets back every value the
  augmentations changed inside it, whatever it is. Changes your own code makes are not
  recorded: make them with `state.set(owner, "name", value)` (e.g.
  `state.set(lamp.data, "energy", 2000)`), or change them back yourself. Augmentations
  called outside a `restoring()` block change the user's scene for good.
- **Output paths**: `"//dataset"` is relative to the `.blend` file. When
  `bpy.data.filepath` is empty (unsaved scene), use an absolute path.
- **Don't save over the user's `.blend`** unless they ask. Use `copy=True`.
- **AOVs need Cycles or EEVEE**; Workbench has none. `max_occlusion`, and `Segmentation`
  with EEVEE or Workbench, use a flat Workbench render internally (every object solid, so
  alpha-clipped leaves or decals are wrong) and restore every setting afterwards. In
  Cycles, `Segmentation` reads the Object Index pass of the beauty render instead, which
  respects alpha; use Cycles when masks of cutout materials matter.
- **Geometry nodes inputs differ by version**: on Blender 4.x the data path is
  `modifiers["GeoNodes"]["Socket_2"]`, on 5.x
  `modifiers["GeoNodes"].properties.inputs.Socket_2.value`. `Modifier` finds them by
  name in every version; for `Number` & co. check `bpy.app.version`.
- **Blender 5.2 EEVEE**: a VALUE AOV listed before a COLOR AOV in the view layer renders
  as 0. List color AOVs first.
- **Edited the library?** The module purge in step 1 picks up the changes; without it,
  Blender keeps the old code until restarted.
- If a call times out on the MCP side, Blender may still be running it. Wait, then check
  the output folder and that the scene was restored before you retry.
