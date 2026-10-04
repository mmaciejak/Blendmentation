# Blendmentation

Generate synthetic, augmented training datasets from Blender scenes. You describe
your dataset as composed steps, in the style of torchvision / albumentations:

1. **Augment:** randomize objects, materials and any other value in the scene.
2. **Generate:** render the image, render passes, shader AOVs and segmentation masks,
   and write a JSON label with bounding boxes, keypoints, camera data, rotations and
   any values you want to record.
3. **Restore:** put the scene back as it was, and repeat.
4. **Export:** convert the dataset to COCO, YOLO or Pascal VOC.

## Requirements

- Blender 4.0 or newer. It is tested on 4.0 and 5.2.
- Nothing to install. The code runs in Blender's bundled Python, and uses `numpy` and
  `OpenImageIO`, which ship with Blender.

## Setup

Inside Blender, the simplest setup is to add the folder that contains
`blendmentation/` to `sys.path` in your script, then import the modules as a package:

```python
import sys
sys.path.append("/path/to/Blendmentation")

from blendmentation.augmentations import augmentations
from blendmentation.generating import generating
from blendmentation.state import state
```

Run the script from Blender's Scripting tab, or headless:

```sh
blender --background scene.blend --python make_dataset.py
```

### As a Python module, without the Blender app

Blender is also published on PyPI as the `bpy` module. Each `bpy` release supports
only one Python version:

| `bpy` | Python |
|---|---|
| 5.1, 5.2 | 3.13 |
| 4.x, 5.0 | 3.11 |

OpenImageIO is bundled with the Blender app but not with the module, so install it too:

```sh
uv venv --python 3.13 .venv                       # or: python3.13 -m venv .venv
uv pip install -e "/path/to/Blendmentation[module]"  # installs bpy and OpenImageIO too
```

The package is installed, so `sys.path` isn't needed. The script is otherwise the same
as inside Blender, except that you open the `.blend` file yourself:

```python
import bpy
from blendmentation.augmentations import augmentations
from blendmentation.generating import generating
from blendmentation.state import state

bpy.ops.wm.open_mainfile(filepath="scene.blend")   # "//" output paths are relative to this file
# ... same augmentations, State and generating Compose as below ...
```

```sh
.venv/bin/python make_dataset.py
```

For type hints in your editor (Blender objects in the augmentation signatures, `bpy`
completions), install the `bpy` type stubs from the `dev` extra:
`uv pip install -e "/path/to/Blendmentation[module,dev]"`.

All features work the same way in the module. Note that Workbench (used by
`Segmentation`) and EEVEE need a GPU; this has only been tested on macOS.

## Quick start

The scene here has two cars and a table made of two objects, a point light and a
camera. The cars use the material "CarPaint". The engine is Cycles or EEVEE, with a
shader AOV "Albedo" in View Layer Properties → Passes → Shader AOV.

```python
import bpy
from blendmentation.export import export

car_1 = bpy.data.objects["Car.001"]
car_2 = bpy.data.objects["Car.002"]
table = [bpy.data.objects["TableTop"], bpy.data.objects["TableLegs"]]
lamp = bpy.data.objects["Light"]
camera = bpy.context.scene.camera

# 1. augmentations, applied to every object in the list
objects_aug = augmentations.Compose([
    augmentations.Translation(x=0.5, y=0.5),
    augmentations.Rotation(z=180),
    augmentations.Scale(x=10, y=10, z=10, p=0.5),     # only half of the time
    augmentations.Material("CarPaint", hue=(0, 1), saturation=(0.5, 1), roughness=(0.1, 0.6)),
])
lamp_aug = augmentations.Compose([
    augmentations.Number("data.energy", percent=40),
    augmentations.Vector("data.color", value_range=(0.8, 1.0)),
    augmentations.Menu("data.type", options=["POINT", "SPOT"]),
    augmentations.Boolean("data.use_shadow", p=0.8),
])
camera_aug = augmentations.Compose([
    augmentations.LookAt([car_1, car_2], distance=(6, 12), elevation=(10, 45), azimuth=(0, 360)),
    augmentations.FocalLength((24, 85), target=[car_1, car_2], keep_size=True),
    augmentations.DepthOfField(car_1, f_stop=(1.4, 5.6), p=0.5),
])

# 2. what to save for every datapoint
classes = {"car": [car_1, car_2], "table": [table]}
generator = generating.Compose(
    [
        generating.Render(),
        generating.AOVToImage(["Albedo"]),
        generating.Passes(["Depth", "Normal"]),
        generating.BBox(classes, iou_deconflict=0.5),
        generating.Segmentation(classes, per="both"),
        generating.RotationMatrix([car_1, car_2]),
        generating.CameraData(),
        generating.Keypoints({"car_1": car_1, "car_1_corner": (car_1, 0)}),
        generating.OutputField("light_energy", 'bpy.data.lights["Light"].energy'),
    ],
    path="//dataset",
    resolution=(640, 480),
)

# 3. the scene state to go back to after every datapoint
initial = state.State(
    [car_1, car_2, lamp, camera],
    fields=objects_aug.augmentations + lamp_aug.augmentations,
)

for _ in range(1000):
    objects_aug([car_1, car_2])
    lamp_aug([lamp])
    camera_aug([camera])
    generator()
    initial.restore()

# 4. training-ready annotations
export.coco("//dataset")
export.yolo("//dataset")
```

