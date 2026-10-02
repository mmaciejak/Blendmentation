from . import bpy_generating as bpy_g


class Generator:
    """Renders and/or bbox generator
    with option to deconflict overlapping bboxes

    Args:
        path (str): path to save the images
        resolution (tuple): size of image to render
        bboxes (bool): True will generate bboxes, default False
        rotation_matrix (bool): True will add roation matrix to labels, default False
        iou_deconflict (float): Images with bboxes oberlapping over this value will not be rendered
        objects (list): objects to label, None = all visible mesh objects in the scene.
            A sublist of objects is labeled as one object, with one bbox and one mask,
            e.g. [car, [table_top, table_leg_1, table_leg_2]]
        aovs (list): names of shader AOVs (View Layer > Passes > Shader AOV) to save
            next to the image as <index>_<aov>.exr, needs Cycles or EEVEE
        aov_format (str): "OPEN_EXR" (32 bit float) or "PNG" (8 bit, values clamped to 0-1)
        segmentation (bool): True will save a black and white mask of the visible pixels
            of every object (group) as <index>_mask_<n>.png, n is the position in objects

    .. note::
        if iou_deconflict is None all images will be rendered
    """

    def __init__(
        self,
        path,
        resolution,
        bboxes=False,
        rotation_matrix=False,
        iou_deconflict=None,
        objects=None,
        aovs=None,
        aov_format="OPEN_EXR",
        segmentation=False,
    ):
        self.path = path
        self.resolution = resolution
        self.bboxes = bboxes
        self.rotation_matrix = rotation_matrix
        self.iou_deconflict = iou_deconflict
        self.objects = objects
        self.aovs = aovs
        self.aov_format = aov_format
        self.segmentation = segmentation

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
            self.aovs,
            self.aov_format,
            self.segmentation,
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
            aovs=self.aovs,
            aov_format=self.aov_format,
            segmentation=self.segmentation,
        )
