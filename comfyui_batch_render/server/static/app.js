// ComfyUI Batch Render -- single-page editor. Vanilla ES modules, no build step.
"use strict";

import { api } from "./api.js";
import { el, clear, fillSelect, statusSetter } from "./components.js";

// --------------------------------------------------------------------------- //
// App state
// --------------------------------------------------------------------------- //

const state = {
  models: { checkpoints: [], loras: [] },
  pipelines: [],
  // The saved pipeline: name + base + scenarios. This is all that Save persists.
  editor: blankPipeline(),
  // Run-time config: workflow + slot map + seed. NOT part of the pipeline; it's
  // applied to whichever pipeline is open and persisted to settings (not files).
  runtime: blankRuntime(),
  loadedName: null, // server name of the pipeline currently loaded (for PUT)
  // Workflow captured from the ComfyUI canvas (API-format dict) for this
  // session, or null when using the manual template-path field instead.
  captured: null,
  run: { active: false, ws: null },
};

// localStorage key the ComfyUI top-menu extension writes the current graph to.
const CAPTURE_KEY = "brp_captured_workflow";

function blankPipeline() {
  return {
    name: "untitled",
    // Exactly one base, always present -- it's a single set of params, not a list.
    bases: [blankLayer("base")],
    scenarios: [],
  };
}

// Run-time config lives outside the pipeline: the workflow to render against,
// the slot mapping detected from it, and the seed policy.
function blankRuntime() {
  return {
    workflow_template: "",
    node_map: {
      prompt: "",
      negative: "",
      seed: "",
      model_src: ["", 0],
      clip_src: ["", 1],
      ckpt: "",
    },
    default_checkpoint: "",
    seed: { mode: "fixed", value: 42, count: 4 },
  };
}

function blankLayer(name) {
  return { name: name || "layer", checkpoint: null, prompt: "", negative: "", loras: [] };
}

// --------------------------------------------------------------------------- //
// Model select helpers
// --------------------------------------------------------------------------- //

// NOTE: model.name is rendered as-is. It already encodes any subfolder, so we
// must NOT prepend model.subfolder again (that was the old duplication bug).
function checkpointOptions(selected) {
  const opts = [{ value: "", label: "(inherit)" }];
  for (const m of state.models.checkpoints) opts.push({ value: m.name, label: m.name });
  return { opts, selected: selected || "" };
}

function loraOptions(selected) {
  const opts = [{ value: "", label: "(select LoRA)" }];
  for (const m of state.models.loras) opts.push({ value: m.name, label: m.name });
  return { opts, selected: selected || "" };
}

function loraTriggersFor(file) {
  const m = state.models.loras.find((x) => x.name === file);
  return m ? m.triggers || "" : "";
}

// --------------------------------------------------------------------------- //
// Combination counter
// --------------------------------------------------------------------------- //

function seedCount() {
  const s = state.runtime.seed;
  return s.mode === "randomize" ? Math.max(1, Number(s.count) || 1) : 1;
}

function updateCombos() {
  // The base is always a single set of params, so it never multiplies the count.
  const b = Math.max(1, state.editor.bases.length);
  const s = state.editor.scenarios.length;
  const seeds = seedCount();
  const total = b * s * seeds;
  const node = document.getElementById("combo-counter");
  if (node) {
    node.textContent = `combinations: 1 base x ${s} scenarios x ${seeds} seeds = ${total}`;
  }
}

// --------------------------------------------------------------------------- //
// Layer cards
// --------------------------------------------------------------------------- //

// Tracks which scenario cards are collapsed. Keyed by the layer object itself so
// the state survives re-renders and reordering, isn't serialized into the saved
// pipeline, and is dropped automatically when a layer is removed.
const collapsedLayers = new WeakSet();

function renderLayerList(kind) {
  const container = document.getElementById(`${kind}-list`);
  clear(container);
  const layers = state.editor[kind];
  if (layers.length === 0) {
    container.appendChild(el("p", { class: "empty", text: `No ${kind} yet.` }));
  }
  layers.forEach((layer, i) => container.appendChild(layerCard(kind, layer, i)));
  if (kind === "scenarios") updateToggleAllLabel();
  updateCombos();
}

