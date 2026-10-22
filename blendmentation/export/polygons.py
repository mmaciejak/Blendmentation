"""Polygons of masks, for the YOLO segmentation export. Plain numpy, no OpenCV: Blender
doesn't ship it.

A mask's outline follows the pixel edges, then moves onto the pixels at the edge, so its
vertices are pixel indices, as OpenCV's findContours gives them, and filling it like
Ultralytics does (cv2.fillPoly) gives back the mask. Each part (4-connected) gets its own outline,
holes are dropped (a polygon can't have them, they get filled), the outlines are
simplified with Douglas-Peucker, and the parts are joined into one polygon by zero-width
bridges, as Ultralytics joins the parts of a COCO polygon (`merge_multi_segment`).
"""

import numpy as np

# a step along a pixel edge, (dx, dy) with y down
STEPS = ((1, 0), (0, 1), (-1, 0), (0, -1))


def edges(mask):
    """Directed pixel edges between the mask and the background, as (x, y) start
    corners and step indices into STEPS. Every pixel's edges run clockwise on screen
    (top edge to the right), so an outline has the mask on its right."""
    padded = np.pad(mask, 1)
    inside = padded[1:-1, 1:-1]
    starts, steps = [], []
    # neighbor offset (rows, columns), start corner of the edge, step
    for (dr, dc), (x, y), step in (((-1, 0), (0, 0), 0), ((0, 1), (1, 0), 1),
                                   ((1, 0), (1, 1), 2), ((0, -1), (0, 1), 3)):
        neighbor = padded[1 + dr:padded.shape[0] - 1 + dr, 1 + dc:padded.shape[1] - 1 + dc]
        rows, columns = np.nonzero(inside & ~neighbor)
        starts.append(np.stack([columns + x, rows + y], axis=1))
        steps.append(np.full(len(rows), step))
    return np.concatenate(starts), np.concatenate(steps)


def outlines(mask):
    """Closed outlines of the mask's parts and holes, as (n, 2) arrays of pixel corners
    (x, y), without the repeated first point. Parts are 4-connected: where two pixels
    touch only at a corner, the outline turns around its own pixel."""
    return [points for points, _ in traced(mask)]


def traced(mask):
    """Like `outlines`, each with the step index of the edge leaving each point."""
    starts, steps = edges(mask)
    outgoing = {}
    for number, (x, y) in enumerate(starts.tolist()):
        outgoing.setdefault((x, y), []).append(number)
    used = np.zeros(len(starts), dtype=bool)
    loops = []
    for first in range(len(starts)):
        if used[first]:
            continue
        points, leaving, number = [], [], first
        while not used[number]:
            used[number] = True
            x, y = starts[number].tolist()
            points.append((x, y))
            step = int(steps[number])
            leaving.append(step)
            dx, dy = STEPS[step]
            corner = (x + dx, y + dy)
            candidates = [n for n in outgoing[corner] if not used[n]]
            if not candidates:
                break
            if len(candidates) > 1:
                # two parts meet at this corner: turn right, around the pixel on the right
                right = (step + 1) % 4
                candidates = [n for n in candidates if steps[n] == right] or candidates
            number = candidates[0]
        loops.append((np.array(points, dtype=np.float64), np.array(leaving)))
    return loops


def pixel_indices(points, leaving):
    """The outline of a part moved from pixel corners onto the pixels at its edge: a
    vertical edge with the mask on its left (going down) moves left by one, a horizontal
    edge with the mask above it (going left) up by one. Then its corners are the (x, y)
    indices of the edge pixels, as OpenCV's findContours gives them, and filling it the
    way OpenCV and Ultralytics do (`cv2.fillPoly`, which includes the edge pixels) gives
    back the mask exactly."""
    arriving = np.roll(leaving, 1)
    moved = points.copy()
    # each point is between the edge arriving at it and the one leaving it
    moved[:, 0] -= (leaving == 1) | (arriving == 1)
    moved[:, 1] -= (leaving == 2) | (arriving == 2)
    return moved


def cut_stairs(points):
    """Drops the inner corners of the stairs a diagonal edge makes (one step across and
    one down, turning left on the outline), so the edge goes straight from step to
    step, as OpenCV's contours do. The dropped corner pixel stays inside, so filling it
    the OpenCV way still gives the mask; at most every other point goes."""
    count = len(points)
    if count < 4:
        return points
    keep = np.ones(count, dtype=bool)
    for i in range(count):
        if not keep[i - 1]:
            continue
        before, after = points[i] - points[i - 1], points[(i + 1) % count] - points[i]
        unit_steps = abs(before).sum() == 1 and abs(after).sum() == 1
        if unit_steps and before[0] * after[1] - before[1] * after[0] == -1:
            keep[i] = False
    return points[keep]


