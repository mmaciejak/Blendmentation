"""Randomize objects, materials and any other value in the scene, in place.

`Compose` applies a list of augmentations to every object it is called with. Each
augmentation takes `p`, the probability that it runs, drawn per object; a call can
override it, `aug(obj, p=0.5)`. After a
call, `applied` says whether it ran and `actual` (or `actual_x/y/z`) holds the values
it set. `Number`, `Vector`, `Boolean`, `Menu`, `MaterialSlot`, `UVMap`, `Visibility`,
`ShadowCatcher`, `FocalLength` and `DepthOfField` also take `otherwise`, a value to set when they don't run. Each call overwrites them, so after a `Compose` call they describe only the
last object; `results` holds the values for every object, by name. Augment inside
`with state.restoring():`, which sets everything back when the datapoint is done.
`Node` sets the inputs of a node (e.g. the group node of a smart material or of a
geometry nodes setup), each with its own `Number`, `Vector`, `Boolean` or `Menu`
written without a data path: the `Node` gives each the path of its input;
`Modifier` does the same for the inputs of a geometry nodes modifier, by name, or the
settings of any other modifier (Subdivision Surface, Bevel...).
`Node.template` writes a `Node` with every input of a node and its range, to paste and
edit; the Blender add-on copies it for the selected node. `Modifier.template` and
`Chain.shape_keys_template` do the same for a modifier and for the shape keys of an
object.
`MaterialSlot` gives an object one of the materials in its own slots, and `UVMap` one
of its UV maps.
`Visibility` shows or hides objects in the render, and the labels follow it.
`ShadowCatcher` makes an object (a floor) a shadow catcher, rendering only the shadows on
it (Cycles).
`KeepAbove`, placed after the transforms, lifts objects out of a floor or terrain;
`PlaceOn` also lowers them, so they rest on it. `Seed` gives every seed in a node
group (e.g. a geometry nodes scatter) its own random value. `PlaceOnCurve` moves a
camera (or any object) to a random point on a curve, optionally facing along it. `OneOf` applies one
augmentation from a list, picked at random by weight, and `Chain` applies a list to one
object as one augmentation, e.g. to pick between whole sequences in a `OneOf`.
`Compose.describe()` is the graph of what a `Compose` can do, with the probability of
each outcome per object; `graph_preview` draws it on an HTML page before generating, and
`generating.Augmentations` saves it and records what happened in every label.

`Number`, `Vector`, `Boolean` and `Menu` change any value by its data path. A path
starting with `bpy.` is absolute: right click a value in Blender > Copy Full Data
Path. Any other path is relative to the augmented object, e.g. `data.energy`.
The augmentations that change an object (`Translation`, `Rotation`, `Scale`,
`Visibility`, `ShadowCatcher`, `KeepAbove`, `PlaceOn`, `LookAt`, `PlaceOnCurve`,
`FocalLength`, `DepthOfField`, `MaterialSlot`, `UVMap`, `SimpleMaterial`) change the object they are called
with, or with `data_path='bpy.data.objects["Camera"]'` always that object, so they can
also be called alone, `aug()`.
Geometry nodes inputs moved in Blender 5, so their path depends on the version:
`modifiers["GeometryNodes"]["Socket_2"]` in 4.x,
`modifiers["GeometryNodes"].properties.inputs.Socket_2.value` in 5.x. `Modifier`
finds them by name in every version.
"""

from __future__ import annotations

import os
import pathlib
import random
import webbrowser
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any, Optional, Union

from ..export import export
from ..values import check_weights, is_number
from . import bpy_augmentations as bpy_a
from . import graph

if TYPE_CHECKING:
    from bpy.types import Modifier as BpyModifier, Node as BpyNode, NodeTree, Object  # pyright: ignore[reportMissingModuleSource]  (bpy.types only exists at runtime)

#: a number v samples from (-v, v), a pair (low, high) from (low, high), a triple
#: (low, high, step) picks one of low, low + step, ... up to high
Offset = Union[float, tuple[float, float], tuple[float, float, float]]
#: a (min, max) range, or (min, max, step) for one of min, min + step, ... max
Range = Union[tuple[float, float], tuple[float, float, float]]
#: a (min, max) range or an exact number
RangeOrValue = Union[float, tuple[float, float]]
#: an object, objects (the center of their bounding boxes), or a point (x, y, z)
Target = Union["Object", Sequence["Object"], tuple[float, float, float]]
#: a number for all components, or one value per component (None keeps it)
Bound = Union[float, Sequence[Optional[float]]]


def check_p(p: float) -> float:
    if not 0 <= p <= 1:
        raise ValueError("p must be between 0 and 1")
    return p


def check_otherwise(otherwise: Any, valid: bool, expected: str) -> Any:
    if otherwise is not None and not valid:
        raise ValueError(f"otherwise must be {expected} or None, got {otherwise!r}")
    return otherwise


def check_offset(offset: Offset, axis: str) -> Offset:
    if is_number(offset):
        return offset
    if not isinstance(offset, Sequence) or len(offset) not in (2, 3) or not all(is_number(v) for v in offset):
        raise ValueError(f"{axis} must be a number, (low, high) or (low, high, step), got {offset!r}")
    if offset[0] > offset[1]:
        raise ValueError(f"{axis}: low must not be above high, got {offset!r}")
    if len(offset) == 3 and offset[2] <= 0:
        raise ValueError(f"{axis}: step must be above 0, got {offset!r}")
    return offset


def check_range(value_range: Any, name: str) -> Any:
    """A (low, high) or (low, high, step) range; check_offset also takes a single number."""
    if is_number(value_range):
        raise ValueError(f"{name} must be (low, high) or (low, high, step), got {value_range!r}")
    return check_offset(value_range, name)


def check_fraction(value: Any, name: str) -> Any:
    """A fraction 0-1, or a (low, high) range of them."""
    values = [value] if is_number(value) else value
    if (not isinstance(values, Sequence) or len(values) not in (1, 2) or not all(is_number(v) for v in values)
            or not all(0 <= v <= 1 for v in values) or values[0] > values[-1]):
        raise ValueError(f"{name} must be a number or (low, high) between 0 and 1, got {value!r}")
    return value


def happens(p: float) -> bool:
    """True with probability p. p = 1 draws no random number, so seeded runs don't change."""
    return p >= 1 or random.random() < p


def flatten(augmentations: Sequence[Any]) -> list[Any]:
    """Each item and, after it, the items in its `augmentations` (a `OneOf`, a `Compose`), depth first."""
    flat = []
    for augmentation in augmentations:
        flat.append(augmentation)
        flat.extend(flatten(getattr(augmentation, "augmentations", ())))
    return flat


def compose_roots(augmentations: Any) -> list[tuple[str, Compose]]:
    """[(root id, Compose)]: one Compose ("0"), a list ("0", "1"...) or a dict {name: Compose}."""
    if isinstance(augmentations, Compose):
        roots = [("0", augmentations)]
    elif isinstance(augmentations, dict):
        roots = list(augmentations.items())
    elif isinstance(augmentations, (list, tuple)):
        roots = [(str(index), compose) for index, compose in enumerate(augmentations)]
    else:
        raise TypeError(f"Give augmentation Composes: one, a list or a dict {{name: Compose}}, got {augmentations!r}")
    if not roots:
        raise ValueError("Give at least one augmentation Compose")
    for name, compose in roots:
        if not isinstance(name, str):
            raise TypeError(f"The names of the Composes must be strings, got {name!r}")
        if not isinstance(compose, Compose):
            raise TypeError(f"Expected an augmentations.Compose, got {compose!r} for '{name}'")
    return roots


def graph_preview(composes: Any, path: str, show: bool = False) -> str:
    """Draws what augmentation `Compose`s can do on an HTML page, before generating.

    The same page as `export.augmentation_report`, without a dataset: a flowchart where
    the steps of a `Compose` or `Chain` run left to right in a frame and a `OneOf` forks
    to its branches (with their weight and share), which join again. Each augmentation is
    a card with a bar of how often it runs per object and, for a `Menu`,
    `MaterialSlot` or `UVMap` with its options listed, a `Boolean`, a `Visibility` or a
    `ShadowCatcher`, one per value (`Compose.describe`). Check the probabilities of a
    pipeline before a long run; after it, `export.augmentation_report` adds what the dataset did.

    Args:
        composes: augmentation `Compose`s: one, a list (ids `"0"`, `"1"`...) or a dict
            `{name: Compose}` (ids the names), as for `generating.Augmentations`.
        path: the HTML file to write (ending in `.html`), or a folder to write
            `augmentations_preview.html` to, made when it doesn't exist yet (e.g. the
            generating path before the first datapoint). `//` paths are relative to the
            .blend file.
        show: open the page in the web browser.

    Returns:
        The path of the HTML file.

    Raises:
        TypeError: an item is not an `augmentations.Compose`.

    Example:
        ```python
        augmentations.graph_preview({"objects": objects_aug, "camera": camera_aug}, "//preview.html", show=True)
        ```
    """
    roots = [graph.describe(compose, name) for name, compose in compose_roots(composes)]
    output = export.dataset_path(path)
    if not output.lower().endswith((".html", ".htm")):
        output = os.path.join(output, "augmentations_preview.html")
    os.makedirs(os.path.dirname(output), exist_ok=True)
    export.write_report(output, "Augmentations preview", None, roots)
    if show:
        webbrowser.open(pathlib.Path(output).as_uri())
    return output


def weight_shares(weights: Optional[Sequence[float]], count: int) -> list[float]:
    """The probability of each of count items picked by weights (None = equal)."""
    if weights is None:
        return [1 / count] * count
    total = sum(weights)
    return [weight / total for weight in weights]


class Compose:
    """Applies a list of augmentations to every object it is called with, in place.

    Each augmentation runs once per object, in list order: all of them on the first
    object, then all of them on the next one. Every augmentation draws its own `p` for
    each object, so with `Visibility(p=0.5)` each object is shown or hidden
    independently. Call it inside `with state.restoring():`, so the scene is set back
    afterwards.

    Every call first clears the `results` of its augmentations, also of the ones
    nested in a `OneOf`, so afterwards they hold the values for the objects of this
    call only, also when the call is skipped by `p` or a `OneOf` didn't pick them
    (then they are empty). `actual` and `applied` on an augmentation describe
    only the last object, read `results` for all of them.

    Args:
        augmentations: augmentations to apply, each a callable taking one object.
        p: probability of applying the whole list, drawn once per call. A call can
            override it with its own `p`.
        name: a name for it in the graph and the report (`Compose.describe`,
            `export.augmentation_report`), e.g. `"slide sideways"`. None: its class name.

    Attributes:
        applied (bool | None): whether the last call applied the augmentations.

    Example:
        ```python
        objects_aug = augmentations.Compose([
            augmentations.Translation(x=0.5, y=0.5),
            augmentations.Rotation(z=180),
        ])
        objects_aug([car_1, car_2])
        objects_aug([car_1, car_2], p=0.5)  # this call: the whole list half of the time
        ```

        The values each augmentation set, per object:

        ```python
        visibility = augmentations.Visibility(p=0.5)
        boxes_aug = augmentations.Compose([visibility])
        boxes_aug([box_1, box_2, box_3])
        visibility.results   # e.g. {"box_1": False, "box_2": True, "box_3": False}
        visibility.actual    # False, only box_3
        ```
    """

    def __init__(self, augmentations: list[Callable[[Object], Any]], p: float = 1.0, name: Optional[str] = None):
        self.augmentations = augmentations
        self.p = check_p(p)
        self.name = check_name(name)
        self.applied: Optional[bool] = None
        self._objects: list[str] = []

    def __call__(self, blender_objects: Sequence[Object], p: Optional[float] = None) -> None:
        """Applies the augmentations to the objects.

        Args:
            blender_objects: objects to augment.
            p: probability of applying the whole list for this call only, instead of
                the `p` given at construction.

        Raises:
            ValueError: if `p` is not between 0 and 1.
        """
        for augmentation in flatten(self.augmentations):
            for name in ("results", "_applied"):
                results = getattr(augmentation, name, None)
                if isinstance(results, dict):
                    results.clear()
        self._objects = [blender_object.name for blender_object in blender_objects]
        self.applied = happens(self.p if p is None else check_p(p))
        if not self.applied:
            return
        for blender_object in blender_objects:
            for augmentation in self.augmentations:
                augmentation(blender_object)

    def describe(self) -> dict[str, Any]:
        """The graph of what this `Compose` can do: every augmentation in it, nested
        ones included, with the probability of each outcome per object.

        The probabilities multiply down the tree: a `Compose` and a `Chain` reach their
        augmentations when they run (`p`), a `OneOf` reaches each one with its weight
        share, a `Node` or `Modifier` reaches its inputs. They assume the `p` given at
        construction, not one given to a call, and leave out what the scene decides:
        options read from Blender, an object not over the `PlaceOn` surface, datapoints
        a `generating.BBox` skips. `generating.Augmentations` saves this graph next to the
        dataset and records what each augmentation did in every label, and
        `export.augmentation_stats` compares the two.

        Each node has:

        - `id`: `"0"` for the `Compose`, then the index of each augmentation down the
          tree, e.g. `"0.2.1"`: the 2nd augmentation of the 3rd one;
        - `type`, `name` (the `name` argument, when given) and `settings`, the
          augmentation's other arguments;
        - `reached`: the probability it is called, `applied`: that it runs (its `p`
          drawn), `skipped`: that it is called but doesn't run, `otherwise`: the value it
          sets then, `{value, probability}`;
        - `values`: for a `Menu`, `MaterialSlot` or `UVMap` with its options listed, a
          `Boolean`, a `Visibility` or a `ShadowCatcher`, each value it can set,
          `{value, probability}`, `otherwise` included;
        - `ignores_object`: it changes the same thing for every object (an absolute data
          path, `Seed`), so only its last call counts;
        - `children`; an augmentation used twice is described once, the second time
          only as `same_as` its first id. The children of a `OneOf` have `weight` (1
          each without `weights`) and `share`, the fraction of its picks they get.

        Returns:
            The `Compose` as the root node, plain values JSON can store.

        Example:
            ```python
            lights_aug = augmentations.Compose([
                augmentations.OneOf([
                    augmentations.Menu("data.type", options=["SPOT", "AREA"]),
                    augmentations.Number("data.energy", (100, 1000)),
                ], weights=[1, 3]),
            ], p=0.5)
            graph = lights_aug.describe()
            graph["children"][0]["children"][0]["applied"]   # 0.125: 0.5 * 1/4
            graph["children"][0]["children"][0]["values"]
            # [{"value": "SPOT", "probability": 0.0625}, {"value": "AREA", "probability": 0.0625}]
            ```
        """
        return graph.describe(self)

    def _graph_children(self) -> list[tuple[Any, float]]:
        return [(augmentation, 1.0) for augmentation in self.augmentations]


