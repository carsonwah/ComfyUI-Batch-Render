# ComfyUI Batch Render

Batch-render images across **base × scenario** combinations on [ComfyUI](https://github.com/comfyanonymous/ComfyUI).

Define a **base** (e.g. a character: checkpoint + prompt + LoRA(s)) and a list of **scenarios**
(each its own checkpoint? + prompt + LoRA(s)), then render every combination automatically —
no more manually swapping a LoRA, editing its prompt, and re-queuing one image at a time.

Base and scenario are the **same shape** (`Layer = { checkpoint?, prompt, loras[] }`); a render
combines one base + one scenario. LoRAs are loaded by splicing a chain of stock `LoraLoader`
nodes into *your own* exported workflow — no required custom nodes, no assumed folder layout.

## Status

Early build. Implemented incrementally as composable tiers:

| Tier | What | Status |
|------|------|--------|
| 0 | Graph patcher (pure functions) | done |
| 1 | Run engine + CLI | done |
| 2 | ComfyUI plugin shell (web server) | planned |
| 3 | Web UI | planned |

## Design

Architecture and rationale live in the project notes (not committed). The short version:

- **Bring your own workflow.** Export your ComfyUI workflow in *API format*; the engine fills a
  few mapped slots (prompt, seed, model/clip splice point, checkpoint) and leaves the rest alone.
- **Native & future-proof.** LoRA loading uses stock `LoraLoader` nodes assembled in the workflow
  JSON; the only external contract is ComfyUI's stable `/prompt` + `/ws` API.
- **Local & flexible.** No assumptions about folder structure or ports; everything is read live or
  configured via a gitignored local `config.yaml` (see `config.example.yaml`).

## Configuration

Copy `config.example.yaml` to `config.yaml` (gitignored) and set your ComfyUI host/port and paths.

## License

MIT
