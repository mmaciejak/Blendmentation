"""Example dataset script.

The scene needs:
- two mesh objects "Part.001" and "Part.002", using the material "material.001"
  (Principled BSDF), both with a shape key "Key 1"
- a point light "Light" and the scene camera
- a shader AOV "Albedo" in View Layer > Passes > Shader AOV, written by an AOV
  Output node in the material
- Cycles or EEVEE as render engine (for the AOV and the passes)

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
lamp = bpy.data.objects["Light"]
camera = bpy.context.scene.camera
output_path = "//dataset"
n_datapoints = 100

mesh_transform = augmentations.Compose(
    [
        augmentations.Translation(x=0.5, y=0.5, z=0.5),
        augmentations.Rotation(x=30.0, y=30.0, z=30.0),
        augmentations.Scale(x=60.0, y=60.0, z=10.0, p=0.5),  # every augmentation takes p, how often it runs
        augmentations.Material(material_id="material.001", hue=(0.0, 1.0), saturation=(0.4, 0.9),
                               value=(0.2, 0.8), roughness=(0.2, 0.8), metallic=(0.0, 0.3)),
        augmentations.Number('data.shape_keys.key_blocks["Key 1"].value', value_range=(0.0, 1.0)),
    ]
)

lamp_transforms = augmentations.Compose(
    [
        augmentations.Translation(x=0.5, y=0.5, z=0.5),
        augmentations.Rotation(x=30.0, y=30.0, z=30.0),
        augmentations.Number("data.energy", percent=40.0),
        augmentations.Number("data.shadow_soft_size", percent=20.0),
        augmentations.Vector("data.color", value_range=(0.8, 1.0)),
        # change the light type in only 30% of the datapoints
        augmentations.Menu("data.type", options=["POINT", "SPOT", "AREA"], weights=[2, 1, 1], p=0.3),
        augmentations.Boolean("data.use_shadow", p=0.8),
    ]
)

# orbit the camera around both parts, always aimed at their center, zoom without changing
# how big the parts are in the image, and sometimes blur what is not in focus
camera_transforms = augmentations.Compose(
    [
        augmentations.LookAt([obj1, obj2], distance=(6, 10), elevation=(10, 50), azimuth=(0, 360), roll=(-5, 5)),
        augmentations.FocalLength((24, 85), target=[obj1, obj2], keep_size=True),
        augmentations.DepthOfField(obj1, f_stop=(1.4, 5.6), p=0.5),
    ]
)

classes = {"part": [obj1, obj2]}
image_generator = generating.Compose(
    [
        generating.Render(),
        generating.AOVToImage(["Albedo"]),
        generating.Passes(["Depth", "Normal"]),
        generating.BBox(classes, iou_deconflict=0.5),
        generating.Segmentation(classes, per="both"),
        generating.RotationMatrix([obj1, obj2]),
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
    [obj1, obj2, lamp, camera],
    fields=mesh_transform.augmentations + lamp_transforms.augmentations,
)


def pipeline():
    mesh_transform([obj1, obj2])
    lamp_transforms([lamp])
    camera_transforms([camera])
    image_generator()
    initial_state.restore()


for i in range(n_datapoints):
    pipeline()

# training-ready annotations next to the images
export.coco(output_path)
export.yolo(output_path)
export.voc(output_path)