class Augmentation:
    """Base class of the augmentations.

    It runs with probability `p`, drawn on every call, so once per object in a
    `Compose`. A skipped augmentation leaves the object unchanged, unless it has an
    `otherwise` value: then it sets that. `Number`, `Vector`, `Boolean`, `Menu`,
    `MaterialSlot`, `UVMap`, `Visibility`, `ShadowCatcher`, `FocalLength` and `DepthOfField` take
    `otherwise`; it is None (keep the value) by default, except for `Boolean` (False),
    `Visibility` (hidden) and `ShadowCatcher` (a normal object).
    A `Compose` skipped by its own `p` runs none of its augmentations, so their
    `otherwise` isn't set either.

    Subclasses implement `apply(obj)` and store what they sampled in `actual`, and
    `set_otherwise(obj)` when they take `otherwise`.

    `applied` and `actual` (`actual_x/y/z` for `Translation`, `Rotation` and `Scale`)
    describe only the last call. A `Compose` calls each augmentation once per object,
    so after `Compose([aug])([a, b, c])` they hold what happened to `c`; the values for
    `a` and `b` were overwritten. `results` keeps them for every object: each call
    stores its `actual` under the object's name, and `Translation`, `Rotation` and
    `Scale` store
    `(x, y, z)`. A call without an object, and every call of an augmentation that
    doesn't use the object (an absolute data path, `Seed`, or a `OneOf` or `Chain` of
    only those), stores it under `None`: in a `Compose` over several objects it runs
    once per object on the same value, so `results` holds only the last one, the one
    kept. With `data_path`, an augmentation that changes an object stores it under that
    object's name. A skipped call stores what it set: its `otherwise` value, or `None`
    when it left the value unchanged. A `Compose` call clears
    `results` first; direct calls add to it, so clear it yourself
    (`aug.results.clear()`) when you call an augmentation in your own loop.

    A direct call can override `p` for that call only, like a `Compose` call:
    `rust(p=0.2)`.

    Args:
        p: probability of applying the augmentation, 0-1.
        name: a name for it in the graph and the report (`Compose.describe`,
            `export.augmentation_report`), e.g. `"slide sideways"`. None: its class name.

    Attributes:
        applied (bool | None): whether the last call applied it.
        actual (Any): the values set by the last call, None when it was skipped
            and left the value unchanged.
        results (dict[str | None, Any]): the values set for each object, by object
            name, since the last `Compose` call (or since it was cleared).

    Example:
        ```python
        visibility = augmentations.Visibility(p=0.5)
        augmentations.Compose([visibility])([box_1, box_2, box_3])
        visibility.results   # e.g. {"box_1": False, "box_2": True, "box_3": False}
        visibility.actual    # False: only the last object, box_3

        rotation = augmentations.Rotation(z=180)
        for car in [car_1, car_2]:
            rotation(car)
            print(car.name, rotation.actual_z)   # read actual after each call

        rotation(car_1, p=0.5)   # this call only: half of the time
        ```
    """

    def __init__(self, p: float = 1.0, otherwise: Any = None, name: Optional[str] = None):
        self.p = check_p(p)
        self.name = check_name(name)
        self.otherwise = otherwise
        self.applied: Optional[bool] = None
        self.actual: Any = None
        self.results: dict[Optional[str], Any] = {}
        self._applied: dict[Optional[str], bool] = {}  # applied of each call, by the keys of results

    def __call__(self, obj: Optional[Object] = None, p: Optional[float] = None) -> None:
        """Applies the augmentation with probability `p`, and records it in `results`.

        Args:
            obj: object to augment, not needed for absolute data paths.
            p: probability of applying it for this call only, instead of the `p` given
                at construction.

        Raises:
            ValueError: if `p` is not between 0 and 1.
        """
        self.applied = happens(self.p if p is None else check_p(p))
        if self.applied:
            self.apply(obj)
        else:
            self.skip(obj)
        key = self._result_key(obj)
        self.results[key] = self._result()
        self._applied[key] = self.applied

    @property
    def ignores_object(self) -> bool:
        """Whether it changes the same thing whatever object it is called with (an
        absolute data path, `Seed`): its `results` are then stored under None, so a
        `Compose` over several objects leaves only the last value, the one kept."""
        return False

    def _result_key(self, obj: Optional[Object]) -> Optional[str]:
        """The key of a call in `results`."""
        return None if obj is None or self.ignores_object else obj.name

    def _graph_children(self) -> list[tuple[Any, float]]:
        """[(child, probability it is reached when this one runs)], for the graph."""
        return [(augmentation, 1.0) for augmentation in getattr(self, "augmentations", ())]

    def _graph_values(self) -> Optional[list[tuple[Any, float]]]:
        """[(value, share)] when it runs, for an augmentation that sets one of a known
        list of values, otherwise None."""
        return None

    def _result(self) -> Any:
        """The value stored in `results` for the last call."""
        return self.actual

    def apply(self, obj: Optional[Object]) -> None:
        raise NotImplementedError

    def skip(self, obj: Optional[Object]) -> None:
        self.actual = None if self.otherwise is None else self.set_otherwise(obj)

    def set_otherwise(self, obj: Optional[Object]) -> Any:
        """Sets the `otherwise` value, and returns what is stored in `actual`."""
        raise NotImplementedError


def check_name(name: Any) -> Optional[str]:
    """None, or a name for the graph: a non-empty string."""
    if name is not None and (not isinstance(name, str) or not name.strip()):
        raise TypeError(f"name must be a non-empty string, got {name!r}")
    return name


def is_absolute(data_path: Any) -> bool:
    """Whether a data path is absolute (starting with `bpy.`)."""
    return isinstance(data_path, str) and data_path.startswith(("bpy.", "bpy["))


def check_object_path(data_path: Optional[str]) -> Optional[str]:
    """None, or an absolute data path (starting with `bpy.`)."""
    if data_path is not None and not is_absolute(data_path):
        raise ValueError(f"data_path must be an absolute path to an object, e.g. "
                         f"'bpy.data.objects[\"Camera\"]', got {data_path!r}")
    return data_path


class ObjectAugmentation(Augmentation):
    """An augmentation that changes the object it is called with, or, with `data_path`,
    always the object at that absolute path: then it ignores the objects of a `Compose`
    (it runs once per object, on the same one) and can be called alone, `aug()`, like a
    data-path augmentation with an absolute path. `results` is then stored under that
    object's name."""

    def __init__(self, p: float = 1.0, otherwise: Any = None, data_path: Optional[str] = None,
                 name: Optional[str] = None):
        super().__init__(p, otherwise, name=name)
        self.data_path = check_object_path(data_path)

    @property
    def ignores_object(self) -> bool:
        return self.data_path is not None

    def _result_key(self, obj: Optional[Object]) -> Optional[str]:
        return None if obj is None else obj.name  # with data_path, the object at it

    def __call__(self, obj: Optional[Object] = None, p: Optional[float] = None) -> None:
        """Applies the augmentation with probability `p` to the object, or to the object
        at `data_path` when it has one, and records it in `results`.

        Args:
            obj: object to augment, not needed with `data_path`.
            p: probability of applying it for this call only, instead of the `p` given
                at construction.

        Raises:
            ValueError: if `p` is not between 0 and 1.
            TypeError: `data_path` doesn't lead to an object.
        """
        if self.data_path is not None:
            obj = bpy_a.object_at(self.data_path)
        super().__call__(obj, p)


class OneOf(Augmentation):
    """Applies one augmentation from a list, picked at random by weight.

    On every call it picks one augmentation and calls it with the object. In a
    `Compose` it picks again for every object. The picked augmentation then draws its
    own `p`; the others don't run, so they don't set their `otherwise` either. A
    `OneOf` can contain another `OneOf`, and a
    [`Chain`][blendmentation.augmentations.augmentations.Chain] to pick a whole sequence.
    `Compose` clears the `results` of the augmentations inside it too.

    Args:
        augmentations: augmentations to pick from, each a callable taking one object.
        weights: relative probability of each augmentation. None = equal.
        p: probability of applying one of them.
        name: a name for it in the graph and the report (`Compose.describe`,
            `export.augmentation_report`), e.g. `"slide sideways"`. None: its class name.

    Attributes:
        actual (int | None): index of the augmentation picked by the last call, None
            when it was skipped.
        results (dict[str | None, int | None]): the index picked for each object.

    Raises:
        ValueError: `augmentations` is empty, the number of weights and augmentations
            differ, a weight is negative or not a number, or the weights sum to 0.

    Example:
        ```python
        # each part is either moved or turned, turned twice as often
        one_of = augmentations.OneOf(
            [augmentations.Translation(x=0.5, y=0.5), augmentations.Rotation(z=180)],
            weights=[1, 2],
        )
        augmentations.Compose([one_of])([car_1, car_2])
        one_of.results   # e.g. {"car_1": 1, "car_2": 0}
        ```
    """

    def __init__(self, augmentations: list[Callable[[Object], Any]],
                 weights: Optional[Sequence[float]] = None, p: float = 1.0, name: Optional[str] = None):
        super().__init__(p, name=name)
        if not augmentations:
            raise ValueError("Give at least one augmentation")
        check_weights(weights, len(augmentations), "augmentation")
        self.augmentations = augmentations
        self.weights = weights

    @property
    def ignores_object(self) -> bool:
        return all(getattr(augmentation, "ignores_object", False) for augmentation in self.augmentations)

    def _graph_children(self) -> list[tuple[Any, float]]:
        return list(zip(self.augmentations, weight_shares(self.weights, len(self.augmentations))))

    def apply(self, obj: Optional[Object]) -> None:
        self.actual = random.choices(range(len(self.augmentations)), weights=self.weights)[0]
        self.augmentations[self.actual](obj)


