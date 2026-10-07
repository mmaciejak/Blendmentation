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

## Requirements

- Blender 4.0 or newer. It is tested on 4.0 and 5.2.
- Nothing else. The code runs in Blender's bundled Python, and uses `numpy` and
  `OpenImageIO`, which ship with Blender.

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

## Quick start

All snippets below start with these imports:

```python
import bpy
from blendmentation.augmentations import augmentations as aug
from blendmentation.export import export
from blendmentation.generating import generating as gen
from blendmentation.state import state
```

Short setups for one task first, then a full example that combines everything.

### HDRI world

A random HDRI, brightness and rotation for the world in every image. The world's nodes:
Texture Coordinate (Generated) → Mapping → three Environment Textures with the HDRIs →
a Menu Switch with the items A, B and C, one per texture → Background → World Output.
A Menu Switch in shader nodes needs Blender 5. The engine is Cycles or EEVEE.

![World nodes: Texture Coordinate, Mapping, three Environment Textures, Menu Switch, Background, World Output](docs/images/hdri-world-nodes.jpg)

```python
world = bpy.context.scene.world

# the paths are relative to the world
world_aug = aug.Compose([
    # HDRI: A, B or C, the options are read from the node
    aug.Menu(
        'node_tree.nodes["Menu Switch"].inputs[0].default_value',
    ),
    # strength
    aug.Number(
        'node_tree.nodes["Background"].inputs[1].default_value',
        value_range=(0, 0.3),
    ),
    # rotation around Z, in radians
    aug.Number(
        'node_tree.nodes["Mapping"].inputs[2].default_value[2]',
        value_range=(0, 6.28),
    ),
])
generator = gen.Compose(
    [gen.Render()],
    path="//dataset",
    resolution=(640, 480),
)
# saves the world's node values
initial = state.State([world])

for _ in range(100):
    # a world goes where the objects usually go
    world_aug([world])
    generator()
    initial.restore()
```

`Menu` takes the options from the Menu Switch; pass `options=["A", "B", "C"]` and
`weights=[2, 1, 1]` to pick some more often. With absolute paths, as Blender's Copy Full
Data Path gives them (`'bpy.data.worlds["World"].node_tree.nodes["Background"].inputs[1].default_value'`),
the same works, and the world doesn't have to be passed to `State`:
`state.State([], fields=world_aug.augmentations)`.

### Material: texture offset, damage and rust

The cube's material "Cube Material" has three nodes that control it: a Vector node
"Texture randomization" moves the Mapping of all its textures, a Value node "Surface
Damage" sets the bump strength, and a Value node "Rust Amount" how much rust is mixed
in. The rust factor also goes to an AOV Output "rust" (Value), which is added as a Value
AOV "rust" in View Layer Properties → Passes → Shader AOV, so every image gets a rust
mask. A Vector node in shader nodes needs Blender 5. The engine is Cycles or EEVEE.

![Material nodes: textures moved by the Vector node "Texture randomization", Value nodes "Surface Damage" and "Rust Amount", and an AOV Output "rust"](docs/images/rust-material-nodes.jpg)

```python
material = bpy.data.materials["Cube Material"]

# the paths are relative to the material
material_aug = aug.Compose([
    # moves all textures: a random offset on each axis
    aug.Vector(
        'node_tree.nodes["Texture randomization"].vector',
        value_range=(-100, 100),
    ),
    aug.Number(
        'node_tree.nodes["Surface Damage"].outputs[0].default_value',
        value_range=(0, 1),
    ),
    # rust in 20% of the images, and then clearly visible
    aug.Number(
        'node_tree.nodes["Rust Amount"].outputs[0].default_value',
        value_range=(0.5, 1),
        p=0.2,
    ),
])
generator = gen.Compose(
    [
        gen.Render(),
        # the rust mask; no file when there is no rust
        gen.AOVToImage(["rust"], skip_empty=True),
    ],
    path="//dataset",
    resolution=(640, 480),
)
# Value and Vector nodes hold their value on an output, not an input,
# so State restores them only when they are in fields
initial = state.State([material], fields=material_aug.augmentations)

for _ in range(100):
    material_aug([material])
    generator()
    initial.restore()
```

A number for `value_range` is used for every component, so `(-100, 100)` is the same
as `((-100, -100, -100), (100, 100, 100))`. The label lists the rust mask under
`"aovs"`, and as `None` for the images without rust.

### Full example

