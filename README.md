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

### Chained transform augmentations

The scene has a milk carton "Milk Box" standing on a floor "Floor", with its rotation
at (0, 0, 0) and its origin at the bottom center.

The most basic augmentations are transforms. You can create a `Compose` with them like
this. Here a milk carton standing on its base gets a random heading and position.

```python
milk_box = bpy.data.objects["Milk Box"]

# standing on its base: any heading, moved on the floor
standing_aug = aug.Compose([
    aug.Rotation(z=180),
    aug.Translation(x=0.5, y=0.5),
])
```

<img src="docs/images/carton-standing.jpg" alt="Three renders of the standing milk carton" width="100%">

You can also control the chance of the whole `Compose` happening with `p`.

```python hl_lines="7"
milk_box = bpy.data.objects["Milk Box"]

# standing on its base: any heading, moved on the floor
standing_aug = aug.Compose([
    aug.Rotation(z=180),
    aug.Translation(x=0.5, y=0.5),
], p=0.8)
```

Let's imagine that in our case we want about 80% of the datapoints with the milk carton
standing up, and about 20% with it lying on its side. Here is a second `Compose` for
that. A single `Rotation` tips the carton over around X and picks the side it lies on
with a stepped Y rotation, which turns it around its long axis. `KeepAbove`, after the
transforms, lifts it out of the floor.

```python hl_lines="2 10-18"
milk_box = bpy.data.objects["Milk Box"]
floor = bpy.data.objects["Floor"]

# standing on its base: any heading, moved on the floor
standing_aug = aug.Compose([
    aug.Rotation(z=180),
    aug.Translation(x=0.5, y=0.5),
], p=0.8)

# lying on one of its four sides
lying_aug = aug.Compose([
    # tipped over around X, turned around its long axis in 90 degree
    # steps (which side is down), any heading
    aug.Rotation(x=(90, 90), y=(0, 270, 90), z=180),
    aug.Translation(x=0.5, y=0.5),
    # after the transforms: lifts it out of the floor
    aug.KeepAbove(floor),
])
```

<img src="docs/images/carton-lying.jpg" alt="Three renders of the milk carton lying on its side" width="100%">

Then add a generating `Compose` with simple bounding boxes, and `BBoxImage`, a copy of
the image with the boxes drawn on it.

```python hl_lines="20-29"
milk_box = bpy.data.objects["Milk Box"]
floor = bpy.data.objects["Floor"]

# standing on its base: any heading, moved on the floor
standing_aug = aug.Compose([
    aug.Rotation(z=180),
    aug.Translation(x=0.5, y=0.5),
], p=0.8)

# lying on one of its four sides
lying_aug = aug.Compose([
    # tipped over around X, turned around its long axis in 90 degree
    # steps (which side is down), any heading
    aug.Rotation(x=(90, 90), y=(0, 270, 90), z=180),
    aug.Translation(x=0.5, y=0.5),
    # after the transforms: lifts it out of the floor
    aug.KeepAbove(floor),
])

generator = gen.Compose(
    [
        gen.Render(),
        gen.BBox({"milk_box": [milk_box]}),
        # a copy of the image with the boxes drawn, to check them
        gen.BBoxImage(),
    ],
    path="//dataset",
    resolution=(640, 480),
)
```

And finally, save the initial state and create the pipeline. It uses the lying-down
augmentation only when the standing one didn't happen: `applied` tells whether the last
call of a `Compose` ran.

```python hl_lines="31 33-39"
milk_box = bpy.data.objects["Milk Box"]
floor = bpy.data.objects["Floor"]

# standing on its base: any heading, moved on the floor
standing_aug = aug.Compose([
    aug.Rotation(z=180),
    aug.Translation(x=0.5, y=0.5),
], p=0.8)

# lying on one of its four sides
lying_aug = aug.Compose([
    # tipped over around X, turned around its long axis in 90 degree
    # steps (which side is down), any heading
    aug.Rotation(x=(90, 90), y=(0, 270, 90), z=180),
    aug.Translation(x=0.5, y=0.5),
    # after the transforms: lifts it out of the floor
    aug.KeepAbove(floor),
])

generator = gen.Compose(
    [
        gen.Render(),
        gen.BBox({"milk_box": [milk_box]}),
        # a copy of the image with the boxes drawn, to check them
        gen.BBoxImage(),
    ],
    path="//dataset",
    resolution=(640, 480),
)

initial = state.State([milk_box])

for _ in range(100):
    standing_aug([milk_box])
    # the 20% where it isn't standing
    if not standing_aug.applied:
        lying_aug([milk_box])
    generator()
    initial.restore()
```

<img src="docs/images/carton-bboxes.jpg" alt="Three BBoxImage previews of the milk carton, standing and lying, with its box drawn" width="100%">

### Augmenting a material with shader nodes

![Material nodes: textures moved by the Vector node "Texture randomization", Value nodes "Surface Damage" and "Rust Amount", and an AOV Output "rust"](docs/images/rust-material-nodes.jpg)

With `Vector` you can randomize a procedural material between datapoints. Here a Vector
node "Texture randomization" moves the Mapping of all the material's textures (a Vector
node in shader nodes needs Blender 5). A single number pair in `value_range` is used for
every component, so `(-100, 100)` is the same as `((-100, -100, -100), (100, 100, 100))`.

