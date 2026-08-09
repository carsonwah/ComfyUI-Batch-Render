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
  run: { active: false, id: null, ws: null },
  reviewOnly: false,
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
  return {
    name: name || "layer",
    enabled: true,
    checkpoint: null,
    prompt: "",
    negative: "",
    participant_count: 0,
    loras: [],
  };
}

// --------------------------------------------------------------------------- //
// Model select helpers
// --------------------------------------------------------------------------- //

// model.name is the bare basename for display; model.file is the full relative
// path ComfyUI's loaders expect (e.g. "char/foo.safetensors"). Selections store
// model.file so LoRAs/checkpoints in subfolders actually load.
function checkpointOptions(selected) {
  const opts = [{ value: "", label: "(inherit)" }];
  for (const m of state.models.checkpoints) opts.push({ value: m.name, label: m.name });
  return { opts, selected: selected || "" };
}

// Locate a LoRA model record by its stored file value (with a basename
// fallback so legacy pipelines that saved just the filename still resolve).
function loraByFile(file) {
  if (!file) return null;
  const loras = state.models.loras;
  return loras.find((m) => m.file === file) || loras.find((m) => m.name === file) || null;
}

function loraTriggersFor(file) {
  const m = loraByFile(file);
  return m ? m.triggers || "" : "";
}

function loraPromptOptions(file) {
  const m = loraByFile(file);
  if (!m) return [];
  const triggers = Array.isArray(m.prompt_options) ? m.prompt_options : [];
  const examples = Array.isArray(m.example_prompts) ? m.example_prompts : [];
  return [...new Set([...triggers, ...examples])];
}

function loraDefaultPrompt(file) {
  const m = loraByFile(file);
  const options = m && Array.isArray(m.prompt_options) ? m.prompt_options : [];
  return options[0] || loraTriggersFor(file);
}

function newLora(role = "scenario") {
  return { file: "", weight: 1.0, triggers: "", role, reviewed: false };
}

// Cap the number of cards rendered at once so a few-thousand-LoRA library does
// not create an enormous DOM. The browser still reports the full match count.
const LORA_RESULT_CAP = 50;

function previewUrl(file) {
  return `/api/brp/preview?kind=loras&file=${encodeURIComponent(file)}`;
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
  const s = state.editor.scenarios.filter((scenario) => scenario.enabled !== false).length;
  const seeds = seedCount();
  const total = b * s * seeds;
  const node = document.getElementById("combo-counter");
  if (node) {
    node.textContent = `combinations: 1 base x ${s} scenarios x ${seeds} seeds = ${total}`;
  }
}

// Metadata recipes often contain people-count tokens. Use them as a helpful
// default, while keeping the count explicitly overridable per scenario.
function inferPeople(text) {
  let best = 1;
  for (const rawLine of String(text || "").toLowerCase().split("\n")) {
    const line = rawLine.replace(/\b(mmf|mff)\s+threesome\b/g, "threesome");
    let count = /\b(threesome|group sex)\b/.test(line) ? 3 : 0;
    let explicit = 0;
    for (const match of line.matchAll(/\b(\d+)\s*(girls?|boys?|women|men|people|persons?)\b/g)) {
      explicit += Number(match[1]) || 0;
    }
    count = Math.max(count, explicit);
    if (/\bmultiple\s+(girls?|boys?|women|men|people|persons?)\b/.test(line)) {
      count = Math.max(count, explicit + 2);
    }
    if (/\bsolo\b/.test(line)) count = Math.max(count, 1);
    best = Math.max(best, count || 1);
  }
  return Math.min(best, 4);
}

function scenarioPeople(layer) {
  const explicit = Number(layer.participant_count) || 0;
  if (explicit > 0) return explicit;
  const source = [layer.prompt]
    .concat(layer.loras.filter((l) => l.role !== "character").map((l) => l.triggers))
    .join("\n");
  return inferPeople(source);
}

function castCount(layer) {
  return 1 + layer.loras.filter((l) => l.role === "character" && l.file).length;
}

function castGuidance(layer) {
  const required = scenarioPeople(layer);
  const assigned = castCount(layer);
  const missing = Math.max(0, required - assigned);
  if (missing > 0) {
    return {
      required, assigned, missing,
      title: "Add the missing cast",
      text: `${required} people detected. The base fills person 1 — click “+ Character” ${missing === 1 ? "once" : `${missing} times`} and choose a character LoRA for ${missing === 1 ? "the other person" : "each additional person"}.`,
    };
  }
  if (required > 1) {
    return {
      required, assigned, missing,
      title: "How cast works",
      text: `Cast complete: the base plus ${required - 1} additional character${required === 2 ? "" : "s"}.`,
    };
  }
  return {
    required, assigned, missing,
    title: "How cast works",
    text: "Solo detected. The base supplies the only character; no additional character LoRA is needed.",
  };
}

