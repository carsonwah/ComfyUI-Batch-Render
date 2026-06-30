"""Loading helpers for pipelines, templates, and CLI config files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from .pipeline import Pipeline


def load_yaml_or_json(path: Any) -> dict:
    """Load a mapping from a ``.json`` / ``.yaml`` / ``.yml`` file by extension."""
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    suffix = p.suffix.lower()
    if suffix in (".yaml", ".yml"):
        data = yaml.safe_load(text)
    elif suffix == ".json":
        data = json.loads(text)
    else:
        # Be forgiving: YAML is a superset of JSON, so try it last.
        data = yaml.safe_load(text)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ValueError(f"expected a mapping at top level of {p}, got {type(data)}")
    return data


def load_pipeline(path: Any) -> Pipeline:
    """Load and parse a pipeline definition file."""
    return Pipeline.from_dict(load_yaml_or_json(path))


def load_template(path: Any) -> dict:
    """Load a ComfyUI API-format workflow template (JSON or YAML)."""
    return load_yaml_or_json(path)


def load_config(path: Any) -> dict:
    """Load a CLI config file (the ``config.example.yaml`` shape)."""
    return load_yaml_or_json(path)
