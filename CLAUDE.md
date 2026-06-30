# CLAUDE.md

Guidance for anyone (human or AI agent) working in this repository. Read this before making changes.

---

## 1. Background — the problem

Generating anime/illustration images in [ComfyUI](https://github.com/comfyanonymous/ComfyUI) with stacks of LoRAs (character, outfit, style, pose, …) is tediously manual: for every variation you re-select LoRAs, retype their trigger words, adjust the prompt, and re-queue **one image at a time**. Producing "the same character across 20 scenes", then repeating that for another character, is hours of clicking.

This project automates that loop. You define reusable building blocks and let the tool render every combination.

## 2. The solution

- A **base** and a **scenario** are the *same shape* — a **Layer** = `{ checkpoint?, prompt, negative?, loras[] }`.
- A render combines **one base + one scenario**. A batch run is the cartesian product `bases × scenarios × seeds`.
- LoRAs are loaded by **splicing a chain of stock `LoraLoader` nodes into the user's own exported workflow** — no required custom nodes, no assumed folder layout.
- The user brings their **own** ComfyUI workflow (exported in *API format*). The tool only needs a few mapped "slots" (prompt node, seed node, the checkpoint's model/clip outputs, optional negative/ckpt) — auto-detected, manually overridable.
- Shipped as a **ComfyUI custom node** with a small **local web UI**; also usable as a **plain CLI/library**.

### Architecture — composable tiers

Each tier is independently usable and tested; a higher tier only *consumes* the one below.

```
Tier 0  patcher   pure functions: (template, base, scenario, seed) -> patched graph   (no I/O)
Tier 1  engine    expand jobs, drive ComfyUI /prompt + /ws, save images + manifest    (library/CLI)
Tier 2  plugin    ComfyUI custom-node shell: web server on PromptServer's aiohttp app  (REST + ws)
Tier 3  web UI    vanilla-JS editor calling Tier 2                                      (browser)
```

You get an end-to-end-usable system after **Tier 1** (CLI). The plugin + UI are additive.

## 3. Repository layout

```
__init__.py                     ComfyUI entry: NODE_CLASS_MAPPINGS, WEB_DIRECTORY,
                                guarded route registration on PromptServer.instance.app
comfyui_batch_render/
  models.py                     Layer, LoraRef, NodeMap dataclasses (+ from_dict)
  patcher.py                    Tier 0: combine_layers, splice_lora_chain, build_render_graph
  detect.py                     pure node-map auto-detection from a workflow
  pipeline.py                   Pipeline, SeedSpec, RenderJob, expand_jobs
  comfy_client.py               async ComfyClient over ComfyUI HTTP + WebSocket API
  runner.py                     run_pipeline (live) + dry_run (offline), output paths, manifest
  config.py                     load yaml/json config, pipeline, template
  cli.py                        `brp expand | dry-run | run | ping`
  paths.py                      stdlib config-dir resolver (BRP_CONFIG_DIR override)
  store.py                      settings + pipeline persistence (JSON)
  server/
    app.py                      testable web layer: Deps protocol, RunManager,
                                create_app / register_routes (no ComfyUI imports)
    bindings.py                 ComfyDeps: ComfyUI-bound Deps (lazy folder_paths/server imports)
    static/                     vanilla-JS web UI (index.html, app.js, api.js, components.js, style.css)
web/comfyui/top_menu.js         frontend extension: adds the "Batch Render" top-menu button
examples/                       placeholder-only sample workflow + pipeline
tests/                          pytest suite (hermetic; no real ComfyUI needed)
config.example.yaml             copy to config.yaml (gitignored) for local settings
```

## 4. How to run

### Development setup (no ComfyUI required)

```sh
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"     # Windows; use .venv/bin/python on POSIX
.venv/Scripts/python -m pytest -q                   # run the test suite
```

### Use as a CLI / library

```sh
# Inspect the job matrix a pipeline expands to:
brp expand   --pipeline examples/pipeline.example.yaml

# Offline: write the patched graph JSON per job (no ComfyUI contacted) — great for verifying:
brp dry-run  --pipeline examples/pipeline.example.yaml --output ./output

# Check connectivity to a running ComfyUI:
brp ping     --host 127.0.0.1 --port 8188

# Real run against a running ComfyUI:
brp run      --pipeline examples/pipeline.example.yaml --host 127.0.0.1 --port 8188
```

### Install as a ComfyUI plugin + web UI

1. Place this repo in your ComfyUI's `custom_nodes/` directory (clone or symlink).
2. Restart ComfyUI. The plugin registers its routes on ComfyUI's own web server (same host/port — read live from `PromptServer.instance.port`, never hardcoded).
3. Click the **Batch Render** button in the top menu, or open `<comfyui-origin>/batch-render`.
4. In the UI: enter the path to a workflow you exported via *Save (API Format)*, click **Detect slots**, add bases/scenarios, pick LoRAs (trigger words auto-fill from sidecar metadata when present), choose a seed mode, **Save**, then **Run** — progress streams live.

Local config (settings, saved pipelines) is stored outside the repo via `paths.config_dir()`; set `BRP_CONFIG_DIR` to relocate or make it portable.

## 5. Principles (follow these)

1. **Native and future-proof over third-party.** Build on stock ComfyUI nodes (`LoraLoader`) and the stable `/prompt` + `/ws` API rather than depending on community custom nodes that may break or go unmaintained. If a dependency seems necessary, justify it against an own-code alternative.
2. **No strong environment assumptions.** Don't hardcode ports, paths, or folder structures. Read models live from ComfyUI (`folder_paths`); read the port from `PromptServer.instance`. Pick uncommon defaults for anything we own. The user's folder taxonomy is theirs, not ours.
3. **Bring-your-own-workflow.** Never require users to adopt "our" workflow. The contract is the minimal slot mapping (prompt / seed / model+clip source / optional negative+ckpt).
4. **Composable tiers.** Keep Tier 0 pure and I/O-free; keep ComfyUI imports out of the testable core. A change should fit one tier.
5. **Testable without ComfyUI.** `server`/`folder_paths` do not exist in the test/dev environment. All ComfyUI imports are isolated to `__init__.py` and `server/bindings.py` (lazy, inside methods). Everything else must import and be tested without ComfyUI present.
6. **Public-repo hygiene.** This repo is open-source. **Never commit** local paths, machine names, real model filenames, or secrets. Use generic placeholders in examples and docs.
7. **Lightweight.** Don't slow ComfyUI boot: register routes synchronously, defer heavy work to `app.on_startup`/background tasks. Keep the UI framework-free (no build step).

## 6. How to contribute

- **Tests must pass and accompany changes.** `pytest -q` should stay green; add tests for new behavior. The suite is hermetic — it must not require a running ComfyUI, a GPU, or network.
- **Testing patterns used here:**
  - Tier 0/1 pure logic: ordinary unit tests.
  - Async client/server: start a real `aiohttp` app on `127.0.0.1:0` (ephemeral port) via `AppRunner`+`TCPSite` and drive it inside `asyncio.run(...)`. We deliberately avoid `pytest-asyncio` and any non-stdlib test deps.
  - Web layer: build `create_app(FakeDeps(...))` with a stub `Deps` (canned models, a real `Store` on a tmp dir, a fake `start_run`). Never import ComfyUI in tests.
- **Keep ComfyUI imports lazy and isolated** to `bindings.py` / root `__init__.py`. If you must touch them, ensure the rest of the package still imports cleanly without ComfyUI.
- **Dependencies:** prefer stdlib. Don't add pip deps without strong justification (see Principle 1). No JS frameworks, no CDN assets, no build step for the UI.
- **Commits:** make small, verifiable commits (this project was built one tier per commit). Verify before committing.
- **Before committing, re-check hygiene:** no local/machine-specific data in the diff.

## 7. Key contracts (quick reference)

**Layer** (base or scenario):
```json
{ "name": "...", "checkpoint": null, "prompt": "", "negative": "", "loras": [ { "file": "path.safetensors", "weight": 1.0, "clip_weight": null, "triggers": "" } ] }
```

**Pipeline** (what the UI saves / the CLI loads):
```json
{ "name": "...", "workflow_template": "path.api.json",
  "node_map": { "prompt": "6", "negative": "7", "seed": "3", "model_src": ["4",0], "clip_src": ["4",1], "ckpt": "4" },
  "seed": { "mode": "fixed", "value": 123 },        // or { "mode": "randomize", "count": 2 }
  "default_checkpoint": null,
  "bases": [ /* Layer */ ], "scenarios": [ /* Layer */ ] }
```

**Combine rules** (`patcher.combine_layers`): positive = base.prompt + base lora triggers + scenario.prompt + scenario lora triggers (empties dropped); checkpoint precedence = **scenario › base › default**; loras = base + scenario concatenated.

**ComfyUI API used** (`comfy_client.py`): `POST /prompt` (`{prompt, client_id}` → `prompt_id`); WS `/ws?clientId=...` (terminal = `executing` with `node: null` for our prompt_id; raise on `execution_error`); `GET /history/{id}`; `GET /view?filename=&subfolder=&type=`; `GET /system_stats`.

## 8. Gotchas

- The repo root **is** a ComfyUI package; `__init__.py` must export `NODE_CLASS_MAPPINGS` + `WEB_DIRECTORY` and must never raise on import outside ComfyUI (its ComfyUI binding is wrapped in try/except).
- The page UI is served by **our** routes (`/batch-render`, `/brp_static`); only auto-loaded frontend extensions go under `WEB_DIRECTORY` (`web/comfyui/`).
- ComfyUI executes prompts **serially** — large matrices are slow, not broken. Show progress; don't parallelize against a single instance.
- Never mutate an input workflow template; `patcher` deep-copies. LoRA rewiring is output-index aware (e.g. a VAE link on output 2 must not be repointed).
