"""The API reference pages show exactly the public names: each lists its module's
`__all__` as its members, so a new public name can't be missing and an internal helper
can't show up."""

import ast
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.mark.parametrize("module", ["augmentations", "generating", "state", "export"])
def test_reference_page_lists_all(module):
    with open(os.path.join(ROOT, "blendmentation", module, f"{module}.py")) as file:
        tree = ast.parse(file.read())
    public = next(ast.literal_eval(node.value) for node in tree.body
                  if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", None) == "__all__")
    with open(os.path.join(ROOT, "docs", f"{module}.md")) as file:
        members = re.findall(r"^ {8}- (\w+)$", file.read(), re.M)
    assert members == public
