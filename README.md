# Blendmentation

Generate synthetic, augmented training datasets from Blender scenes. You describe
your dataset as composed steps, in the style of torchvision / albumentations:

1. **Augment:** randomize objects, materials and any other value in the scene.
2. **Generate:** render the image, render passes, shader AOVs and segmentation masks,
   and write a JSON label with bounding boxes, keypoints, camera data, rotations and
   any values you want to record.
3. **Restore:** put the scene back as it was, and repeat.
4. **Export:** convert the dataset to COCO, YOLO or Pascal VOC.

**Documentation: [mmaciejak.github.io/Blendmentation](https://mmaciejak.github.io/Blendmentation/)**

## Requirements

- Blender 4.0 or newer. It is tested on 4.0 and 5.2.
- Nothing else. The code runs in Blender's bundled Python, and uses `numpy` and
  `OpenImageIO`, which ship with Blender.

## Installation

It is not on PyPI yet, so pip installs it from GitHub (this needs `git`).

- **Inside Blender:** install it with Blender's Python into your Blender user scripts
  folder, with `--no-deps` so Blender keeps its own numpy. The exact command is in the
  [installation guide](https://mmaciejak.github.io/Blendmentation/installation/).
- **As a Python module, without the Blender app** (Python 3.13 for `bpy` 5.1+, 3.11
  for `bpy` 4.2–5.0):

  ```sh
  pip install "blendmentation[module] @ git+https://github.com/mmaciejak/Blendmentation"
  ```

## Quick start

The scene here has two cars and a table made of two objects, a plant that is in no
class and may hide them, a point light and a camera. The cars use the material "CarPaint". The engine is Cycles or EEVEE, with a
shader AOV "Albedo" in View Layer Properties → Passes → Shader AOV.

```python
import bpy
from blendmentation.augmentations import augmentations
from blendmentation.export import export
from blendmentation.generating import generating
from blendmentation.state import state

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
    augmentations.Number("data.energy", value_range=(600, 1400)),
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
classes = {
    "car": [car_1, car_2],
    "table": {"instances": [table], "max_truncation": 0.8},  # wins over the BBox argument
}
generator = generating.Compose(
    [
        generating.Render(),
        generating.AOVToImage(["Albedo"]),
        generating.Passes(["Depth", "Normal"]),
        generating.BBox(classes, iou_deconflict=0.5, max_truncation=0.3, max_occlusion=0.5),
        generating.BBoxImage(),                        # copy of the image with the boxes drawn, to check them
        generating.Segmentation(classes, per="both"),
        generating.SegmentationImage(),                # copy of the image with the masks drawn
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

## Documentation

- [Installation](https://mmaciejak.github.io/Blendmentation/installation/): inside Blender, or as a Python module.
- [Augmentations](https://mmaciejak.github.io/Blendmentation/augmentations/): transforms, camera, materials and any value by data path.
- [State](https://mmaciejak.github.io/Blendmentation/state/): saving and restoring the scene.
- [Generating](https://mmaciejak.github.io/Blendmentation/generating/): renders, passes, AOVs, masks, labels and preview images.
- [Export](https://mmaciejak.github.io/Blendmentation/export/): COCO, YOLO and Pascal VOC.
- [Development](https://mmaciejak.github.io/Blendmentation/development/): tests, releases and known issues.

The docs are built from `docs/` and the docstrings with `pip install -e ".[docs]"` and
`mkdocs serve`.

## License

[GNU AGPL v3](LICENSE).
