# Blendmentation

Generate synthetic, augmented training datasets from Blender scenes. You describe
your dataset as composed steps, in the style of torchvision / albumentations:

1. **Augment:** randomize objects, materials and any other value in the scene.
2. **Generate:** render the image, render passes, shader AOVs and segmentation masks,
   and write a JSON label with bounding boxes, keypoints, camera data, rotations and
   any values you want to record.
3. **Restore:** put the scene back as it was, and repeat.
4. **Export:** convert the dataset to COCO, YOLO, Pascal VOC or BOP (6D pose).

## Approach

Blendmentation automates and augments ordinary Blender scenes, built with the workflows
you already use. Instead of a custom function for every use case, the variation in your
synthetic data generation (SDG) pipeline lives in Blender itself, in shader nodes and
geometry nodes. Blendmentation randomizes it by changing their values (`Number`,
`Vector`, `Boolean`, `Menu`, `Node`, `Seed`), so whatever you can build with nodes, you can
augment.

A few augmentations are shortcuts for common setups, so a basic pipeline is quick and
easy to get going: `KeepAbove`, `PlaceOn`, `LookAt`, `SimpleMaterial`, `FocalLength` and
`DepthOfField`. The transforms (`Translation`, `Rotation`, `Scale`) are there for simple
scenes, and for small changes outside of a geometry nodes setup. For cluttered scenes,
we recommend placing the objects with geometry nodes instead.

The generating step is different. Apart from the color render, producing training data
(masks, bounding boxes, poses, labels) is not part of the usual Blender workflow, so
Blendmentation does it itself.

## Requirements

- Blender 4.0 or newer. It is tested on 4.0 and 5.2.
- Nothing else. The code runs in Blender's bundled Python, and uses `numpy` and
  `OpenImageIO`, which ship with Blender.

## Quick start

A scene with two cars, "Car.001" and "Car.002", using the material "CarPaint":

```python
import bpy
from blendmentation.augmentations import augmentations as aug
from blendmentation.export import export
from blendmentation.generating import generating as gen
from blendmentation.state import state

cars = [bpy.data.objects["Car.001"], bpy.data.objects["Car.002"]]
classes = {"car": cars}

# 1. augment
objects_aug = aug.Compose([
    aug.Translation(x=0.5, y=0.5),
    aug.Rotation(z=180),
    aug.SimpleMaterial("CarPaint", hue=(0, 1)),
])

# 2. what to save for every datapoint
generator = gen.Compose([
    gen.Render(),
    gen.BBox(classes),
    gen.Segmentation(classes),
], path="//dataset", resolution=(640, 480))

for _ in range(1000):
    # 3. everything changed inside is set back afterwards
    with state.restoring():
        objects_aug(cars)
        generator()

# 4. training-ready annotations
export.coco("//dataset")
```

More examples, built up step by step, are in the [Quick start](quick-start.md).

## Next steps

- [Quick start](quick-start.md): examples built up step by step, and a full example with every feature.
- [Installation](installation.md): inside Blender, or as a Python module.
- [Augmentations](augmentations.md), [State](state.md), [Generating](generating.md) and [Export](export.md): the reference for each module, with examples.
- [Compatibility with Blender MCP](blender-mcp.md): an agent skill so AI agents can build datasets in your open Blender through Blender MCP.

## Contact

[csmx.eu](https://csmx.eu)  ·    [contact@csmx.eu](mailto:contact@csmx.eu).