function refreshScenarioCardStatus(layer) {
  const index = state.editor.scenarios.indexOf(layer);
  if (index < 0) return;
  const card = document.querySelector(`[data-scenario-index="${index}"]`);
  if (!card) return;
  const guidance = castGuidance(layer);
  const attention = scenarioNeedsAttention(layer);
  const enabled = layer.enabled !== false;
  const status = card.querySelector(".status-pill");
  if (status) {
    status.className = `status-pill ${enabled ? (attention ? "attention" : "ready") : "disabled"}`;
    status.textContent = enabled ? (attention ? "Needs review" : "Ready") : "Disabled";
  }
  const people = card.querySelector(".people-pill");
  if (people) {
    people.className = `people-pill ${guidance.missing ? "warning" : ""}`;
    people.textContent = `${guidance.assigned}/${guidance.required} cast`;
    people.title = guidance.missing
      ? "Add character LoRAs to fill this scenario's cast"
      : "Assigned / required people";
  }
  const guide = card.querySelector(".cast-guide");
  if (guide) {
    guide.className = `cast-guide ${guidance.missing ? "needs-cast" : "cast-ready"}`;
    guide.querySelector(".cast-guide-icon").textContent = guidance.missing ? "!" : "✓";
    guide.querySelector("strong").textContent = guidance.title;
    guide.querySelector("p").textContent = guidance.text;
  }
  const autoOption = card.querySelector('.people-select option[value="0"]');
  if (autoOption) autoOption.textContent = `Auto (${scenarioPeople(layer)})`;
}

function scenarioNeedsAttention(layer) {
  if (layer.enabled === false) return false;
  const unreviewed = layer.loras.some((l) => l.file && !l.reviewed);
  return unreviewed || castCount(layer) < scenarioPeople(layer);
}

function updateReviewSummary() {
  const allScenarios = state.editor.scenarios;
  const scenarios = state.editor.scenarios.filter((scenario) => scenario.enabled !== false);
  const needsReview = scenarios.filter(scenarioNeedsAttention).length;
  const castGaps = scenarios.filter((s) => castCount(s) < scenarioPeople(s)).length;
  const summary = document.getElementById("review-summary");
  const cast = document.getElementById("cast-summary");
  const filter = document.getElementById("review-filter");
  const next = document.getElementById("review-next");
  if (summary) {
    summary.textContent = needsReview
      ? `${needsReview} scenario${needsReview === 1 ? "" : "s"} need attention`
      : scenarios.length ? "All scenarios reviewed" : allScenarios.length ? "No scenarios enabled" : "Nothing to review";
  }
  if (cast) cast.textContent = castGaps ? ` · ${castGaps} cast gap${castGaps === 1 ? "" : "s"}` : "";
  if (filter) {
    filter.textContent = state.reviewOnly ? "Show all scenarios" : "Show needs review";
    filter.setAttribute("aria-pressed", String(state.reviewOnly));
    filter.classList.toggle("active", state.reviewOnly);
  }
  if (next) next.disabled = needsReview === 0;
}

