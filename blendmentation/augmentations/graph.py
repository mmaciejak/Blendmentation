"""The graph of an augmentation `Compose` (what can happen, with probabilities) and the
record of a call (what happened, per object). Plain Python, no Blender needed.

Every node of the graph is an augmentation, with an id: the root's id, then the index
of each child down the tree, joined by dots ("0.2.1"). The augmentations describe
their tree through two hooks: `_graph_children()`, `[(child, share)]` where share is
the probability a child is reached when its parent runs (a `OneOf`'s weight share,
otherwise 1), and `_graph_values()`, `[(value, share)]` for augmentations that pick one
of a known list of values, or None.
"""

import numbers
from collections.abc import Mapping
from typing import Any, Optional

# attributes that hold state, or that the graph shows in another way (children,
# probabilities, values), left out of a node's settings
STATE = {"applied", "actual", "actual_x", "actual_y", "actual_z", "results", "_applied", "_objects", "augmentations",
         "p", "otherwise", "weights", "inputs", "by_kind", "color", "name"}


def plain(value: Any) -> Any:
    """A value JSON can store: datablocks and objects by name, vectors and other
    sequences as lists, anything else unknown as its repr."""
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, numbers.Integral):
        return int(value)
    if isinstance(value, numbers.Real):
        return float(value)
    if isinstance(value, Mapping):
        return {key if key is None or isinstance(key, (str, int, float, bool)) else str(plain(key)): plain(item)
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    if hasattr(value, "name") and isinstance(value.name, str):
        return value.name
    if hasattr(value, "__len__") and hasattr(value, "__getitem__"):
        return [plain(item) for item in value]
    return repr(value)


def is_augmentation(value: Any) -> bool:
    return hasattr(value, "results") or hasattr(value, "_graph_children")


def holds_augmentation(value: Any) -> bool:
    """Whether a setting is an augmentation, or a list or dict holding one."""
    if is_augmentation(value):
        return True
    if isinstance(value, Mapping):
        return any(holds_augmentation(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(holds_augmentation(item) for item in value)
    return False


def settings(node: Any) -> dict[str, Any]:
    """The arguments of an augmentation, as plain values."""
    found = {}
    for name, value in vars(node).items():
        if name in STATE or name.startswith("__") or holds_augmentation(value):
            continue
        if name == "data_path" and value is None:
            continue
        found[name] = plain(value)
    return found


def probability(value: float) -> float:
    """Rounded, so 0.8 * 0.75 is 0.6 and not 0.6000000000000001."""
    return round(value, 12)


def children(node: Any) -> list[tuple[Any, float]]:
    hook = getattr(node, "_graph_children", None)
    return list(hook()) if hook else []


def describe(node: Any, node_id: str = "0") -> dict:
    """The graph of an augmentation (or a `Compose`) and everything in it, with its id.
    An augmentation used in several places is described at the first, with `used_at`
    listing the others, where it is only `same_as` the first id: its `results` hold the
    calls of every place."""
    nodes: dict[str, dict] = {}
    entry = describe_node(node, node_id, 1.0, {}, nodes)
    for item in nodes.values():
        if "same_as" in item:
            nodes[item["same_as"]].setdefault("used_at", []).append(item["id"])
    return entry


def describe_node(node: Any, node_id: str, reached: float, seen: dict[int, str], nodes: dict[str, dict]) -> dict:
    """The graph of one augmentation and its children.

    Args:
        node: the augmentation.
        node_id: its id; its children get `<node_id>.<index>`.
        reached: probability it is called, per object of a call of the root.
        seen: ids of the augmentations already described, by `id()`: an augmentation
            used in two places is described once, the second time as `same_as`.
        nodes: every node described, by id.
    """
    p = getattr(node, "p", 1.0)
    p = 1.0 if p is None else float(p)
    applied = reached * p
    entry: dict[str, Any] = {"id": node_id, "type": type(node).__name__}
    if not is_augmentation(node):
        entry["name"] = getattr(node, "__name__", repr(node))
    elif getattr(node, "name", None):
        entry["name"] = node.name  # given by the user
    entry.update(reached=probability(reached), applied=probability(applied))
    nodes[node_id] = entry
    if id(node) in seen:
        entry["same_as"] = seen[id(node)]
        return entry
    seen[id(node)] = node_id
    if is_augmentation(node):
        entry.update(p=p, skipped=probability(reached - applied))
        otherwise = getattr(node, "otherwise", None)
        if otherwise is not None:
            entry["otherwise"] = {"value": plain(otherwise), "probability": probability(reached - applied)}
        entry["ignores_object"] = bool(getattr(node, "ignores_object", False))
        entry["settings"] = settings(node)
        values = value_probabilities(node, applied, reached - applied)
        if values is not None:
            entry["values"] = values
    kids = children(node)
    if kids:
        entry["children"] = [describe_node(child, f"{node_id}.{index}", applied * share, seen, nodes)
                             for index, (child, share) in enumerate(kids)]
    return entry


def value_probabilities(node: Any, applied: float, skipped: float) -> Optional[list[dict]]:
    """[{value, probability}] for an augmentation that picks one of a known list of
    values: each value's probability per object, the `otherwise` value included."""
    hook = getattr(node, "_graph_values", None)
    picks = hook() if hook else None
    if picks is None:
        return None
    values: list[dict] = []

    def add(value, probability):
        value = plain(value)
        for item in values:
            if item["value"] == value:
                item["probability"] += probability
                return
        values.append({"value": value, "probability": probability})

    for value, share in picks:
        add(value, applied * share)
    otherwise = getattr(node, "otherwise", None)
    if otherwise is not None:
        add(otherwise, skipped)
    for item in values:
        item["probability"] = probability(item["probability"])
    return values


def record(node: Any, node_id: str = "0", out: Optional[dict[str, Any]] = None,
           seen: Optional[set[int]] = None) -> dict[str, Any]:
    """What the last call of an augmentation (or a `Compose`) and everything in it did:
    `{node id: {object name: {"applied": bool, "value": ...}}}`, by the ids of
    `describe`. A `Compose` lists the objects it was called with, `{"applied": bool}`
    each. An augmentation that didn't run for any object is left out, and the key is
    None when it ignores the object."""
    out = {} if out is None else out
    seen = set() if seen is None else seen
    if id(node) in seen:
        return out
    seen.add(id(node))
    objects = getattr(node, "_objects", None)
    if objects is not None:
        out[node_id] = {name: {"applied": node.applied} for name in objects}
    else:
        results = getattr(node, "results", None)
        applied = getattr(node, "_applied", {})
        if results:
            out[node_id] = {key: {"applied": applied.get(key), "value": plain(value)} for key, value in results.items()}
    for index, (child, _) in enumerate(children(node)):
        record(child, f"{node_id}.{index}", out, seen)
    return out