function layerCard(kind, layer, index) {
  const ckSel = el("select", {});
  const ck = checkpointOptions(layer.checkpoint);
  fillSelect(ckSel, ck.opts, ck.selected);
  ckSel.addEventListener("change", () => {
    layer.checkpoint = ckSel.value || null;
  });

  const posInput = el("textarea", {
    placeholder: "positive prompt",
    rows: 2,
    on: { input: () => (layer.prompt = posInput.value) },
  });
  posInput.value = layer.prompt;
  const negInput = el("textarea", {
    placeholder: "negative prompt",
    rows: 2,
    on: { input: () => (layer.negative = negInput.value) },
  });
  negInput.value = layer.negative;

  const lorasBox = el("div", { class: "loras-box" });
  renderLoras(lorasBox, layer);

  const body = el("div", { class: "card-body" }, [
    field("Checkpoint", ckSel),
    field("Positive", posInput),
    field("Negative", negInput),
    el("div", { class: "loras-section" }, [
      el("div", { class: "row-between" }, [
        el("span", { class: "sub-label", text: "LoRAs" }),
        el("button", {
          class: "small",
          text: "+ LoRA",
          on: {
            click: () => {
              layer.loras.push({ file: "", weight: 1.0, triggers: "" });
              renderLoras(lorasBox, layer);
            },
          },
        }),
      ]),
      lorasBox,
    ]),
  ]);

  const head = [];
  // Scenarios can be collapsed to keep a long list scrollable. The base is a
  // single card, so it's always expanded and gets no toggle.
  const collapsible = kind !== "bases";
  if (collapsible) {
    if (collapsedLayers.has(layer)) body.hidden = true;
    const caret = el("button", {
      class: "small caret",
      text: collapsedLayers.has(layer) ? "▸" : "▾",
      title: "collapse / expand",
      on: {
        click: () => {
          const collapsed = !collapsedLayers.has(layer);
          if (collapsed) collapsedLayers.add(layer);
          else collapsedLayers.delete(layer);
          body.hidden = collapsed;
          caret.textContent = collapsed ? "▸" : "▾";
          if (kind === "scenarios") updateToggleAllLabel();
        },
      },
    });
    head.push(caret);
  }

  head.push(
    el("input", {
      type: "text",
      class: "card-title",
      value: layer.name,
      on: {
        input: (e) => {
          layer.name = e.target.value;
        },
      },
    })
  );
  // The base is a single, always-present set of params -- it can't be removed.
  if (kind !== "bases") {
    head.push(
      el("button", {
        class: "btn-danger small",
        text: "Remove",
        on: {
          click: (ev) =>
            confirmDestructive(ev.currentTarget, () => {
              state.editor[kind].splice(index, 1);
              renderLayerList(kind);
            }),
        },
      })
    );
  }

  return el("div", { class: "card" }, [
    el("div", { class: "card-head" }, head),
    body,
  ]);
}

function renderLoras(box, layer) {
  clear(box);
  if (layer.loras.length === 0) {
    box.appendChild(el("p", { class: "empty small", text: "No LoRAs." }));
    return;
  }
  layer.loras.forEach((lora, i) => {
    const sel = el("select", { class: "lora-select" });
    const lo = loraOptions(lora.file);
    fillSelect(sel, lo.opts, lo.selected);

    const triggers = el("input", {
      type: "text",
      class: "lora-triggers",
      placeholder: "triggers",
      value: lora.triggers,
      on: { input: () => (lora.triggers = triggers.value) },
    });

    sel.addEventListener("change", () => {
      lora.file = sel.value;
      // Auto-fill triggers from the chosen model (editable afterwards).
      const t = loraTriggersFor(sel.value);
      lora.triggers = t;
      triggers.value = t;
    });

    const weight = el("input", {
      type: "number",
      class: "lora-weight",
      step: "0.05",
      value: String(lora.weight),
      on: {
        input: () => {
          lora.weight = parseFloat(weight.value);
          if (Number.isNaN(lora.weight)) lora.weight = 1.0;
        },
      },
    });

    box.appendChild(
      el("div", { class: "lora-row" }, [
        sel,
        weight,
        triggers,
        el("button", {
          class: "btn-danger small",
          text: "x",
          title: "remove LoRA",
          on: {
            click: (ev) =>
              confirmDestructive(ev.currentTarget, () => {
                layer.loras.splice(i, 1);
                renderLoras(box, layer);
              }),
          },
        }),
      ])
    );
  });
}

function field(label, control) {
  return el("label", { class: "field" }, [
    el("span", { class: "sub-label", text: label }),
    control,
  ]);
}

// True when there's at least one scenario and every one is collapsed.
function allScenariosCollapsed() {
  const s = state.editor.scenarios;
  return s.length > 0 && s.every((l) => collapsedLayers.has(l));
}

// Collapse or expand every scenario card at once, then re-render the list.
function setAllScenariosCollapsed(collapsed) {
  for (const layer of state.editor.scenarios) {
    if (collapsed) collapsedLayers.add(layer);
    else collapsedLayers.delete(layer);
  }
  renderLayerList("scenarios");
}