function reviewNextScenario() {
  const index = state.editor.scenarios.findIndex(scenarioNeedsAttention);
  if (index < 0) return;
  collapsedLayers.delete(state.editor.scenarios[index]);
  renderLayerList("scenarios");
  const card = document.querySelector(`[data-scenario-index="${index}"]`);
  if (card) {
    card.scrollIntoView({ behavior: "smooth", block: "start" });
    card.classList.add("review-focus");
    setTimeout(() => card.classList.remove("review-focus"), 1200);
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
  let shown = 0;
  layers.forEach((layer, i) => {
    if (kind === "scenarios" && state.reviewOnly && !scenarioNeedsAttention(layer)) return;
    container.appendChild(layerCard(kind, layer, i));
    shown += 1;
  });
  if (kind === "scenarios" && layers.length && !shown) {
    container.appendChild(el("div", { class: "review-complete" }, [
      el("strong", { text: "Review complete" }),
      el("span", { class: "muted small", text: "No scenarios need attention." }),
    ]));
  }
  if (kind === "scenarios") {
    updateToggleAllLabel();
    updateReviewSummary();
  }
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
    rows: 5,
    class: "prompt-editor",
    on: { input: () => (layer.prompt = posInput.value) },
  });
  posInput.value = layer.prompt;
  const negInput = el("textarea", {
    placeholder: "negative prompt",
    rows: 4,
    class: "prompt-editor negative-editor",
    on: { input: () => (layer.negative = negInput.value) },
  });
  negInput.value = layer.negative;

  const overrideFields = [
    field("Checkpoint", ckSel),
    field("Positive", posInput),
    field("Negative", negInput),
  ];

  const lorasBox = el("div", { class: "loras-box" });
  renderLoras(lorasBox, layer, kind === "bases" ? "base" : "scenario");
  const lorasSection = el("div", { class: "loras-section" }, [
    el("div", { class: "row-between" }, [
      el("div", {}, [
        el("span", { class: "section-title", text: kind === "bases" ? "Identity LoRAs" : "Scenario LoRAs" }),
        el("p", { class: "muted small section-help", text: kind === "bases" ? "Character identity, appearance, and default outfit." : "Pose, outfit, scene, or style applied in this scenario." }),
      ]),
      el("button", {
        class: "small",
        text: "+ LoRA",
        on: {
          click: () => {
            layer.loras.push(newLora(kind === "bases" ? "base" : "scenario"));
            renderLoras(lorasBox, layer, kind === "bases" ? "base" : "scenario");
          },
        },
      }),
    ]),
    lorasBox,
  ]);

  let body;
  if (kind === "bases") {
    // The base carries the main prompt/checkpoint, so show those fields directly.
    body = el("div", { class: "card-body" }, [...overrideFields, lorasSection]);
  } else {
    const castBox = el("div", { class: "loras-box cast-box" });
    renderLoras(castBox, layer, "character");
    const inferred = scenarioPeople(layer);
    const peopleSelect = el("select", { class: "people-select" });
    fillSelect(peopleSelect, [
      { value: "0", label: `Auto (${inferred})` },
      { value: "1", label: "1 person" },
      { value: "2", label: "2 people" },
      { value: "3", label: "3 people" },
      { value: "4", label: "4+ people" },
    ], String(Number(layer.participant_count) || 0));
    peopleSelect.addEventListener("change", () => {
      layer.participant_count = Number(peopleSelect.value) || 0;
      renderLayerList("scenarios");
    });
    const guidance = castGuidance(layer);
    const castGuide = el("div", {
      class: `cast-guide ${guidance.missing ? "needs-cast" : "cast-ready"}`,
      role: "note",
    }, [
      el("span", { class: "cast-guide-icon", text: guidance.missing ? "!" : "✓" }),
      el("div", {}, [
        el("strong", { text: guidance.title }),
        el("p", { text: guidance.text }),
        el("small", { text: "Auto reads person tokens from the selected prompt. If it guessed wrong, change the people dropdown." }),
      ]),
    ]);
    const castSection = el("div", { class: "cast-section" }, [
      el("div", { class: "cast-heading row-between" }, [
        el("div", {}, [
          el("span", { class: "section-title", text: "Cast" }),
          el("p", { class: "muted small section-help", text: "Base = person 1 · each added Character = one more person." }),
        ]),
        el("div", { class: "row" }, [
          peopleSelect,
          el("button", {
            class: "small",
            text: "+ Character",
            on: { click: () => {
              layer.loras.push(newLora("character"));
              renderLayerList("scenarios");
            } },
          }),
        ]),
      ]),
      castGuide,
      castBox,
    ]);
    // Scenarios usually only add LoRAs. Tuck the checkpoint/prompt overrides
    // behind a toggle so the common case stays uncluttered -- but reveal them
    // by default when a loaded scenario actually sets one.
    const hasOverrides = !!(
      layer.checkpoint ||
      (layer.prompt || "").trim() ||
      (layer.negative || "").trim()
    );
    const overridesBox = el(
      "div",
      { class: "overrides-box", hidden: !hasOverrides },
      overrideFields
    );
    const labelText = () =>
      (overridesBox.hidden ? "▸ " : "▾ ") + "Override checkpoint / prompts";
    const toggle = el("button", {
      class: "small override-toggle",
      text: labelText(),
      title: "Set a checkpoint or positive/negative prompt just for this scenario",
      on: {
        click: () => {
          overridesBox.hidden = !overridesBox.hidden;
          toggle.textContent = labelText();
        },
      },
    });
    body = el("div", { class: "card-body" }, [castSection, lorasSection, toggle, overridesBox]);
  }

  const head = [];
  // Scenarios can be collapsed to keep a long list scrollable. The base is a
  // single card, so it's always expanded and gets no toggle.
  const collapsible = kind !== "bases";
  let caret = null;
  const toggleCollapsed = () => {
    if (!collapsible) return;
    const collapsed = !collapsedLayers.has(layer);
    if (collapsed) collapsedLayers.add(layer);
    else collapsedLayers.delete(layer);
    body.hidden = collapsed;
    if (caret) caret.textContent = collapsed ? "▸" : "▾";
    if (kind === "scenarios") updateToggleAllLabel();
  };
  if (collapsible) {
    if (collapsedLayers.has(layer)) body.hidden = true;
    caret = el("button", {
      class: "small caret",
      text: collapsedLayers.has(layer) ? "▸" : "▾",
      title: "collapse / expand",
      on: {
        click: toggleCollapsed,
      },
    });
    head.push(caret);
    const leadLora = layer.loras.find((l) => l.file && l.role !== "character" && l.role !== "base");
    const leadModel = leadLora ? loraByFile(leadLora.file) : null;
    head.push(leadModel && leadModel.preview
      ? el("img", { class: "scenario-cover", loading: "lazy", src: previewUrl(leadModel.file), alt: "" })
      : el("div", { class: "scenario-cover scenario-cover-empty", text: String(index + 1).padStart(2, "0") }));
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
  if (kind === "scenarios") {
    const enabled = layer.enabled !== false;
    const enabledCheckbox = el("input", { type: "checkbox" });
    enabledCheckbox.checked = enabled;
    enabledCheckbox.addEventListener("change", () => {
      layer.enabled = enabledCheckbox.checked;
      renderLayerList("scenarios");
    });
    head.push(el("label", {
      class: "scenario-enabled-control",
      title: enabled ? "Disable this scenario" : "Enable this scenario",
    }, [enabledCheckbox, el("span", { text: "Enabled" })]));
    const required = scenarioPeople(layer);
    const assigned = castCount(layer);
    const attention = scenarioNeedsAttention(layer);
    head.push(el("span", {
      class: `status-pill ${enabled ? (attention ? "attention" : "ready") : "disabled"}`,
      text: enabled ? (attention ? "Needs review" : "Ready") : "Disabled",
    }));
    head.push(el("span", {
      class: `people-pill ${assigned < required ? "warning" : ""}`,
      text: `${assigned}/${required} cast`,
      title: assigned < required ? "Add character LoRAs to fill this scenario's cast" : "Assigned / required people",
    }));
  }
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

  const cardHead = el("div", {
    class: "card-head",
    title: collapsible ? "Click the header to collapse or expand" : null,
    on: collapsible ? {
      click: (event) => {
        if (event.target.closest("button, input, select, textarea, label, a")) return;
        toggleCollapsed();
      },
    } : null,
  }, head);
  return el("article", { class: `card ${kind === "scenarios" ? "scenario-card" : "base-card"} ${layer.enabled === false ? "scenario-disabled" : ""}`, dataset: { scenarioIndex: index } }, [
    cardHead,
    body,
  ]);
}

