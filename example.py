"""Example dataset script.

The scene needs:
- two mesh objects "Part.001" and "Part.002", using the material "material.001"
  (Principled BSDF), both with a shape key "Key 1"
- a mesh object "Clutter" that is in no class and can hide the parts, it is in the
  render in 60% of the datapoints and is set down on the floor
- a mesh object "Floor" under the parts, they are kept above it
- a geometry nodes group "Scatter" in a modifier on "Floor", with a Distribute Points on
  Faces node, scattering pebbles on the floor: instances of the objects of a collection
  "Pebbles" (Collection Info with Separate Children, Instance on Points with Pick
  Instance), without random scale (BOP has one size per class)
- a point light "Light" and the scene camera
- a world with a "Background" node (the default world has one)
- a shader AOV "Albedo" in View Layer > Passes > Shader AOV, written by an AOV
  Output node in the material
- Cycles or EEVEE as render engine (for the AOV and the passes)
- a transparent render (Render Properties > Film > Transparent) with RGBA output, and
  a folder "backgrounds" with images next to the .blend file, for the backgrounds

Run it from Blender's Scripting tab, with `blender -b scene.blend --python example.py`,
or with bpy as a Python module (see the README).
"""

import os
import sys

# not needed after `pip install -e .`
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import bpy  # noqa: E402

from blendmentation.augmentations import augmentations  # noqa: E402
from blendmentation.export import export  # noqa: E402
from blendmentation.generating import generating  # noqa: E402
from blendmentation.state import state  # noqa: E402

obj1 = bpy.data.objects["Part.001"]
obj2 = bpy.data.objects["Part.002"]
clutter = bpy.data.objects["Clutter"]
floor = bpy.data.objects["Floor"]
lamp = bpy.data.objects["Light"]
camera = bpy.context.scene.camera
world = bpy.context.scene.world
scatter = bpy.data.node_groups["Scatter"]
pebbles = bpy.data.collections["Pebbles"]
output_path = "//dataset"
n_datapoints = 100

# kept in a variable to read what it did to each part, see pipeline()
keep_above = augmentations.KeepAbove(floor, margin=0.01)
mesh_transform = augmentations.Compose(
    [
        augmentations.Translation(x=0.5, y=0.5, z=0.5),
        augmentations.Rotation(x=30.0, y=30.0, z=30.0),
        augmentations.SimpleMaterial(material_id="material.001", hue=(0.0, 1.0), saturation=(0.4, 0.9),
                               value=(0.2, 0.8), roughness=(0.2, 0.8), metallic=(0.0, 0.3)),
        augmentations.Number('data.shape_keys.key_blocks["Key 1"].value', value_range=(0.0, 1.0)),
        # after everything that moves or deforms the parts: lifts them out of the floor
        keep_above,
    ]
)

lamp_transforms = augmentations.Compose(
    [
        augmentations.Translation(x=0.5, y=0.5, z=0.5),
        augmentations.Rotation(x=30.0, y=30.0, z=30.0),
        augmentations.Number("data.shadow_soft_size", value_range=(0.1, 0.5)),
        # either a new brightness or a new color, a new brightness twice as often
        augmentations.OneOf(
            [
                augmentations.Number("data.energy", value_range=(600.0, 1400.0)),
                augmentations.Vector("data.color", value_range=(0.8, 1.0)),
            ],
            weights=[2, 1],
        ),
        # change the light type in only 30% of the datapoints
        augmentations.Menu("data.type", options=["POINT", "SPOT", "AREA"], weights=[2, 1, 1], p=0.3),
        augmentations.Boolean("data.use_shadow", p=0.8),
    ]
)

# a world is augmented like an object, the path is relative to it. SmartMaterial sets
# several inputs of one node (e.g. a smart material's group node), each its own way
world_transforms = augmentations.Compose([
    augmentations.SmartMaterial('node_tree.nodes["Background"]', {
        "Strength": (0.5, 1.5, 0.25),  # (low, high, step): 0.5, 0.75, ... 1.5
        # tinted in 30% of the datapoints, white in the others; alpha is kept
        "Color": augmentations.Input((0.6, 1.0), p=0.3, otherwise=1.0),
    }),
])

