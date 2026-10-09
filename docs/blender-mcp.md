# Compatibility with Blender MCP

[Blender MCP](https://github.com/ahujasid/blender-mcp) lets an AI agent (Claude Code,
Claude Desktop, Cursor and others) control a running Blender by executing Python in it.
Blendmentation runs in that same Python, so an agent can build and run a dataset
pipeline on the scene you have open: you describe the dataset, the agent inspects the
scene, writes the augmentations and generating steps, checks a preview and generates
the datapoints.

The repository has an agent skill for this, in
[`skills/blendmentation`](https://github.com/mmaciejak/Blendmentation/tree/main/skills/blendmentation).
It tells the agent how to:

- make the library importable inside Blender (adding the repository to `sys.path`, and
  reloading it after the library changes);
- work with how the MCP code tool runs code: a fresh namespace per call, only printed
  output returned, and a time limit per call;
- inspect the scene and agree on the classes, augmentations and outputs with you;
- check the pipeline with one preview datapoint before generating;
- generate in batches that fit the time limit, always restoring the scene, or switch to a
  headless `blender --background` run for large datasets;
- export to COCO, YOLO, Pascal VOC or BOP.

## Setup

1. **Blender MCP:** install the Blender add-on and the MCP server, and connect it to your
   agent, as described in the [Blender MCP README](https://github.com/ahujasid/blender-mcp).
   Other Blender MCP servers work too, if they have a tool that executes Python in Blender.
2. **Blendmentation:** get a copy of the repository
   (`git clone https://github.com/mmaciejak/Blendmentation`). Blender doesn't need it
   installed; the agent adds the folder to `sys.path`. Installing it into Blender with pip,
   as in [Installation](installation.md), works too.
3. **The skill:** copy or link the skill folder into your agent's skills folder. For
   Claude Code, that is `~/.claude/skills/` for all projects, or `.claude/skills/` in a
   project:

    ```sh
    mkdir -p ~/.claude/skills
    cp -r Blendmentation/skills/blendmentation ~/.claude/skills/
    ```

    Other agents that support Agent Skills (`SKILL.md` folders) load it from their own
    skills folder. When you work inside the repository, the agent can also read the skill
    from `skills/blendmentation/SKILL.md` directly.

Tell the agent where the repository is, unless it is the folder the agent works in.

## Using it

Open your scene in Blender, start the add-on's server, and ask for a dataset, for
example:

> Make a 200 image detection dataset of the two cars in the open scene, with random
> camera angles around them, random car colors and segmentation masks, and export it to
> COCO.

The agent shows you what it found in the scene and the pipeline it plans, generates one
preview datapoint to check, then generates the rest and exports it.

## Things to know

- Blender is busy while a batch renders, and its window doesn't respond until the batch ends.
- The scene is your live scene. Every datapoint is set back with `state.restoring()`, but save your work
  before you start.
- For large datasets the agent can save a copy of the scene and render it headless with
  `blender --background`, which is faster and leaves your Blender free. It needs shell
  access for that.
