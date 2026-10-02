import pytest

bpy = pytest.importorskip("bpy")

from blendmentation import bpy_paths  # noqa: E402


def test_parse():
    assert bpy_paths.parse('a.b["x\\"y"][3].c') == [
        ("attr", "a"), ("attr", "b"), ("item", 'x"y'), ("item", 3), ("attr", "c")
    ]
    assert bpy_paths.parse("bpy.data.materials['my \"mat\"']")[-1] == ("item", 'my "mat"')


@pytest.mark.parametrize("path", ["location]", "a b", ""])
def test_parse_errors(path):
    with pytest.raises(ValueError):
        bpy_paths.parse(path)


def test_get_and_set(cube):
    obj = cube("Cube")
    bpy_paths.set_value("location[2]", 4.0, obj)
    assert obj.location.z == 4.0
    assert bpy_paths.get_value('bpy.data.objects["Cube"].location[2]') == 4.0
    with pytest.raises(ValueError, match="needs an object"):
        bpy_paths.get_value("location")
