# Generating

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

## Steps

| Step | Output |
|---|---|
| `Render(file_format="PNG")` | `<index>.png`, or `.jpg` / `.exr` for `"JPEG"` / `"OPEN_EXR"` |
| `AOVToImage(names, file_format="OPEN_EXR")` | `<index>_<aov>.exr`, or `.png` for `"PNG"` (8-bit, clamped to 0–1) |
| `Passes(names, file_format="OPEN_EXR")` | `<index>_<pass>.exr` / `.png` for built-in passes (depth, normal…) |
| `BBox(classes, iou_deconflict=None)` | `"bboxes"` in the label |
| `BBoxImage(file_format="PNG", line_width=2, show_class=True)` | `<index>_bboxes.png` (or `.jpg`), the image with the bboxes drawn on it |
| `Segmentation(classes, per="instance")` | mask PNGs and `"masks"` in the label |
| `SegmentationImage(file_format="PNG", opacity=0.5, line_width=2, show_class=True)` | `<index>_segmentation.png` (or `.jpg`), the image with the masks drawn on it |
| `RotationMatrix(objects)` | `"rotation_matrices"` in the label |
| `OutputField(name, data_path, objects=None)` | `name` in the label |
| `CameraData()` | `"camera"` in the label |
| `Keypoints(points)` | `"keypoints"` in the label |

You can list the steps in any order. They always run in this order:

1. Label steps (`BBox`, `RotationMatrix`, `OutputField`, `CameraData`, `Keypoints`).
   Because these come first, a datapoint skipped by `iou_deconflict` is never rendered.
2. `Render`.
3. `AOVToImage`, `Passes` and `BBoxImage`.
4. `Segmentation`.
5. `SegmentationImage`.

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

**BBoxImage.** Saves a copy of the rendered image with the boxes of the `BBox` step
drawn on it, for checking the labels by eye. Each class has its own color, and with
`show_class` its name (in capitals) is written above the box. It reuses the render of
`Render`, so it adds no render time, and the main image stays clean. It needs a `BBox`
step in the same `Compose`.

**Segmentation.** Masks are black-and-white PNGs of the **visible** pixels only.
Objects that are not in `classes` still hide what is behind them.

| `per` | Files |
|---|---|
| `"instance"` (default) | `<index>_mask_<n>.png`, where `n` counts instances across all classes |
| `"class"` | `<index>_mask_<class>.png` |
| `"both"` | both sets of files |

Segmentation uses one extra, fast Workbench render, whatever engine you render with.

**SegmentationImage.** Saves a copy of the rendered image with the masks of the
`Segmentation` step laid over it in their class color (`opacity` 0–1), each instance
outlined (`line_width`, 0 for none) and, with `show_class`, its class name written
above. Class colors match `BBoxImage`. Instance masks are used when there are any,
otherwise the class masks. It reuses the render of `Render`, so it adds no render time,
and the main image stays clean. It needs a `Segmentation` step in the same `Compose`.

**RotationMatrix.** Saves each object's 3×3 rotation relative to the camera.

**OutputField.** Saves the value at a data path to the label:

- an absolute path saves one value;
- a relative path saves `{object name: value}` for the given `objects`.

Values are converted to JSON: numbers, bools and strings stay as they are, menus become
the option name, vectors and matrices become lists, and datablocks (materials,
objects…) become their name.

**custom_dict.** Calling `generator(custom_dict={...})` adds extra keys to the label.

## Label file

`<index>.json` is written when there is more than the image to record:

```json
{
  "resolution": [640, 480],
  "image": "000000.png",
  "bbox_image": "000000_bboxes.png",
  "segmentation_image": "000000_segmentation.png",
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

## API reference

::: blendmentation.generating.generating
