"""Cross-platform config-directory resolution (stdlib only).

No third-party deps (no ``platformdirs``): a tiny resolver that honours the
``BRP_CONFIG_DIR`` override and otherwise picks the OS-conventional location.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_APP_NAME = "ComfyUI-Batch-Render"
_XDG_SLUG = "comfyui-batch-render"


def config_dir() -> Path:
    """Return the app config directory, creating it if missing.

    Resolution order:
      1. ``BRP_CONFIG_DIR`` env override (used by tests + portable installs).
      2. Windows: ``%LOCALAPPDATA%\\ComfyUI-Batch-Render``.
      3. macOS: ``~/Library/Application Support/ComfyUI-Batch-Render``.
      4. else (Linux/other): ``${XDG_CONFIG_HOME:-~/.config}/comfyui-batch-render``.
    """
    override = os.environ.get("BRP_CONFIG_DIR")
    if override:
        base = Path(override)
    elif sys.platform == "win32":
        root = os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
        base = Path(root) / _APP_NAME
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / _APP_NAME
    else:
        root = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
        base = Path(root) / _XDG_SLUG

    base.mkdir(parents=True, exist_ok=True)
    return base


def pipelines_dir() -> Path:
    """Return ``config_dir()/pipelines``, creating it if missing."""
    p = config_dir() / "pipelines"
    p.mkdir(parents=True, exist_ok=True)
    return p