def signed_area(points):
    """Shoelace area; positive for an outline that runs clockwise on screen (y down),
    the direction of a part's outline, negative for a hole's."""
    x, y = points[:, 0], points[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(np.roll(x, -1), y))


def simplify_path(points, tolerance):
    """Douglas-Peucker on an open path: keeps the end points and the points farther
    than tolerance from the simplified path. Distances are to the segment, not the
    whole line, so a spike that goes out and back along one line keeps its tip."""
    keep = np.zeros(len(points), dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        first, last = stack.pop()
        if last - first < 2:
            continue
        start, end = points[first], points[last]
        segment = end - start
        between = points[first + 1:last] - start
        squared = float(segment @ segment)
        along = np.clip(between @ segment / squared, 0, 1) if squared else np.zeros(len(between))
        offsets = between - along[:, None] * segment
        distances = np.hypot(offsets[:, 0], offsets[:, 1])
        farthest = int(np.argmax(distances))
        if distances[farthest] > tolerance:
            middle = first + 1 + farthest
            keep[middle] = True
            stack += [(first, middle), (middle, last)]
    return points[keep]


def simplify(loop, tolerance):
    """Douglas-Peucker on a closed outline, split at its first point and the point
    farthest from it. Collinear points always go; at least 3 points stay, repeated for
    a part one pixel wide (a line) or one pixel (a point)."""
    if len(loop) <= 3:
        return loop
    far = int(np.argmax(np.hypot(*(loop - loop[0]).T)))
    if far == 0:
        return loop[[0, 0, 0]]  # one pixel
    first = simplify_path(loop[:far + 1], tolerance)
    second = simplify_path(np.concatenate([loop[far:], loop[:1]]), tolerance)
    simplified = np.concatenate([first, second[1:-1]])
    if len(simplified) >= 3:
        return simplified
    # thinner than the tolerance: the point farthest from the line through the two
    (ax, ay), offsets = simplified[1] - simplified[0], loop - simplified[0]
    far_from_line = int(np.argmax(np.abs(ax * offsets[:, 1] - ay * offsets[:, 0])))
    return np.array([simplified[0], simplified[1], loop[far_from_line]])


def closest(first, second):
    """Indices of the closest pair of points of two outlines."""
    distances = ((first[:, None, :] - second[None, :, :]) ** 2).sum(axis=2)
    i, j = np.unravel_index(int(np.argmin(distances)), distances.shape)
    return int(i), int(j)


def join(parts):
    """One polygon through every part: each next part is bridged from the closest point
    of the polygon so far, walked around, and the walk returns over the same bridge, so
    the bridges add no area."""
    order = sorted(parts, key=lambda part: -abs(signed_area(part)))
    polygon = order[0]
    for part in order[1:]:
        i, j = closest(polygon, part)
        around = np.concatenate([part[j:], part[:j + 1]])
        polygon = np.concatenate([polygon[:i + 1], around, polygon[i:]])
    return polygon


def mask_polygon(mask, tolerance=0.0):
    """One polygon covering the mask, holes filled: (n, 2) array of (x, y) pixel
    indices of the pixels on its outline (see `pixel_indices`), with n >= 3, or None for
    an empty mask."""
    parts = [cut_stairs(pixel_indices(points, leaving)) for points, leaving in traced(np.asarray(mask, dtype=bool))
             if signed_area(points) > 0]
    if not parts:
        return None
    return join([simplify(part, tolerance) for part in parts])


def draw(polygon, shape):
    """The polygon drawn back as a boolean mask of shape (height, width), the way OpenCV's
    fillPoly draws integer points: the pixels inside it and those its edges pass through
    the centers of (exact for edges along a row, a column or a diagonal). For checking."""
    height, width = shape
    ys, xs = np.mgrid[0:height, 0:width]
    inside = np.zeros(shape, dtype=bool)
    on_edge = np.zeros(shape, dtype=bool)
    points = np.asarray(polygon, dtype=np.int64)
    for (ax, ay), (bx, by) in zip(points, np.roll(points, -1, axis=0)):
        cross = (bx - ax) * (ys - ay) - (by - ay) * (xs - ax)
        on_edge |= ((cross == 0) & (xs >= min(ax, bx)) & (xs <= max(ax, bx))
                    & (ys >= min(ay, by)) & (ys <= max(ay, by)))
        if ay != by:
            crosses = (ys >= min(ay, by)) & (ys < max(ay, by))
            inside ^= crosses & (xs < ax + (ys - ay) * (bx - ax) / (by - ay))
    return inside | on_edge