// Keep the "Collapse/Expand all" button's label and enabled state in sync with
// the current scenarios.
function updateToggleAllLabel() {
  const btn = document.getElementById("toggle-all-scenarios");
  if (!btn) return;
  const hasScenarios = state.editor.scenarios.length > 0;
  btn.disabled = !hasScenarios;
  btn.textContent = allScenariosCollapsed() ? "Expand all" : "Collapse all";
}

// --------------------------------------------------------------------------- //
// Editor <-> DOM binding for the top-level fields
// --------------------------------------------------------------------------- //

// Render the saved-pipeline fields (name + base + scenarios). Called on new /
// load; it must NOT touch run-time config, which lives independently.
function renderPipelineForm() {
  setVal("pl-name", state.editor.name);
  renderLayerList("bases");
  renderLayerList("scenarios");
}

// Render the run-time config (workflow + slot map + seed) into the form.
function renderRuntimeForm() {
  const r = state.runtime;
  setVal("pl-template", r.workflow_template);
  setVal("nm-prompt", r.node_map.prompt || "");
  setVal("nm-negative", r.node_map.negative || "");
  setVal("nm-seed", r.node_map.seed || "");
  setVal("nm-ckpt", r.node_map.ckpt || "");
  setVal("nm-model-src", (r.node_map.model_src || ["", 0]).join(","));
  setVal("nm-clip-src", (r.node_map.clip_src || ["", 1]).join(","));
  setVal("pl-default-ckpt", r.default_checkpoint || "");

  const mode = r.seed.mode === "randomize" ? "randomize" : "fixed";
  const radios = document.querySelectorAll('input[name="seed-mode"]');
  radios.forEach((x) => (x.checked = x.value === mode));
  setVal("seed-value", r.seed.value == null ? "" : r.seed.value);
  setVal("seed-count", r.seed.count == null ? "" : r.seed.count);
  syncSeedInputs();

  document.getElementById("detect-notes").textContent = "";
  setCaptureUI();
}

function setVal(id, v) {
  const node = document.getElementById(id);
  if (node) node.value = v;
}

function readPipelineForm() {
  state.editor.name = getVal("pl-name").trim() || "untitled";
  // Layer fields (name/prompt/loras) are bound live as they're edited.
}

function readRuntimeForm() {
  const r = state.runtime;
  r.workflow_template = getVal("pl-template").trim();
  r.node_map.prompt = getVal("nm-prompt").trim();
  r.node_map.negative = getVal("nm-negative").trim();
  r.node_map.seed = getVal("nm-seed").trim();
  r.node_map.ckpt = getVal("nm-ckpt").trim();
  r.node_map.model_src = parsePair(getVal("nm-model-src"), 0);
  r.node_map.clip_src = parsePair(getVal("nm-clip-src"), 1);
  r.default_checkpoint = getVal("pl-default-ckpt").trim();

  const mode = document.querySelector('input[name="seed-mode"]:checked');
  r.seed.mode = mode ? mode.value : "fixed";
  const v = getVal("seed-value").trim();
  r.seed.value = v === "" ? null : parseInt(v, 10);
  const c = getVal("seed-count").trim();
  r.seed.count = c === "" ? 1 : parseInt(c, 10);
}

function getVal(id) {
  const node = document.getElementById(id);
  return node ? String(node.value) : "";
}

// "4,0" -> ["4", 0]. Falls back to [id, fallbackIdx]. Empty id -> ["", idx].
function parsePair(raw, fallbackIdx) {
  const parts = String(raw).split(",").map((s) => s.trim());
  const id = parts[0] || "";
  let idx = parseInt(parts[1], 10);
  if (Number.isNaN(idx)) idx = fallbackIdx;
  return [id, idx];
}

// --------------------------------------------------------------------------- //
// Assemble + validate the pipeline dict the API expects
// --------------------------------------------------------------------------- //

// The saved-pipeline dict: name + base + scenarios only. Workflow / slots / seed
// are run-time config and deliberately excluded.
function assemblePipeline() {
  readPipelineForm();
  const e = state.editor;
  return {
    name: e.name,
    bases: e.bases.map(layerToDict),
    scenarios: e.scenarios.map(layerToDict),
  };
}