# a new random seed for every seed in the node group, so the pebbles move every time
scatter_seed = augmentations.Seed(scatter)

# the clutter is in the render 60% of the time, the bboxes, masks and keypoints follow.
# Only the clutter is scaled: BOP (export.bop) has one 3D model, with one size, per class
clutter_transforms = augmentations.Compose([
    augmentations.Visibility(p=0.6),
    augmentations.Scale(x=60.0, y=60.0, z=10.0, p=0.5),  # every augmentation takes p, how often it runs
    augmentations.Rotation(z=(0, 270, 90)),  # (low, high, step): turned by 0, 90, 180 or 270 degrees
    augmentations.PlaceOn(floor),  # after the transforms: up or down until it rests on the floor
])

# orbit the camera around both parts, always aimed at their center, zoom without changing
# how big the parts are in the image, and sometimes blur what is not in focus
camera_transforms = augmentations.Compose(
    [
        augmentations.LookAt([obj1, obj2], distance=(6, 10), elevation=(10, 50), azimuth=(0, 360), roll=(-5, 5)),
        augmentations.FocalLength((24, 85), target=[obj1, obj2], keep_size=True),
        # blurred in half of the datapoints; otherwise=False turns it off in the others
        augmentations.DepthOfField(obj1, f_stop=(1.4, 5.6), p=0.5, otherwise=False),
    ]
)

# per-class skip settings win over the BBox arguments
classes = {
    "part": {"instances": [obj1, obj2], "iou_deconflict": 0.3},
    # every pebble the Floor's geometry nodes scatter, cut by the frame or hidden by the parts as they come
    "pebble": {"instances": [generating.Instances(floor, of=pebbles)], "max_truncation": None, "max_occlusion": None},
}
image_generator = generating.Compose(
    [
        generating.Render(),
        # behind the transparent render: a random color, gray or color noise, or a photo twice as often
        generating.Background(weights={"color": 1, "white_noise": 1, "color_noise": 1, "image": 2},
                              noise_size=(1, 8), images_path="//backgrounds"),
        generating.AOVToImage(["Albedo"]),
        generating.Passes(["Depth", "Normal"]),
        generating.BBox(classes, max_truncation=0.3, max_occlusion=0.5),  # Clutter may hide half a part
        generating.BBoxImage(),  # extra image with the bboxes drawn on it, the main image stays clean
        # no file for empty masks; full_masks adds masks that ignore occlusion (for BOP)
        generating.Segmentation(classes, per="both", skip_empty=True, full_masks=True),
        generating.SegmentationImage(opacity=0.5),  # extra image with the masks drawn on it
        generating.RotationMatrix([obj1, obj2]),
        generating.Pose(classes),  # 6D pose in OpenCV camera axes
        generating.CameraData(),
        generating.Keypoints({
            "part_1_origin": obj1,
            "part_1_corner": (obj1, 0),
            "part_2_origin": obj2,
        }),
        generating.OutputField("light_energy", 'bpy.data.lights["Light"].energy'),
    ],
    path=output_path,
    resolution=(460, 460),
)

initial_state = state.State(
    [obj1, obj2, clutter, lamp, camera, world, scatter],  # world, node group: their node values
    fields=mesh_transform.augmentations + lamp_transforms.augmentations,
)


def pipeline():
    mesh_transform([obj1, obj2])
    lamp_transforms([lamp], p=0.7)  # a Compose call can take p: the whole list runs 70% of the time
    clutter_transforms([clutter])
    camera_transforms([camera])
    world_transforms([world])
    scatter_seed()
    # results holds the value for every object, by name (actual only the last one)
    image_generator({"lift": keep_above.results})
    initial_state.restore()


for i in range(n_datapoints):
    pipeline()

# training-ready annotations next to the images
export.coco(output_path)
export.yolo(output_path)
export.voc(output_path, bbox_from="mask")  # boxes of the visible pixels (from the masks), not the whole object
# BOP for 6D pose estimation (poses, camera, masks, depth). It assumes rigid objects with
# one mesh per class, so leave out the shape key augmentation for real pose training
export.bop(output_path)
