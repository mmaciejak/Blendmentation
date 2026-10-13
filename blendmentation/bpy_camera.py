"""Camera values shared by augmentations and generating."""


def focus_distance(camera):
    """Distance from the camera to its focal plane, as Blender computes it: along the view
    axis to the focus object (or its bone), otherwise the focus distance setting."""
    dof = camera.data.dof
    target = dof.focus_object
    if target is None:
        return dof.focus_distance
    location = target.matrix_world.translation
    bone = getattr(dof, "focus_subtarget", "")
    if bone and target.pose and bone in target.pose.bones:
        location = target.matrix_world @ target.pose.bones[bone].head
    forward = camera.matrix_world.col[2].xyz.normalized()
    return max(abs(forward.dot(camera.matrix_world.translation - location)), 1e-5)
