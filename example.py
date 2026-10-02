import sys
sys.path += [r"E:\blendmentation"]

import bpy
from blendmentation.augmentations import augmentations
from blendmentation.state import state
from blendmentation.generating import generating

obj1 = bpy.object
obj2 = bpy.object
lamp = bpy.lampobject

mesh_transform = augmentations.Compose(
    [
    augmentations.Translation(x=0.5, y= 0.5, z =0.5),
    augmentations.Rotation(x=30.0, y=30.0, z = 30.0),
    augmentations.Scale(x=60.0, y=60.0, z=10.0),
    augmentations.Material(material_id='material.001', hue=(0.0, 1.0), saturation=(0.4, 0.9),
                           value=(0.2, 0.8), roughness=(0.2, 0.8), metallic=(0.0, 0.3)),
    augmentations.Number('data.shape_keys.key_blocks["Key 1"].value', value_range=(0.0, 1.0)),
    ]
)

lamp_transforms = augmentations.Compose(
    [
    augmentations.Translation(x=0.5, y= 0.5, z =0.5),
    augmentations.Rotation(x=30.0, y=30.0, z = 30.0),
    augmentations.Number('data.energy', percent=40.0),
    augmentations.Number('data.shadow_soft_size', percent=20.0),
    augmentations.Vector('data.color', value_range=(0.8, 1.0)),
    ]
)

image_generator = generating.Compose(
    [
    generating.Render(),
    generating.AOVToImage(["Albedo"]),
    generating.BBox({"part": [obj1, obj2]}, iou_deconflict=0.5),
    generating.RotationMatrix([obj1, obj2]),
    generating.Segmentation({"part": [obj1, obj2]}, per="both"),
    generating.OutputField("light_energy", 'bpy.data.lights["Light"].energy'),
    ],
    path="",
    resolution=(460, 460),
)

def pipeline():
    mesh_transform([obj1, obj2])
    lamp_transforms([lamp])
    image_generator()
    initial_state.restore()

initial_state = state.State([obj1, obj2, lamp],
                            fields=mesh_transform.augmentations + lamp_transforms.augmentations)
n_datapoints = 100

for i in range (0, n_datapoints):
    pipeline()