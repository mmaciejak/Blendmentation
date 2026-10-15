"""Checks of plain Python values, shared by the subpackages. No Blender needed."""

import math
import numbers
from typing import Any, Optional


def is_number(value: Any) -> bool:
    """Whether value is an int or float, and not a bool (which Python counts as an int)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def is_point(value: Any) -> bool:
    """Whether value is a point (x, y, z): a list or tuple of three numbers."""
    return isinstance(value, (list, tuple)) and len(value) == 3 and all(is_number(v) for v in value)


def check_weights(weights: Any, count: Optional[int] = None, item: str = "option") -> None:
    """Raises a ValueError unless weights is None or a list of finite numbers >= 0 (numpy
    ones too) that don't sum to 0, with count of them when count is given. random.choices
    accepts negative weights and then picks a wrong option every time."""
    if weights is None:
        return
    if count is not None and len(weights) != count:
        raise ValueError(f"Give one weight per {item}, got {len(weights)} weights for {count}")
    if not all(isinstance(w, numbers.Real) and not isinstance(w, bool) and 0 <= float(w) < math.inf
               for w in weights):
        raise ValueError(f"weights must be numbers, not negative or infinite, got {weights!r}")
    if not sum(weights):
        raise ValueError(f"weights sum to 0, at least one needs a weight above 0, got {weights!r}")
