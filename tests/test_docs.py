"""The docs pages that list their members explicitly list every public function."""

import ast
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_export_page_lists_every_export():
    with open(os.path.join(ROOT, "blendmentation", "export", "export.py")) as file:
        tree = ast.parse(file.read())
    # public functions are the ones documented with an example
    public = [node.name for node in tree.body if isinstance(node, ast.FunctionDef)
              and "Example:" in (ast.get_docstring(node) or "")]
    with open(os.path.join(ROOT, "docs", "export.md")) as file:
        members = re.search(r"members: \[(.*)\]", file.read()).group(1).replace(" ", "").split(",")
    assert sorted(members) == sorted(public)
