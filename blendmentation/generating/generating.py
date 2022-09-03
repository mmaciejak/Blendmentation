import bpy_generating as bpy_g


class Generator:
    """Renders and/or bbox generator
    with option to deconflict overlapping bboxes

    Args:
        path (str): path to save the images
        resolution (tuple): size of image to render
        bboxes (bool): True will generate bboxes
        iou_deconflict (float): Images with bboxes oberlapping over this value will not be rendered

    .. note::
        if iou_deconflict is None all images will be rendered
    """

    def __init__(self, path, resolution, bboxes, iou_deconflict=None):
        self.path = path
        self.resolution = resolution
        self.bboxes = bboxes
        self.iou_deconflict = iou_deconflict

    def generate(self):
        """Generate the renders and bboxes"""

        bpy_g.render(self.path, self.resolution, self.bboxes, self.iou_deconflict)

    def preview(self, scaling_factor):
        """Allows for quick preview with limited image resolution

        Args:
            scaling_factor (float): scaling factor for preview images
        """

        bpy_g.render(
            self.path,
            self.resolution // scaling_factor,
            self.bboxes,
            self.iou_deconflict,
        )
