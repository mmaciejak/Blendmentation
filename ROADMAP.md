# Roadmap

Ideas that are not decided yet. Nothing here has a date or a version.

## `Node` template generator

Writing the `inputs` dict of a `Node` by hand is slow. The node shows input
names shortened ("Paint Col...", "Base met..."), and you have to know each input's
type to know what to give it. A generator would read a node in the scene and write a
ready-to-edit `inputs` dict in the `Input` format, to paste into a script:

```python
print(augmentations.Node.template(
    'bpy.data.materials["Master material"].node_tree.nodes["Ferrous metal"]'
))
```

```python
{
    "Texture ofset": Input((0.0, 0.0)),            # vector, now (0, 0, 0)
    "Surface effect": Input(options=["Dotted", "Brushed", "Hammered", "Grinded", "Weathered"]),   # menu, now "Grinded"
    "Surface effect strength": Input((1.0, 1.0)),  # float, now 1.0
    "Base metal color": Input((0.5, 0.5)),         # color, now (0.5, 0.5, 0.5, 1)
    "Rust strength": Input((1.0, 1.0)),            # float, now 1.0
    "Paint Color": Input((0.8, 0.8)),              # color, now (0.8, 0.0, 0.0, 1)
    ...
}
```

You would widen the ranges you want, and delete the inputs to leave alone.

It would also catch what is easy to get wrong by hand: the node's name, which is not the
title on the node (a group node shows its group's name, the node is e.g. "Group.004");
menu inputs, which show their current option instead of their name; and names with a
stray space at the end ("Paint disccoloration ").

It would also help agents using the library through Blender MCP: one call gives the
node's inputs, their types and their options.

### Open questions

- **Where it lives:** a static method `Node.template(node)`, a function in
  `augmentations`, or a method on an existing `Node`, which would print the
  inputs it doesn't set yet.
- **What it returns:** source text to print and paste, or a real dict of `Input`s that can
  be passed to `Node` as it is (and printed through `Input.__repr__`).
- **Default ranges:** the current value as `(v, v)`, so nothing changes until it is
  edited; the socket's min and max from the group interface (`min_value`, `max_value`),
  where it has them; or something in between, like the current value ± 10 %.
- **Colors:** `(min, max)` as a number for every channel, or one value per channel, so
  the current color is visible in the template.
- **Booleans:** `Input(p=...)` with what as `p`? 1 when the input is on, 0 when it is off?
- **Which inputs:** every input, or only those not connected to another node, since
  connected inputs can't be augmented. Show the connected ones as comments?
- **Unsupported sockets** (shader, string, object, image...): leave them out, or list
  them as comments so the user knows why they are missing.
- **Inputs that share a name:** key them by index, with the name in a comment.
- **Other nodes:** it would work on any node, not only group nodes, e.g. the Principled
  BSDF has around 30 inputs. Is that too much without a filter (`only=["Coat*"]`)?