// A no-wrap triggers textarea with a line-number gutter down the left. Lines
// don't wrap (wrap=off), so every logical line is exactly one fixed-height row
// -- the gutter is just the numbers 1..N at the same line-height, scrolled in
// lockstep with the textarea. Returns { element, setValue } so the picker can
// auto-fill triggers programmatically and still refresh the numbers.
function createTriggerEditor(initialValue, onInput) {
  const gutterInner = el("div", { class: "lora-gutter-inner" });
  const gutter = el("div", { class: "lora-gutter" }, [gutterInner]);
  const textarea = el("textarea", {
    class: "lora-triggers",
    placeholder: "Edit the exact prompt text used for this LoRA…",
    rows: "5",
    wrap: "off",
    value: initialValue,
  });
  const wrap = el("div", { class: "lora-triggers-wrap" }, [gutter, textarea]);

  function renderNumbers() {
    const n = Math.max(1, textarea.value.split("\n").length);
    let s = "";
    for (let k = 1; k <= n; k++) s += (k > 1 ? "\n" : "") + k;
    gutterInner.textContent = s;
  }
  function syncMetrics() {
    // Mirror the textarea's text metrics so number rows line up with text rows.
    const cs = getComputedStyle(textarea);
    gutterInner.style.fontFamily = cs.fontFamily;
    gutterInner.style.fontSize = cs.fontSize;
    gutterInner.style.lineHeight = cs.lineHeight;
    gutterInner.style.paddingTop = cs.paddingTop;
  }
  function syncScroll() {
    gutterInner.style.transform = `translateY(${-textarea.scrollTop}px)`;
  }

  textarea.addEventListener("input", () => {
    onInput(textarea.value);
    renderNumbers();
  });
  textarea.addEventListener("scroll", syncScroll);
  requestAnimationFrame(() => {
    syncMetrics();
    renderNumbers();
    syncScroll();
  });

  return {
    element: wrap,
    setValue(v) {
      textarea.value = v;
      renderNumbers();
      syncScroll();
    },
  };
}

