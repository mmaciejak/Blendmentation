"""Checks of plain Python values, shared by the subpackages. No Blender needed."""

from typing import Any


def is_number(value: Any) -> bool:
    """Whether value is an int or float, and not a bool (which Python counts as an int)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def is_point(value: Any) -> bool:
    """Whether value is a point (x, y, z): a list or tuple of three numbers."""
    return isinstance(value, (list, tuple)) and len(value) == 3 and all(is_number(v) for v in value)
