// Thin fetch wrappers around the /api/brp endpoints. No external deps.
"use strict";

const API = "/api/brp";

async function request(method, path, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const resp = await fetch(`${API}${path}`, opts);
  let data = null;
  try {
    data = await resp.json();
  } catch (_e) {
    data = null;
  }
  if (!resp.ok) {
    const msg = (data && data.error) || `HTTP ${resp.status}`;
    throw new Error(msg);
  }
  return data;
}

export const api = {
  health: () => request("GET", "/health"),
  models: (kind) => request("GET", `/models?kind=${encodeURIComponent(kind)}`),
  listPipelines: () => request("GET", "/pipelines"),
  getPipeline: (name) =>
    request("GET", `/pipelines/${encodeURIComponent(name)}`),
  savePipeline: (name, body) =>
    request("PUT", `/pipelines/${encodeURIComponent(name)}`, body),
  createPipeline: (body) => request("POST", "/pipelines", body),
  deletePipeline: (name) =>
    request("DELETE", `/pipelines/${encodeURIComponent(name)}`),
  detect: (payload) => request("POST", "/detect", payload),
  run: (payload) => request("POST", "/run", payload),
  getSettings: () => request("GET", "/settings"),
  saveSettings: (patch) => request("POST", "/settings", patch),
};
