"""Public evaluator must preserve input paths and reject structurally invalid models."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

from evaluate import run_command

ROOT = Path(__file__).resolve().parents[2]


def run_cli(*arguments):
    return subprocess.run([sys.executable, str(ROOT / "public_evaluator/evaluate.py"),
                           *map(str, arguments)], capture_output=True, text=True)


def test_missing_root_and_includes_is_invalid(tmp_path):
    original = tmp_path / "original"
    restored = tmp_path / "restored"
    original.mkdir()
    restored.mkdir()
    (original / "CASE.DATA").write_text("INCLUDE 'grid.inc' /\n")
    (original / "grid.inc").write_text("GRID\n")
    (restored / "unrelated.txt").write_text("wrong")
    archive = tmp_path / "archive"
    archive.write_bytes(b"fixture")
    proc = run_cli("--model", original, "--archive", archive, "--restored-model", restored,
                   "--workdir", tmp_path / "work")
    result = json.loads(proc.stdout)
    assert result["valid"] is False
    assert proc.returncode != 0
    assert result["validation_notes"]


def test_overlapping_restore_is_rejected_without_deleting_input(tmp_path):
    model = tmp_path / "model"
    model.mkdir()
    sentinel = model / "CASE.DATA"
    sentinel.write_text("RUNSPEC\n")
    proc = run_cli("--model", model, "--solution-dir", ROOT / "baseline",
                   "--restored-model", model, "--workdir", tmp_path / "work")
    assert proc.returncode != 0
    assert sentinel.read_text() == "RUNSPEC\n"


def test_existing_restored_content_is_preserved(tmp_path):
    model, restored = tmp_path / "model", tmp_path / "restored"
    model.mkdir()
    restored.mkdir()
    (model / "CASE.DATA").write_text("RUNSPEC\n")
    sentinel = restored / "user.txt"
    sentinel.write_text("keep")
    proc = run_cli("--model", model, "--solution-dir", ROOT / "baseline",
                   "--restored-model", restored, "--workdir", tmp_path / "work")
    assert proc.returncode != 0
    assert sentinel.read_text() == "keep"


def test_command_timeout_is_bounded(tmp_path):
    with pytest.raises(TimeoutError):
        run_command([sys.executable, "-c", "import time; time.sleep(30)"],
                    tmp_path / "timeout.log", timeout_seconds=.1)


def test_artifact_json_cannot_overwrite_input_model(tmp_path):
    model, restored = tmp_path / "model", tmp_path / "restored"
    model.mkdir()
    restored.mkdir()
    for directory in (model, restored):
        (directory / "CASE.DATA").write_text("RUNSPEC\n")
    archive = tmp_path / "archive"
    archive.write_bytes(b"fixture")
    sentinel = model / "CASE.DATA"
    proc = run_cli("--model", model, "--archive", archive, "--restored-model", restored,
                   "--json-output", sentinel, "--workdir", tmp_path / "work")
    assert proc.returncode != 0
    assert sentinel.read_text() == "RUNSPEC\n"