## Augmentations

Augmentations change the scene **in place**. `augmentations.Compose(list)` applies
every augmentation to every object you call it with: `compose([obj_1, obj_2])`. To
see the values actually sampled, read `.actual_x/.actual_y/.actual_z` on
`Translation`, `Rotation` and `Scale`, and `.actual` on the others.

Every augmentation, and `Compose` itself, takes `p`: the probability that it runs at
all (default 1). It is drawn on every call, so once per object inside a `Compose`, and
once per call for a `Compose`. After a call, `.applied` says whether it ran; when it was
skipped, the object is untouched and the actual values are `None`. `Boolean` is the
exception: `p` is the probability of `True`, and otherwise it sets `False`.

### Transforms

| Augmentation | Parameters | Effect |
|---|---|---|
| `Translation(x, y, z)` | blender units | added to the location |
| `Rotation(x, y, z)` | degrees | added to the rotation; works in euler, quaternion and axis-angle modes |
| `Scale(x, y, z)` | percent | the scale is multiplied by 1 ± p/100 |

Each parameter is a number `v`, which samples from `(-v, v)`, or a pair `(low, high)`.

### LookAt

```python
LookAt(target, distance=None, elevation=None, azimuth=None, roll=None, focal_length=None)
```

Moves a camera (or a light, or any object) to a random point on a sphere around a
target, and points it at the target, upright. The target stays in the centre of the
view.

- **`target`:** an object, a list of objects (aimed at the centre of their bounding
  boxes), or a point `(x, y, z)`.
- **`distance`:** in blender units.
- **`elevation`:** degrees above the target's horizontal plane. Avoid exactly ±90.
- **`azimuth`:** degrees around the world Z axis, where 0 is +X.
- **`roll`:** degrees around the camera's local Z axis. `None` keeps the camera upright.
- **`focal_length`:** the lens in mm; cameras only.

Each parameter is a `(min, max)` range, an exact number, or `None` to keep the current
value. Pass the camera to `State` to restore its transform and lens.

```python
camera_aug = augmentations.Compose([
    augmentations.LookAt(car_1, distance=(4, 9), elevation=(5, 45), azimuth=(0, 360),
                         roll=(-10, 10), focal_length=(24, 85)),
])
camera_aug([bpy.context.scene.camera])
```

### FocalLength

```python
FocalLength(focal_length, target=None, keep_size=False)
```

Sets the camera lens in mm, from a `(min, max)` range or an exact number. It works
with perspective cameras only.

With `keep_size=True` and a `target` (an object, a list of objects, or a point), the
camera also moves along the line to the target by the same ratio as the lens change.
This is a dolly zoom: the target keeps its size in the image while the perspective
changes.

- **When it's exact:** for parts of the target at its centre's depth, when the target
  is in the centre of the view (e.g. after `LookAt`).
- **When it's approximate:** an object seen at an angle has parts at other depths, so
  their size changes slightly. In testing, a plane seen up to 30° off head-on
  changed by about 0.01%. An off-centre target also moves in the image.

### DepthOfField

```python
DepthOfField(target=None, f_stop=None, p=1.0)
```

Turns depth of field on, focused on the target with a random f-stop. With `p` below 1,
the camera's depth of field settings are left as they are in the other images, so with
depth of field off in the scene only some images are blurred.

- **Focus:** on the `target` (an object, a list of objects, or a point). Focus is
  measured from where the camera is when this runs, so put it after `LookAt` and
  `FocalLength` in the `Compose`.
- **`f_stop`:** a `(min, max)` range or an exact number. Lower values give more blur.
- **`None`:** leaves the current focus or f-stop as it is.

