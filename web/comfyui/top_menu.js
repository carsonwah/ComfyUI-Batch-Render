// ComfyUI frontend extension: adds a "Batch Render" entry that opens our
// standalone UI in a new tab.
//
// Primary mechanism is the documented commands + menuCommands API (a top-menu
// item). We also register a best-effort action-bar button and a DOM fallback so
// a clickable entry shows up across frontend versions.
import { app } from "../../scripts/app.js";

const BRP_URL = "/batch-render";

// Same-origin handoff: the Batch Render UI reads this key once on load to pick
// up the workflow currently open on the canvas (so the user never has to find
// or export an "API-format" file). See server/static/app.js (consumeCapture).
const CAPTURE_KEY = "brp_captured_workflow";

// "repeat" icon (Material-style), used for the DOM-fallback button.
const REPEAT_SVG =
  '<svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor" aria-hidden="true">' +
  '<path d="M7 7h10v3l4-4-4-4v3H5v6h2V7zm10 10H7v-3l-4 4 4 4v-3h12v-6h-2v4z"/>' +
  "</svg>";

// Best-effort: snapshot the current graph in ComfyUI's API ("output") format
// and stash it for the Batch Render tab. Any failure is non-fatal -- the UI
// falls back to its manual template-path field.
async function captureCurrentWorkflow() {
  // Clear any stale capture first so a failure here never resurfaces an old
  // workflow in the new tab.
  try {
    window.localStorage.removeItem(CAPTURE_KEY);
  } catch (_e) {}
  try {
    if (!app || typeof app.graphToPrompt !== "function") return false;
    const prompt = await app.graphToPrompt();
    const apiGraph = prompt && prompt.output;
    if (
      !apiGraph ||
      typeof apiGraph !== "object" ||
      Object.keys(apiGraph).length === 0
    ) {
      return false;
    }
    window.localStorage.setItem(
      CAPTURE_KEY,
      JSON.stringify({
        template: apiGraph,
        source: "comfyui-canvas",
        ts: Date.now(),
      })
    );
    return true;
  } catch (err) {
    console.warn("[BatchRender] could not capture current workflow:", err);
    return false;
  }
}

async function openBatchRender() {
  const url = window.location.origin + BRP_URL;
  await captureCurrentWorkflow();
  console.info("[BatchRender] opening", url);
  window.open(url, "_blank");
}

app.registerExtension({
  name: "BatchRender.TopMenu",

  // Documented, reliable: a top-menu command (appears under a "Batch Render"
  // menu). Handler field is `function` (NOT `onClick`).
  commands: [
    {
      id: "BatchRender.open",
      label: "Open Batch Render UI",
      icon: "pi pi-replay",
      function: openBatchRender,
    },
  ],
  menuCommands: [{ path: ["Batch Render"], commands: ["BatchRender.open"] }],

  // Best-effort: an icon-only button in the action bar on frontends that
  // support it. Provide both handler field names; unknown keys are ignored.
  actionBarButtons: [
    {
      id: "BatchRender.open.actionbar",
      icon: "pi pi-replay",
      tooltip: "Open the Batch Render UI",
      function: openBatchRender,
      onClick: openBatchRender,
    },
  ],

  async setup() {
    console.info(
      "[BatchRender] loaded. Use the top menu: 'Batch Render > Open Batch Render UI', " +
        "or open " + window.location.origin + BRP_URL + " directly."
    );
    // DOM fallback: inject a plain button into the top menu bar.
    try {
      if (document.getElementById("brp-open-btn")) return;
      const menu =
        document.querySelector(".comfyui-menu-right") ||
        document.querySelector(".comfyui-body-top .comfyui-menu") ||
        document.querySelector(".comfy-menu");
      if (!menu) return;
      const btn = document.createElement("button");
      btn.id = "brp-open-btn";
      btn.innerHTML = REPEAT_SVG;
      btn.title = "Open the Batch Render UI";
      btn.setAttribute("aria-label", "Open the Batch Render UI");
      btn.style.cssText =
        "display:inline-flex;align-items:center;justify-content:center;cursor:pointer;margin:0 4px;";
      btn.addEventListener("click", openBatchRender);
      menu.appendChild(btn);
    } catch (err) {
      console.warn("[BatchRender] could not inject fallback button:", err);
    }
  },
});
