import sys
sys.path += [r"E:\blendmentation"]

import bpy
from blendmentation.augmentations import augmentations
from blendmentation.state import state
from blendmentation.generating import generating

mesh_transform = augmentations.Compose(
    [
    augmentations.Translation(x=0.5, y= 0.5, z =0.5),
    augmentations.Rotation(x=30.0, y=30.0, z = 30.0),
    augmentations.Scale(x=60.0, y=60.0, z=10.0),
    augmentations.Color(material_id = 'material.001', h = 20.0, s=60.0,v=20.0),
    augmentations.Shader(material_id= 'material.001', roughness=40.0, normals = 5.0)
    ]
)

lamp_transforms = augmentations.Compose(
    [
    augmentations.Translation(x=0.5, y= 0.5, z =0.5),
    augmentations.Rotation(x=30.0, y=30.0, z = 30.0),
    augmentations.Lamp(strength=40.0,size=20.0,temp=30.0),
    ]
)

image_generator = generating.Generator(path ="",
                                       resolution=(460,460),
                                       bboxes=True,
                                       rotation_matrix=True)

def pipeline():
    mesh_transform([obj1, obj2])
    lamp_transforms([lamp])
    lamp_transforms.augmentations.Lamp
    image_generator.generate()
    initial_state.restore()

obj1 = bpy.object
obj2 = bpy.object
lamp = bpy.lampobject

initial_state = state.State([obj1, obj2, lamp])
n_datapoints = 100

for i in range (0, n_datapoints):
    pipeline()