class Chain(Augmentation):
    """Applies a list of augmentations to one object, in order, as one augmentation.

    Where `Compose` is called with a list of objects, a `Chain` is called with one
    object like any other augmentation, so it can go where one augmentation goes: in a
    `OneOf`, to pick between whole sequences, or in a `Compose`. Each augmentation in it
    draws its own `p`; when the `Chain` itself doesn't run, none of them do, so they
    don't set their `otherwise` either. `Compose` clears the `results` of the
    augmentations inside it.

    Args:
        augmentations: augmentations to apply, each a callable taking one object.
        p: probability of applying the whole list.
        name: a name for it in the graph and the report (`Compose.describe`,
            `export.augmentation_report`), e.g. `"slide sideways"`. None: its class name.

    Attributes:
        actual (list | None): what each augmentation set in the last call, as in its
            `results` (`(x, y, z)` for `Translation`, `Rotation` and `Scale`, `None` for
            a callable without `actual`), None when it was skipped.

    Raises:
        ValueError: `augmentations` is empty.

    Example:
        ```python
        # a dim, warm light, or just another brightness
        lamp_aug = augmentations.Compose([
            augmentations.OneOf([
                augmentations.Number("data.energy", value_range=(600, 1400)),
                augmentations.Chain([
                    augmentations.Number("data.energy", value_range=(300, 600)),
                    augmentations.Vector("data.color", value_range=((1, 0.7, 0.5), (1, 0.85, 0.7))),
                ]),
            ], weights=[2, 1]),
        ])
        ```

        A material picked from the object's slots, augmented only when it is picked:

        ```python
        material_aug = augmentations.Compose([
            augmentations.OneOf([
                augmentations.Chain([
                    augmentations.MaterialSlot(["Ferrous metal"]),
                    augmentations.Node('active_material.node_tree.nodes["Ferrous metal"]', {
                        "Rust strength": augmentations.Number((0.3, 1)),
                    }),
                ]),
                augmentations.Chain([
                    augmentations.MaterialSlot(["Plastic"]),
                    augmentations.Node('active_material.node_tree.nodes["Principled BSDF"]', {
                        "Roughness": augmentations.Number((0.1, 0.6)),
                    }),
                ]),
            ], weights=[3, 1]),
        ])
        ```
    """

    def __init__(self, augmentations: list[Callable[[Optional[Object]], Any]], p: float = 1.0,
                 name: Optional[str] = None):
        super().__init__(p, name=name)
        if not augmentations:
            raise ValueError("Give at least one augmentation")
        self.augmentations = augmentations

    @property
    def ignores_object(self) -> bool:
        return all(getattr(augmentation, "ignores_object", False) for augmentation in self.augmentations)

    def apply(self, obj: Optional[Object]) -> None:
        for augmentation in self.augmentations:
            augmentation(obj)
        self.actual = [augmentation._result() if isinstance(augmentation, Augmentation)
                       else getattr(augmentation, "actual", None) for augmentation in self.augmentations]

    @staticmethod
    def shape_keys_template(obj: Object, prefix: str = "augmentations.", relative: bool = True) -> str:
        """Writes the source of a `Chain` that sets the value of every shape key of an
        object, to paste into a script and edit.

        Each shape key gets a `Number` with the range of its slider (Range Min and Max,
        0 to 1 by default) and its current value in a comment. By default the paths are
        relative (`data.shape_keys.key_blocks["Smile"].value`): pass the object to the
        `Compose`, and the `Chain` also works for every other object with shape keys of
        those names. With `relative=False` they are absolute
        (`bpy.data.objects["Face"].data.shape_keys...`) and always change this object,
        whatever the `Chain` is called with. A comment on top says which, with the
        object's name. The basis is left out,
        and muted or driven shape keys are only comments, since their value has no
        effect or a driver overwrites it. With the default `p=1` the `Chain` does the same
        as its `Number`s listed one by one in a `Compose`; it keeps them together, e.g.
        to give the whole `Chain` a `p` or to put it in a `OneOf`.

        In the Blender app, the add-on does the same for the active object and copies it
        to the clipboard: 3D Viewport sidebar (N) > Blendmentation > Copy Shape Keys
        Template, Relative or Absolute. See [Installation](installation.md#inside-blender-as-an-add-on).

        Args:
            obj: object with shape keys (a mesh, curve or lattice).
            prefix: written before `Chain` and `Number`, e.g. `"aug."` after
                `from blendmentation.augmentations import augmentations as aug`.
            relative: paths relative to the object; False: absolute paths through it.

        Returns:
            The source of the `Chain`, ending in a newline.

        Raises:
            ValueError: the object has no shape keys besides the basis, or they are
                not relative (Evaluation Time sets them).

        Example:
            ```python
            print(augmentations.Chain.shape_keys_template(bpy.data.objects["Face"]))
            ```

            prints

            ```python
            # Relative paths: they start from the object it is called with, here "Face".
            # Pass that object to the Compose, compose([bpy.data.objects["Face"]]), or one set up the same way.
            augmentations.Chain([
                augmentations.Number('data.shape_keys.key_blocks["Smile"].value', (0.0, 1.0)),  # now 0.0
                augmentations.Number('data.shape_keys.key_blocks["Blink"].value', (0.0, 1.0)),  # now 0.25
                # 'data.shape_keys.key_blocks["Old"].value': muted, now 0.0
            ])
            ```
        """
        return bpy_a.shape_keys_template(obj, prefix, relative)


class AxisAugmentation(ObjectAugmentation):
    """Base class of `Translation`, `Rotation` and `Scale`, sampled per axis.

    Each axis is a number `v`, sampled from `(-v, v)`, a pair `(low, high)`, or a
    triple `(low, high, step)`, which picks one of `low`, `low + step`, ... up to
    `high`, each equally likely.

    Args:
        x: offset on the X axis.
        y: offset on the Y axis.
        z: offset on the Z axis.
        p: probability of applying the augmentation.
        data_path: absolute data path of an object to change instead of the one it is
            called with, e.g. `'bpy.data.objects["Camera"]'`; then it can also be
            called alone, `aug()`. None (the default): the object it is called with.
        name: a name for it in the graph and the report (`Compose.describe`,
            `export.augmentation_report`), e.g. `"slide sideways"`. None: its class name.

    Raises:
        ValueError: an axis is not a number, a pair or a triple, low is above high, or
            the step is not above 0.

    Attributes:
        actual_x (float | None): value sampled for X in the last call, None when skipped.
        actual_y (float | None): value sampled for Y.
        actual_z (float | None): value sampled for Z.
        results (dict[str | None, tuple[float, float, float] | None]): `(x, y, z)` for
            each object, by name, None when skipped.
    """

    def __init__(self, x: Offset = 0, y: Offset = 0, z: Offset = 0, p: float = 1.0,
                 data_path: Optional[str] = None, name: Optional[str] = None):
        super().__init__(p, data_path=data_path, name=name)
        self.x = check_offset(x, "x")
        self.y = check_offset(y, "y")
        self.z = check_offset(z, "z")
        self.actual_x: Optional[float] = None
        self.actual_y: Optional[float] = None
        self.actual_z: Optional[float] = None

    def skip(self, obj: Optional[Object]) -> None:
        self.actual_x = self.actual_y = self.actual_z = None

    def _result(self) -> Optional[tuple[float, float, float]]:
        if not self.applied:
            return None
        return (self.actual_x, self.actual_y, self.actual_z)  # pyright: ignore[reportReturnType]  (set by apply)


class Translation(AxisAugmentation):
    """Moves the object by a random offset per axis, in Blender units.

    Each axis is a number `v`, sampled from `(-v, v)`, a pair `(low, high)`, or a
    triple `(low, high, step)` for one of `low`, `low + step`, ... up to `high`.
    The offset is added to the location.

    Args:
        x: offset on X in Blender units.
        y: offset on Y in Blender units.
        z: offset on Z in Blender units.
        p: probability of applying the augmentation.
        data_path: absolute data path of an object to change instead of the one it is
            called with, e.g. `'bpy.data.objects["Camera"]'`; then it can also be
            called alone, `aug()`. None (the default): the object it is called with.
        name: a name for it in the graph and the report (`Compose.describe`,
            `export.augmentation_report`), e.g. `"slide sideways"`. None: its class name.

    Example:
        ```python
        augmentations.Translation(x=0.5, y=0.5, z=(0, 1))
        augmentations.Translation(x=(-1, 1, 0.5))   # -1, -0.5, 0, 0.5 or 1
        ```
    """

    def apply(self, obj: Optional[Object]) -> None:
        self.actual_x, self.actual_y, self.actual_z = bpy_a.translation(obj, self.x, self.y, self.z)


class Rotation(AxisAugmentation):
    """Rotates the object by a random angle per axis, in degrees.

    Each axis is a number `v`, sampled from `(-v, v)`, a pair `(low, high)`, or a
    triple `(low, high, step)` for one of `low`, `low + step`, ... up to `high`, each
    equally likely. The angle is added to the rotation, in euler, quaternion and
    axis-angle modes.

    For whole turns in steps, leave out the last step: `(0, 270, 90)`, not
    `(0, 360, 90)`, where 360 is the same as 0 and would make it twice as likely.

    Args:
        x: angle around X in degrees.
        y: angle around Y in degrees.
        z: angle around Z in degrees.
        p: probability of applying the augmentation.
        data_path: absolute data path of an object to change instead of the one it is
            called with, e.g. `'bpy.data.objects["Camera"]'`; then it can also be
            called alone, `aug()`. None (the default): the object it is called with.
        name: a name for it in the graph and the report (`Compose.describe`,
            `export.augmentation_report`), e.g. `"slide sideways"`. None: its class name.

    Example:
        ```python
        augmentations.Rotation(z=180)          # any heading
        augmentations.Rotation(z=(0, 270, 90))  # 0, 90, 180 or 270 degrees
        # tilted by -30, -15, 0, 15 or 30 degrees, and turned in 45 degree steps
        augmentations.Rotation(x=(-30, 30, 15), z=(0, 315, 45))
        ```
    """

    def apply(self, obj: Optional[Object]) -> None:
        self.actual_x, self.actual_y, self.actual_z = bpy_a.rotation(obj, self.x, self.y, self.z)


class Scale(AxisAugmentation):
    """Scales the object by a random percentage per axis.

    Each axis is a number `v`, sampled from `(-v, v)`, a pair `(low, high)`, or a
    triple `(low, high, step)` for one of `low`, `low + step`, ... up to `high`, in
    percent: the scale is multiplied by `1 + sample / 100`.

    Args:
        x: change of the X scale in percent.
        y: change of the Y scale in percent.
        z: change of the Z scale in percent.
        p: probability of applying the augmentation.
        data_path: absolute data path of an object to change instead of the one it is
            called with, e.g. `'bpy.data.objects["Camera"]'`; then it can also be
            called alone, `aug()`. None (the default): the object it is called with.
        name: a name for it in the graph and the report (`Compose.describe`,
            `export.augmentation_report`), e.g. `"slide sideways"`. None: its class name.

    Example:
        ```python
        augmentations.Scale(x=10, y=10, z=10, p=0.5)   # ±10 %, in half of the datapoints
        ```
    """

    def apply(self, obj: Optional[Object]) -> None:
        self.actual_x, self.actual_y, self.actual_z = bpy_a.scale(obj, self.x, self.y, self.z)


class Visibility(ObjectAugmentation):
    """Shows the object in the render with probability `p`, otherwise hides it.

    It sets the object's render visibility (`hide_render`) both ways: an object hidden
    in the scene is shown when the draw says visible, and a visible one is hidden when
    it says not. The viewport visibility is left unchanged. Children are not affected,
    pass them to the `Compose` too.

    The labels follow the render: a hidden object has no pixels in the masks, passes
    and AOVs, is left out of its instance's bbox (an instance with every object
    hidden has bbox None and is ignored by the `BBox` skip settings), doesn't hide
    keypoints behind it, and keypoints on it are not visible.

    Args:
        p: probability that the object is visible.
        otherwise: what happens the rest of the time: False hides the object, None
            leaves its visibility unchanged.
        data_path: absolute data path of an object to change instead of the one it is
            called with, e.g. `'bpy.data.objects["Camera"]'`; then it can also be
            called alone, `aug()`. None (the default): the object it is called with.
        name: a name for it in the graph and the report (`Compose.describe`,
            `export.augmentation_report`), e.g. `"slide sideways"`. None: its class name.

    Attributes:
        actual (bool | None): whether the last call made the object visible, None when
            it left it unchanged. In a `Compose` that is the last object; `results` has
            every object.

    Example:
        ```python
        distractors_aug = augmentations.Compose([augmentations.Visibility(p=0.7)])
        distractors_aug([box_1, box_2, box_3])   # each one in about 70 % of the images
        visibility = distractors_aug.augmentations[0]
        visible = [name for name, shown in visibility.results.items() if shown]

        # shown in 70 % of the images, otherwise as it is in the scene
        augmentations.Visibility(p=0.7, otherwise=None)
        ```
    """

    def __init__(self, p: float = 0.5, otherwise: Optional[bool] = False, data_path: Optional[str] = None,
                 name: Optional[str] = None):
        super().__init__(p, check_otherwise(otherwise, isinstance(otherwise, bool), "True or False"), data_path,
                         name=name)

    def apply(self, obj: Optional[Object]) -> None:
        self.actual = bpy_a.visibility(obj, True)

    def _graph_values(self) -> list[tuple[Any, float]]:
        return [(True, 1.0)]

    def set_otherwise(self, obj: Optional[Object]) -> bool:
        return bpy_a.visibility(obj, self.otherwise)


class ShadowCatcher(ObjectAugmentation):
    """Makes the object a shadow catcher with probability `p`, otherwise a normal object.

    It sets the object's Shadow Catcher option (Object Properties > Visibility,
    `is_shadow_catcher`) both ways. A shadow catcher shows only the shadows and
    reflections other objects cast on it, so with a transparent film (Render Properties
    > Film > Transparent) a floor under the objects leaves their shadows over a
    `generating.Background` or a photo composited later, and with `p` below 1 it is
    sometimes a floor and sometimes only shadows. Only Cycles renders shadow catchers;
    other engines render the object as usual.

    The labels don't change: the object is still in the render, so it still hides what
    is behind it in the masks, and it keeps its own bbox and masks when it is in the
    classes.

    Args:
        p: probability that the object is a shadow catcher.
        otherwise: what happens the rest of the time: False makes it a normal object,
            None leaves the option unchanged.
        data_path: absolute data path of an object to change instead of the one it is
            called with, e.g. `'bpy.data.objects["Floor"]'`; then it can also be
            called alone, `aug()`. None (the default): the object it is called with.
        name: a name for it in the graph and the report (`Compose.describe`,
            `export.augmentation_report`), e.g. `"floor or shadows"`. None: its class name.

    Attributes:
        actual (bool | None): whether the last call made the object a shadow catcher,
            None when it left it unchanged. In a `Compose` that is the last object;
            `results` has every object.

    Example:
        ```python
        floor_aug = augmentations.Compose([augmentations.ShadowCatcher()])
        floor_aug([floor])   # only the shadows on it are rendered

        # only shadows in half of the images, the floor in the others
        augmentations.ShadowCatcher(p=0.5, data_path='bpy.data.objects["Floor"]')()
        ```
    """

    def __init__(self, p: float = 1.0, otherwise: Optional[bool] = False, data_path: Optional[str] = None,
                 name: Optional[str] = None):
        super().__init__(p, check_otherwise(otherwise, isinstance(otherwise, bool), "True or False"), data_path,
                         name=name)

    def apply(self, obj: Optional[Object]) -> None:
        self.actual = bpy_a.shadow_catcher(obj, True)

    def _graph_values(self) -> list[tuple[Any, float]]:
        return [(True, 1.0)]

    def set_otherwise(self, obj: Optional[Object]) -> bool:
        return bpy_a.shadow_catcher(obj, self.otherwise)


