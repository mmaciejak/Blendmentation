# Augmentations

Augmentations change the scene **in place**. `augmentations.Compose(list)` applies
every augmentation to every object you call it with: `compose([obj_1, obj_2])`. To
see the values actually sampled, read `.actual_x/.actual_y/.actual_z` on
`Translation`, `Rotation` and `Scale`, and `.actual` on the others.

Every augmentation, and `Compose` itself, takes `p`: the probability that it runs at
all (default 1). It is drawn on every call, so once per object inside a `Compose`, and
once per call for a `Compose`. After a call, `.applied` says whether it ran; when it was
skipped, the object is untouched and the actual values are `None`. `Boolean` is the
exception: `p` is the probability of `True`, and otherwise it sets `False`.

## Transforms

| Augmentation | Parameters | Effect |
|---|---|---|
| `Translation(x, y, z)` | blender units | added to the location |
| `Rotation(x, y, z)` | degrees | added to the rotation; works in euler, quaternion and axis-angle modes |
| `Scale(x, y, z)` | percent | the scale is multiplied by 1 ± p/100 |

Each parameter is a number `v`, which samples from `(-v, v)`, or a pair `(low, high)`.

## LookAt

```python
LookAt(target, distance=None, elevation=None, azimuth=None, roll=None, focal_length=None)
```

Moves a camera (or a light, or any object) to a random point on a sphere around a
target, and points it at the target, upright. The target stays in the centre of the
view.

- **`target`:** an object, a list of objects (aimed at the centre of their bounding
  boxes), or a point `(x, y, z)`.
- **`distance`:** in blender units.
- **`elevation`:** degrees above the target's horizontal plane. Avoid exactly ±90.
- **`azimuth`:** degrees around the world Z axis, where 0 is +X.
- **`roll`:** degrees around the camera's local Z axis. `None` keeps the camera upright.
- **`focal_length`:** the lens in mm; cameras only.

Each parameter is a `(min, max)` range, an exact number, or `None` to keep the current
value. Pass the camera to `State` to restore its transform and lens.

```python
camera_aug = augmentations.Compose([
    augmentations.LookAt(car_1, distance=(4, 9), elevation=(5, 45), azimuth=(0, 360),
                         roll=(-10, 10), focal_length=(24, 85)),
])
camera_aug([bpy.context.scene.camera])
```

## FocalLength

```python
FocalLength(focal_length, target=None, keep_size=False)
```

Sets the camera lens in mm, from a `(min, max)` range or an exact number. It works
with perspective cameras only.

With `keep_size=True` and a `target` (an object, a list of objects, or a point), the
camera also moves along the line to the target by the same ratio as the lens change.
This is a dolly zoom: the target keeps its size in the image while the perspective
changes.

- **When it's exact:** for parts of the target at its centre's depth, when the target
  is in the centre of the view (e.g. after `LookAt`).
- **When it's approximate:** an object seen at an angle has parts at other depths, so
  their size changes slightly. In testing, a plane seen up to 30° off head-on
  changed by about 0.01%. An off-centre target also moves in the image.

## DepthOfField

```python
DepthOfField(target=None, f_stop=None, p=1.0)
```

Turns depth of field on, focused on the target with a random f-stop. With `p` below 1,
the camera's depth of field settings are left as they are in the other images, so with
depth of field off in the scene only some images are blurred.

- **Focus:** on the `target` (an object, a list of objects, or a point). Focus is
  measured from where the camera is when this runs, so put it after `LookAt` and
  `FocalLength` in the `Compose`.
- **`f_stop`:** a `(min, max)` range or an exact number. Lower values give more blur.
- **`None`:** leaves the current focus or f-stop as it is.

```python
camera_aug = augmentations.Compose([
    augmentations.LookAt(car_1, distance=(4, 9), elevation=(5, 45), azimuth=(0, 360)),
    augmentations.FocalLength((24, 85), target=car_1, keep_size=True),
    augmentations.DepthOfField(car_1, f_stop=(1.4, 5.6), p=0.5),
])
```

## Material

```python
Material(material_id, hue=None, saturation=None, value=None, roughness=None, metallic=None)
```

Sets the Principled BSDF Base Color (as hue / saturation / value), Roughness and
Metallic to random values in `(min, max)` ranges, all 0–1. A parameter left as `None`
is not changed, and the sockets you change must not be connected to other nodes.

`Material` changes the **material** itself, so every object that uses it changes too.

## Any value, by data path

These four augmentations change any value you can reach through a data path: shader
node inputs, geometry nodes inputs, shape keys, light settings, modifier toggles,
and so on.

| Augmentation | For | Parameters |
|---|---|---|
| `Number(data_path, value_range=None, percent=None)` | int / float | `value_range=(min, max)` sets an absolute value; `percent=v` or `(low, high)` scales the current value. Give one of the two. Ints are rounded. |
| `Vector(data_path, value_range=None, percent=None)` | location, color, vector inputs… | Same as `Number`, applied to each component. A bound is a number for all components, or a sequence with one value per component; `None` keeps that component. |
| `Boolean(data_path, p=0.5)` | bool | `True` with probability `p`, otherwise `False`. |
| `Menu(data_path, options=None, weights=None)` | menus / enums | Picks a random option. With no `options`, it uses all the options of the menu. `weights` sets relative probabilities. |

```python
augmentations.Number('bpy.data.materials["Wood"].node_tree.nodes["Bump"].inputs["Strength"].default_value', value_range=(0.1, 1.0))
augmentations.Number('data.shape_keys.key_blocks["Smile"].value', value_range=(0, 1))
augmentations.Vector('bpy.data.materials["Mat"].node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value',
                     value_range=((0, 0, 0, None), (1, 1, 1, None)))      # random RGB, keep alpha
augmentations.Boolean('modifiers["GeometryNodes"]["Socket_3"]', p=0.3)
augmentations.Menu('data.type', options=["POINT", "SPOT", "AREA"], weights=[1, 1, 2])
```

**Data paths.** A path that starts with `bpy.` is absolute. To get one, right-click a
value in Blender's UI and choose **Copy Full Data Path**. An absolute path ignores the
object it is called with, so it can also be called on its own: `aug()`. Any other path
is relative to the object being augmented, for example `location[2]` or `data.energy`.
To change one component of a vector, add `[index]` to the path, e.g. `location[2]`.

**Geometry nodes inputs.** Blender 5 moved these inputs, so the path differs by
version. Copy Full Data Path gives the right one:

- Blender 4.x: `modifiers["GeometryNodes"]["Socket_2"]`
- Blender 5.x: `modifiers["GeometryNodes"].properties.inputs.Socket_2.value`

**Absolute paths inside a `Compose`.** These run once per object, so with `percent`
the change is applied several times over. Call them on their own instead.

## API reference

::: blendmentation.augmentations.augmentations
