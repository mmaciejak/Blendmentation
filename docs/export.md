# Export

After generating, convert the dataset folder to standard formats:

```python
from blendmentation.export import export

export.coco("//dataset")   # <path>/coco.json
export.yolo("//dataset")   # <index>.txt next to every image, classes.txt, dataset.yaml
export.voc("//dataset")    # <path>/Annotations/<index>.xml
```

- **What gets exported:** datapoints with an image (a `Render` step) and with
  bboxes and/or instance masks.
- **Class ids:** classes are numbered in order of first appearance. Pass
  `classes=["car", "table"]` to `coco` or `yolo` to fix the order.
- **No Blender needed:** export runs in plain Python, so you can run it on another
  machine. COCO with masks needs `numpy` and `OpenImageIO`, which come with Blender.

| Format | Contents |
|---|---|
| `coco(path, output=None, classes=None, bbox_from="label")` | Instance segmentation as RLE from the instance masks, and bboxes. Instances whose mask is empty (fully hidden) are skipped. `bbox_from="mask"` uses the visible pixels of the mask for the bbox, instead of the geometric bbox that includes hidden parts. Each annotation also has an `"objects"` field with the object names. |
| `yolo(path, classes=None)` | One `class x_center y_center width height` line per bbox, normalized to 0–1. `dataset.yaml` points at the folder, ready for Ultralytics. |
| `voc(path, output_dir=None)` | One XML file per image, with 1-based pixel bboxes. Boxes touching the image border are marked `truncated`. |

## API reference

::: blendmentation.export.export
    options:
      members: [coco, yolo, voc]
