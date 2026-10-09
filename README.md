# Blendmentation

Generate synthetic, augmented training datasets from Blender scenes. You describe
your dataset as composed steps, in the style of torchvision / albumentations:

1. **Augment:** randomize objects, materials and any other value in the scene.
2. **Generate:** render the image, render passes, shader AOVs and segmentation masks,
   and write a JSON label with bounding boxes, keypoints, camera data, rotations and
   any values you want to record.
3. **Restore:** put the scene back as it was, and repeat.
4. **Export:** convert the dataset to COCO, YOLO, Pascal VOC or BOP (6D pose).

**Documentation: [blendmentation.docs.csmx.eu](https://blendmentation.docs.csmx.eu/)**

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

More examples, built up step by step, are in the [Quick start](https://blendmentation.docs.csmx.eu/quick-start/) of the docs.

## Installation

It is not on PyPI yet, so get it from GitHub: clone the repository, or pip install it
from there (this needs `git`). The `main` branch always holds the
[latest release](https://github.com/mmaciejak/Blendmentation/releases/latest).

- **Inside Blender, from a copy of the repository:** clone it and add the cloned folder
  to `sys.path` in your script (`sys.path.append("/path/to/Blendmentation")`):

  ```sh
  git clone https://github.com/mmaciejak/Blendmentation
  ```

- **Inside Blender, with pip:** install it with Blender's Python into your
  Blender user scripts folder, with `--no-deps` so Blender keeps its own numpy. The exact
  command is in the [installation guide](https://blendmentation.docs.csmx.eu/installation/).
- **As a Python module, without the Blender app** (Python 3.13 for `bpy` 5.1+, 3.11
  for `bpy` 4.2–5.0):

  ```sh
  pip install "blendmentation[module] @ git+https://github.com/mmaciejak/Blendmentation"
  ```

## Documentation

- [Quick start](https://blendmentation.docs.csmx.eu/quick-start/): examples built up step by step, and a full example with every feature.
- [Installation](https://blendmentation.docs.csmx.eu/installation/): inside Blender, or as a Python module.
- [Augmentations](https://blendmentation.docs.csmx.eu/augmentations/): transforms, camera, materials and any value by data path.
- [State](https://blendmentation.docs.csmx.eu/state/): setting the scene back after every datapoint.
- [Generating](https://blendmentation.docs.csmx.eu/generating/): renders, passes, AOVs, masks, labels and preview images.
- [Export](https://blendmentation.docs.csmx.eu/export/): COCO, YOLO, Pascal VOC and BOP.
- [Compatibility with Blender MCP](https://blendmentation.docs.csmx.eu/blender-mcp/): an agent skill ([`skills/blendmentation`](skills/blendmentation/SKILL.md)) for AI agents that control Blender through [Blender MCP](https://github.com/ahujasid/blender-mcp).
- [Development](https://blendmentation.docs.csmx.eu/development/): tests, releases and known issues.

The docs are built from `docs/` and the docstrings with `pip install -e ".[docs]"` and
`mkdocs serve`.

## Contact

[csmx.eu](https://csmx.eu), [contact@csmx.eu](mailto:contact@csmx.eu).

## License

[GNU AGPL v3](LICENSE).
