"""comfyui_batch_render — batch render images across base + scenario combinations on ComfyUI.

Layered, composable design (see README):
  Tier 0  patcher   — pure graph-patching functions (no I/O)
  Tier 1  engine    — run engine + CLI that drives the ComfyUI HTTP/WS API
  Tier 2  plugin     — ComfyUI custom-node shell (added later)
  Tier 3  web UI     — browser editor (added later)
"""

__version__ = "0.1.0"
