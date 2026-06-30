"""Hermetic tests for the JSON store (no ComfyUI, isolated tmp base_dir)."""

from __future__ import annotations

import pytest

from comfyui_batch_render.store import Store


def test_settings_defaults_and_roundtrip(tmp_path):
    store = Store(tmp_path)

    # Defaults are merged in memory even before anything is written.
    settings = store.get_settings()
    assert settings["comfyui"]["host"] == "127.0.0.1"
    assert settings["comfyui"]["port"] is None
    assert settings["output_dir"] == "./output"
    assert settings["default_template"] is None

    # Deep-merge update persists and survives a fresh Store instance.
    updated = store.update_settings({"comfyui": {"port": 9999}, "output_dir": "/x"})
    assert updated["comfyui"]["port"] == 9999
    assert updated["comfyui"]["host"] == "127.0.0.1"  # default preserved
    assert updated["output_dir"] == "/x"

    assert Store(tmp_path).get_settings()["comfyui"]["port"] == 9999


def test_secret_redaction(tmp_path):
    store = Store(tmp_path)
    store.update_settings({"civitai_api_key": "supersecret"})

    settings = store.get_settings()
    assert "civitai_api_key" not in settings
    assert settings["civitai_api_key_set"] is True

    store.update_settings({"civitai_api_key": ""})
    assert store.get_settings()["civitai_api_key_set"] is False


def test_pipeline_crud(tmp_path):
    store = Store(tmp_path)
    assert store.list_pipelines() == []

    store.save_pipeline("My Pipeline", {"bases": [1, 2], "scenarios": [3]})
    listing = store.list_pipelines()
    assert len(listing) == 1
    assert listing[0]["name"] == "My Pipeline"
    assert listing[0]["bases"] == 2
    assert listing[0]["scenarios"] == 1

    loaded = store.get_pipeline("My Pipeline")
    assert loaded["name"] == "My Pipeline"

    assert store.delete_pipeline("My Pipeline") is True
    assert store.list_pipelines() == []
    assert store.delete_pipeline("My Pipeline") is False

    with pytest.raises(KeyError):
        store.get_pipeline("My Pipeline")


def test_path_traversal_rejected(tmp_path):
    store = Store(tmp_path)
    # slugify strips the dangerous characters, so this lands inside the dir;
    # an explicitly traversal-y name must never escape pipelines/.
    store.save_pipeline("../../evil", {"x": 1})
    files = list((tmp_path / "pipelines").glob("*.json"))
    assert len(files) == 1
    assert files[0].parent == (tmp_path / "pipelines")


def test_config_dir_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("BRP_CONFIG_DIR", str(tmp_path / "cfg"))
    from comfyui_batch_render.paths import config_dir, pipelines_dir

    assert config_dir() == (tmp_path / "cfg")
    assert pipelines_dir() == (tmp_path / "cfg" / "pipelines")
    assert (tmp_path / "cfg" / "pipelines").is_dir()
