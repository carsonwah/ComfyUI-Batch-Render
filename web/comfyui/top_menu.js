// ComfyUI frontend extension: adds a "Batch Render" entry that opens our
// standalone UI in a new tab.
//
// Primary mechanism is the documented commands + menuCommands API (a top-menu
// item). We also register a best-effort action-bar button and a DOM fallback so
// a clickable entry shows up across frontend versions.
import { app } from "../../scripts/app.js";

const BRP_URL = "/batch-render";

// Workflow handoff to the Batch Render UI so the user never has to find or
// export an "API-format" file. Two channels, in order of robustness:
//   1. Server relay (POST /api/brp/capture): works even when the UI opens in a
//      *different* browser process than ComfyUI -- e.g. the desktop app's
//      Electron webview vs. the external system browser that window.open
//      launches. localStorage cannot bridge that gap (separate storage).
//   2. localStorage (same-origin only): a belt-and-suspenders fast path for the
//      all-in-one-browser case.
// See server/static/app.js (consumeCapture) and server/app.py (_post_capture).
const CAPTURE_KEY = "brp_captured_workflow";
const CAPTURE_API = "/api/brp/capture";

// "layers" icon (Lucide). We reuse one source-of-truth path set for both:
//   - the DOM-fallback button (inline SVG, see LAYERS_SVG), and
//   - the ComfyUI menu/action-bar slots, whose `icon` field is a CSS *class*
//     string (not markup) -- so we expose `BRP_ICON_CLASS`, a class we style
//     with a mask-image of the same SVG, tinted via currentColor. This frees us
//     from PrimeIcons without adding a font, CDN, or build step.
const LAYERS_PATHS =
  '<path d="M12.83 2.18a2 2 0 0 0-1.66 0L2.6 6.08a1 1 0 0 0 0 1.83l8.58 3.91a2 2 0 0 0 1.66 0l8.58-3.9a1 1 0 0 0 0-1.83z"/>' +
  '<path d="M2 12a1 1 0 0 0 .58.91l8.6 3.91a2 2 0 0 0 1.65 0l8.58-3.9A1 1 0 0 0 22 12"/>' +
  '<path d="M2 17a1 1 0 0 0 .58.91l8.6 3.91a2 2 0 0 0 1.65 0l8.58-3.9A1 1 0 0 0 22 17"/>';

const LAYERS_SVG =
  '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" ' +
  'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
  LAYERS_PATHS +
  "</svg>";

const BRP_ICON_CLASS = "brp-layers-icon";

// Inject the CSS class used by the menu/action-bar `icon` slots. Mask + a
// currentColor background makes the SVG inherit the menu's text color, so it
// matches PrimeIcons sizing/tinting. Idempotent.
function ensureIconStyle() {
  try {
    if (document.getElementById("brp-icon-style")) return;
    const svg =
      '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" ' +
      'stroke="black" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
      LAYERS_PATHS +
      "</svg>";
    const url = 'url("data:image/svg+xml,' + encodeURIComponent(svg) + '")';
    const style = document.createElement("style");
    style.id = "brp-icon-style";
    style.textContent =
      "." + BRP_ICON_CLASS + "{" +
      "display:inline-block;width:1.25rem;height:1.25rem;vertical-align:middle;" +
      "background-color:currentColor;" +
      "-webkit-mask:" + url + " center/contain no-repeat;" +
      "mask:" + url + " center/contain no-repeat;}";
    document.head.appendChild(style);
  } catch (err) {
    console.warn("[BatchRender] could not inject icon style:", err);
  }
}

// Best-effort: snapshot the current graph in ComfyUI's API ("output") format
// and stash it for the Batch Render tab. Any failure is non-fatal -- the UI
// falls back to its manual template-path field.
async function captureCurrentWorkflow() {
  // Clear any stale same-origin capture first so a failure here never
  // resurfaces an old workflow in the new tab.
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
    const payload = {
      template: apiGraph,
      source: "comfyui-canvas",
      ts: Date.now(),
    };
    // Primary channel: hand off through the server so a cross-process UI (the
    // desktop app opens the page in the external browser) can still read it. We
    // await this so the slot is filled before window.open below.
    try {
      const res = await fetch(CAPTURE_API, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        console.warn("[BatchRender] server capture returned", res.status);
      }
    } catch (err) {
      console.warn("[BatchRender] server capture failed:", err);
    }
    // Secondary channel: same-origin fast path for the all-in-one-browser case.
    try {
      window.localStorage.setItem(CAPTURE_KEY, JSON.stringify(payload));
    } catch (_e) {}
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
      icon: BRP_ICON_CLASS,
      function: openBatchRender,
    },
  ],
  menuCommands: [{ path: ["Batch Render"], commands: ["BatchRender.open"] }],

  // Best-effort: an icon-only button in the action bar on frontends that
  // support it. Provide both handler field names; unknown keys are ignored.
  actionBarButtons: [
    {
      id: "BatchRender.open.actionbar",
      icon: BRP_ICON_CLASS,
      tooltip: "Open the Batch Render UI",
      function: openBatchRender,
      onClick: openBatchRender,
    },
  ],

  async setup() {
    ensureIconStyle();
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
      btn.innerHTML = LAYERS_SVG;
      btn.title = "Open the Batch Render UI";
      btn.setAttribute("aria-label", "Open the Batch Render UI");
      btn.style.cssText =
        "display:inline-flex;align-items:center;justify-content:center;cursor:pointer;" +
        "width:2rem;height:2rem;padding:0;margin:0 4px;" +
        "background:transparent;border:none;color:inherit;opacity:1;";
      btn.addEventListener("click", openBatchRender);
      menu.appendChild(btn);
    } catch (err) {
      console.warn("[BatchRender] could not inject fallback button:", err);
    }
  },
});
