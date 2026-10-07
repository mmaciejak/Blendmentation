# Blendmentation

Generate synthetic, augmented training datasets from Blender scenes. You describe
your dataset as composed steps, in the style of torchvision / albumentations:

1. **Augment:** randomize objects, materials and any other value in the scene.
2. **Generate:** render the image, render passes, shader AOVs and segmentation masks,
   and write a JSON label with bounding boxes, keypoints, camera data, rotations and
   any values you want to record.
3. **Restore:** put the scene back as it was, and repeat.
4. **Export:** convert the dataset to COCO, YOLO, Pascal VOC or BOP (6D pose).

## Requirements

- Blender 4.0 or newer. It is tested on 4.0 and 5.2.
- Nothing else. The code runs in Blender's bundled Python, and uses `numpy` and
  `OpenImageIO`, which ship with Blender.

## Quick start

The scene here has two cars and a table made of two objects, a plant "Plant" that is in no
class and may hide them, a floor "Floor", a point light and a camera. The cars use the material "CarPaint". The engine is Cycles or EEVEE, with a
shader AOV "Albedo" in View Layer Properties → Passes → Shader AOV. The render is transparent
(Render Properties → Film → Transparent, RGBA output), and a folder "backgrounds" with photos
is next to the .blend file.

```python
import bpy
from blendmentation.augmentations import augmentations
from blendmentation.export import export
from blendmentation.generating import generating
from blendmentation.state import state

car_1 = bpy.data.objects["Car.001"]
car_2 = bpy.data.objects["Car.002"]
table = [bpy.data.objects["TableTop"], bpy.data.objects["TableLegs"]]
plant = bpy.data.objects["Plant"]
floor = bpy.data.objects["Floor"]
lamp = bpy.data.objects["Light"]
camera = bpy.context.scene.camera

# 1. augmentations, applied to every object in the list
keep_above = augmentations.KeepAbove(floor, margin=0.01)
objects_aug = augmentations.Compose([
    augmentations.Translation(x=0.5, y=0.5),
    augmentations.Rotation(z=180),
    augmentations.Material("CarPaint", hue=(0, 1), saturation=(0.5, 1), roughness=(0.1, 0.6)),
    keep_above,                                       # after the transforms: lifts the cars out of the floor
])
lamp_aug = augmentations.Compose([
    augmentations.Number("data.energy", value_range=(600, 1400)),
    augmentations.Vector("data.color", value_range=(0.8, 1.0)),
    augmentations.Menu("data.type", options=["POINT", "SPOT"]),
    augmentations.Boolean("data.use_shadow", p=0.8),
])
plant_aug = augmentations.Compose([
    augmentations.Visibility(p=0.7),                  # in the render 70% of the time, labels follow
    augmentations.Scale(x=10, y=10, z=10, p=0.5),     # only half of the time; BOP needs fixed-size cars
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
        generating.Background(weights={"color": 1, "white_noise": 1, "color_noise": 1, "image": 2},
                              images_path="//backgrounds"),  # random background behind the render
        generating.AOVToImage(["Albedo"]),
        generating.Passes(["Depth", "Normal"]),
        generating.BBox(classes, iou_deconflict=0.5, max_truncation=0.3, max_occlusion=0.5),
        generating.BBoxImage(),                        # copy of the image with the boxes drawn, to check them
        generating.Segmentation(classes, per="both", skip_empty=True,   # no file for empty masks
                                full_masks=True),               # + masks ignoring occlusion, for BOP
        generating.SegmentationImage(),                # copy of the image with the masks drawn
        generating.RotationMatrix([car_1, car_2]),
        generating.Pose(classes),                      # 6D pose of every instance, OpenCV camera axes
        generating.CameraData(),
        generating.Keypoints({"car_1": car_1, "car_1_corner": (car_1, 0)}),
        generating.OutputField("light_energy", 'bpy.data.lights["Light"].energy'),
    ],
    path="//dataset",
    resolution=(640, 480),
)

# 3. the scene state to go back to after every datapoint
initial = state.State(
    [car_1, car_2, plant, lamp, camera],
    fields=objects_aug.augmentations + lamp_aug.augmentations,
)

for _ in range(1000):
    objects_aug([car_1, car_2])
    lamp_aug([lamp], p=0.7)                           # the whole Compose 70% of the time
    plant_aug([plant])
    camera_aug([camera])
    generator({"lift": keep_above.results})          # extra label key: how far each car was lifted
    initial.restore()

# 4. training-ready annotations
export.coco("//dataset")
export.yolo("//dataset")
export.bop("//dataset")                               # 6D pose: poses, masks, depth
```

## Next steps

- [Installation](installation.md): inside Blender, or as a Python module.
- [Augmentations](augmentations.md), [State](state.md), [Generating](generating.md) and [Export](export.md): the reference for each module, with examples.
- [Compatibility with Blender MCP](blender-mcp.md): an agent skill so AI agents can build datasets in your open Blender through Blender MCP.

## Contact

[csmx.eu](https://csmx.eu)  ·    [contact@csmx.eu](mailto:contact@csmx.eu).
