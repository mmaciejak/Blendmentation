from . import bpy_generating as bpy_g


class Generator:
    """Renders and/or bbox generator
    with option to deconflict overlapping bboxes

    Args:
        path (str): path to save the images
        resolution (tuple): size of image to render
        bboxes (bool): True will generate bboxes
        rotation_matrix (bool): True will add roation matrix to labels
        iou_deconflict (float): Images with bboxes oberlapping over this value will not be rendered
        objects (list): objects to label, None = all visible mesh objects in the scene

    .. note::
        if iou_deconflict is None all images will be rendered
    """

    def __init__(self, path, resolution, bboxes, rotation_matrix, iou_deconflict=None, objects=None):
        self.path = path
        self.resolution = resolution
        self.bboxes = bboxes
        self.rotation_matrix = rotation_matrix
        self.iou_deconflict = iou_deconflict
        self.objects = objects

    def generate(self, custom_dict:dict=None):
        """Generate the renders and bboxes
        
        Args:
            custom_dict (dict): optional - key and values to be saved in the labels,
            apart from the standard data

        Returns:
            bool: False if the image was skipped by iou_deconflict
        """

        return bpy_g.render(
            self.path,
            self.resolution,
            self.bboxes,
            self.rotation_matrix,
            self.iou_deconflict,
            custom_dict,
            self.objects,
        )

    def preview(self, scaling_factor):
        """Allows for quick preview with limited image resolution

        Args:
            scaling_factor (float): scaling factor for preview images
        """

        return bpy_g.render(
            self.path,
            tuple(int(size / scaling_factor) for size in self.resolution),
            self.bboxes,
            self.rotation_matrix,
            self.iou_deconflict,
            objects=self.objects,
        )