function renderLoras(box, layer, role = "scenario") {
  clear(box);
  const matching = layer.loras
    .map((lora, index) => ({ lora, index }))
    .filter(({ lora }) => {
      if (role === "base") return lora.role === "base" || !lora.role;
      if (role === "character") return lora.role === "character";
      return lora.role !== "character" && lora.role !== "base";
    });
  if (matching.length === 0) {
    const copy = role === "character"
      ? "No additional characters assigned."
      : role === "base" ? "Add the primary character LoRA." : "Add a pose, outfit, or scene LoRA.";
    box.appendChild(el("p", { class: "empty small", text: copy }));
    return;
  }
  matching.forEach(({ lora, index: sourceIndex }) => {
    const triggerEditor = createTriggerEditor(lora.triggers, (v) => {
      lora.triggers = v;
      lora.selected_prompts = [];
      lora.reviewed = false;
      updateReviewSummary();
      if (role !== "base") refreshScenarioCardStatus(layer);
    });

    const picker = createLoraPicker(lora.file, (file, chosenPrompt = null) => {
      lora.file = file;
      const defaultPrompt = loraDefaultPrompt(file);
      // Start with one coherent recipe instead of concatenating every metadata
      // alternative. The review badge remains until the user confirms it.
      const t = chosenPrompt || defaultPrompt;
      lora.selected_prompts = t ? [t] : [];
      lora.triggers = t;
      lora.reviewed = false;
      if (role === "base") {
        renderLoras(box, layer, role);
        updateReviewSummary();
      } else {
        renderLayerList("scenarios");
      }
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

    const model = loraByFile(lora.file);
    const preview = model && model.preview
      ? el("img", { class: "lora-preview", loading: "lazy", src: previewUrl(model.file), alt: prettyName(model.name) })
      : el("div", { class: "lora-preview lora-preview-empty" }, [
          el("span", { text: role === "character" || role === "base" ? "Character" : "Scenario" }),
          el("small", { text: "No preview" }),
        ]);

    const options = lora.file ? loraPromptOptions(lora.file) : [];
    let selected = Array.isArray(lora.selected_prompts) ? lora.selected_prompts : null;
    if (!selected) selected = options.filter((option) => String(lora.triggers || "").includes(option));
    const recipes = el("div", { class: "recipe-list" });
    if (options.length) {
      options.forEach((option, optionIndex) => {
        const checked = selected.includes(option);
        const checkbox = el("input", { type: "checkbox" });
        checkbox.checked = checked;
        checkbox.addEventListener("change", () => {
          const current = new Set(Array.isArray(lora.selected_prompts) ? lora.selected_prompts : selected);
          if (checkbox.checked) current.add(option);
          else current.delete(option);
          lora.selected_prompts = options.filter((x) => current.has(x));
          lora.triggers = lora.selected_prompts.join(",\n");
          lora.reviewed = false;
          triggerEditor.setValue(lora.triggers);
          reviewButton.textContent = "Mark reviewed";
          reviewButton.classList.remove("reviewed");
          updateReviewSummary();
          if (role !== "base") refreshScenarioCardStatus(layer);
        });
        recipes.appendChild(el("label", { class: `recipe-option ${checked ? "selected" : ""}` }, [
          checkbox,
          el("span", { class: "recipe-number", text: String(optionIndex + 1) }),
          el("span", { class: "recipe-text", text: option }),
        ]));
        checkbox.addEventListener("change", () => checkbox.closest(".recipe-option")?.classList.toggle("selected", checkbox.checked));
      });
    } else {
      recipes.appendChild(el("p", { class: "muted small", text: lora.file ? "No prompt recipes in metadata — edit the prompt below." : "Choose a LoRA to load prompt recipes." }));
    }

    const reviewButton = el("button", {
      class: `small review-button ${lora.reviewed ? "reviewed" : ""}`,
      text: lora.reviewed ? "Reviewed ✓" : "Mark reviewed",
      on: { click: () => {
        lora.reviewed = !lora.reviewed;
        reviewButton.textContent = lora.reviewed ? "Reviewed ✓" : "Mark reviewed";
        reviewButton.classList.toggle("reviewed", lora.reviewed);
        if (role === "base") updateReviewSummary();
        else renderLayerList("scenarios");
      } },
    });

    box.appendChild(
      el("div", { class: `lora-row lora-role-${role}` }, [
        el("div", { class: "lora-visual" }, [preview]),
        el("div", { class: "lora-content" }, [
          el("div", { class: "lora-controls" }, [picker, field("Weight", weight), reviewButton,
        el("button", {
          class: "btn-danger small",
          text: "Remove",
          title: "remove LoRA",
          on: {
            click: (ev) =>
              confirmDestructive(ev.currentTarget, () => {
                layer.loras.splice(sourceIndex, 1);
                if (role === "base") {
                  renderLoras(box, layer, role);
                  updateReviewSummary();
                } else {
                  renderLayerList("scenarios");
                }
              }),
          },
        }),
          ]),
          el("div", { class: "prompt-workbench" }, [
            el("div", { class: "recipe-panel" }, [
              el("div", { class: "recipe-head row-between" }, [
                el("span", { class: "sub-label", text: options.length > 1 ? `Prompt recipes · choose one or combine (${options.length})` : "Metadata prompt" }),
                options.length > 1 ? el("span", { class: "attention-note", text: "Review alternatives" }) : null,
              ]),
              recipes,
            ]),
            field("Final prompt sent to ComfyUI", triggerEditor.element),
          ]),
        ]),
      ])
    );
  });
}

// Drop the model file extension for display only (it just adds clutter); the
// stored `file` value keeps its extension so the LoRA still loads.
function prettyName(name) {
  return (name || "").replace(/\.(safetensors|ckpt|pt|sft)$/i, "");
}

function loraDisplayName(file) {
  const m = loraByFile(file);
  return prettyName(m ? m.name : file || "");
}

// A full-library browser is more usable than an inline dropdown once a ComfyUI
// install has hundreds of LoRAs. Each picker remembers its most recent query.
function createLoraPicker(initialFile, onPick) {
  let currentFile = initialFile || "";
  let lastQuery = "";
  const wrap = el("div", { class: "lora-picker" });
  const input = el("input", {
    type: "text",
    class: "lora-search",
    placeholder: "Search or browse LoRAs…",
    value: loraDisplayName(currentFile),
    title: currentFile || "Open the LoRA browser",
    readonly: true,
  });
  const browse = el("button", { class: "small", text: "Browse", type: "button" });

  const open = () => openLoraBrowser({
    initialFile: currentFile,
    initialQuery: lastQuery,
    onQuery: (query) => (lastQuery = query),
    onPick: (file, prompt) => {
      currentFile = file;
      const model = loraByFile(file);
      input.value = prettyName(model ? model.name : file);
      input.title = file;
      onPick(file, prompt);
    },
  });
  input.addEventListener("click", open);
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      open();
    }
  });
  browse.addEventListener("click", open);
  wrap.append(input, browse);
  return wrap;
}