class KeepAbove(ObjectAugmentation):
    """Moves the object up, if needed, so it stays above a surface, e.g. a floor or terrain.

    It checks whether the object's lowest point is at least `margin` above the top of
    the surface where they overlap seen from above, and if not, moves the object up
    along world Z until it is. An object that is already high enough, or not over the
    surface, is left where it is; it is never moved down. Both are compared as
    evaluated meshes (modifiers included) in world space, so uneven surfaces work.
    It sets `matrix_world`, so parented objects work too. Only the object's own
    geometry counts, not its children's.

    !!! warning "Put it after the transforms"
        It checks the object where it is when it runs, so put it in the `Compose`
        after `Translation`, `Rotation`, `Scale` and any other augmentation that moves
        or deforms the object (e.g. a `Number` changing a modifier or shape key).
        Augmentations after it can push the object into the surface again.

    Args:
        surface: object to stay above. It must have faces (a mesh, curve, text...).
        margin: minimum gap between the surface and the object's lowest point, in
            Blender units.
        p: probability of applying the augmentation.
        data_path: absolute data path of an object to change instead of the one it is
            called with, e.g. `'bpy.data.objects["Camera"]'`; then it can also be
            called alone, `aug()`. None (the default): the object it is called with.
        name: a name for it in the graph and the report (`Compose.describe`,
            `export.augmentation_report`), e.g. `"slide sideways"`. None: its class name.

    Attributes:
        actual (float | None): how far the last call moved the object up, 0 when it
            was already above. In a `Compose` that is the last object; `results` has
            every object.

    Raises:
        ValueError: the object is the surface.
        TypeError: the surface has no faces.

    Example:
        ```python
        objects_aug = augmentations.Compose([
            augmentations.Translation(x=0.5, y=0.5, z=(-0.3, 0.3)),
            augmentations.Rotation(x=30, y=30, z=180),
            augmentations.KeepAbove(floor, margin=0.01),   # after the transforms
        ])
        objects_aug([car_1, car_2])
        ```
    """

    def __init__(self, surface: Object, margin: float = 0.0, p: float = 1.0, data_path: Optional[str] = None,
                 name: Optional[str] = None):
        super().__init__(p, data_path=data_path, name=name)
        self.surface = surface
        self.margin = margin

    def apply(self, obj: Optional[Object]) -> None:
        self.actual = bpy_a.keep_above(obj, self.surface, self.margin)


class PlaceOn(ObjectAugmentation):
    """Moves the object up or down so it rests on a surface, e.g. a floor or table.

    Like `KeepAbove`, but it also moves the object down: its lowest point ends up
    exactly `margin` above the top of the surface where they overlap seen from above,
    so a floating object drops onto the surface and a sunken one is lifted out. Only
    the height changes, along world Z. An object that is not over the surface is left
    where it is. Both are compared as evaluated meshes (modifiers included) in world
    space, so uneven surfaces work. It sets `matrix_world`, so parented objects work
    too. Only the object's own geometry counts, not its children's.

    Use it when the object must touch the surface, e.g. after a rotation that tips it
    over around an origin that is not at its bottom. Use `KeepAbove` when the object
    may also float above it.

    !!! warning "Put it after the transforms"
        It places the object where it is when it runs, so put it in the `Compose` after
        `Translation`, `Rotation`, `Scale` and any other augmentation that moves or
        deforms the object.

    Args:
        surface: object to rest on. It must have faces (a mesh, curve, text...).
        margin: gap between the surface and the object's lowest point, in Blender
            units.
        p: probability of applying the augmentation.
        data_path: absolute data path of an object to change instead of the one it is
            called with, e.g. `'bpy.data.objects["Camera"]'`; then it can also be
            called alone, `aug()`. None (the default): the object it is called with.
        name: a name for it in the graph and the report (`Compose.describe`,
            `export.augmentation_report`), e.g. `"slide sideways"`. None: its class name.

    Attributes:
        actual (float | None): how far the last call moved the object, positive up and
            negative down, 0 when it was not over the surface. In a `Compose` that is
            the last object; `results` has every object.

    Raises:
        ValueError: the object is the surface.
        TypeError: the surface has no faces.

    Example:
        ```python
        lying_aug = augmentations.Compose([
            augmentations.Rotation(x=(90, 90), y=(0, 270, 90), z=180),
            augmentations.Translation(x=0.5, y=0.5),
            augmentations.PlaceOn(floor),   # after the transforms
        ])
        lying_aug([milk_box])
        ```
    """

    def __init__(self, surface: Object, margin: float = 0.0, p: float = 1.0, data_path: Optional[str] = None,
                 name: Optional[str] = None):
        super().__init__(p, data_path=data_path, name=name)
        self.surface = surface
        self.margin = margin

    def apply(self, obj: Optional[Object]) -> None:
        self.actual = bpy_a.place_on(obj, self.surface, self.margin)


class Seed(Augmentation):
    """Gives every seed in a node group its own random value.

    On every call, each unlinked integer Seed input of the group's nodes (Distribute
    Points on Faces, Distribute Points in Volume, Random Value, Hash Value...) gets a
    different random int, nodes inside nested node groups included. Seeds connected to
    other nodes are left alone. Seeds exposed as modifier inputs are not group nodes;
    set those with `Number`.

    It works on the node group, not on the object passed by `Compose`, so it can be
    called as `seed()`. In a `Compose` it runs once per object, and the last seeds are
    kept; `results` holds only those, under `None`.

    Args:
        node_group: the node group, e.g. `bpy.data.node_groups["Geometry Nodes"]`, or
            any other node tree (a material's `node_tree`).
        p: probability of applying the augmentation.
        otherwise: seed to set on all of them when it doesn't run, None keeps them.
        name: a name for it in the graph and the report (`Compose.describe`,
            `export.augmentation_report`), e.g. `"slide sideways"`. None: its class name.

    Attributes:
        actual (dict[str, int] | None): the seed set on each node, by node name
            (`"Group node/Node"` for nodes in nested groups).

    Raises:
        TypeError: `node_group` is not a node tree.
        ValueError: the node group has no unlinked Seed inputs, or `otherwise` is not
            an int.

    Example:
        ```python
        scatter = bpy.data.node_groups["Geometry Nodes"]
        seed = augmentations.Seed(scatter)
        for _ in range(100):
            with state.restoring():                 # the seeds are set back too
                seed()                              # a new scatter every time
                generator()
        ```
    """

    def __init__(self, node_group: NodeTree, p: float = 1.0, otherwise: Optional[int] = None,
                 name: Optional[str] = None):
        valid = isinstance(otherwise, int) and not isinstance(otherwise, bool)
        super().__init__(p, check_otherwise(otherwise, valid, "an int"), name=name)
        bpy_a.check_seed_group(node_group)
        self.node_group = node_group

    @property
    def ignores_object(self) -> bool:
        return True

    def apply(self, obj: Optional[Object] = None) -> None:
        self.actual = bpy_a.seeds(self.node_group)

    def set_otherwise(self, obj: Optional[Object]) -> dict[str, int]:
        return bpy_a.seeds(self.node_group, self.otherwise)


class LookAt(ObjectAugmentation):
    """Moves a camera (or a light, or any object) on a sphere around a target and points it
    at the target, upright.

    The target stays in the center of the camera view. It sets `matrix_world`, so
    parented cameras work too. Each parameter is a `(min, max)` range, an exact number,
    or None to keep the current value.

    Args:
        target: an object, a list of objects (the center of their bounding boxes),
            or a point `(x, y, z)`.
        distance: distance from the target in Blender units.
        elevation: angle above the target's horizontal plane in degrees. Avoid
            exactly 90 / -90.
        azimuth: angle around the world Z axis in degrees, 0 = +X.
        roll: rotation around the camera's view axis in degrees. None = upright.
        focal_length: camera lens in mm, cameras only.
        p: probability of applying the augmentation.
        data_path: absolute data path of an object to change instead of the one it is
            called with, e.g. `'bpy.data.objects["Camera"]'`; then it can also be
            called alone, `aug()`. None (the default): the object it is called with.
        name: a name for it in the graph and the report (`Compose.describe`,
            `export.augmentation_report`), e.g. `"slide sideways"`. None: its class name.

    Attributes:
        actual (dict | None): the distance, elevation, azimuth, roll and focal length
            set by the last call.

    Example:
        ```python
        camera_aug = augmentations.Compose([
            augmentations.LookAt([car_1, car_2], distance=(6, 12), elevation=(10, 45),
                                 azimuth=(0, 360), roll=(-5, 5)),
        ])
        camera_aug([bpy.context.scene.camera])
        ```
    """

    def __init__(
        self,
        target: Target,
        distance: Optional[RangeOrValue] = None,
        elevation: Optional[RangeOrValue] = None,
        azimuth: Optional[RangeOrValue] = None,
        roll: Optional[RangeOrValue] = None,
        focal_length: Optional[RangeOrValue] = None,
        p: float = 1.0,
        data_path: Optional[str] = None,
        name: Optional[str] = None,
    ):
        super().__init__(p, data_path=data_path, name=name)
        self.target = target
        self.distance = distance
        self.elevation = elevation
        self.azimuth = azimuth
        self.roll = roll
        self.focal_length = focal_length

    def apply(self, obj: Optional[Object]) -> None:
        self.actual = bpy_a.look_at(
            obj, self.target, self.distance, self.elevation, self.azimuth, self.roll, self.focal_length
        )


class PlaceOnCurve(ObjectAugmentation):
    """Moves a camera (or any object) to a random point on a curve, e.g. a camera path.

    The point is picked uniformly along the curve's length (a curve with several splines
    counts them one after another, in their order), so straight and curved parts get
    the same density. The curve is read as Blender evaluates it (modifiers included),
    as the line it draws at its resolution, without bevel or extrusion. It sets
    `matrix_world`, so parented objects work too.

    By default only the location changes: the object keeps its rotation and scale.
    With `align`, it also turns to face along the curve direction at that point, the
    way the curve was drawn (from its first point to its last): a camera's view axis
    (-Z) follows the tangent, upright (its Y axis up towards world Z). The curve's tilt
    is ignored. Avoid vertical parts of the curve with `align`, where "up" is undefined.

    !!! tip "Vary it after"
        Put `Translation` after it in the `Compose` to move the object off the curve
        (in world axes, Blender units), and `Rotation` to turn it from there, e.g.
        `Rotation(z=(90, 90))` after `align=True` makes the camera look to the left
        of the path, and `Rotation(x=5, y=5, z=10)` adds a little shake. To aim at
        something instead, put `LookAt(target)` after it without `distance`,
        `elevation` and `azimuth`: it keeps the point on the curve.

    Args:
        curve: the curve object to place the object on.
        align: also rotate the object to face along the curve direction.
        position: where on the curve, as a fraction of its length from its start, a
            `(min, max)` range (the default is all of it) or an exact number, 0-1.
        p: probability of applying the augmentation.
        data_path: absolute data path of an object to change instead of the one it is
            called with, e.g. `'bpy.data.objects["Camera"]'`; then it can also be
            called alone, `aug()`. None (the default): the object it is called with.
        name: a name for it in the graph and the report (`Compose.describe`,
            `export.augmentation_report`), e.g. `"slide sideways"`. None: its class name.

    Attributes:
        actual (dict | None): the `position` (fraction of the length) and the world
            `location` set by the last call.

    Raises:
        TypeError: `curve` is not a curve object.
        ValueError: `position` is not between 0 and 1, the object is the curve, the
            curve has no length, or it evaluates to faces.

    Example:
        ```python
        camera_path = bpy.data.objects["CameraPath"]
        camera_aug = augmentations.Compose([
            augmentations.PlaceOnCurve(camera_path, align=True),
            augmentations.Translation(z=(-0.2, 0.2)),   # a little up or down
            augmentations.Rotation(x=5, z=(-20, 20)),   # look around a bit
        ])
        camera_aug([bpy.context.scene.camera])
        ```
    """

    def __init__(self, curve: Object, align: bool = False, position: RangeOrValue = (0.0, 1.0), p: float = 1.0,
                 data_path: Optional[str] = None, name: Optional[str] = None):
        super().__init__(p, data_path=data_path, name=name)
        bpy_a.check_curve(curve)
        self.curve = curve
        self.align = align
        self.position = check_fraction(position, "position")

    def apply(self, obj: Optional[Object]) -> None:
        self.actual = bpy_a.place_on_curve(obj, self.curve, self.align, self.position)


