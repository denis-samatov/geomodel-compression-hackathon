"""Round-trip test for the baseline compress/decompress scripts.

baseline/tests/README.md describes this as the manual verification
procedure ("run compress.py, then decompress.py, then evaluate.py") --
this automates the first two steps and asserts the restored model is
byte-for-byte identical to the input.
"""

import json
import subprocess
import sys
from pathlib import Path

BASELINE_DIR = Path(__file__).resolve().parent.parent
COMPRESS_SCRIPT = BASELINE_DIR / "compress.py"
DECOMPRESS_SCRIPT = BASELINE_DIR / "decompress.py"


def make_sample_model(root: Path) -> Path:
    """Build a small synthetic model tree resembling a real submission."""
    model_dir = root / "model"
    (model_dir / "INCLUDE").mkdir(parents=True)
    (model_dir / "RESULTS").mkdir(parents=True)

    (model_dir / "CASE.DATA").write_text(
        "RUNSPEC\nTITLE\nSample case\nINCLUDE\n'INCLUDE/GRID.INC' /\n", encoding="utf-8"
    )
    (model_dir / "INCLUDE" / "GRID.INC").write_text("GRID\n10 10 5 /\n", encoding="utf-8")
    (model_dir / "RESULTS" / "case.UNRST").write_bytes(bytes(range(256)) * 4)
    (model_dir / "readme.txt").write_text("Sample model for testing.\n", encoding="utf-8")

    return model_dir


def run_script(script: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_compress_decompress_round_trip_is_lossless(tmp_path):
    model_dir = make_sample_model(tmp_path)
    archive_path = tmp_path / "artifact.tar.gz"
    restored_dir = tmp_path / "restored"

    compress_proc = run_script(
        COMPRESS_SCRIPT, "--input", str(model_dir), "--output", str(archive_path)
    )
    assert compress_proc.returncode == 0, compress_proc.stderr
    assert archive_path.exists()

    metadata_path = archive_path.with_suffix(archive_path.suffix + ".metadata.json")
    assert metadata_path.exists()
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["file_count"] == 4

    decompress_proc = run_script(
        DECOMPRESS_SCRIPT, "--input", str(archive_path), "--output", str(restored_dir)
    )
    assert decompress_proc.returncode == 0, decompress_proc.stderr

    original_files = {
        p.relative_to(model_dir).as_posix(): p.read_bytes()
        for p in model_dir.rglob("*")
        if p.is_file()
    }
    restored_files = {
        p.relative_to(restored_dir).as_posix(): p.read_bytes()
        for p in restored_dir.rglob("*")
        if p.is_file() and p.name != "baseline_restore_metadata.json"
    }

    assert restored_files == original_files


def test_compress_missing_input_directory_fails_clearly(tmp_path):
    proc = run_script(
        COMPRESS_SCRIPT,
        "--input",
        str(tmp_path / "does-not-exist"),
        "--output",
        str(tmp_path / "out.tar.gz"),
    )
    assert proc.returncode != 0
    assert "не найдена" in proc.stderr or "FileNotFoundError" in proc.stderr


def test_decompress_missing_archive_fails_clearly(tmp_path):
    proc = run_script(
        DECOMPRESS_SCRIPT,
        "--input",
        str(tmp_path / "missing.tar.gz"),
        "--output",
        str(tmp_path / "restored"),
    )
    assert proc.returncode != 0
    assert "не найден" in proc.stderr or "FileNotFoundError" in proc.stderr