function openLoraBrowser({ initialFile, initialQuery, onQuery, onPick }) {
  let folder = "";
  const dialog = el("dialog", { class: "lora-browser-dialog" });
  const search = el("input", {
    type: "search",
    class: "lora-browser-search",
    placeholder: "Search filename, model name, tags, or folder…",
    value: initialQuery || "",
  });
  const folderList = el("nav", { class: "lora-folder-list", "aria-label": "LoRA folders" });
  const results = el("div", { class: "lora-browser-results" });
  const count = el("span", { class: "muted small" });

  const normalizedFolder = (model) => String(model.subfolder || "").replace(/\\/g, "/");
  const folderNames = new Set();
  for (const model of state.models.loras) {
    const parts = normalizedFolder(model).split("/").filter(Boolean);
    for (let i = 1; i <= parts.length; i += 1) folderNames.add(parts.slice(0, i).join("/"));
  }
  const folders = [...folderNames].sort((a, b) => a.localeCompare(b, undefined, { sensitivity: "base" }));

  function inScope(model) {
    const modelFolder = normalizedFolder(model);
    return !folder || modelFolder === folder || modelFolder.startsWith(`${folder}/`);
  }

  function matches(model) {
    if (!inScope(model)) return false;
    const query = search.value.trim().toLowerCase();
    if (!query) return true;
    return [model.name, model.model_name, model.subfolder, ...(model.tags || [])]
      .join(" ")
      .toLowerCase()
      .includes(query);
  }

  function choose(model, prompt = null) {
    try { dialog.close(); } catch (_error) {}
    dialog.remove();
    onPick(model.file, prompt);
  }

  function renderFolders() {
    clear(folderList);
    const addFolder = (value, label, depth, modelsCount) => {
      folderList.appendChild(el("button", {
        class: `lora-folder ${folder === value ? "active" : ""}`,
        type: "button",
        text: `${label} (${modelsCount})`,
        title: value || "All folders",
        style: `--folder-depth:${depth}`,
        on: { click: () => {
          folder = value;
          renderFolders();
          renderResults();
        } },
      }));
    };
    addFolder("", "All LoRAs", 0, state.models.loras.length);
    for (const name of folders) {
      const scopedCount = state.models.loras.filter((model) => {
        const modelFolder = normalizedFolder(model);
        return modelFolder === name || modelFolder.startsWith(`${name}/`);
      }).length;
      addFolder(name, name.split("/").pop(), name.split("/").length, scopedCount);
    }
  }

  function resultCard(model) {
    const image = model.preview
      ? el("img", { class: "lora-browser-preview", loading: "lazy", src: previewUrl(model.file), alt: "" })
      : el("div", { class: "lora-browser-preview lora-preview-empty", text: "No preview" });
    const examples = Array.isArray(model.example_prompts) ? model.example_prompts : [];
    const exampleList = el("div", { class: "lora-example-list" });
    if (examples.length) {
      examples.forEach((prompt, index) => {
        exampleList.appendChild(el("div", { class: "lora-example" }, [
          el("div", { class: "lora-example-text", text: prompt, title: prompt }),
          el("button", {
            class: "small",
            type: "button",
            text: "Use prompt",
            title: `Select this LoRA and use example ${index + 1} as its prompt`,
            on: { click: () => choose(model, prompt) },
          }),
        ]));
      });
    } else {
      exampleList.appendChild(el("p", { class: "muted small", text: "No example prompts in metadata." }));
    }
    return el("article", { class: `lora-browser-card ${model.file === initialFile ? "selected" : ""}` }, [
      image,
      el("div", { class: "lora-browser-card-body" }, [
        el("div", { class: "row-between" }, [
          el("div", { class: "lora-meta" }, [
            el("strong", { class: "lora-name", text: prettyName(model.name) }),
            el("div", { class: "lora-sub", text: model.subfolder || "Root folder" }),
          ]),
          model.base_model ? el("span", { class: "lora-badge", text: model.base_model }) : null,
        ]),
        el("div", { class: "row-between lora-browser-actions" }, [
          el("span", { class: "sub-label", text: examples.length ? `Example prompts (${examples.length})` : "Prompt examples" }),
          el("button", {
            class: "small primary",
            type: "button",
            text: model.file === initialFile ? "Select again" : "Select LoRA",
            on: { click: () => choose(model) },
          }),
        ]),
        exampleList,
      ]),
    ]);
  }

  function renderResults() {
    clear(results);
    onQuery(search.value);
    const allMatches = state.models.loras.filter(matches);
    const shown = allMatches.slice(0, LORA_RESULT_CAP);
    const scopeName = folder || "all folders";
    count.textContent = allMatches.length > shown.length
      ? `${allMatches.length} matches in ${scopeName}; showing first ${shown.length}`
      : `${allMatches.length} match${allMatches.length === 1 ? "" : "es"} in ${scopeName}`;
    if (!shown.length) {
      results.appendChild(el("div", { class: "lora-browser-empty" }, [
        el("strong", { text: "No matching LoRAs" }),
        el("span", { class: "muted small", text: "Try a different search or choose a broader folder." }),
      ]));
      return;
    }
    shown.forEach((model) => results.appendChild(resultCard(model)));
  }

  const close = () => {
    try { dialog.close(); } catch (_error) {}
    dialog.remove();
  };
  dialog.appendChild(el("div", { class: "lora-browser-shell" }, [
    el("div", { class: "lora-browser-head row-between" }, [
      el("div", {}, [el("h2", { text: "Choose a LoRA" }), el("p", { class: "muted small", text: "Search the library, browse folders, or use a prompt from an example image." })]),
      el("button", { class: "small", type: "button", text: "Close", on: { click: close } }),
    ]),
    el("div", { class: "lora-browser-toolbar" }, [search, count]),
    el("div", { class: "lora-browser-main" }, [folderList, results]),
  ]));
  dialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    close();
  });
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) close();
  });
  search.addEventListener("input", renderResults);
  document.body.appendChild(dialog);
  renderFolders();
  renderResults();
  if (typeof dialog.showModal === "function") dialog.showModal();
  else dialog.setAttribute("open", "");
  search.focus();
  search.select();
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

  const enableBtn = document.getElementById("enable-all-scenarios");
  if (!enableBtn) return;
  enableBtn.disabled = !hasScenarios;
  const allEnabled = hasScenarios && state.editor.scenarios.every((layer) => layer.enabled !== false);
  enableBtn.textContent = allEnabled ? "Disable all" : "Enable all";
}