class FocalLength(ObjectAugmentation):
    """Sets a random camera lens, optionally as a dolly zoom.

    With `keep_size`, the camera also moves along the line to the target by the same
    ratio as the lens, so the target keeps its size in the image while the perspective
    changes. With `size` as well, the target gets a random size instead, in percent of
    its size in the image before the call: `size=(80, 120)` makes it up to 20 % smaller
    or bigger (the camera moves farther or closer). That is exact for parts at the
    target's depth when the target is in the center of the view (e.g. after `LookAt`);
    an off-center target also moves in the image. Perspective cameras only.

    Args:
        focal_length: lens in mm, a `(min, max)` range or an exact number.
        target: an object, a list of objects (the center of their bounding boxes),
            or a point `(x, y, z)`. Needed for `keep_size`.
        keep_size: move the camera so the target keeps its size in the image.
        size: with `keep_size`, the target's new size in the image in percent of its
            size before the call, a `(min, max)` range or an exact number. None (the
            default) keeps it exactly.
        p: probability of applying the augmentation.
        otherwise: lens in mm to set when it doesn't run, None keeps the lens. Not
            with `keep_size`.
        data_path: absolute data path of an object to change instead of the one it is
            called with, e.g. `'bpy.data.objects["Camera"]'`; then it can also be
            called alone, `aug()`. None (the default): the object it is called with.
        name: a name for it in the graph and the report (`Compose.describe`,
            `export.augmentation_report`), e.g. `"slide sideways"`. None: its class name.

    Attributes:
        actual (dict | None): `focal_length` set by the last call, and with
            `keep_size` the new `distance` to the target and the `size` in percent
            (100 without `size`), None when it was skipped without `otherwise`.

    Raises:
        ValueError: `keep_size` without a `target`, `otherwise` with `keep_size`,
            `size` without `keep_size`, or a `size` that is not a number or
            `(min, max)` above 0.

    Example:
        ```python
        augmentations.FocalLength((24, 85), target=[car_1, car_2], keep_size=True)
        # the same, with the cars 80 to 120 % of the size they had before
        augmentations.FocalLength((24, 85), target=[car_1, car_2], keep_size=True, size=(80, 120))
        # a random lens in 30 % of the images, otherwise 50 mm
        augmentations.FocalLength((24, 85), p=0.3, otherwise=50)
        ```
    """

    def __init__(self, focal_length: RangeOrValue, target: Optional[Target] = None, keep_size: bool = False,
                 size: Optional[RangeOrValue] = None, p: float = 1.0, otherwise: Optional[float] = None,
                 data_path: Optional[str] = None, name: Optional[str] = None):
        super().__init__(p, check_otherwise(otherwise, is_number(otherwise), "a lens in mm"), data_path, name=name)
        if keep_size and target is None:
            raise ValueError("keep_size needs a target")
        if keep_size and otherwise is not None:
            raise ValueError("otherwise can't be used with keep_size")
        if size is not None:
            if not keep_size:
                raise ValueError("size needs keep_size=True")
            bounds: Any = [size] if is_number(size) else size
            if (isinstance(bounds, str) or not isinstance(bounds, Sequence) or len(bounds) not in (1, 2)
                    or not all(is_number(v) and v > 0 for v in bounds) or bounds[0] > bounds[-1]):
                raise ValueError(f"size must be a percentage above 0 or a (min, max) range of them, got {size!r}")
        self.focal_length = focal_length
        self.target = target
        self.keep_size = keep_size
        self.size = size

    def apply(self, obj: Optional[Object]) -> None:
        self.actual = bpy_a.focal_length(obj, self.focal_length, self.target, self.keep_size, self.size)

    def set_otherwise(self, obj: Optional[Object]) -> dict[str, float]:
        return bpy_a.focal_length(obj, self.otherwise, None, False)


class DepthOfField(ObjectAugmentation):
    """Turns on depth of field, focused on a target, with a random f-stop, and
    optionally a little out of focus.

    Focus is measured along the view axis from where the camera is when this runs, so
    put it after `LookAt` / `FocalLength` in a `Compose`.

    `defocus` moves the focus off that distance by a random percent, added like a
    `Translation` offset: `defocus=10` focuses anywhere from 10 % nearer to 10 % farther,
    `(0, 20)` only farther (behind the target). It works from the target's distance, or
    without a target from the scene's focus (its Focus Distance, or the distance to its
    Focus on Object, which is then replaced by the distance).

    Args:
        target: an object, a list of objects (the center of their bounding boxes), or a
            point `(x, y, z)` to focus on. None keeps the current focus.
        f_stop: aperture f-stop, a `(min, max)` range or an exact number, lower gives
            more blur. None keeps the current one.
        defocus: percent added to the focus distance: a number `v` samples from
            `(-v, v)`, `(low, high)` from that range, `(low, high, step)` one of low,
            low + step, ... high. None (the default) focuses exactly. The distance
            doesn't go below 0.
        p: probability of applying the augmentation.
        otherwise: what happens the rest of the time: False turns depth of field off,
            None leaves the depth of field settings unchanged.
        data_path: absolute data path of an object to change instead of the one it is
            called with, e.g. `'bpy.data.objects["Camera"]'`; then it can also be
            called alone, `aug()`. None (the default): the object it is called with.
        name: a name for it in the graph and the report (`Compose.describe`,
            `export.augmentation_report`), e.g. `"slide sideways"`. None: its class name.

    Attributes:
        actual (dict | bool | None): `{"f_stop", "focus_distance"}` set by the last
            call, with `"defocus"` (the percent added) when `defocus` is given, False
            when it turned depth of field off, None when it left it unchanged.

    Raises:
        ValueError: `defocus` is not a number, `(low, high)` or `(low, high, step)`.

    Example:
        ```python
        # blurred in half of the images, sharp in the others
        augmentations.DepthOfField(car_1, f_stop=(1.4, 5.6), p=0.5, otherwise=False)
        # focused up to 15 % in front of or behind the cars, so they are a little soft
        augmentations.DepthOfField([car_1, car_2], f_stop=(1.4, 2.8), defocus=15)
        # the focus set in the scene, moved up to 20 % farther
        augmentations.DepthOfField(defocus=(0, 20))
        ```
    """

    def __init__(self, target: Optional[Target] = None, f_stop: Optional[RangeOrValue] = None,
                 defocus: Optional[Offset] = None, p: float = 1.0, otherwise: Optional[bool] = None,
                 data_path: Optional[str] = None, name: Optional[str] = None):
        super().__init__(p, check_otherwise(otherwise, otherwise is False, "False (depth of field off)"), data_path,
                         name=name)
        self.target = target
        self.f_stop = f_stop
        self.defocus = None if defocus is None else check_offset(defocus, "defocus")

    def apply(self, obj: Optional[Object]) -> None:
        self.actual = bpy_a.depth_of_field(obj, self.target, self.f_stop, self.defocus)

    def set_otherwise(self, obj: Optional[Object]) -> bool:
        return bpy_a.disable_depth_of_field(obj)


class MaterialSlot(ObjectAugmentation):
    """Gives the object one of the materials in its own material slots, picked at random.

    Add the materials to pick from to the object's slots in Blender (Material
    Properties, `+`). On every call it assigns every face of the mesh to the picked
    slot, and makes it the active slot, so later augmentations in the same `Compose`
    reach the picked material through a relative path,
    `active_material.node_tree.nodes[...]`. `state.restoring()` sets the faces' slots
    and the active slot back.

    It doesn't copy materials: an object shows a material that other objects may use
    too, and augmenting that material changes it for all of them. Which objects share
    a material, and a mesh (with its slots, by default), is up to how the scene is set
    up: give objects their own copies to augment them separately.

    Args:
        slots: slots to pick from, by index or material name. None = all of them.
        weights: relative probability of each slot. None = equal.
        p: probability of applying the augmentation.
        otherwise: slot to use when it doesn't run, by index or material name. None
            keeps the faces' slots.
        data_path: absolute data path of an object to change instead of the one it is
            called with, e.g. `'bpy.data.objects["Camera"]'`; then it can also be
            called alone, `aug()`. None (the default): the object it is called with.
        name: a name for it in the graph and the report (`Compose.describe`,
            `export.augmentation_report`), e.g. `"slide sideways"`. None: its class name.

    Attributes:
        actual (str | None): name of the picked slot's material (None for an empty
            slot, or when skipped without `otherwise`).

    Raises:
        ValueError: the number of weights and slots differ, a weight is negative or not
            a number, the weights sum to 0, or `otherwise` is not an index or a name. At
            a call: the object has no material slots, or (with `slots` None) the number
            of weights and slots differ.
        TypeError: at a call, the object is not a mesh.
        KeyError: at a call, a slot in `slots` doesn't exist.

    Note:
        A geometry nodes modifier that sets the material (Set Material) wins over the
        faces' slots.

    Example:
        ```python
        # the cube's slots: "Ferrous metal", "Plastic", "Rubber"
        cube_aug = augmentations.Compose([
            augmentations.MaterialSlot(weights=[3, 1, 1]),
            # the picked material, whichever it is
            augmentations.Number(
                'active_material.node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value',
                value_range=(0.1, 0.6),
            ),
        ])
        cube_aug([cube])
        ```

        Each material augmented its own way: a `Node` with an absolute path per
        material. The ones that weren't picked are changed too, but don't show; to
        augment only the picked one, see the example of
        [`Chain`][blendmentation.augmentations.augmentations.Chain].

        ```python
        cube_aug = augmentations.Compose([
            augmentations.MaterialSlot(["Ferrous metal", "Plastic"]),
            augmentations.Node(
                'bpy.data.materials["Ferrous metal"].node_tree.nodes["Ferrous metal"]',
                {"Rust strength": augmentations.Number((0.3, 1))},
            ),
            augmentations.Node(
                'bpy.data.materials["Plastic"].node_tree.nodes["Principled BSDF"]',
                {"Base Color": augmentations.Vector((0, 1))},
            ),
        ])
        ```
    """

    def __init__(self, slots: Optional[Sequence[Union[int, str]]] = None, weights: Optional[Sequence[float]] = None,
                 p: float = 1.0, otherwise: Optional[Union[int, str]] = None, data_path: Optional[str] = None,
                 name: Optional[str] = None):
        valid = isinstance(otherwise, str) or (isinstance(otherwise, int) and not isinstance(otherwise, bool))
        super().__init__(p, check_otherwise(otherwise, valid, "a slot index or a material name"), data_path, name=name)
        check_weights(weights, None if slots is None else len(slots), "slot")
        self.slots = slots
        self.weights = weights

    def apply(self, obj: Optional[Object]) -> None:
        self.actual = bpy_a.material_slot(obj, self.slots, self.weights)

    def _graph_values(self) -> Optional[list[tuple[Any, float]]]:
        return None if self.slots is None else list(zip(self.slots, weight_shares(self.weights, len(self.slots))))

    def set_otherwise(self, obj: Optional[Object]) -> Optional[str]:
        return bpy_a.material_slot(obj, [self.otherwise], None)


class UVMap(ObjectAugmentation):
    """Makes one of the object's UV maps, picked at random, the one it renders with.

    Add the UV maps to the mesh in Blender (Object Data Properties > UV Maps, `+`), e.g.
    the same texture laid out at different scales or rotations; Blender allows at most 8
    UV maps per mesh. On every call it makes
    the picked UV map the render one (the camera icon in the list) and the active one.
    Textures without a UV Map node, and the UV output of a Texture Coordinate node, use
    it. `state.restoring()` sets both back.

    The UV maps belong to the mesh, so objects sharing the mesh share the pick.

    Args:
        uv_maps: UV maps to pick from, by index or name. None = all of them.
        weights: relative probability of each UV map. None = equal.
        p: probability of applying the augmentation.
        otherwise: UV map to use when it doesn't run, by index or name. None keeps the
            current one.
        data_path: absolute data path of an object to change instead of the one it is
            called with, e.g. `'bpy.data.objects["Crate"]'`; then it can also be called
            alone, `aug()`. None (the default): the object it is called with.
        name: a name for it in the graph and the report (`Compose.describe`,
            `export.augmentation_report`), e.g. `"slide sideways"`. None: its class name.

    Attributes:
        actual (str | None): name of the picked UV map (None when skipped without
            `otherwise`).

    Raises:
        ValueError: the number of weights and UV maps differ, a weight is negative or
            not a number, the weights sum to 0, or `otherwise` is not an index or a name.
            At a call: the object has no UV maps, or (with `uv_maps` None) the number of
            weights and UV maps differ.
        TypeError: at a call, the object is not a mesh.
        KeyError: at a call, a UV map in `uv_maps` doesn't exist.

    Note:
        A UV Map or Attribute node that names its UV map, and a geometry nodes modifier
        that replaces the UV maps, don't follow the pick.

    Example:
        ```python
        # the crate's UV maps: "UVMap", "Rotated", "Scaled"
        crate_aug = augmentations.Compose([
            augmentations.UVMap(weights=[2, 1, 1]),
        ])
        crate_aug([crate])
        ```
    """

    def __init__(self, uv_maps: Optional[Sequence[Union[int, str]]] = None, weights: Optional[Sequence[float]] = None,
                 p: float = 1.0, otherwise: Optional[Union[int, str]] = None, data_path: Optional[str] = None,
                 name: Optional[str] = None):
        valid = isinstance(otherwise, str) or (isinstance(otherwise, int) and not isinstance(otherwise, bool))
        super().__init__(p, check_otherwise(otherwise, valid, "a UV map index or name"), data_path, name=name)
        check_weights(weights, None if uv_maps is None else len(uv_maps), "UV map")
        self.uv_maps = uv_maps
        self.weights = weights

    def apply(self, obj: Optional[Object]) -> None:
        self.actual = bpy_a.uv_map(obj, self.uv_maps, self.weights)

    def _graph_values(self) -> Optional[list[tuple[Any, float]]]:
        return None if self.uv_maps is None else list(zip(self.uv_maps, weight_shares(self.weights, len(self.uv_maps))))

    def set_otherwise(self, obj: Optional[Object]) -> Optional[str]:
        return bpy_a.uv_map(obj, [self.otherwise], None)