// The /run payload: merge the open pipeline with the current run-time config so
// the engine (Pipeline.from_dict) still receives node_map + seed. The captured
// canvas graph, if any, rides along as `template`.
function assembleRunPayload() {
  readPipelineForm();
  readRuntimeForm();
  const e = state.editor;
  const r = state.runtime;
  const pipeline = {
    name: e.name,
    bases: e.bases.map(layerToDict),
    scenarios: e.scenarios.map(layerToDict),
    workflow_template: r.workflow_template,
    node_map: {
      prompt: r.node_map.prompt,
      negative: r.node_map.negative || null,
      seed: r.node_map.seed,
      model_src: r.node_map.model_src,
      clip_src: r.node_map.clip_src,
      ckpt: r.node_map.ckpt || null,
    },
    seed:
      r.seed.mode === "randomize"
        ? { mode: "randomize", count: r.seed.count || 1 }
        : { mode: "fixed", value: r.seed.value == null ? 0 : r.seed.value },
    default_checkpoint: r.default_checkpoint || null,
  };
  const payload = { pipeline };
  if (state.captured) payload.template = state.captured.template;
  return payload;
}

function layerToDict(layer) {
  return {
    name: layer.name,
    checkpoint: layer.checkpoint || null,
    prompt: layer.prompt || "",
    negative: layer.negative || "",
    loras: layer.loras
      .filter((l) => l.file)
      .map((l) => ({
        file: l.file,
        weight: Number.isFinite(l.weight) ? l.weight : 1.0,
        triggers: l.triggers || "",
      })),
  };
}

// --------------------------------------------------------------------------- //
// Pipeline list (sidebar)
// --------------------------------------------------------------------------- //

async function refreshPipelines() {
  try {
    const data = await api.listPipelines();
    state.pipelines = data.pipelines || [];
  } catch (err) {
    state.pipelines = [];
  }
  renderPipelineList();
}

function renderPipelineList() {
  const list = document.getElementById("pipeline-list");
  clear(list);
  if (state.pipelines.length === 0) {
    list.appendChild(el("p", { class: "empty", text: "No saved pipelines." }));
    return;
  }
  for (const p of state.pipelines) {
    const row = el("div", { class: "pl-row" }, [
      el("button", {
        class: "pl-open",
        text: `${p.name}`,
        title: `${p.bases} bases x ${p.scenarios} scenarios`,
        on: { click: () => loadPipeline(p.name) },
      }),
      el("button", {
        class: "small",
        text: "Clone",
        title: "Clone into a new pipeline",
        on: { click: () => clonePipeline(p.name) },
      }),
      el("button", {
        class: "btn-danger small",
        text: "Del",
        title: "Delete pipeline",
        on: { click: (ev) => confirmDestructive(ev.currentTarget, () => deletePipeline(p.name)) },
      }),
    ]);
    list.appendChild(row);
  }
}

// Two-step confirm for destructive buttons. The first click arms the button
// (it turns red and reads "Confirm?"); a second click within CONFIRM_WINDOW_MS
// runs `onConfirm`. Anything else -- the timeout, or arming another button --
// resets it, so a stray single click never destroys anything.
const CONFIRM_WINDOW_MS = 4000;
let armedBtn = null;
let armedTimer = null;

function disarmConfirm() {
  if (armedTimer) {
    clearTimeout(armedTimer);
    armedTimer = null;
  }
  const btn = armedBtn;
  armedBtn = null;
  if (btn) {
    btn.classList.remove("armed");
    btn.textContent = btn.dataset.restoreText;
    btn.title = btn.dataset.restoreTitle || "";
  }
}

function confirmDestructive(btn, onConfirm) {
  if (btn === armedBtn) {
    disarmConfirm();
    onConfirm();
    return;
  }
  disarmConfirm(); // reset any other button left armed
  btn.dataset.restoreText = btn.textContent;
  btn.dataset.restoreTitle = btn.title;
  btn.classList.add("armed");
  btn.textContent = "Confirm?";
  btn.title = "Click again to confirm";
  armedBtn = btn;
  armedTimer = setTimeout(disarmConfirm, CONFIRM_WINDOW_MS);
}

async function loadPipeline(name) {
  try {
    const data = await api.getPipeline(name);
    state.editor = normalizeLoaded(data.pipeline || {});
    state.loadedName = data.pipeline && data.pipeline.name ? data.pipeline.name : name;
    // Run-time config (workflow / slots / seed) and any canvas capture are
    // independent of the pipeline -- leave them untouched on load.
    renderPipelineForm();
    setEditorStatus(`loaded "${name}"`, "ok");
  } catch (err) {
    setEditorStatus(`load failed: ${err.message}`, "err");
  }
}

// Load an existing pipeline but treat it as brand new: copy its base +
// scenarios into the editor under a fresh, non-colliding name and clear
// loadedName so the next Save creates a new file instead of overwriting the
// source. The user can rename before saving. Run-time config is untouched.
async function clonePipeline(name) {
  try {
    const data = await api.getPipeline(name);
    const loaded = normalizeLoaded(data.pipeline || {});
    loaded.name = uniquePipelineName(loaded.name);
    state.editor = loaded;
    state.loadedName = null;
    renderPipelineForm();
    setEditorStatus(`cloned "${name}" -> "${loaded.name}" (unsaved)`, "ok");
  } catch (err) {
    setEditorStatus(`clone failed: ${err.message}`, "err");
  }
}

