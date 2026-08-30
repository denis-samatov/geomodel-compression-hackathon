"""End-to-end round-trip test: evaluate.py --solution-dir baseline.

This automates the exact manual procedure baseline/tests/README.md
describes as sufficient verification ("run compress.py, then decompress.py,
then evaluate.py with --solution-dir baseline"). Since the baseline is a
lossless full tar.gz copy, a correct evaluator run must score it perfectly
on structure and content -- this is also therefore a regression test for
the evaluator itself, not just the baseline.
"""

import json
import subprocess
import sys
from pathlib import Path

EVALUATOR_DIR = Path(__file__).resolve().parent.parent
BASELINE_DIR = EVALUATOR_DIR.parent / "baseline"
EVALUATE_SCRIPT = EVALUATOR_DIR / "evaluate.py"


def make_sample_model(root: Path) -> Path:
    model_dir = root / "model"
    (model_dir / "INCLUDE").mkdir(parents=True)
    (model_dir / "RESULTS").mkdir(parents=True)
    (model_dir / "CASE.DATA").write_text(
        "RUNSPEC\nTITLE\nSample case\nINCLUDE\n'INCLUDE/GRID.INC' /\n", encoding="utf-8"
    )
    (model_dir / "INCLUDE" / "GRID.INC").write_text("GRID\n10 10 5 /\n", encoding="utf-8")
    (model_dir / "RESULTS" / "case.UNRST").write_bytes(bytes(range(256)) * 4)
    return model_dir


def test_baseline_scores_perfectly_via_evaluator(tmp_path):
    model_dir = make_sample_model(tmp_path)
    json_output = tmp_path / "result.json"

    proc = subprocess.run(
        [
            sys.executable,
            str(EVALUATE_SCRIPT),
            "--model",
            str(model_dir),
            "--solution-dir",
            str(BASELINE_DIR),
            "--workdir",
            str(tmp_path / ".evaluator-workdir"),
            "--json-output",
            str(json_output),
        ],
        capture_output=True,
        text=True,
        cwd=str(EVALUATOR_DIR),
        check=False,
    )

    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    assert json_output.exists()

    result = json.loads(json_output.read_text(encoding="utf-8"))
    assert result["valid"] is True

    metrics = result["metrics"]
    assert metrics["structure_integrity"] == 1.0
    assert metrics["exact_match_ratio"] == 1.0
    assert metrics["content_accuracy"] == 1.0
    assert metrics["file_count_original"] == 3
    # decompress.py writes one extra sidecar (baseline_restore_metadata.json),
    # which metrics.py's is_ignored_restored_extra_file() correctly excludes
    # from the structure/content scores asserted above.
    assert metrics["file_count_restored"] == 4
    # A full tar.gz copy of already-small text/binary content won't compress
    # much, but the round trip must still be lossless.
    assert metrics["compression_ratio"] > 0
