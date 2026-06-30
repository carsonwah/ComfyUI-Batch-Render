"""JSON persistence for settings and saved pipelines.

All state lives under :func:`paths.config_dir` (overridable via
``BRP_CONFIG_DIR``). This module has no ComfyUI imports and is fully testable.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from .runner import slugify

# Code defaults merged into settings in memory (never partially persisted).
_DEFAULT_SETTINGS: dict = {
    "comfyui": {"host": "127.0.0.1", "port": None},
    "output_dir": "./output",
    "default_template": None,
}

# Keys treated as secrets: redacted from get_settings as "<key>_set": bool.
_SECRET_KEYS = ("civitai_api_key",)


def _deep_merge(base: dict, patch: dict) -> dict:
    """Recursively merge ``patch`` into a copy of ``base`` (dicts only)."""
    out = copy.deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _redact(settings: dict) -> dict:
    """Replace secret keys with a boolean ``<key>_set`` flag."""
    out = copy.deepcopy(settings)
    for key in _SECRET_KEYS:
        if key in out:
            present = bool(out.pop(key))
            out[f"{key}_set"] = present
    return out


class Store:
    """File-backed settings + pipeline storage."""

    def __init__(self, base_dir: Path | None = None) -> None:
        if base_dir is None:
            from .paths import config_dir

            base_dir = config_dir()
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.settings_path = self.base_dir / "settings.json"
        self.pipelines_path = self.base_dir / "pipelines"
        self.pipelines_path.mkdir(parents=True, exist_ok=True)

    # -- settings ----------------------------------------------------------- #

    def _read_settings_raw(self) -> dict:
        if not self.settings_path.exists():
            return {}
        try:
            data = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        return data if isinstance(data, dict) else {}

    def get_settings(self) -> dict:
        """Return defaults merged with stored settings, secrets redacted."""
        merged = _deep_merge(_DEFAULT_SETTINGS, self._read_settings_raw())
        return _redact(merged)

    def update_settings(self, patch: dict) -> dict:
        """Shallow-merge ``patch`` into stored settings, persist, return redacted."""
        if not isinstance(patch, dict):
            raise ValueError("settings patch must be a dict")
        stored = self._read_settings_raw()
        merged = _deep_merge(stored, patch)
        self.settings_path.write_text(
            json.dumps(merged, indent=2), encoding="utf-8"
        )
        return _redact(_deep_merge(_DEFAULT_SETTINGS, merged))

    # -- pipelines ---------------------------------------------------------- #

    def _pipeline_path(self, name: str) -> Path:
        """Resolve the on-disk path for ``name``, rejecting path traversal."""
        slug = slugify(name)
        path = (self.pipelines_path / f"{slug}.json").resolve()
        parent = self.pipelines_path.resolve()
        if parent != path.parent:
            raise ValueError(f"invalid pipeline name: {name!r}")
        return path

    def list_pipelines(self) -> list[dict]:
        """Return a name + light metadata record for each saved pipeline."""
        out: list[dict] = []
        for path in sorted(self.pipelines_path.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if not isinstance(data, dict):
                continue
            out.append(
                {
                    "slug": path.stem,
                    "name": data.get("name", path.stem),
                    "bases": len(data.get("bases", []) or []),
                    "scenarios": len(data.get("scenarios", []) or []),
                }
            )
        return out

    def get_pipeline(self, name: str) -> dict:
        """Load a saved pipeline dict by name. Raises ``KeyError`` if missing."""
        path = self._pipeline_path(name)
        if not path.exists():
            raise KeyError(name)
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"corrupt pipeline file: {path}")
        return data

    def save_pipeline(self, name: str, data: dict) -> None:
        """Persist ``data`` under ``name`` (stamping ``name`` into the body)."""
        if not isinstance(data, dict):
            raise ValueError("pipeline data must be a dict")
        path = self._pipeline_path(name)
        body = dict(data)
        body.setdefault("name", name)
        path.write_text(json.dumps(body, indent=2), encoding="utf-8")

    def delete_pipeline(self, name: str) -> bool:
        """Delete a saved pipeline. Returns True if a file was removed."""
        path = self._pipeline_path(name)
        if path.exists():
            path.unlink()
            return True
        return False