class SimpleMaterial(ObjectAugmentation):
    """Sets random Principled BSDF base color, roughness and metallic values.

    Each parameter is an absolute `(min, max)` range, 0-1, or an exact value, and None
    leaves the value unchanged. The base color is set through hue, saturation and value. The sockets
    must not be connected to other nodes.

    It changes the **material**, so every object using it is affected.

    Args:
        material_id: name of the material.
        hue: range of the base color hue.
        saturation: range of the base color saturation.
        value: range of the base color value (brightness).
        roughness: range of the roughness.
        metallic: range of the metallic.
        p: probability of applying the augmentation.
        data_path: absolute data path of an object to change instead of the one it is
            called with, e.g. `'bpy.data.objects["Camera"]'`; then it can also be
            called alone, `aug()`. None (the default): the object it is called with.
        name: a name for it in the graph and the report (`Compose.describe`,
            `export.augmentation_report`), e.g. `"slide sideways"`. None: its class name.

    Tip:
        Use `SimpleMaterial` for negative data, secondary objects, or to make a model
        generalize over shape while ignoring the material. For finer control over the
        materials of hero objects, use [`Node`][blendmentation.augmentations.augmentations.Node]
        to set the inputs of a node group, or [`Number`][blendmentation.augmentations.augmentations.Number]
        to set individual shader node inputs.

    Example:
        ```python
        augmentations.SimpleMaterial("CarPaint", hue=(0, 1), saturation=(0.5, 1), roughness=(0.1, 0.6))
        augmentations.SimpleMaterial("CarPaint", hue=(0, 1), metallic=1)  # always fully metallic
        ```
    """

    def __init__(
        self,
        material_id: str,
        hue: Optional[RangeOrValue] = None,
        saturation: Optional[RangeOrValue] = None,
        value: Optional[RangeOrValue] = None,
        roughness: Optional[RangeOrValue] = None,
        metallic: Optional[RangeOrValue] = None,
        p: float = 1.0,
        data_path: Optional[str] = None,
        name: Optional[str] = None,
    ):
        super().__init__(p, data_path=data_path, name=name)
        self.material_id = material_id
        self.hue = hue
        self.saturation = saturation
        self.value = value
        self.roughness = roughness
        self.metallic = metallic

    def apply(self, obj: Optional[Object]) -> None:
        self.actual = bpy_a.material(
            obj, self.material_id, self.hue, self.saturation, self.value, self.roughness, self.metallic
        )


def split_path(data_path: Any, main: Any) -> tuple[Optional[str], Any]:
    """The data path and the main argument of a `Number`, `Vector`, `Boolean` or `Menu`:
    without a path (inside a `Node` or `Modifier`), the first argument is the main one."""
    if data_path is None or isinstance(data_path, str):
        return data_path, main
    if main is not None:
        raise TypeError(f"The data path must be a string, got {data_path!r}")
    return None, data_path


class PathAugmentation(Augmentation):
    """A `Number`, `Vector`, `Boolean` or `Menu`: sets the value at its data path. Inside
    a `Node` or `Modifier` it has none of its own; the `Node` or `Modifier` gives it the
    path of the input it sets before each call (`data_path`), and for a color input
    `color=True`."""

    data_path: Optional[str]
    color = False

    @property
    def ignores_object(self) -> bool:
        return is_absolute(self.data_path)

    def path(self) -> str:
        if self.data_path is None:
            raise ValueError(f"{type(self).__name__} has no data path: give it one, or use it as an "
                             f"input of a Node or Modifier, which gives it the input's path")
        return self.data_path


class Number(PathAugmentation):
    """Sets any int or float value, given by its data path, to a random value.

    For shader node inputs, geometry nodes inputs, shape keys, light settings and so
    on. Ints are rounded. With a step, the value is one of min, min + step, ... max.

    As an input of a [`Node`][blendmentation.augmentations.augmentations.Node] or
    [`Modifier`][blendmentation.augmentations.augmentations.Modifier], leave the data
    path out: `Number((0, 1))`. The `Node` or `Modifier` gives it the path of the input.

    Args:
        data_path: path to the value, absolute (starting with `bpy.`) or relative to
            the object (or the World, material… passed in its place). Use `[index]` for
            one vector component, e.g. `'location[2]'`. Left out inside a `Node` or
            `Modifier`.
        value_range: `(min, max)` range of the new value, or `(min, max, step)`.
        p: probability of applying the augmentation.
        otherwise: value to set when it doesn't run, None keeps the value.
        name: a name for it in the graph and the report (`Compose.describe`,
            `export.augmentation_report`), e.g. `"slide sideways"`. None: its class name.

    Raises:
        ValueError: `value_range` is not `(min, max)` or `(min, max, step)`, min is
            above max, or step is not above 0. At a call, there is no data path (outside
            a `Node` or `Modifier`).

    !!! info "With an absolute path"
        A path starting with `bpy.` doesn't use the object, so it can be called alone,
        `aug()`. In a `Compose` it runs once per object the `Compose` is called with,
        and the last value set is kept; `results` holds only that one, under `None`.

    Example:
        ```python
        augmentations.Number("data.energy", value_range=(600, 1400))
        augmentations.Number("data.energy", value_range=(600, 1400, 200))  # 600, 800, ... 1400
        augmentations.Number('data.shape_keys.key_blocks["Smile"].value', value_range=(0, 1))
        # rust in 20 % of the images, none in the others
        augmentations.Number('node_tree.nodes["Rust"].outputs[0].default_value',
                             value_range=(0.5, 1), p=0.2, otherwise=0)
        # an input of a Node: the path comes from the Node
        augmentations.Node('node_tree.nodes["Rust"]', {"Scale": augmentations.Number((1, 5))})
        ```
    """

    def __init__(self, data_path: Union[str, Range, None] = None, value_range: Optional[Range] = None,
                 p: float = 1.0, otherwise: Optional[float] = None, name: Optional[str] = None):
        super().__init__(p, check_otherwise(otherwise, is_number(otherwise), "a number"), name=name)
        self.data_path, value_range = split_path(data_path, value_range)
        self.value_range = check_range(value_range, "value_range")

    def apply(self, obj: Optional[Object]) -> None:
        self.actual = bpy_a.number(obj, self.path(), self.value_range)

    def set_otherwise(self, obj: Optional[Object]) -> float:
        return bpy_a.number(obj, self.path(), (self.otherwise, self.otherwise))


class Vector(PathAugmentation):
    """Sets any vector value, given by its data path, to random values per component.

    For locations, colors, vector node inputs and so on. Each bound of `value_range` is
    a number for all components, or a sequence with one value per component, where
    None keeps that component. Int components are rounded.

    As an input of a [`Node`][blendmentation.augmentations.augmentations.Node] or
    [`Modifier`][blendmentation.augmentations.augmentations.Modifier], leave the data
    path out: `Vector((-1, 1))`. The `Node` or `Modifier` gives it the path of the
    input. It sets vector, rotation (X, Y, Z angles in radians) and color inputs; on a
    color input a bound (or `otherwise`) of one number or 3 values sets red, green and
    blue and keeps alpha, so `Vector((0, 1))` is any color.

    Args:
        data_path: path to the value, absolute (starting with `bpy.`) or relative to
            the object (or the World, material… passed in its place). Left out inside a
            `Node` or `Modifier`.
        value_range: `(min, max)` bounds of the new values.
        p: probability of applying the augmentation.
        otherwise: vector to set when it doesn't run, a number for all components or
            one value per component (None keeps that component). None keeps the vector.
        name: a name for it in the graph and the report (`Compose.describe`,
            `export.augmentation_report`), e.g. `"slide sideways"`. None: its class name.

    !!! info "With an absolute path"
        A path starting with `bpy.` doesn't use the object, so it can be called alone,
        `aug()`. In a `Compose` it runs once per object the `Compose` is called with,
        and the last value set is kept; `results` holds only that one, under `None`.

    Example:
        ```python
        augmentations.Vector("data.color", value_range=(0.8, 1.0))
        # a random RGB color that keeps alpha
        augmentations.Vector(
            'bpy.data.materials["Mat"].node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value',
            value_range=((0, 0, 0, None), (1, 1, 1, None)),
        )
        # a random offset in half of the images, no offset in the others
        augmentations.Vector('node_tree.nodes["Offset"].vector', value_range=(-1, 1), p=0.5, otherwise=0)
        # inputs of a Node: the path comes from the Node
        augmentations.Node('active_material.node_tree.nodes["Principled BSDF"]', {
            "Base Color": augmentations.Vector((0, 1)),   # any color, alpha kept
        })
        ```
    """

    def __init__(self, data_path: Union[str, tuple[Bound, Bound], None] = None,
                 value_range: Optional[tuple[Bound, Bound]] = None, p: float = 1.0,
                 otherwise: Optional[Bound] = None, name: Optional[str] = None):
        valid = is_number(otherwise) or (
            isinstance(otherwise, Sequence) and not isinstance(otherwise, str)
            and all(value is None or is_number(value) for value in otherwise))
        super().__init__(p, check_otherwise(otherwise, valid, "a number or one value per component"), name=name)
        self.data_path, value_range = split_path(data_path, value_range)
        if isinstance(value_range, str) or not isinstance(value_range, Sequence) or len(value_range) != 2:
            raise ValueError(f"value_range must be (min, max), got {value_range!r}")
        self.value_range = value_range

    def bounds(self, low: Any, high: Any) -> tuple:
        """The bounds as given, or on a color input with alpha kept."""
        if self.color:
            return color_bound(low), color_bound(high)
        return low, high

    def apply(self, obj: Optional[Object]) -> None:
        self.actual = bpy_a.vector(obj, self.path(), self.bounds(*self.value_range))

    def set_otherwise(self, obj: Optional[Object]) -> tuple:
        return bpy_a.vector(obj, self.path(), self.bounds(self.otherwise, self.otherwise))


class Boolean(PathAugmentation):
    """Sets any boolean value, given by its data path, to True with probability `p`,
    otherwise to `otherwise` (False by default).

    For geometry nodes switches, object visibility, modifier toggles and so on.

    As an input of a [`Node`][blendmentation.augmentations.augmentations.Node] or
    [`Modifier`][blendmentation.augmentations.augmentations.Modifier], leave the data
    path out: `Boolean(p=0.3)`. The `Node` or `Modifier` gives it the path of the input.

    Args:
        data_path: path to the value, absolute (starting with `bpy.`) or relative to
            the object (or the World, material… passed in its place). Left out inside a
            `Node` or `Modifier`.
        p: probability of True.
        otherwise: value to set the rest of the time, None keeps the value.
        name: a name for it in the graph and the report (`Compose.describe`,
            `export.augmentation_report`), e.g. `"slide sideways"`. None: its class name.

    !!! info "With an absolute path"
        A path starting with `bpy.` doesn't use the object, so it can be called alone,
        `aug()`. In a `Compose` it runs once per object the `Compose` is called with,
        and the last value set is kept; `results` holds only that one, under `None`.

    Example:
        ```python
        augmentations.Boolean("data.use_shadow", p=0.8)
        # turned on in 30 % of the images, otherwise as it is in the scene
        augmentations.Boolean("data.use_shadow", p=0.3, otherwise=None)
        # an input of a Node: the path comes from the Node
        augmentations.Node('node_tree.nodes["Scatter"]', {"Mossy": augmentations.Boolean(p=0.3)})
        ```
    """

    def __init__(self, data_path: Union[str, float, None] = None, p: Optional[float] = None,
                 otherwise: Optional[bool] = False, name: Optional[str] = None):
        data_path, p = split_path(data_path, p)
        super().__init__(0.5 if p is None else p,
                         check_otherwise(otherwise, isinstance(otherwise, bool), "True or False"), name=name)
        self.data_path = data_path

    def apply(self, obj: Optional[Object]) -> None:
        self.actual = bpy_a.boolean(obj, self.path(), True)

    def _graph_values(self) -> list[tuple[Any, float]]:
        return [(True, 1.0)]

    def set_otherwise(self, obj: Optional[Object]) -> bool:
        return bpy_a.boolean(obj, self.path(), self.otherwise)