function toggleAllScenariosEnabled() {
  const shouldEnable = state.editor.scenarios.some((layer) => layer.enabled === false);
  state.editor.scenarios.forEach((layer) => (layer.enabled = shouldEnable));
  renderLayerList("scenarios");
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
    bases: e.bases.map((layer) => layerToDict(layer, "base")),
    scenarios: e.scenarios.map((layer) => layerToDict(layer, "scenario")),
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
    bases: e.bases.map((layer) => layerToDict(layer, "base")),
    scenarios: e.scenarios.map((layer) => layerToDict(layer, "scenario")),
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

function layerToDict(layer, defaultRole = "scenario") {
  return {
    name: layer.name,
    enabled: layer.enabled !== false,
    checkpoint: layer.checkpoint || null,
    prompt: layer.prompt || "",
    negative: layer.negative || "",
    participant_count: Number(layer.participant_count) || 0,
    loras: layer.loras
      .filter((l) => l.file)
      .map((l) => ({
        file: l.file,
        weight: Number.isFinite(l.weight) ? l.weight : 1.0,
        triggers: l.triggers || "",
        role: l.role || defaultRole,
        reviewed: !!l.reviewed,
        selected_prompts: Array.isArray(l.selected_prompts) ? l.selected_prompts : [],
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
    // Long scenario libraries open as compact visual rows. The user can scan
    // thumbnails first, then expand only the scenario they want to edit.
    state.editor.scenarios.forEach((layer) => collapsedLayers.add(layer));
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
    state.editor.scenarios.forEach((layer) => collapsedLayers.add(layer));
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
    enabled: l.enabled !== false,
    checkpoint: l.checkpoint || null,
    prompt: l.prompt || "",
    negative: l.negative || "",
    participant_count: Number(l.participant_count) || 0,
    loras: (l.loras || []).map((x) => ({
      file: x.file || "",
      weight: x.weight == null ? 1.0 : x.weight,
      triggers: x.triggers || "",
      // Missing role is the legacy format: it remains valid in either a base
      // or a scenario and is interpreted from the containing layer.
      role: x.role || null,
      reviewed: !!x.reviewed,
      selected_prompts: Array.isArray(x.selected_prompts) ? x.selected_prompts : null,
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
    const name = state.captured.name;
    if (text) {
      text.textContent = name
        ? `✓ Using "${name}" from ComfyUI (${n} nodes).`
        : `✓ Using the workflow open in ComfyUI (${n} nodes).`;
    }
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

function applyCapture(template, name) {
  state.captured = { template, name: name || null };
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
    const cap = (res && res.captured) || null;
    const template = cap && cap.template;
    if (template && typeof template === "object" && Object.keys(template).length) {
      // Drain the stale same-origin copy so the channels can't disagree later.
      try {
        window.localStorage.removeItem(CAPTURE_KEY);
      } catch (_e) {}
      applyCapture(template, cap.name);
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
      applyCapture(template, data.name);
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
    const cap = (res && res.captured) || null;
    const template = cap && cap.template;
    if (template && typeof template === "object" && Object.keys(template).length) {
      applyCapture(template, cap.name);
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
    // Leave "Advanced / slot mapping" collapsed -- detection fills it in for the
    // rare case the user wants to review, but it shouldn't clutter the UI by
    // default.
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
  if (!active) state.run.id = null;
  const btn = document.getElementById("run-btn");
  if (btn) btn.disabled = active;
  updateStopButton();
}

function updateStopButton() {
  const btn = document.getElementById("stop-btn");
  if (btn) btn.disabled = !state.run.active || !state.run.id;
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
    return;
  }
  // This socket receives events for every browser tab. Only the run started by
  // this tab should alter its progress controls.
  if (msg.run_id !== state.run.id) return;
  if (msg.type === "progress") {
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
  } else if (msg.type === "cancelled") {
    logLine("batch stopped", "ok");
    setRunActive(false);
  }
}

async function runPipeline() {
  if (state.run.active) return;
  if (!state.editor.scenarios.some((scenario) => scenario.enabled !== false)) {
    setEditorStatus("enable at least one scenario before running", "err");
    return;
  }
  const runPayload = assembleRunPayload();
  persistRuntime(); // remember the run-time config used for this run
  clear(document.getElementById("run-log"));
  setProgress(0, 0);
  setRunActive(true);
  openProgressSocket();
  logLine(`starting run for "${runPayload.pipeline.name}"...`);
  try {
    const res = await api.run(runPayload);
    state.run.id = res.run_id;
    updateStopButton();
    logLine(`run id: ${res.run_id}`);
    // The run can finish before the browser processes its WebSocket event.
    // Reconcile once after receiving its id so controls cannot get stuck.
    const status = await api.getRun(res.run_id);
    if (status.status === "done") {
      handleProgress({ type: "done", run_id: res.run_id, manifest: status.manifest });
    } else if (status.status === "error") {
      handleProgress({ type: "error", run_id: res.run_id, error: status.error });
    } else if (status.status === "cancelled") {
      handleProgress({ type: "cancelled", run_id: res.run_id });
    }
  } catch (err) {
    logLine(`run failed to start: ${err.message}`, "err");
    setRunActive(false);
  }
}

async function stopRun() {
  const runId = state.run.id;
  if (!state.run.active || !runId) return;
  const btn = document.getElementById("stop-btn");
  if (btn) btn.disabled = true;
  logLine("stopping batch...");
  try {
    await api.cancelRun(runId);
  } catch (err) {
    logLine(`could not stop batch: ${err.message}`, "err");
    updateStopButton();
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
  document.getElementById("stop-btn").addEventListener("click", stopRun);
  document
    .getElementById("add-scenario")
    .addEventListener("click", () => {
      state.editor.scenarios.push(
        blankLayer(`scenario ${state.editor.scenarios.length + 1}`)
      );
      renderLayerList("scenarios");
    });
  document
    .getElementById("enable-all-scenarios")
    .addEventListener("click", toggleAllScenariosEnabled);
  document
    .getElementById("toggle-all-scenarios")
    .addEventListener("click", () => setAllScenariosCollapsed(!allScenariosCollapsed()));
  document.getElementById("review-filter").addEventListener("click", () => {
    state.reviewOnly = !state.reviewOnly;
    renderLayerList("scenarios");
  });
  document.getElementById("review-next").addEventListener("click", reviewNextScenario);
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
