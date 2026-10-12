"""Draws on images as numpy arrays: the boxes, outlines and class names of the preview
images, and the backgrounds. Plain numpy, Blender's OpenImageIO has no text rendering."""

import numpy as np


# preview colors, one per class in order of appearance
CLASS_COLORS = (
    (230, 25, 75), (60, 180, 75), (0, 130, 200), (255, 225, 25), (245, 130, 48),
    (145, 30, 180), (70, 240, 240), (240, 50, 230), (210, 245, 60), (250, 190, 212),
)


# 3x5 pixel font for the class names, blender's OpenImageIO cannot render text.
# Rows top to bottom, other characters are drawn as "?"
FONT = {
    "A": "010101111101101", "B": "110101110101110", "C": "011100100100011", "D": "110101101101110",
    "E": "111100110100111", "F": "111100110100100", "G": "011100101101011", "H": "101101111101101",
    "I": "111010010010111", "J": "001001001101010", "K": "101101110101101", "L": "100100100100111",
    "M": "101111111101101", "N": "110101101101101", "O": "010101101101010", "P": "110101110100100",
    "Q": "010101101110011", "R": "110101110101101", "S": "011100010001110", "T": "111010010010010",
    "U": "101101101101111", "V": "101101101101010", "W": "101101111111101", "X": "101101010101101",
    "Y": "101101010010010", "Z": "111001010100111", "0": "111101101101111", "1": "010110010010111",
    "2": "110001010100111", "3": "110001010001110", "4": "101101111001001", "5": "111100110001110",
    "6": "011100111101111", "7": "111001010010010", "8": "111101111101111", "9": "111101111001110",
    " ": "000000000000000", "_": "000000000000111", "-": "000000111000000", ".": "000000000000010",
    ":": "000010000010000", "?": "110001010000010",
}


def noise(rng, height, width, cell_size, channels):
    """Random 8 bit (height, width, channels) noise in square cells of cell_size pixels."""
    rows = (np.arange(height) / cell_size).astype(np.int64)
    columns = (np.arange(width) / cell_size).astype(np.int64)
    cells = rng.integers(0, 256, (rows[-1] + 1, columns[-1] + 1, channels), dtype=np.uint8)
    return cells[rows][:, columns]


def srgb_to_linear(pixels):
    """8 bit sRGB to linear float."""
    values = pixels / 255.0
    return np.where(values <= 0.04045, values / 12.92, ((values + 0.055) / 1.055) ** 2.4)


def over_background(pixels, background):
    """Premultiplied 8 bit RGBA pixels over an 8 bit RGB background, opaque 8 bit RGBA."""
    alpha = pixels[:, :, 3:] / 255.0
    rgb = np.clip(np.rint(pixels[:, :, :3] + background * (1.0 - alpha)), 0, 255).astype(np.uint8)
    return np.concatenate([rgb, np.full(alpha.shape, 255, dtype=np.uint8)], axis=2)


def text_pixels(text, scale):
    """Boolean (height, width) array of the text in the 3x5 pixel font, scaled up."""
    glyphs = [np.array([int(bit) for bit in FONT.get(char, FONT["?"])], dtype=bool).reshape(5, 3)
              for char in text.upper()]
    spaced = [np.pad(glyph, ((0, 0), (0, 1))) for glyph in glyphs]
    pixels = np.concatenate(spaced, axis=1)[:, :-1] if spaced else np.zeros((5, 0), dtype=bool)
    return np.kron(pixels, np.ones((scale, scale), dtype=bool))


def paint(pixels, rows, columns, color):
    """Paints the clipped area with an RGB color, opaque when the image has alpha."""
    rows, columns = slice(max(rows.start, 0), max(rows.stop, 0)), slice(max(columns.start, 0), max(columns.stop, 0))
    pixels[rows, columns, :3] = color
    if pixels.shape[2] == 4:
        pixels[rows, columns, 3] = 255


def draw_tab(pixels, x, y, text, color, scale):
    """Draws the text on a filled tab above the point (below it when at the top edge)."""
    height, width = pixels.shape[:2]
    glyphs = text_pixels(text, scale)
    padding = scale
    tab_height, tab_width = glyphs.shape[0] + 2 * padding, glyphs.shape[1] + 2 * padding
    top = y - tab_height if y - tab_height >= 0 else y
    left = min(x, width - tab_width)
    paint(pixels, slice(top, top + tab_height), slice(left, left + tab_width), color)
    # dark text on bright colors
    text_color = (0, 0, 0) if 0.299 * color[0] + 0.587 * color[1] + 0.114 * color[2] > 150 else (255, 255, 255)
    rows, columns = np.nonzero(glyphs)
    rows, columns = rows + top + padding, columns + left + padding
    inside = (rows >= 0) & (rows < height) & (columns >= 0) & (columns < width)
    pixels[rows[inside], columns[inside], :3] = text_color


def draw_bbox(pixels, box, color, line_width):
    """Draws a bbox outline."""
    height, width = pixels.shape[:2]
    x_min, y_min = int(np.floor(box[0])), int(np.floor(box[1]))
    x_max, y_max = min(int(np.ceil(box[2])), width), min(int(np.ceil(box[3])), height)
    paint(pixels, slice(y_min, y_max), slice(x_min, x_min + line_width), color)
    paint(pixels, slice(y_min, y_max), slice(x_max - line_width, x_max), color)
    paint(pixels, slice(y_min, y_min + line_width), slice(x_min, x_max), color)
    paint(pixels, slice(y_max - line_width, y_max), slice(x_min, x_max), color)


def erode(mask, steps):
    """Shrinks a boolean mask by steps pixels (4-neighborhood)."""
    for _ in range(steps):
        padded = np.pad(mask, 1)
        mask = mask & padded[:-2, 1:-1] & padded[2:, 1:-1] & padded[1:-1, :-2] & padded[1:-1, 2:]
    return mask


def class_color(class_names, class_name):
    """Color of a class, by its position among the classes in the labels."""
    return CLASS_COLORS[class_names.index(class_name) % len(CLASS_COLORS)]
