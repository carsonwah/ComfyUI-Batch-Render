"""CLI tests against the example fixtures (expand + dry-run)."""

from __future__ import annotations

import json
from pathlib import Path

from comfyui_batch_render.cli import main

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
PIPELINE = EXAMPLES / "pipeline.example.yaml"


def test_expand_runs(capsys):
    rc = main(["expand", "--pipeline", str(PIPELINE)])
    assert rc == 0
    out = capsys.readouterr().out
    # 2 bases x 2 scenarios x 1 fixed seed = 4 jobs
    assert "total: 4 job(s)" in out
    assert "seed=42" in out


def test_dry_run_writes_files(tmp_path, capsys):
    rc = main(
        ["dry-run", "--pipeline", str(PIPELINE), "--output", str(tmp_path)]
    )
    assert rc == 0

    slug = "example-portraits"
    graphs = sorted((tmp_path / slug / "_graphs").glob("*.json"))
    assert len(graphs) == 4

    manifest_path = tmp_path / slug / "manifest.json"
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["job_count"] == 4
    assert manifest["pipeline"] == "example-portraits"

    out = capsys.readouterr().out
    assert "manifest written to" in out


def test_dry_run_explicit_template(tmp_path):
    template = EXAMPLES / "portrait.example.api.json"
    rc = main(
        [
            "dry-run",
            "--pipeline",
            str(PIPELINE),
            "--template",
            str(template),
            "--output",
            str(tmp_path),
        ]
    )
    assert rc == 0
    graphs = sorted((tmp_path / "example-portraits" / "_graphs").glob("*.json"))
    assert len(graphs) == 4