```python
camera_aug = augmentations.Compose([
    augmentations.LookAt(car_1, distance=(4, 9), elevation=(5, 45), azimuth=(0, 360)),
    augmentations.FocalLength((24, 85), target=car_1, keep_size=True),
    augmentations.DepthOfField(car_1, f_stop=(1.4, 5.6), p=0.5),
])
```

### Material

```python
Material(material_id, hue=None, saturation=None, value=None, roughness=None, metallic=None)
```

Sets the Principled BSDF Base Color (as hue / saturation / value), Roughness and
Metallic to random values in `(min, max)` ranges, all 0–1. A parameter left as `None`
is not changed, and the sockets you change must not be connected to other nodes.

`Material` changes the **material** itself, so every object that uses it changes too.

### Any value, by data path

These four augmentations change any value you can reach through a data path: shader
node inputs, geometry nodes inputs, shape keys, light settings, modifier toggles,
and so on.

| Augmentation | For | Parameters |
|---|---|---|
| `Number(data_path, value_range=None, percent=None)` | int / float | `value_range=(min, max)` sets an absolute value; `percent=v` or `(low, high)` scales the current value. Give one of the two. Ints are rounded. |
| `Vector(data_path, value_range=None, percent=None)` | location, color, vector inputs… | Same as `Number`, applied to each component. A bound is a number for all components, or a sequence with one value per component; `None` keeps that component. |
| `Boolean(data_path, p=0.5)` | bool | `True` with probability `p`, otherwise `False`. |
| `Menu(data_path, options=None, weights=None)` | menus / enums | Picks a random option. With no `options`, it uses all the options of the menu. `weights` sets relative probabilities. |

```python
augmentations.Number('bpy.data.materials["Wood"].node_tree.nodes["Bump"].inputs["Strength"].default_value', value_range=(0.1, 1.0))
augmentations.Number('data.shape_keys.key_blocks["Smile"].value', value_range=(0, 1))
augmentations.Vector('bpy.data.materials["Mat"].node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value',
                     value_range=((0, 0, 0, None), (1, 1, 1, None)))      # random RGB, keep alpha
augmentations.Boolean('modifiers["GeometryNodes"]["Socket_3"]', p=0.3)
augmentations.Menu('data.type', options=["POINT", "SPOT", "AREA"], weights=[1, 1, 2])
```

**Data paths.** A path that starts with `bpy.` is absolute. To get one, right-click a
value in Blender's UI and choose **Copy Full Data Path**. An absolute path ignores the
object it is called with, so it can also be called on its own: `aug()`. Any other path
is relative to the object being augmented, for example `location[2]` or `data.energy`.
To change one component of a vector, add `[index]` to the path, e.g. `location[2]`.

**Geometry nodes inputs.** Blender 5 moved these inputs, so the path differs by
version. Copy Full Data Path gives the right one:

- Blender 4.x: `modifiers["GeometryNodes"]["Socket_2"]`
- Blender 5.x: `modifiers["GeometryNodes"].properties.inputs.Socket_2.value`

**Absolute paths inside a `Compose`.** These run once per object, so with `percent`
the change is applied several times over. Call them on their own instead.

## State

```python
State(objects, fields=())
```

`State` saves the scene so it can be restored after every datapoint. It saves:

- the transforms of the `objects`, and the lens and depth of field of cameras;
- all node values of the materials on those objects;
- the value at the data path of every `Number` / `Vector` / `Boolean` / `Menu` in `fields`.

`fields` can also contain path strings. Other augmentations in `fields` are ignored,
so you can pass a whole `compose.augmentations` list.

`restore()` puts it all back. A data path augmentation that you **don't** list in
`fields` is not restored; with `percent`, its changes then build up from one
datapoint to the next.

## Generating

```python
generating.Compose(steps, path, resolution)
```

Calling the generating `Compose` produces one datapoint and returns `False` if it was
skipped (see `iou_deconflict` below). `preview(factor)` does the same at the
resolution divided by `factor`.

- **Paths:** `path` is relative to the `.blend` file when it starts with `//`, and
  `""` means the `.blend` file's folder.
- **File numbering:** each datapoint takes the next free number in the folder
  (`000000`, `000001`, …), so you can stop and resume.
- **Settings:** the scene's resolution and output settings are restored after each
  datapoint.

### Steps