The scene here has two cars and a table made of two objects, a plant "Plant" that is in no
class and may hide them, a floor "Floor", a point light and a camera. The cars use the material "CarPaint". The engine is Cycles or EEVEE, with a
shader AOV "Albedo" in View Layer Properties → Passes → Shader AOV. The render is transparent
(Render Properties → Film → Transparent, RGBA output), and a folder "backgrounds" with photos
is next to the .blend file.

```python
car_1 = bpy.data.objects["Car.001"]
car_2 = bpy.data.objects["Car.002"]
table = [bpy.data.objects["TableTop"], bpy.data.objects["TableLegs"]]
plant = bpy.data.objects["Plant"]
floor = bpy.data.objects["Floor"]
lamp = bpy.data.objects["Light"]
camera = bpy.context.scene.camera

# 1. augmentations, applied to every object in the list
keep_above = aug.KeepAbove(floor, margin=0.01)
objects_aug = aug.Compose([
    aug.Translation(x=0.5, y=0.5),
    aug.Rotation(z=180),
    aug.Material(
        "CarPaint",
        hue=(0, 1),
        saturation=(0.5, 1),
        roughness=(0.1, 0.6),
    ),
    # after the transforms: lifts the cars out of the floor
    keep_above,
])
lamp_aug = aug.Compose([
    aug.Number("data.energy", value_range=(600, 1400)),
    aug.Vector("data.color", value_range=(0.8, 1.0)),
    aug.Menu("data.type", options=["POINT", "SPOT"]),
    aug.Boolean("data.use_shadow", p=0.8),
])
plant_aug = aug.Compose([
    # in the render 70% of the time, labels follow
    aug.Visibility(p=0.7),
    # only half of the time; BOP needs fixed-size cars
    aug.Scale(x=10, y=10, z=10, p=0.5),
])
camera_aug = aug.Compose([
    aug.LookAt(
        [car_1, car_2],
        distance=(6, 12),
        elevation=(10, 45),
        azimuth=(0, 360),
    ),
    aug.FocalLength((24, 85), target=[car_1, car_2], keep_size=True),
    aug.DepthOfField(car_1, f_stop=(1.4, 5.6), p=0.5),
])

# 2. what to save for every datapoint
classes = {
    "car": [car_1, car_2],
    # wins over the BBox argument
    "table": {"instances": [table], "max_truncation": 0.8},
}
steps = [
    gen.Render(),
    # random background behind the render
    gen.Background(
        weights={
            "color": 1,
            "white_noise": 1,
            "color_noise": 1,
            "image": 2,
        },
        images_path="//backgrounds",
    ),
    gen.AOVToImage(["Albedo"]),
    gen.Passes(["Depth", "Normal"]),
    gen.BBox(
        classes,
        iou_deconflict=0.5,
        max_truncation=0.3,
        max_occlusion=0.5,
    ),
    # copy of the image with the boxes drawn, to check them
    gen.BBoxImage(),
    gen.Segmentation(
        classes,
        per="both",
        skip_empty=True,  # no file for empty masks
        full_masks=True,  # + masks ignoring occlusion, for BOP
    ),
    # copy of the image with the masks drawn
    gen.SegmentationImage(),
    gen.RotationMatrix([car_1, car_2]),
    # 6D pose of every instance, OpenCV camera axes
    gen.Pose(classes),
    gen.CameraData(),
    gen.Keypoints({"car_1": car_1, "car_1_corner": (car_1, 0)}),
    gen.OutputField("light_energy", 'bpy.data.lights["Light"].energy'),
]
generator = gen.Compose(steps, path="//dataset", resolution=(640, 480))

# 3. the scene state to go back to after every datapoint
initial = state.State(
    [car_1, car_2, plant, lamp, camera],
    fields=objects_aug.augmentations + lamp_aug.augmentations,
)

for _ in range(1000):
    objects_aug([car_1, car_2])
    # the whole Compose 70% of the time
    lamp_aug([lamp], p=0.7)
    plant_aug([plant])
    camera_aug([camera])
    # extra label key: how far each car was lifted
    generator({"lift": keep_above.results})
    initial.restore()

# 4. training-ready annotations
export.coco("//dataset")
# boxes of the visible pixels, from the masks
export.yolo("//dataset", bbox_from="mask")
# 6D pose: poses, masks, depth
export.bop("//dataset")
```

## Documentation

- [Installation](https://blendmentation.docs.csmx.eu/installation/): inside Blender, or as a Python module.
- [Augmentations](https://blendmentation.docs.csmx.eu/augmentations/): transforms, camera, materials and any value by data path.
- [State](https://blendmentation.docs.csmx.eu/state/): saving and restoring the scene.
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
