import bpy_generating as bpy_g


class Generator:
    """Renders and/or bbox generator
    with option to deconflict overlapping bboxes

    Args:
        path (str): path to save the images
        resolution (tuple): size of image to render
        bboxes (bool): True will generate bboxes
        rotation_matrix (bool): True will add roation matrix to labels
        iou_deconflict (float): Images with bboxes oberlapping over this value will not be rendered

    .. note::
        if iou_deconflict is None all images will be rendered
    """

    def __init__(self, path, resolution, bboxes, rotation_matrix, iou_deconflict=None):
        self.path = path
        self.resolution = resolution
        self.bboxes = bboxes
        self.rotation_matrix = rotation_matrix
        self.iou_deconflict = iou_deconflict

    def generate(self, custom_dict:dict=None):
        """Generate the renders and bboxes
        
        Args:
            custom_dict (dict): optional - key and values to be saved in the labels,
            apart from the standard data
        """

        bpy_g.render(self.path, self.resolution, self.bboxes, self.iou_deconflict, custom_dict)

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
