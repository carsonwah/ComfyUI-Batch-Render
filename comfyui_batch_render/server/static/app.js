// Minimal vanilla-JS bootstrap that proves the server + API + page wiring.
// Stage 4 will expand this into the real editor.
"use strict";

const API = "/api/brp";

async function getJSON(url) {
  const resp = await fetch(url);
  if (!resp.ok) throw new Error(`${url} -> HTTP ${resp.status}`);
  return resp.json();
}

function renderModels(elId, countId, models) {
  const list = document.getElementById(elId);
  const count = document.getElementById(countId);
  list.innerHTML = "";
  count.textContent = String(models.length);
  for (const m of models) {
    const li = document.createElement("li");
    const label = m.subfolder ? `${m.subfolder}/${m.name}` : m.name;
    li.textContent = label;
    if (m.triggers) {
      const span = document.createElement("span");
      span.className = "triggers";
      span.textContent = ` — ${m.triggers}`;
      li.appendChild(span);
    }
    list.appendChild(li);
  }
}

function renderPipelines(pipelines) {
  const list = document.getElementById("pipelines");
  document.getElementById("pipelines-count").textContent = String(pipelines.length);
  list.innerHTML = "";
  for (const p of pipelines) {
    const li = document.createElement("li");
    li.textContent = `${p.name} (${p.bases} bases × ${p.scenarios} scenarios)`;
    list.appendChild(li);
  }
}

async function main() {
  const health = document.getElementById("health");
  try {
    const h = await getJSON(`${API}/health`);
    const target = h.comfyui ? `${h.comfyui.host}:${h.comfyui.port ?? "?"}` : "unknown";
    health.textContent = `server ok — v${h.version} — ComfyUI ${target}`;
    health.classList.add("ok");
  } catch (err) {
    health.textContent = `server unreachable: ${err.message}`;
    health.classList.add("err");
    return;
  }

  try {
    const [ckpts, loras, pipes] = await Promise.all([
      getJSON(`${API}/models?kind=checkpoints`),
      getJSON(`${API}/models?kind=loras`),
      getJSON(`${API}/pipelines`),
    ]);
    renderModels("checkpoints", "checkpoints-count", ckpts.models || []);
    renderModels("loras", "loras-count", loras.models || []);
    renderPipelines(pipes.pipelines || []);
  } catch (err) {
    console.error(err);
  }
}

main();