| Step | Output |
|---|---|
| `Render(file_format="PNG")` | `<index>.png`, or `.jpg` / `.exr` for `"JPEG"` / `"OPEN_EXR"` |
| `AOVToImage(names, file_format="OPEN_EXR")` | `<index>_<aov>.exr`, or `.png` for `"PNG"` (8-bit, clamped to 0–1) |
| `Passes(names, file_format="OPEN_EXR")` | `<index>_<pass>.exr` / `.png` for built-in passes (depth, normal…) |
| `BBox(classes, iou_deconflict=None)` | `"bboxes"` in the label |
| `Segmentation(classes, per="instance")` | mask PNGs and `"masks"` in the label |
| `RotationMatrix(objects)` | `"rotation_matrices"` in the label |
| `OutputField(name, data_path, objects=None)` | `name` in the label |
| `CameraData()` | `"camera"` in the label |
| `Keypoints(points)` | `"keypoints"` in the label |

You can list the steps in any order. They always run in this order:

1. Label steps (`BBox`, `RotationMatrix`, `OutputField`, `CameraData`, `Keypoints`).
   Because these come first, a datapoint skipped by `iou_deconflict` is never rendered.
2. `Render`.
3. `AOVToImage` and `Passes`.
4. `Segmentation`.

Settings are checked before anything renders, so a typo fails immediately.

**Classes.** `BBox` and `Segmentation` take `{class name: [instances]}`. An instance is
an object, or a sublist of objects that should count as one object (one bbox, one mask):

```python
{"car": [car_1, car_2], "table": [[table_top, leg_1, leg_2, leg_3, leg_4]]}
```

**AOVToImage.** This step saves shader AOVs. Each AOV must be added in View Layer
Properties → Passes → Shader AOV, and the engine must be Cycles or EEVEE. The AOVs
come from the same render as `Render`, so they add no render time. Without `Render`,
the scene is still rendered once, but no image is saved.

**Passes.** This step saves Blender's built-in render passes: `"Depth"`, `"Mist"`,
`"Normal"`, `"Position"`, `"Vector"` (motion), `"UV"`, `"ObjectIndex"` and
`"MaterialIndex"`.

- **No extra setup:** each pass is turned on in the view layer only for that render,
  so you don't need to enable it yourself.
- **Shared render:** passes come from the same render as `Render` and `AOVToImage`, so
  they add no render time.
- **Engine support differs:** Cycles has all passes; EEVEE has no `UV` or index
  passes; Workbench only has `Depth`. A pass that the engine didn't render gives an
  error. In Cycles, `Vector` also needs motion blur turned off.
- **Format:** use EXR for anything outside 0–1, such as depth or position. PNG clamps
  values to 0–1.
- **Channel names:** EXR channels keep their Blender names (`Z`, `X Y Z`, `U V A`…).

**CameraData.** Saves the active camera as `"camera"` in the label:

- its name and type;
- `matrix_world` (the camera's placement in the world, in Blender's convention);
- `extrinsics_opencv`, a 3×4 world-to-camera `[R|t]` with OpenCV axes (x right, y
  down, z forward);
- `clip_start` and `clip_end`;
- for a perspective camera: lens, sensor size and fit, and `intrinsics`, the 3×4 `K`
  matrix in pixels. It accounts for sensor fit, lens shift and pixel aspect, so
  `K @ extrinsics_opencv @ [x, y, z, 1]` gives the pixel position from the top-left
  corner;
- for an orthographic camera: `ortho_scale`.

**Keypoints.** Projects 3D points into the image. Pass `{name: source}`, where a
source is one of:

| Source | Point |
|---|---|
| `obj` | the object's origin, e.g. an empty |
| `(mesh_obj, 12)` | vertex 12 |
| `(mesh_obj, "hand_L")` | the centre of the vertex group |
| `(armature, "forearm.L")` | the bone's head |
| `(x, y, z)` | a point in world space |

Vertices include deformations from armatures and modifiers. Each keypoint is saved with:

- `position`: pixels from the top-left corner;
- `depth`: distance along the camera's view direction;
- `in_frame`;
- `visible`: false when the point is out of frame or hidden behind geometry (checked
  with a ray cast from the camera).

**BBox.** Boxes are in pixels as `[x_min, y_min, x_max, y_max]`, from the top-left
corner. They are calculated from the geometry (modifiers included), so parts hidden
behind other objects are inside the box. An object out of frame gets `null`. With
`iou_deconflict`, a datapoint where any two boxes overlap more than that IoU is skipped.

**Segmentation.** Masks are black-and-white PNGs of the **visible** pixels only.
Objects that are not in `classes` still hide what is behind them.