// Derive a name not already used by a saved pipeline: "X" -> "X copy",
// "X copy 2", "X copy 3", ... Comparison is case-insensitive on the name.
function uniquePipelineName(base) {
  const taken = new Set(
    state.pipelines.map((p) => String(p.name || "").toLowerCase())
  );
  let candidate = `${base} copy`;
  let n = 2;
  while (taken.has(candidate.toLowerCase())) {
    candidate = `${base} copy ${n}`;
    n += 1;
  }
  return candidate;
}

// Merge a stored pipeline dict onto a blank template. Only name + base +
// scenarios are part of a pipeline now; any legacy workflow/seed/node_map fields
// in older files are ignored (and dropped on the next Save).
function normalizeLoaded(p) {
  const base = blankPipeline();
  return {
    name: p.name || base.name,
    // Always exactly one base; keep the first if a legacy pipeline had several.
    bases: [(p.bases || []).map(normalizeLayer)[0] || blankLayer("base")],
    scenarios: (p.scenarios || []).map(normalizeLayer),
  };
}

function normalizeLayer(l) {
  return {
    name: l.name || "layer",
    checkpoint: l.checkpoint || null,
    prompt: l.prompt || "",
    negative: l.negative || "",
    loras: (l.loras || []).map((x) => ({
      file: x.file || "",
      weight: x.weight == null ? 1.0 : x.weight,
      triggers: x.triggers || "",
    })),
  };
}

async function deletePipeline(name) {
  try {
    await api.deletePipeline(name);
    if (state.loadedName === name) {
      state.editor = blankPipeline();
      state.loadedName = null;
      renderPipelineForm();
    }
    await refreshPipelines();
  } catch (err) {
    setEditorStatus(`delete failed: ${err.message}`, "err");
  }
}

function newPipeline() {
  state.editor = blankPipeline();
  state.loadedName = null;
  renderPipelineForm();
  setEditorStatus("new pipeline", "ok");
}

async function savePipeline() {
  const body = assemblePipeline();
  if (!body.name) {
    setEditorStatus("name is required", "err");
    return;
  }
  try {
    // Use PUT to upsert by the current name.
    await api.savePipeline(body.name, body);
    state.loadedName = body.name;
    await refreshPipelines();
    setEditorStatus(`saved "${body.name}"`, "ok");
  } catch (err) {
    setEditorStatus(`save failed: ${err.message}`, "err");
  }
}

// --------------------------------------------------------------------------- //
// Captured workflow (handoff from the ComfyUI canvas)
// --------------------------------------------------------------------------- //

// Detect/run need a template reference: the captured API graph if present,
// otherwise the manual path from the form. Returns null when neither is set.
function templateRef() {
  if (state.captured) return { template: state.captured.template };
  const path = state.runtime.workflow_template;
  return path ? { path } : null;
}

function setCaptureUI() {
  const banner = document.getElementById("capture-banner");
  const text = document.getElementById("capture-text");
  const input = document.getElementById("pl-template");
  if (!banner) return;
  if (state.captured) {
    const n = Object.keys(state.captured.template || {}).length;
    if (text) text.textContent = `✓ Using the workflow open in ComfyUI (${n} nodes).`;
    banner.hidden = false;
    if (input) {
      input.disabled = true;
      input.placeholder = "(using captured workflow)";
    }
  } else {
    banner.hidden = true;
    if (input) {
      input.disabled = false;
      input.placeholder = "path to exported API workflow (.json)";
    }
  }
}

function applyCapture(template) {
  state.captured = { template };
  // A captured workflow has no on-disk path; clear the stale field value.
  state.runtime.workflow_template = "";
  setVal("pl-template", "");
  setCaptureUI();
}

function clearCapture() {
  if (!state.captured) return;
  state.captured = null;
  setCaptureUI();
  // Also drop the server-side slot so a later reload doesn't resurface it.
  api.clearCapture().catch(() => {});
  try {
    window.localStorage.removeItem(CAPTURE_KEY);
  } catch (_e) {}
  const input = document.getElementById("pl-template");
  if (input) input.focus();
}