```python
material = bpy.data.materials["Cube Material"]

# the paths are relative to the material
material_aug = aug.Compose([
    # moves all textures: a random offset on each axis
    aug.Vector(
        'node_tree.nodes["Texture randomization"].vector',
        value_range=(-100, 100),
    ),
])
```

<img src="docs/images/material-vector.jpg" alt="Three renders with different texture offsets" width="100%">

Any other single value, such as a Value node, can be controlled with `Number`. Here
"Surface Damage" sets the bump strength.

```python hl_lines="10-14"
material = bpy.data.materials["Cube Material"]

# the paths are relative to the material
material_aug = aug.Compose([
    # moves all textures: a random offset on each axis
    aug.Vector(
        'node_tree.nodes["Texture randomization"].vector',
        value_range=(-100, 100),
    ),
    # bump strength
    aug.Number(
        'node_tree.nodes["Surface Damage"].outputs[0].default_value',
        value_range=(0, 1),
    ),
])
```

<img src="docs/images/material-surface.jpg" alt="Three renders with different surface damage" width="100%">

With `p` you control the probability of an augmentation, here how often rust appears
in the dataset. `otherwise` is the value set the rest of the time, here no rust; without
it the value stays as it is.

```python hl_lines="15-21"
material = bpy.data.materials["Cube Material"]

# the paths are relative to the material
material_aug = aug.Compose([
    # moves all textures: a random offset on each axis
    aug.Vector(
        'node_tree.nodes["Texture randomization"].vector',
        value_range=(-100, 100),
    ),
    # bump strength
    aug.Number(
        'node_tree.nodes["Surface Damage"].outputs[0].default_value',
        value_range=(0, 1),
    ),
    # rust in 20% of the images, clearly visible; none in the others
    aug.Number(
        'node_tree.nodes["Rust Amount"].outputs[0].default_value',
        value_range=(0.5, 1),
        p=0.2,
        otherwise=0,
    ),
])
```

<img src="docs/images/material-rust.jpg" alt="Three renders with rust" width="100%">

To render the images, and a mask of the rust we just added, we can use a setup like
this. The rust factor goes to an AOV Output "rust" (Value), and a Value AOV "rust" is
added in View Layer Properties → Passes → Shader AOV. The engine is Cycles or EEVEE.

```python hl_lines="23-31"
material = bpy.data.materials["Cube Material"]

# the paths are relative to the material
material_aug = aug.Compose([
    # moves all textures: a random offset on each axis
    aug.Vector(
        'node_tree.nodes["Texture randomization"].vector',
        value_range=(-100, 100),
    ),
    # bump strength
    aug.Number(
        'node_tree.nodes["Surface Damage"].outputs[0].default_value',
        value_range=(0, 1),
    ),
    # rust in 20% of the images, clearly visible; none in the others
    aug.Number(
        'node_tree.nodes["Rust Amount"].outputs[0].default_value',
        value_range=(0.5, 1),
        p=0.2,
        otherwise=0,
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
```

Before we start generating, it is a good idea to save the state of the material, so it
can be restored after every augmentation.

```python hl_lines="32-33"
material = bpy.data.materials["Cube Material"]

# the paths are relative to the material
material_aug = aug.Compose([
    # moves all textures: a random offset on each axis
    aug.Vector(
        'node_tree.nodes["Texture randomization"].vector',
        value_range=(-100, 100),
    ),
    # bump strength
    aug.Number(
        'node_tree.nodes["Surface Damage"].outputs[0].default_value',
        value_range=(0, 1),
    ),
    # rust in 20% of the images, clearly visible; none in the others
    aug.Number(
        'node_tree.nodes["Rust Amount"].outputs[0].default_value',
        value_range=(0.5, 1),
        p=0.2,
        otherwise=0,
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
# saves the whole material: node values and settings
initial = state.State([material])
```

And now we can generate the images with the rust masks. The label lists each mask under
`"aovs"`, and `None` for the images without rust.

```python hl_lines="35-38"
material = bpy.data.materials["Cube Material"]

# the paths are relative to the material
material_aug = aug.Compose([
    # moves all textures: a random offset on each axis
    aug.Vector(
        'node_tree.nodes["Texture randomization"].vector',
        value_range=(-100, 100),
    ),
    # bump strength
    aug.Number(
        'node_tree.nodes["Surface Damage"].outputs[0].default_value',
        value_range=(0, 1),
    ),
    # rust in 20% of the images, clearly visible; none in the others
    aug.Number(
        'node_tree.nodes["Rust Amount"].outputs[0].default_value',
        value_range=(0.5, 1),
        p=0.2,
        otherwise=0,
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
# saves the whole material: node values and settings
initial = state.State([material])

for _ in range(100):
    material_aug([material])
    generator()
    initial.restore()
```

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
    # one of 8 headings, 45 degrees apart: (low, high, step)
    aug.Rotation(z=(0, 315, 45)),
])
camera_aug = aug.Compose([
    aug.LookAt(
        [car_1, car_2],
        distance=(6, 12),
        elevation=(10, 45),
        azimuth=(0, 360),
    ),
    aug.FocalLength((24, 85), target=[car_1, car_2], keep_size=True),
    # blurred in half of the images, sharp in the others
    aug.DepthOfField(car_1, f_stop=(1.4, 5.6), p=0.5, otherwise=False),
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
