// ComfyUI frontend extension: adds a "Batch Render" button to the top action
// bar that opens our standalone UI in a new tab.
import { app } from "../../scripts/app.js";

const BRP_URL = "/batch-render";
const ICON_SVG =
  '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">' +
  '<rect x="1" y="1" width="6" height="6" rx="1" fill="currentColor"/>' +
  '<rect x="9" y="1" width="6" height="6" rx="1" fill="currentColor"/>' +
  '<rect x="1" y="9" width="6" height="6" rx="1" fill="currentColor"/>' +
  '<rect x="9" y="9" width="6" height="6" rx="1" fill="currentColor"/>' +
  "</svg>";

function openBatchRender() {
  window.open(window.location.origin + BRP_URL, "_blank");
}

app.registerExtension({
  name: "BatchRender.TopMenu",

  // Modern API: contribute a button to the action bar when available.
  actionBarButtons() {
    return [
      {
        id: "batch-render-open",
        label: "Batch Render",
        icon: "pi pi-th-large",
        tooltip: "Open the Batch Render UI",
        onClick: openBatchRender,
      },
    ];
  },

  async setup() {
    // Fallback: if the action bar API did not pick up the button, inject a
    // ComfyButton (or a plain button) into the menu manually.
    try {
      if (document.getElementById("batch-render-open-btn")) return;

      let btn = null;
      try {
        const { ComfyButton } = await import("../../scripts/ui/components/button.js");
        btn = new ComfyButton({
          icon: "view-grid",
          content: "Batch Render",
          tooltip: "Open the Batch Render UI",
          action: openBatchRender,
        }).element;
      } catch (e) {
        btn = document.createElement("button");
        btn.innerHTML = ICON_SVG + " Batch Render";
        btn.style.cssText =
          "display:inline-flex;align-items:center;gap:4px;cursor:pointer;";
        btn.onclick = openBatchRender;
      }
      btn.id = "batch-render-open-btn";

      const menu =
        document.querySelector(".comfyui-menu-right") ||
        document.querySelector(".comfyui-menu") ||
        document.querySelector(".comfy-menu");
      if (menu) menu.appendChild(btn);
    } catch (err) {
      console.warn("[BatchRender] could not inject top-menu button:", err);
    }
  },
});
