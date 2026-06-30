# CLAUDE.md

Operational notes for AI agents editing this repo. Read before changing code.

## What this is
ComfyUI plugin (+ CLI/library) that batch-renders `bases × scenarios × seeds`. A **base** and a **scenario** are the same shape — a **Layer** `{checkpoint?, prompt, negative?, loras[]}`. One render = one base + one scenario. LoRAs load by splicing stock `LoraLoader` nodes into the user's own API-format workflow (no required custom nodes). Users bring their own workflow; we only fill mapped slots.

## Tiers (a change usually fits one)
- **Tier 0** `patcher.py`, `detect.py`, `models.py` — pure, no I/O. `build_render_graph(template, node_map, base, scenario, seed, default_ckpt)`, `combine_layers`, `splice_lora_chain`, `detect_node_map`.
- **Tier 1** `pipeline.py`, `runner.py`, `comfy_client.py`, `config.py`, `cli.py` — job expansion + drive ComfyUI HTTP/WS + save output/manifest. `run_pipeline` (live), `dry_run` (offline).
- **Tier 2** `server/app.py` (testable web layer: `Deps` protocol, `RunManager`, `create_app`/`register_routes`), `server/bindings.py` (`ComfyDeps`, ComfyUI-bound), `store.py`, `paths.py`. Root `__init__.py` = ComfyUI entry.
- **Tier 3** `server/static/*` (vanilla JS, no build), `web/comfyui/top_menu.js` (top-menu button).

## Hard rules
1. **No ComfyUI imports outside `__init__.py` and `server/bindings.py`** (and there, lazy/inside methods). `server` and `folder_paths` don't exist in dev/test. Everything else must import without ComfyUI.
2. **Tier 0 stays pure** (no I/O/network). Never mutate an input template — deep-copy. LoRA rewiring is output-index aware (don't repoint e.g. a VAE link on output 2).
3. **No new deps** without strong justification. Prefer stdlib + the stable `LoraLoader`/`/prompt`/`/ws` API over community custom nodes. No JS framework/CDN/build step.
4. **No hardcoded env.** Read models from `folder_paths`, port from `PromptServer.instance`. Uncommon defaults for anything we own. No assumed folder structure.
5. **Public repo — never commit local paths, machine names, real model filenames, or secrets.** Examples use placeholders. Re-check the diff before committing.
6. **Don't slow ComfyUI boot:** register routes sync, defer heavy work to `app.on_startup`/background tasks. Root `__init__.py` must never raise outside ComfyUI (binding is try/except wrapped).

## Tests (must stay green + hermetic — no ComfyUI/GPU/network)
```sh
.venv/Scripts/python -m pytest -q     # POSIX: .venv/bin/python
```
- Async client/server tests: real `aiohttp` app on `127.0.0.1:0` via `AppRunner`+`TCPSite`, driven inside `asyncio.run`. No `pytest-asyncio`, no non-stdlib test deps.
- Web tests: `create_app(FakeDeps())` — canned models, real `Store` on tmp dir, fake `start_run`.

## Commands
```sh
brp expand  --pipeline examples/pipeline.example.yaml      # show job matrix
brp dry-run --pipeline ... --output ./output               # patch graphs offline, no server
brp ping    --host 127.0.0.1 --port 8188
brp run     --pipeline ... --host 127.0.0.1 --port 8188
```
Plugin install: repo into ComfyUI `custom_nodes/`, restart, click **Batch Render** (serves `/batch-render`, API `/api/brp/*`, ws `/ws/brp-progress`). Local config via `paths.config_dir()` (`BRP_CONFIG_DIR` to override). `init.md`/`SOLUTION.md`/`config.yaml` are gitignored.

## Contracts
**Pipeline** (UI saves / CLI loads):
```json
{ "name":"...", "workflow_template":"path.api.json",
  "node_map":{"prompt":"6","negative":"7","seed":"3","model_src":["4",0],"clip_src":["4",1],"ckpt":"4"},
  "seed":{"mode":"fixed","value":123},            // or {"mode":"randomize","count":2}
  "default_checkpoint":null, "bases":[Layer], "scenarios":[Layer] }
```
**Layer**: `{name, checkpoint?, prompt, negative?, loras:[{file, weight, clip_weight?, triggers}]}`.
**combine_layers**: positive = base.prompt + base lora triggers + scenario.prompt + scenario lora triggers (empties dropped); checkpoint = scenario › base › default; loras concatenated.
**ComfyUI API**: `POST /prompt {prompt,client_id}→prompt_id`; WS `/ws?clientId=` (done = `executing` with `node:null` for our id; raise on `execution_error`); `GET /history/{id}`; `GET /view?filename=&subfolder=&type=`; `GET /system_stats`.