class Menu(PathAugmentation):
    """Sets any menu (enum) value, given by its data path, to a random option.

    For geometry nodes menu inputs, node settings like the Principled BSDF
    distribution, the light type and so on.

    As an input of a [`Node`][blendmentation.augmentations.augmentations.Node] or
    [`Modifier`][blendmentation.augmentations.augmentations.Modifier], leave the data
    path out: `Menu(["Sand", "Gravel"])`. The `Node` or `Modifier` gives it the path of
    the input.

    Args:
        data_path: path to the value, absolute (starting with `bpy.`) or relative to
            the object (or the World, material… passed in its place). Left out inside a
            `Node` or `Modifier`.
        options: options to choose from. None = all options of the menu: a Menu
            Switch's, or a group node's input that leads to one; required when Blender
            doesn't list them, e.g. for a built-in node's menu like Transform Geometry's
            Mode.
        weights: relative probability of each option, in the order of `options`, or
            without them in Blender's order (a light's type: POINT, SUN, SPOT, AREA; a
            Menu Switch: its items top to bottom). None = equal.
        p: probability of applying the augmentation.
        otherwise: option to set when it doesn't run, None keeps the value. It doesn't
            have to be one of `options`.
        name: a name for it in the graph and the report (`Compose.describe`,
            `export.augmentation_report`), e.g. `"slide sideways"`. None: its class name.

    Raises:
        ValueError: the number of weights and options differ (with `options` None, at
            a call), a weight is negative or not a number, or the weights sum to 0.
        TypeError: the data path is not a string, e.g. `Menu(["A", "B"], [3, 1])`
            inside a `Node`, where the weights must be given by name:
            `Menu(["A", "B"], weights=[3, 1])`.

    !!! info "With an absolute path"
        A path starting with `bpy.` doesn't use the object, so it can be called alone,
        `aug()`. In a `Compose` it runs once per object the `Compose` is called with,
        and the last value set is kept; `results` holds only that one, under `None`.

    Example:
        ```python
        augmentations.Menu("data.type", options=["POINT", "SPOT", "AREA"], weights=[2, 1, 1])
        # a spot or area light in 30 % of the images, a point light in the others
        augmentations.Menu("data.type", options=["SPOT", "AREA"], p=0.3, otherwise="POINT")
        # an input of a Node: the path comes from the Node
        augmentations.Node('node_tree.nodes["Ground"]', {"Type": augmentations.Menu(["Sand", "Gravel"])})
        ```
    """

    def __init__(self, data_path: Union[str, Sequence[Any], None] = None, options: Optional[Sequence[Any]] = None,
                 weights: Optional[Sequence[float]] = None, p: float = 1.0, otherwise: Any = None,
                 name: Optional[str] = None):
        super().__init__(p, otherwise, name=name)
        if not (data_path is None or isinstance(data_path, str)) and options is not None:
            raise TypeError(f"Without a data path the first argument is the options, so {options!r} can't be "
                            f"options too: pass the weights by name, Menu({data_path!r}, weights={options!r})")
        self.data_path, options = split_path(data_path, options)
        check_weights(weights, None if options is None else len(options))
        self.options = options
        self.weights = weights

    def apply(self, obj: Optional[Object]) -> None:
        self.actual = bpy_a.menu(obj, self.path(), self.options, self.weights)

    def _graph_values(self) -> Optional[list[tuple[Any, float]]]:
        return None if self.options is None else list(zip(self.options, weight_shares(self.weights, len(self.options))))

    def set_otherwise(self, obj: Optional[Object]) -> Any:
        return bpy_a.menu(obj, self.path(), [self.otherwise], None)


def color_bound(bound: Any) -> Any:
    """A bound or otherwise for a color input: a number or 3 values set R, G, B and keep alpha."""
    if is_number(bound):
        return (bound, bound, bound, None)
    if isinstance(bound, Sequence) and not isinstance(bound, str) and len(bound) == 3:
        return (*bound, None)
    return bound


#: the augmentation that sets each socket type, and its main argument
INPUT_AUGMENTATIONS = {"VALUE": "Number", "INT": "Number", "VECTOR": "Vector", "ROTATION": "Vector",
                       "RGBA": "Vector", "BOOLEAN": "Boolean", "MENU": "Menu"}
#: socket types as they are named in errors
SOCKET_NAMES = {"VALUE": "a float", "INT": "an int", "VECTOR": "a vector", "ROTATION": "a rotation",
                "RGBA": "a color", "BOOLEAN": "a boolean", "MENU": "a menu"}


def input_augmentation(kind: str, key: Union[str, int], spec: Any) -> PathAugmentation:
    """The `Number`, `Vector`, `Boolean` or `Menu` (without a path) for a plain value
    given as an input of a `Node` or `Modifier`, by socket type."""
    if kind not in INPUT_AUGMENTATIONS:
        raise TypeError(f"Input {key!r} is a {kind} socket, only number, vector, rotation, color, boolean "
                        f"and menu inputs can be augmented")
    augmentation = {"Number": Number, "Vector": Vector, "Boolean": Boolean, "Menu": Menu}[INPUT_AUGMENTATIONS[kind]]
    if augmentation in (Number, Vector) and spec is None:
        raise ValueError(f"Input {key!r} is {SOCKET_NAMES[kind]} input, give it a range")
    try:
        return augmentation(spec)
    except (TypeError, ValueError) as error:
        raise ValueError(f"Input {key!r} is {SOCKET_NAMES[kind]} input: {error}") from None


class InputAugmentation:
    """One input of a `Node` or `Modifier`. On every call it finds the data path of the
    input's value and its socket type, gives the path to the input's `Number`,
    `Vector`, `Boolean` or `Menu` and calls it."""

    def __init__(self, key: Union[str, int], spec: Any):
        if isinstance(spec, Augmentation) and not isinstance(spec, PathAugmentation):
            raise TypeError(f"Input {key!r}: {type(spec).__name__} can't set an input, use Number, Vector, "
                            f"Boolean or Menu")
        if isinstance(spec, PathAugmentation) and spec.data_path is not None:
            raise ValueError(f"Input {key!r}: leave the data path out, the input's path is used")
        self.key = key
        self.spec = spec
        # a plain value becomes one augmentation per socket type
        self.by_kind: dict[str, PathAugmentation] = {}
        self.applied: Optional[bool] = None
        self.actual: Any = None
        self.results: dict[Optional[str], Any] = {}
        self._applied: dict[Optional[str], Optional[bool]] = {}

    @property
    def augmentations(self) -> list[PathAugmentation]:
        """The input's augmentation, so `Compose` clears its `results` too."""
        return [self.spec] if isinstance(self.spec, PathAugmentation) else list(self.by_kind.values())

    def resolve(self, obj: Optional[Object]) -> tuple[str, str]:
        """The data path of the input's value for this object, and its socket type."""
        raise NotImplementedError

    def augmentation(self, kind: str) -> PathAugmentation:
        """The input's augmentation for a socket type, checked against it."""
        if not isinstance(self.spec, PathAugmentation):
            if kind not in self.by_kind:
                self.by_kind[kind] = input_augmentation(kind, self.key, self.spec)
            return self.by_kind[kind]
        expected = INPUT_AUGMENTATIONS.get(kind)
        if expected is None:
            raise TypeError(f"Input {self.key!r} is a {kind} socket, only number, vector, rotation, color, "
                            f"boolean and menu inputs can be augmented")
        if type(self.spec).__name__ != expected:
            raise TypeError(f"Input {self.key!r} is {SOCKET_NAMES[kind]} input: set it with {expected}, "
                            f"not {type(self.spec).__name__}")
        return self.spec

    def __call__(self, obj: Optional[Object] = None) -> None:
        data_path, kind = self.resolve(obj)
        augmentation = self.augmentation(kind)
        augmentation.data_path, augmentation.color = data_path, kind == "RGBA"
        try:
            augmentation(obj)
        finally:
            augmentation.data_path = None  # it is the Node's, given again on the next call
        self.applied, self.actual = augmentation.applied, augmentation.actual
        key = None if obj is None or self.ignores_object else obj.name
        self.results[key] = self.actual
        self._applied[key] = self.applied

    def _graph_children(self) -> list[tuple[Any, float]]:
        return [(augmentation, 1.0) for augmentation in self.augmentations]

    @property
    def ignores_object(self) -> bool:
        raise NotImplementedError


class NodeInput(InputAugmentation):
    """One input of a `Node`, at a fixed data path."""

    def __init__(self, key: Union[str, int], data_path: str, spec: Any):
        super().__init__(key, spec)
        self.data_path = data_path

    @property
    def ignores_object(self) -> bool:
        return is_absolute(self.data_path)

    def resolve(self, obj: Optional[Object]) -> tuple[str, str]:
        return self.data_path, bpy_a.socket_type(obj, self.data_path)


class ModifierInput(InputAugmentation):
    """One input or setting of a `Modifier`. A geometry nodes input's data path depends
    on the Blender version and the node group's socket identifier, so it is found on
    every call."""

    def __init__(self, key: Union[str, int], modifier: str, spec: Any):
        input_key(key)
        super().__init__(key, spec)
        self.modifier = modifier

    @property
    def ignores_object(self) -> bool:
        return is_absolute(self.modifier)

    def resolve(self, obj: Optional[Object]) -> tuple[str, str]:
        return bpy_a.modifier_input(obj, self.modifier, self.key)


class InputsAugmentation(Augmentation):
    """A `Node` or `Modifier`: runs one `InputAugmentation` per input, its `actual` is
    `{key: the input's actual}`."""

    def __init__(self, inputs: dict[Union[str, int], Any], p: float, name: Optional[str] = None):
        super().__init__(p, name=name)
        if not inputs:
            raise ValueError("Give at least one input")
        self.inputs = inputs
        self.augmentations: list[InputAugmentation] = []

    @property
    def ignores_object(self) -> bool:
        return all(augmentation.ignores_object for augmentation in self.augmentations)

    def apply(self, obj: Optional[Object]) -> None:
        self.actual = {}
        for augmentation in self.augmentations:
            augmentation(obj)
            self.actual[augmentation.key] = augmentation.actual