| `per` | Files |
|---|---|
| `"instance"` (default) | `<index>_mask_<n>.png`, where `n` counts instances across all classes |
| `"class"` | `<index>_mask_<class>.png` |
| `"both"` | both sets of files |

Segmentation uses one extra, fast Workbench render, whatever engine you render with.

**RotationMatrix.** Saves each object's 3×3 rotation relative to the camera.

**OutputField.** Saves the value at a data path to the label:

- an absolute path saves one value;
- a relative path saves `{object name: value}` for the given `objects`.

Values are converted to JSON: numbers, bools and strings stay as they are, menus become
the option name, vectors and matrices become lists, and datablocks (materials,
objects…) become their name.

**custom_dict.** Calling `generator(custom_dict={...})` adds extra keys to the label.

### Label file

`<index>.json` is written when there is more than the image to record:

```json
{
  "resolution": [640, 480],
  "image": "000000.png",
  "aovs": {"Albedo": "000000_Albedo.exr"},
  "bboxes": [
    {"class": "car", "objects": ["Car.001"], "bbox": [102.4, 87.1, 233.9, 190.2]},
    {"class": "table", "objects": ["TableTop", "TableLegs"], "bbox": [300.0, 120.5, 512.3, 401.0]}
  ],
  "masks": [
    {"class": "car", "objects": ["Car.001"], "mask": "000000_mask_0.png", "per": "instance"},
    {"class": "car", "objects": ["Car.001", "Car.002"], "mask": "000000_mask_car.png", "per": "class"}
  ],
  "rotation_matrices": [{"object": "Car.001", "rotation_matrix": [[1, 0, 0], [0, 1, 0], [0, 0, 1]]}],
  "passes": {"Depth": "000000_Depth.exr", "Normal": "000000_Normal.exr"},
  "camera": {"name": "Camera", "type": "PERSP", "intrinsics": [[888.9, 0, 320], [0, 888.9, 240], [0, 0, 1]],
             "extrinsics_opencv": [[1, 0, 0, 0], [0, 0, -1, 0], [0, 1, 0, 10]], "...": "..."},
  "keypoints": [{"name": "nose", "position": [310.2, 140.8], "depth": 9.1, "in_frame": true, "visible": true}],
  "light_energy": 1000.0
}
```

## Export

After generating, convert the dataset folder to standard formats:

```python
from blendmentation.export import export

export.coco("//dataset")   # <path>/coco.json
export.yolo("//dataset")   # <index>.txt next to every image, classes.txt, dataset.yaml
export.voc("//dataset")    # <path>/Annotations/<index>.xml
```

- **What gets exported:** datapoints with an image (a `Render` step) and with
  bboxes and/or instance masks.
- **Class ids:** classes are numbered in order of first appearance. Pass
  `classes=["car", "table"]` to `coco` or `yolo` to fix the order.
- **No Blender needed:** export runs in plain Python, so you can run it on another
  machine. COCO with masks needs `numpy` and `OpenImageIO`, which come with Blender.

| Format | Contents |
|---|---|
| `coco(path, output=None, classes=None, bbox_from="label")` | Instance segmentation as RLE from the instance masks, and bboxes. Instances whose mask is empty (fully hidden) are skipped. `bbox_from="mask"` uses the visible pixels of the mask for the bbox, instead of the geometric bbox that includes hidden parts. Each annotation also has an `"objects"` field with the object names. |
| `yolo(path, classes=None)` | One `class x_center y_center width height` line per bbox, normalized to 0–1. `dataset.yaml` points at the folder, ready for Ultralytics. |
| `voc(path, output_dir=None)` | One XML file per image, with 1-based pixel bboxes. Boxes touching the image border are marked `truncated`. |

## Tests

The tests run with pytest and use Blender as a Python module, so they need a
`bpy`-compatible Python and a GPU (for Workbench):

```sh
uv venv --python 3.13 .venv
uv pip install -e ".[module,test]"
.venv/bin/python -m pytest
```

Without `bpy`, only the tests that don't need Blender run, and the rest are skipped.
The suite passes with `bpy` 5.2 (Python 3.13) and `bpy` 4.5 (Python 3.11).

## Known issues

- **EEVEE AOV bug (Blender 5.2):** a Value AOV listed before a Color AOV in the view
  layer renders as all zeros. Put Color AOVs first in the list. Cycles is not affected.
- **Blender 6:** Blender 5.2 warns that `Material.use_nodes` and light node trees will
  be removed in Blender 6.