// Pick up the workflow handed off from the ComfyUI canvas. Two channels:
//   1. Server relay (GET /api/brp/capture) -- the only one that works when this
//      page is in a different browser process than ComfyUI (desktop app case).
//      The slot persists until replaced/cleared, so a page refresh keeps it.
//   2. localStorage -- same-origin fast path / offline fallback.
// Best effort: bad/missing data just leaves the manual field in place.
async function consumeCapture() {
  // Primary: server relay.
  try {
    const res = await api.getCapture();
    const template = res && res.captured && res.captured.template;
    if (template && typeof template === "object" && Object.keys(template).length) {
      // Drain the stale same-origin copy so the channels can't disagree later.
      try {
        window.localStorage.removeItem(CAPTURE_KEY);
      } catch (_e) {}
      applyCapture(template);
      return true;
    }
  } catch (_e) {
    /* server unreachable / no capture -- fall through to localStorage */
  }
  // Secondary: same-origin localStorage. Read-once to avoid resurfacing it.
  let raw = null;
  try {
    raw = window.localStorage.getItem(CAPTURE_KEY);
    if (raw) window.localStorage.removeItem(CAPTURE_KEY);
  } catch (_e) {
    return false;
  }
  if (!raw) return false;
  try {
    const data = JSON.parse(raw);
    const template = data && data.template;
    if (template && typeof template === "object" && Object.keys(template).length) {
      applyCapture(template);
      return true;
    }
  } catch (_e) {
    /* ignore malformed handoff */
  }
  return false;
}

// Pull the latest server-side capture and re-map slots. Invoked when the server
// signals a new capture over the websocket (a "Re-sync" click here, or the user
// re-clicking the Batch Render icon in ComfyUI).
async function refreshFromServerCapture() {
  try {
    const res = await api.getCapture();
    const template = res && res.captured && res.captured.template;
    if (template && typeof template === "object" && Object.keys(template).length) {
      applyCapture(template);
      setEditorStatus("re-synced the workflow from ComfyUI", "ok");
      await detectSlots();
      return true;
    }
  } catch (_e) {
    /* ignore -- nothing usable on the server */
  }
  return false;
}

// "Re-sync" button: ask ComfyUI (via the server) to push a fresh snapshot of
// the open canvas. The actual refresh arrives over the websocket as a "capture"
// signal handled in handleProgress -> refreshFromServerCapture.
async function requestRecapture() {
  setEditorStatus("re-syncing from ComfyUI...", "");
  try {
    const res = await api.requestRecapture();
    if (!res || !res.ok) {
      setEditorStatus(
        "couldn't reach ComfyUI to re-sync (is it running?)",
        "err"
      );
    }
  } catch (err) {
    setEditorStatus(`re-sync failed: ${err.message}`, "err");
  }
}

// --------------------------------------------------------------------------- //
// Detect
// --------------------------------------------------------------------------- //

async function detectSlots() {
  readRuntimeForm();
  const ref = templateRef();
  if (!ref) {
    setEditorStatus("capture a workflow from ComfyUI, or enter a template path", "err");
    return;
  }
  try {
    const res = await api.detect(ref);
    const nm = res.node_map || {};
    const r = state.runtime;
    if (nm.prompt) r.node_map.prompt = nm.prompt;
    if (nm.negative) r.node_map.negative = nm.negative;
    if (nm.seed) r.node_map.seed = nm.seed;
    if (nm.ckpt) r.node_map.ckpt = nm.ckpt;
    if (nm.model_src) r.node_map.model_src = nm.model_src;
    if (nm.clip_src) r.node_map.clip_src = nm.clip_src;
    if (res.default_checkpoint) r.default_checkpoint = res.default_checkpoint;

    // Re-sync the node_map inputs only (keep layers as-is).
    setVal("nm-prompt", r.node_map.prompt || "");
    setVal("nm-negative", r.node_map.negative || "");
    setVal("nm-seed", r.node_map.seed || "");
    setVal("nm-ckpt", r.node_map.ckpt || "");
    setVal("nm-model-src", (r.node_map.model_src || ["", 0]).join(","));
    setVal("nm-clip-src", (r.node_map.clip_src || ["", 1]).join(","));
    setVal("pl-default-ckpt", r.default_checkpoint || "");

    const notes = (res.notes || []).join(" ");
    document.getElementById("detect-notes").textContent = notes || "Detected all slots.";
    // Open advanced section so the user can review.
    const adv = document.getElementById("advanced");
    if (adv) adv.open = true;
    setEditorStatus("slots detected", "ok");
    persistRuntime(); // remember the detected slots across reloads
  } catch (err) {
    setEditorStatus(`detect failed: ${err.message}`, "err");
    document.getElementById("detect-notes").textContent = "";
  }
}

// --------------------------------------------------------------------------- //
// Run + websocket progress
// --------------------------------------------------------------------------- //