class Node(InputsAugmentation):
    """Sets the inputs of a node to random values, each with its own range, probability
    and `otherwise`.

    For the group node of a smart material, of a geometry nodes setup, or any other
    node in a shader, world or geometry nodes tree. The inputs of a geometry nodes
    *modifier* are not a node: set them with
    [`Modifier`][blendmentation.augmentations.augmentations.Modifier].

    `inputs` maps each input to augment, by name (or index, for inputs that share a
    name), to the augmentation that sets it, written **without a data path**: the
    `Node` gives each one the path of its input. Inputs that are left out keep their
    values.

    | Socket | Set it with |
    | --- | --- |
    | Float, Int | [`Number`][blendmentation.augmentations.augmentations.Number]`((min, max))` or `((min, max, step))` |
    | Vector | [`Vector`][blendmentation.augmentations.augmentations.Vector]`((min, max))` |
    | Rotation | `Vector((min, max))` of the X, Y, Z angles in radians |
    | Color | `Vector((min, max))`: a number or 3 values set red, green and blue and keep alpha, so `(0, 1)` is any color; 4 values set alpha too |
    | Boolean | [`Boolean`][blendmentation.augmentations.augmentations.Boolean]`(p=...)`, the probability of True |
    | Menu | [`Menu`][blendmentation.augmentations.augmentations.Menu]`([options])`, or `Menu()` for every option |

    They take all their usual arguments but the path (`p`, `otherwise`, `weights`…).
    The path inherited from the `Node` is that of the input's value, so
    `augmentations.Node('active_material.node_tree.nodes["Ferrous metal"]', {"Rust
    strength": augmentations.Number((0, 1))})` sets
    `active_material.node_tree.nodes["Ferrous metal"].inputs["Rust strength"].default_value`:
    relative to the object when the node's path is, absolute when it is. The `Node`
    gives the path before each call and takes it back after, so the same `Number` does
    nothing on its own (it raises a `ValueError` without a path). An augmentation that
    doesn't match the socket, e.g. a `Number` on a color input, raises a `TypeError`
    at the call. For short, an input can also be just the range or options, e.g.
    `"Rust strength": (0, 1)`, and `Node` picks the augmentation by the socket type.

    Each input draws its own `p`, and `Node`'s own `p` applies the whole node: when it
    doesn't run, no input changes, also not to its `otherwise`.

    The node is given by its data path, with the node's name: Sidebar (N) > Node >
    Name, not the title on the node (a group node shows its group's name), or right
    click an input > Copy Full Data Path. The path is absolute (starting with `bpy.`) or
    relative to the object, e.g. `active_material.node_tree.nodes["Ferrous metal"]` to augment the
    material of each object in a `Compose`. The inputs must not be connected to other
    nodes. It changes the **node tree** (the material, the node group…), so everything
    using it is affected.

    Args:
        node: data path of the node.
        inputs: what to set each input to, by input name or index.
        p: probability of applying the augmentation.
        name: a name for it in the graph and the report (`Compose.describe`,
            `export.augmentation_report`), e.g. `"slide sideways"`. None: its class name.

    Attributes:
        actual (dict | None): the value set to each input by the last call, by its
            key in `inputs` (None for an input skipped by its `p` without
            `otherwise`), None when it was skipped.
        augmentations (list): one entry per input, with the `data_path` of its value;
            `Compose` clears their `results`.

    Raises:
        ValueError: `inputs` is empty, an input's key is not a name or index, or its
            augmentation has a data path of its own. At the first call: a short value
            doesn't fit its socket type, e.g. a Float without a range.
        TypeError: an input is set with an augmentation other than `Number`,
            `Vector`, `Boolean` or `Menu`. At a call: the augmentation doesn't match
            the socket, or the input is not a number, vector, rotation, color, boolean
            or menu socket.

    !!! info "With an absolute path"
        A path starting with `bpy.` doesn't use the object, so it can be called alone,
        `node()`. In a `Compose` it runs once per object the `Compose` is called with,
        and the last values set are kept; `results` holds only those, under `None`.

    Tip:
        `Node` is the same as a `Chain` of `Number`, `Vector`, `Boolean` and `Menu`
        with the full path of each input's value,
        `'node_tree.nodes["Ferrous metal"].inputs["Rust strength"].default_value'`,
        written once for the node. Its `actual` is keyed by input.

    Example:
        ```python
        ferrous_metal = augmentations.Node(
            'bpy.data.materials["Master material"].node_tree.nodes["Ferrous metal"]',
            {
                "Texture ofset": augmentations.Vector((-100, 100)),   # every axis
                "Base metal type": augmentations.Menu(),              # any option
                "Surface effect": augmentations.Menu(["Weathered", "Grinded"], weights=[3, 1]),
                "Base metal color": augmentations.Vector(((0.3, 0.3, 0.3), (0.6, 0.6, 0.6))),  # alpha kept
                "Rust strength": augmentations.Number((0, 1, 0.25)),  # 0, 0.25, ... 1
                # rust in 20 % of the images, none in the others
                "Rust spread": augmentations.Number((0.5, 1), p=0.2, otherwise=0),
                "Paint Color": augmentations.Vector((0, 1)),          # any color
                "Paint": augmentations.Boolean(p=0.3),                # painted in 30 %
            },
        )
        with state.restoring():
            ferrous_metal()   # absolute path: no object needed
            ferrous_metal.actual   # e.g. {"Texture ofset": (12.0, -40.3, 77.1), "Base metal type": "Cast metal", ...}
            generator()
        ```

        A group node in a geometry nodes tree:

        ```python
        rocks = augmentations.Node(
            'bpy.data.node_groups["Scatter"].nodes["Rock generator"]',
            {"Count": augmentations.Number((10, 50)),
             "Size": augmentations.Number((0.5, 2), p=0.5, otherwise=1)},
        )
        ```
    """

    def __init__(self, node: str, inputs: dict[Union[str, int], Any], p: float = 1.0, name: Optional[str] = None):
        super().__init__(inputs, p, name=name)
        self.node = node
        self.augmentations = [NodeInput(key, f"{node}.inputs[{input_key(key)}].default_value", spec)
                              for key, spec in inputs.items()]

    @staticmethod
    def template(node: Union[str, BpyNode], obj: Optional[Object] = None, prefix: str = "augmentations.",
                 relative: bool = False) -> str:
        """Writes the source of a `Node` that sets every input of a node, to paste into a
        script and edit.

        Each input gets the augmentation that sets it, without a data path (the `Node`
        gives it), with the widest range the node allows, so you narrow the ranges you
        want and delete the inputs to leave alone:

        | Socket | Written as |
        | --- | --- |
        | Float, Int | `Number((min, max))` |
        | Vector | `Vector((min, max))` |
        | Rotation | `Vector((v, v))` with the current X, Y, Z angles in radians |
        | Color | `Vector((0.0, 1.0))`: any color, alpha kept |
        | Boolean | `Boolean(p=0.5)` |
        | Menu | `Menu([...])` with every option: a Menu Switch's, or a group input's that leads to one. A built-in node's menu (e.g. Transform Geometry's Mode) is a `Warning!` comment, since Python can't list its options: add `Menu([...])` yourself |

        The min and max of a Float, Int or Vector are those of the socket: for a group
        node, Min and Max of the input in the group's interface; for other nodes, the
        socket type's (0 to 1 for a factor). Without them, it is `(v, v)` with the
        current value. A comment after each input gives its type and current value.
        Connected inputs and sockets `Node` can't set (shader, geometry, string,
        object...) are only comments, and hidden ones (unavailable in the node's current
        mode) are left out.
        Inputs that share a name are keyed by index, with the name in the comment. The
        node's name and the input names are written exactly, trailing spaces included.

        The path is absolute by default (`bpy.data.materials["Paint"].node_tree...`): the
        `Node` always changes that node, so it works with any objects passed to the
        `Compose`, or called alone. With `relative=True` it starts from `obj`, through
        its active material, another material slot, its data (a light) or the
        geometry nodes modifier using the node's group (`active_material.node_tree...`):
        pass the object to the `Compose`, and the `Node` also works for other objects
        set up the same way. A world's node is relative to the world. A comment on top
        says which, with the object's name.

        In the Blender app, the add-on does the same for the active node in the node
        editor and copies it to the clipboard: Sidebar (N) > Blendmentation > Copy Node
        Template, Relative (from the active object) or Absolute; or Node menu, right
        click a node or F3 > Copy Blendmentation Template. See
        [Installation](installation.md#inside-blender-as-an-add-on).

        Args:
            node: the node, or its data path. A path is kept in the text as it is, so a
                path relative to `obj` makes a relative template.
            obj: object a relative path starts from.
            prefix: written before `Node`, `Number`, `Vector`, `Boolean` and `Menu`, e.g. `"aug."` after
                `from blendmentation.augmentations import augmentations as aug`.
            relative: for a node, write its path relative to `obj`.

        Returns:
            The source of the `Node`, ending in a newline.

        Raises:
            TypeError: `node` is not a node.
            ValueError: the node has no inputs, or `relative` and the node can't be
                reached from `obj`.

        Example:
            ```python
            print(augmentations.Node.template(
                'bpy.data.materials["Master material"].node_tree.nodes["Ferrous metal"]'
            ))
            ```

            prints

            ```python
            # Absolute paths: they always change the material "Master material", so it works with any
            # objects passed to the Compose, or called alone without one.
            augmentations.Node('bpy.data.materials["Master material"].node_tree.nodes["Ferrous metal"]', {
                # "Texture Coordinate": connected
                "Base metal color": augmentations.Vector((0.0, 1.0)),  # color, now (0.1385, 0.1385, 0.1385, 1.0)
                "Base metal type": augmentations.Menu(["Shiny metal", "Cast metal"]),  # menu, now "Shiny metal"
                "Texture scale": augmentations.Number((-10000.0, 10000.0)),  # float, now 1.0
                "Rust strength": augmentations.Number((0.0, 1.0)),     # float, now 0.0
                ...
            })
            ```
        """
        return bpy_a.node_template(node, obj, prefix, relative)


def input_key(key: Union[str, int]) -> str:
    """`key` as a data path subscript: an index, or a quoted name."""
    if isinstance(key, int) and not isinstance(key, bool):
        return str(key)
    if not isinstance(key, str):
        raise ValueError(f"Inputs are given by name or index, got {key!r}")
    return '"' + key.replace("\\", "\\\\").replace('"', '\\"') + '"'


class Modifier(InputsAugmentation):
    """Sets the settings of a modifier to random values, each with its own range,
    probability and `otherwise`: the inputs shown on a geometry nodes modifier, or the
    settings of any other modifier (Subdivision Surface, Bevel, Array, Displace...).

    It works like [`Node`][blendmentation.augmentations.augmentations.Node]: `inputs`
    maps each one to a `Number`, `Vector`, `Boolean` or `Menu` **without a data path**,
    as in `Node` (see its table), or for short just the range or options. The
    `Modifier` gives each one the path of its value before each call. The keys are:

    - for a geometry nodes modifier, the input's name in the node group (or its index
      among the group's inputs, Geometry included). The path depends on the Blender
      version, and `Modifier` builds it. A rotation is X, Y, Z angles in radians. An
      input set to be read from an attribute keeps reading it.
    - for any modifier, a setting by its Python name, e.g. `"levels"` for Subdivision
      Surface's Levels Viewport, `"width"` for Bevel's Amount: hover over the setting
      with Python tooltips on (Preferences > Interface), or use
      [`Modifier.template`][blendmentation.augmentations.augmentations.Modifier.template].
      A menu's options are Blender's identifiers, e.g. `"CATMULL_CLARK"`. On a geometry
      nodes modifier this also works, e.g. `"show_render"` to turn it on and off, when
      no input has that name.

    The modifier is given by its data path, relative to the object (e.g.
    `modifiers["Scatter"]`, to augment that modifier on every object in a `Compose`),
    or absolute (`bpy.data.objects["Floor"].modifiers["Scatter"]`). It changes the
    modifier only, not a geometry nodes modifier's node group, so objects with the same
    group keep their own values.

    Args:
        modifier: data path of the modifier.
        inputs: what to set each input or setting to, by input name or index (geometry
            nodes), or setting name.
        p: probability of applying the augmentation.
        name: a name for it in the graph and the report (`Compose.describe`,
            `export.augmentation_report`), e.g. `"slide sideways"`. None: its class name.

    Attributes:
        actual (dict | None): the value set to each input by the last call, by its
            key in `inputs` (None for an input skipped by its `p` without
            `otherwise`), None when it was skipped.

    Raises:
        ValueError: `inputs` is empty, an input's key is not a name or index, or its
            augmentation has a data path of its own. At the first call: a short value
            doesn't fit its socket type, e.g. a Float without a range.
        KeyError: at a call, the modifier has no such input or setting.
        TypeError: an input is set with an augmentation other than `Number`,
            `Vector`, `Boolean` or `Menu`. At a call: the path is not a modifier, the
            augmentation doesn't match the input, or the input is not a number,
            vector, rotation, color, boolean or menu.

    Tip:
        Each input can also be set on its own, with `Number`, `Vector`, `Boolean` or
        `Menu` and the data path of its value. That path uses the socket's identifier,
        not its name, and changed in Blender 5:
        `modifiers["Scatter"]["Socket_2"]` in 4.x,
        `modifiers["Scatter"].properties.inputs.Socket_2.value` in 5.x. `Modifier`
        finds the inputs by name and builds the path for the running version.

    Example:
        ```python
        floor = bpy.data.objects["Floor"]
        scatter = augmentations.Modifier('modifiers["Scatter"]', {
            "Density": augmentations.Number((5, 20)),
            "Rock size": augmentations.Number((0.5, 2), p=0.5, otherwise=1),
            "Mossy": augmentations.Boolean(p=0.3),          # True 30 %
            "Ground": augmentations.Menu(["Sand", "Gravel"]),
        })
        with state.restoring():
            augmentations.Compose([scatter])([floor])
            scatter.actual   # e.g. {"Density": 12.7, "Rock size": 1, "Mossy": False, "Ground": "Sand"}
            generator()
        ```

        Any other modifier, by its settings' Python names:

        ```python
        smoothing = augmentations.Modifier('modifiers["Subdivision"]', {
            "render_levels": augmentations.Number((1, 3)),
            "subdivision_type": augmentations.Menu(["CATMULL_CLARK", "SIMPLE"]),
        })
        bevel = augmentations.Modifier('modifiers["Bevel"]', {
            "width": augmentations.Number((0.01, 0.05)),
            "segments": augmentations.Number((1, 4)),
            "show_render": augmentations.Boolean(p=0.7),   # beveled edges in 70 %
        })
        ```
    """

    def __init__(self, modifier: str, inputs: dict[Union[str, int], Any], p: float = 1.0, name: Optional[str] = None):
        super().__init__(inputs, p, name=name)
        self.modifier = modifier
        self.augmentations = [ModifierInput(key, modifier, spec) for key, spec in inputs.items()]

    @staticmethod
    def template(modifier: Union[str, BpyModifier], obj: Optional[Object] = None, prefix: str = "augmentations.",
                 relative: bool = True) -> str:
        """Writes the source of a `Modifier` that sets every input of a geometry nodes
        modifier, or every setting of another modifier, to paste into a script and edit.

        Each input gets the augmentation that sets it, without a data path (the
        `Modifier` gives it), as in [`Node.template`][blendmentation.augmentations.augmentations.Node.template]:
        `Number((min, max))` and `Vector((min, max))`, `Vector((0.0, 1.0))` for a color,
        `Vector((v, v))` for a rotation, `Boolean(p=0.5)` and `Menu([...])` with every
        option. A comment after each gives its type and current value.

        - A geometry nodes modifier: its inputs by name, the min and max from the input
          in the node group's interface (`(v, v)` with the current value without them).
          The Geometry input is left out; inputs set to read an attribute, and sockets
          `Modifier` can't set (object, material, string...) are only comments. Inputs
          that share a name are keyed by index.
        - Any other modifier: its settings by their Python name, with the name shown in
          the UI in the comment, the min and max of the setting's slider. Settings
          about the modifier's UI (Show in Edit Mode...) and settings `Modifier` can't
          set (objects, vertex groups, textures...) are left out.

        A last comment says how to turn the modifier on and off in the render
        (`"show_render"`); it is not set by default.

        The path is relative by default (`modifiers["Scatter"]`): pass the object to
        the `Compose`, and the `Modifier` also works for other objects with a modifier
        of that name. With `relative=False` it is absolute
        (`bpy.data.objects["Floor"].modifiers["Scatter"]`) and always changes that
        modifier. A comment on top says which, with the object's name.

        In the Blender app, the add-on does the same for the active modifier of the
        active object (the one highlighted in the Modifier Properties) and copies it to
        the clipboard: 3D Viewport sidebar (N) >
        Blendmentation > Copy Modifier Template, Relative or Absolute. See
        [Installation](installation.md#inside-blender-as-an-add-on).

        Args:
            modifier: the modifier, or its data path. A path is kept in the text as it
                is, so a path relative to `obj` makes a relative template.
            obj: object a relative path starts from.
            prefix: written before `Modifier`, `Number`, `Vector`, `Boolean` and
                `Menu`, e.g. `"aug."` after
                `from blendmentation.augmentations import augmentations as aug`.
            relative: for a modifier, write its path relative to its object; False:
                absolute.

        Returns:
            The source of the `Modifier`, ending in a newline.

        Raises:
            TypeError: `modifier` is not a modifier.
            ValueError: a geometry nodes modifier has no node group, or the modifier
                has no inputs or settings to set.

        Example:
            ```python
            print(augmentations.Modifier.template(bpy.data.objects["Floor"].modifiers["Scatter"]))
            ```

            prints

            ```python
            # Relative paths: they start from the object it is called with, here "Floor".
            # Pass that object to the Compose, compose([bpy.data.objects["Floor"]]), or one set up the same way.
            augmentations.Modifier('modifiers["Scatter"]', {
                "Density": augmentations.Number((0.0, 50.0)),      # float, now 10.0
                "Mossy": augmentations.Boolean(p=0.5),             # boolean, now False
                "Ground": augmentations.Menu(["Sand", "Gravel"]),  # menu, now "Sand"
            })
            ```
        """
        return bpy_a.modifier_template(modifier, obj, prefix, relative)
