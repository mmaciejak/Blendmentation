# State

```python
State(objects, fields=())
```

`State` saves the scene so it can be restored after every datapoint. It saves:

- the transforms of the `objects`, and the lens and depth of field of cameras;
- all node values of the materials on those objects;
- the value at the data path of every `Number` / `Vector` / `Boolean` / `Menu` in `fields`.

`fields` can also contain path strings. Other augmentations in `fields` are ignored,
so you can pass a whole `compose.augmentations` list.

`restore()` puts it all back. A data path augmentation that you **don't** list in
`fields` is not restored; with `percent`, its changes then build up from one
datapoint to the next.

## API reference

::: blendmentation.state.state