function wsUrl() {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}/ws/brp-progress`;
}

function setRunActive(active) {
  state.run.active = active;
  const btn = document.getElementById("run-btn");
  if (btn) btn.disabled = active;
}

function logLine(text, level) {
  const log = document.getElementById("run-log");
  log.appendChild(el("div", { class: "log-line" + (level ? " " + level : ""), text }));
  log.scrollTop = log.scrollHeight;
}

function setProgress(done, total) {
  const bar = document.getElementById("progress-fill");
  const label = document.getElementById("progress-label");
  const pct = total ? Math.round((done / total) * 100) : 0;
  bar.style.width = `${pct}%`;
  label.textContent = total ? `${done} / ${total} (${pct}%)` : `${done} / ?`;
}

function openProgressSocket() {
  // Reuse a live socket so it can stay open between runs -- that's what lets an
  // idle UI receive "capture" re-sync signals.
  const existing = state.run.ws;
  if (existing && existing.readyState === WebSocket.OPEN) return existing;
  if (existing) {
    try {
      existing.close();
    } catch (_e) {}
  }
  const ws = new WebSocket(wsUrl());
  state.run.ws = ws;
  ws.addEventListener("message", (evt) => {
    let msg;
    try {
      msg = JSON.parse(evt.data);
    } catch (_e) {
      return;
    }
    handleProgress(msg);
  });
  ws.addEventListener("error", () => logLine("websocket error", "err"));
  return ws;
}

function handleProgress(msg) {
  if (msg.type === "capture") {
    // The server got a fresh canvas snapshot; pull it in.
    refreshFromServerCapture();
  } else if (msg.type === "progress") {
    if (msg.total != null) setProgress(msg.done || 0, msg.total);
    const job = msg.job || {};
    const where =
      job.base != null
        ? `base "${job.base}" / scenario "${job.scenario}" seed ${job.seed}`
        : `job ${job.index}`;
    logLine(`#${(job.index ?? 0) + 1} ${where}`);
  } else if (msg.type === "done") {
    logLine("completed", "ok");
    const m = msg.manifest || {};
    if (m.job_count != null) logLine(`manifest: ${m.job_count} jobs`, "ok");
    setRunActive(false);
  } else if (msg.type === "error") {
    logLine(`error: ${msg.error}`, "err");
    setRunActive(false);
  }
}

async function runPipeline() {
  if (state.run.active) return;
  const runPayload = assembleRunPayload();
  persistRuntime(); // remember the run-time config used for this run
  clear(document.getElementById("run-log"));
  setProgress(0, 0);
  setRunActive(true);
  openProgressSocket();
  logLine(`starting run for "${runPayload.pipeline.name}"...`);
  try {
    const res = await api.run(runPayload);
    logLine(`run id: ${res.run_id}`);
  } catch (err) {
    logLine(`run failed to start: ${err.message}`, "err");
    setRunActive(false);
  }
}

// --------------------------------------------------------------------------- //
// Settings
// --------------------------------------------------------------------------- //

async function loadSettings() {
  try {
    const data = await api.getSettings();
    const s = data.settings || {};
    setVal("set-output", s.output_dir || "");
    setVal("set-template", s.default_template || "");
    const comfy = s.comfyui || {};
    setVal("set-host", comfy.host || "");
    setVal("set-port", comfy.port == null ? "" : comfy.port);
  } catch (err) {
    setEditorStatus(`settings load failed: ${err.message}`, "err");
  }
}

async function saveSettings() {
  const portRaw = getVal("set-port").trim();
  const patch = {
    output_dir: getVal("set-output").trim(),
    default_template: getVal("set-template").trim() || null,
    comfyui: {
      host: getVal("set-host").trim() || "127.0.0.1",
      port: portRaw === "" ? null : parseInt(portRaw, 10),
    },
  };
  try {
    await api.saveSettings(patch);
    document.getElementById("settings-status").textContent = "settings saved";
  } catch (err) {
    document.getElementById("settings-status").textContent = `save failed: ${err.message}`;
  }
}

// --------------------------------------------------------------------------- //
// Run-time config persistence (settings-backed, not part of any pipeline)
// --------------------------------------------------------------------------- //

// Persist the current run-time config under settings.run so it survives reloads
// and carries across pipelines. Best-effort: a failed write is non-fatal.
async function persistRuntime() {
  readRuntimeForm();
  const r = state.runtime;
  const patch = {
    run: {
      workflow_path: r.workflow_template || null,
      node_map: r.node_map,
      default_checkpoint: r.default_checkpoint || null,
      seed: r.seed,
    },
  };
  try {
    await api.saveSettings(patch);
  } catch (_e) {
    /* best effort */
  }
}

