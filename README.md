# Blendmentation

Generate synthetic, augmented training datasets from Blender scenes. You describe
your dataset as composed steps, in the style of torchvision / albumentations:

1. **Augment:** randomize objects, materials and any other value in the scene.
2. **Generate:** render the image, shader AOVs and segmentation masks, and write a
   JSON label with bounding boxes, rotations and any values you want to record.
3. **Restore:** put the scene back as it was, and repeat.

## Requirements

- Blender 4.0 or newer. It is tested on 4.0 and 5.2.
- Nothing to install. The code runs in Blender's bundled Python, and uses `numpy` and
  `OpenImageIO`, which ship with Blender.

## Setup

Add the folder that contains `blendmentation/` to `sys.path` in your script, then
import the modules as a package:

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

## Quick start

```python
import bpy

car_1 = bpy.data.objects["Car.001"]
car_2 = bpy.data.objects["Car.002"]
table = [bpy.data.objects["TableTop"], bpy.data.objects["TableLegs"]]
lamp = bpy.data.objects["Light"]

# 1. augmentations, applied to every object in the list
objects_aug = augmentations.Compose([
    augmentations.Translation(x=0.5, y=0.5),
    augmentations.Rotation(z=180),
    augmentations.Scale(x=10, y=10, z=10),
    augmentations.Material("CarPaint", hue=(0, 1), roughness=(0.1, 0.6)),
])
lamp_aug = augmentations.Compose([
    augmentations.Number("data.energy", percent=40),
    augmentations.Vector("data.color", value_range=(0.8, 1.0)),
])

# 2. what to save for every datapoint
classes = {"car": [car_1, car_2], "table": [table]}
generator = generating.Compose(
    [
        generating.Render(),
        generating.BBox(classes, iou_deconflict=0.5),
        generating.Segmentation(classes, per="both"),
        generating.OutputField("light_energy", 'bpy.data.lights["Light"].energy'),
    ],
    path="//dataset",
    resolution=(640, 480),
)

# 3. the scene state to go back to after every datapoint
initial = state.State(
    [car_1, car_2, lamp],
    fields=objects_aug.augmentations + lamp_aug.augmentations,
)

for _ in range(1000):
    objects_aug([car_1, car_2])
    lamp_aug([lamp])
    generator()
    initial.restore()
```

## Augmentations

Augmentations change the scene **in place**. `augmentations.Compose(list)` applies
every augmentation to every object you call it with: `compose([obj_1, obj_2])`. To
see the values actually sampled, read `.actual_x/.actual_y/.actual_z` on
`Translation`, `Rotation` and `Scale`, and `.actual` on the others.

### Transforms

| Augmentation | Parameters | Effect |
|---|---|---|
| `Translation(x, y, z)` | blender units | added to the location |
| `Rotation(x, y, z)` | degrees | added to the rotation; works in euler, quaternion and axis-angle modes |
| `Scale(x, y, z)` | percent | the scale is multiplied by 1 ± p/100 |

Each parameter is a number `v`, which samples from `(-v, v)`, or a pair `(low, high)`.

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
| `Boolean(data_path, probability=0.5)` | bool | `True` with the given probability. |
| `Menu(data_path, options=None, weights=None)` | menus / enums | Picks a random option. With no `options`, it uses all the options of the menu. `weights` sets relative probabilities. |

```python
augmentations.Number('bpy.data.materials["Wood"].node_tree.nodes["Bump"].inputs["Strength"].default_value', value_range=(0.1, 1.0))
augmentations.Number('data.shape_keys.key_blocks["Smile"].value', value_range=(0, 1))
augmentations.Vector('bpy.data.materials["Mat"].node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value',
                     value_range=((0, 0, 0, None), (1, 1, 1, None)))      # random RGB, keep alpha
augmentations.Boolean('modifiers["GeometryNodes"]["Socket_3"]', probability=0.3)
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

- the transforms of the `objects`;
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
| `BBox(classes, iou_deconflict=None)` | `"bboxes"` in the label |
| `Segmentation(classes, per="instance")` | mask PNGs and `"masks"` in the label |
| `RotationMatrix(objects)` | `"rotation_matrices"` in the label |
| `OutputField(name, data_path, objects=None)` | `name` in the label |

You can list the steps in any order. They always run in this order:

1. Label steps (`BBox`, `RotationMatrix`, `OutputField`). Because these come first,
   a datapoint skipped by `iou_deconflict` is never rendered.
2. `Render`.
3. `AOVToImage`.
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
    {"class": "car", "objects": ["Car.001"], "mask": "000000_mask_0.png"},
    {"class": "car", "objects": ["Car.001", "Car.002"], "mask": "000000_mask_car.png"}
  ],
  "rotation_matrices": [{"object": "Car.001", "rotation_matrix": [[1, 0, 0], [0, 1, 0], [0, 0, 1]]}],
  "light_energy": 1000.0
}
```

## Known issues

- **EEVEE AOV bug (Blender 5.2):** a Value AOV listed before a Color AOV in the view
  layer renders as all zeros. Put Color AOVs first in the list. Cycles is not affected.
- **Blender 6:** Blender 5.2 warns that `Material.use_nodes` and light node trees will
  be removed in Blender 6.
