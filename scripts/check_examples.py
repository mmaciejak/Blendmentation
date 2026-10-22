"""Runs the usage examples, as the release check: `example.py`, the Full example of the
Quick start page and the README example (the same as the docs home page), each with a
few datapoints, in a scene built for it by `scripts/example_scenes.py`.

Every Blender app (`--blender`) and every Python with bpy as a module (`--python`) gets
its own scenes, since a .blend saved by a newer Blender may not open in an older one.
The examples are taken from the files as they are, so a run checks what users read.

    python scripts/check_examples.py --out /tmp/examples \\
        --blender "/Applications/Blender.app/Contents/MacOS/Blender" \\
        --python .venv/bin/python --python .venv42/bin/python

It prints OK or FAIL for every run, with its log, and exits with 1 if any failed. The
apps run with --factory-startup, so an installed Blendmentation add-on can't shadow the
repository, and module mode puts the repository first on the path for the same reason.
"""

import argparse
import os
import re
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCENES = os.path.join(REPO, "scripts", "example_scenes.py")
PATH_LINE = f"sys.path.insert(0, {REPO!r})"


def replaced(text, old, new, where):
    if old not in text:
        raise SystemExit(f"{where} changed: {old!r} not found, update scripts/check_examples.py")
    return text.replace(old, new)


def python_blocks(file_name):
    with open(os.path.join(REPO, file_name)) as file:
        return re.findall(r"```python[^\n]*\n(.*?)```", file.read(), re.S)


def example_scripts():
    """{name: (scene, source)} of the examples, with the repository on the path and a
    few datapoints."""
    with open(os.path.join(REPO, "example.py")) as file:
        example = file.read()
    example = replaced(example, "sys.path.append(os.path.dirname(os.path.abspath(__file__)))", PATH_LINE,
                       "example.py")
    example = re.sub(r"^n_datapoints = \d+$", "n_datapoints = 3", example, flags=re.M)

    with open(os.path.join(REPO, "docs", "quick-start.md")) as file:
        quick_start = file.read()
    setup = re.search(r"```python\n(import sys\nsys\.path\.append.*?)```", quick_start, re.S).group(1)
    setup = re.sub(r"sys\.path\.append\(.*\)", PATH_LINE, setup)
    full = re.search(r"## Full example.*?```python\n(.*?)```", quick_start, re.S).group(1)
    full = re.sub(r"for _ in range\(\d+\):", "for _ in range(3):", full)

    readme, home = python_blocks("README.md")[0], python_blocks(os.path.join("docs", "index.md"))[0]
    if readme != home:
        raise SystemExit("The README example and the one on docs/index.md differ, keep them identical")
    readme = "import sys\n" + PATH_LINE + "\n" + re.sub(r"range\(\d+\)", "range(2)", readme)
    return {"example.py": ("example", example), "quick start": ("quick", setup + "\n" + full),
            "readme": ("quick", readme)}


def run(command, log, env=None):
    """Runs the command in the folder of its log, writing its output to the log."""
    with open(log, "w") as file:
        result = subprocess.run(command, stdout=file, stderr=subprocess.STDOUT, env=env, cwd=os.path.dirname(log))
    return result.returncode == 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", required=True, help="folder for the scenes, datasets and logs")
    parser.add_argument("--blender", action="append", default=[], help="a Blender executable")
    parser.add_argument("--python", action="append", default=[], help="a Python with bpy and OpenImageIO")
    args = parser.parse_args()
    if not args.blender and not args.python:
        parser.error("give at least one --blender or --python")
    scripts = example_scripts()
    module_env = dict(os.environ, PYTHONPATH=REPO + os.pathsep + os.environ.get("PYTHONPATH", ""))
    # the runs start in their own folders: paths, not commands on PATH, made absolute
    setups = [(mode, os.path.abspath(path) if os.sep in path else path)
              for mode, paths in (("app", args.blender), ("module", args.python)) for path in paths]
    failed = 0
    for number, (mode, executable) in enumerate(setups):
        for name, (scene, source) in scripts.items():
            folder = os.path.abspath(os.path.join(args.out, f"{number}_{mode}", name.replace(" ", "_")))
            os.makedirs(folder, exist_ok=True)
            script = os.path.join(folder, "example.py")
            with open(script, "w") as file:
                file.write(source)
            blend = os.path.join(folder, "scene.blend")
            if mode == "app":
                build = [executable, "-b", "--factory-startup", "--python-exit-code", "1", "--python", SCENES,
                         "--", "build", scene, folder]
                example = [executable, "-b", "--factory-startup", blend, "--python-exit-code", "1",
                           "--python", script]
            else:
                build = [executable, SCENES, "build", scene, folder]
                example = [executable, SCENES, "run", blend, script]
            ok = (run(build, os.path.join(folder, "build.log"), module_env)
                  and run(example, os.path.join(folder, "run.log"), module_env))
            failed += not ok
            print(f"{'OK  ' if ok else 'FAIL'} {name:12} {mode:6} {executable}  ({folder})", flush=True)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