// Hydrate state.runtime from settings.run on startup.
async function loadRuntimeFromSettings() {
  try {
    const data = await api.getSettings();
    const run = (data.settings && data.settings.run) || {};
    const r = state.runtime;
    if (run.workflow_path) r.workflow_template = run.workflow_path;
    const nm = run.node_map;
    if (nm && typeof nm === "object") {
      r.node_map = {
        prompt: nm.prompt || "",
        negative: nm.negative || "",
        seed: nm.seed || "",
        model_src: Array.isArray(nm.model_src) ? nm.model_src : ["", 0],
        clip_src: Array.isArray(nm.clip_src) ? nm.clip_src : ["", 1],
        ckpt: nm.ckpt || "",
      };
    }
    if (run.default_checkpoint) r.default_checkpoint = run.default_checkpoint;
    const s = run.seed;
    if (s && typeof s === "object") {
      r.seed = {
        mode: s.mode === "randomize" ? "randomize" : "fixed",
        value: s.value == null ? 42 : s.value,
        count: s.count == null ? 4 : s.count,
      };
    }
  } catch (_e) {
    /* no saved run config -- keep defaults */
  }
}

// --------------------------------------------------------------------------- //
// Misc wiring
// --------------------------------------------------------------------------- //

let setEditorStatus = () => {};

function syncSeedInputs() {
  const mode = document.querySelector('input[name="seed-mode"]:checked');
  const isRandom = mode && mode.value === "randomize";
  const fixedWrap = document.getElementById("seed-fixed-wrap");
  const randWrap = document.getElementById("seed-rand-wrap");
  if (fixedWrap) fixedWrap.style.display = isRandom ? "none" : "";
  if (randWrap) randWrap.style.display = isRandom ? "" : "none";
  updateCombos();
}

async function loadModels() {
  try {
    const [ck, lo] = await Promise.all([
      api.models("checkpoints"),
      api.models("loras"),
    ]);
    state.models.checkpoints = ck.models || [];
    state.models.loras = lo.models || [];
  } catch (err) {
    state.models.checkpoints = [];
    state.models.loras = [];
  }
}

async function loadHealth() {
  const setHealth = statusSetter(document.getElementById("health"));
  try {
    const h = await api.health();
    const t = h.comfyui ? `${h.comfyui.host}:${h.comfyui.port ?? "?"}` : "unknown";
    setHealth(`server v${h.version} - ComfyUI ${t}`, "ok");
  } catch (err) {
    setHealth(`server unreachable: ${err.message}`, "err");
  }
}

function wireEvents() {
  document.getElementById("new-btn").addEventListener("click", newPipeline);
  document.getElementById("save-btn").addEventListener("click", savePipeline);
  document.getElementById("detect-btn").addEventListener("click", detectSlots);
  document.getElementById("capture-clear")?.addEventListener("click", clearCapture);
  document.getElementById("capture-resync").addEventListener("click", requestRecapture);
  document.getElementById("run-btn").addEventListener("click", runPipeline);
  document
    .getElementById("add-scenario")
    .addEventListener("click", () => {
      state.editor.scenarios.push(
        blankLayer(`scenario ${state.editor.scenarios.length + 1}`)
      );
      renderLayerList("scenarios");
    });
  document
    .getElementById("toggle-all-scenarios")
    .addEventListener("click", () => setAllScenariosCollapsed(!allScenariosCollapsed()));
  document.querySelectorAll('input[name="seed-mode"]').forEach((r) =>
    r.addEventListener("change", () => {
      readRuntimeForm();
      syncSeedInputs();
      persistRuntime();
    })
  );
  ["seed-value", "seed-count"].forEach((id) => {
    const node = document.getElementById(id);
    node.addEventListener("input", () => {
      readRuntimeForm();
      updateCombos();
    });
    node.addEventListener("change", persistRuntime);
  });
  // Manual workflow-path edits are remembered once the field loses focus.
  document.getElementById("pl-template").addEventListener("change", () => {
    readRuntimeForm();
    persistRuntime();
  });
  document.getElementById("settings-save").addEventListener("click", saveSettings);
}

async function main() {
  setEditorStatus = statusSetter(document.getElementById("editor-status"));
  wireEvents();
  // Open the progress socket up front so the UI can receive live "capture"
  // re-sync signals even before any run is started.
  openProgressSocket();
  await loadHealth();
  await loadModels();
  await loadSettings();
  await loadRuntimeFromSettings(); // restore run-time config before first render
  renderPipelineForm();
  renderRuntimeForm();
  await refreshPipelines();

  // Pick up a workflow handed off from the ComfyUI canvas, if any, and map its
  // slots straight away so the user lands on a ready-to-edit pipeline.
  if (await consumeCapture()) {
    setEditorStatus("loaded the workflow open in ComfyUI", "ok");
    await detectSlots();
  }
}

window.addEventListener("DOMContentLoaded", main);
