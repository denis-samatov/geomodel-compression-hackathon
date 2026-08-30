"""Unit tests for public_evaluator/metrics.py.

This module determines every participant's public score and previously had
no test coverage at all -- these tests exercise the pure scoring functions
plus a synthetic end-to-end structure/content comparison.
"""

from metrics import (
    build_manifest,
    compare_files_with_tolerance,
    compression_ratio,
    compression_score,
    exact_match_ratio,
    file_role,
    harmonic_mean,
    runtime_score,
)


def test_compression_ratio_basic():
    assert compression_ratio(model_size_bytes=1000, archive_size_bytes=250) == 4.0


def test_compression_ratio_zero_archive_is_safe():
    assert compression_ratio(model_size_bytes=1000, archive_size_bytes=0) == 0.0


def test_harmonic_mean_normal_case():
    assert harmonic_mean(1.0, 1.0) == 1.0
    assert round(harmonic_mean(0.5, 1.0), 4) == round(2 * 0.5 * 1.0 / 1.5, 4)


def test_harmonic_mean_zero_precision_or_recall():
    assert harmonic_mean(0.0, 1.0) == 0.0
    assert harmonic_mean(1.0, 0.0) == 0.0


def test_file_role_classification():
    assert file_role("CASE.DATA") == "root_data"
    assert file_role("INCLUDE/GRID.INC") == "include"
    assert file_role("RESULTS/case.UNRST") == "results"
    assert file_role(".snf/cache.bin") == "snf"
    assert file_role("notes.txt.bak") == "auxiliary"
    assert file_role("some/nested/other.txt") == "other"


def test_file_role_referenced_include_takes_priority():
    referenced = {"deck/local.inc"}
    assert file_role("deck/local.inc", referenced) == "referenced_include"


def test_compare_files_with_tolerance_exact_match(tmp_path):
    a = tmp_path / "a.data"
    b = tmp_path / "b.data"
    a.write_text("PORO\n0.2 0.2 0.3 /\n", encoding="utf-8")
    b.write_text("PORO\n0.2 0.2 0.3 /\n", encoding="utf-8")
    assert compare_files_with_tolerance(a, b) is True


def test_compare_files_with_tolerance_within_float_tolerance(tmp_path):
    a = tmp_path / "a.data"
    b = tmp_path / "b.data"
    a.write_text("PORO\n0.20000001 /\n", encoding="utf-8")
    b.write_text("PORO\n0.20000002 /\n", encoding="utf-8")
    assert compare_files_with_tolerance(a, b) is True


def test_compare_files_with_tolerance_real_mismatch(tmp_path):
    a = tmp_path / "a.data"
    b = tmp_path / "b.data"
    a.write_text("PORO\n0.2 /\n", encoding="utf-8")
    b.write_text("PORO\n0.5 /\n", encoding="utf-8")
    assert compare_files_with_tolerance(a, b) is False


def test_compare_files_with_tolerance_ignores_deck_comments(tmp_path):
    a = tmp_path / "a.data"
    b = tmp_path / "b.data"
    a.write_text("PORO -- original comment\n0.2 /\n", encoding="utf-8")
    b.write_text("PORO -- a totally different comment\n0.2 /\n", encoding="utf-8")
    assert compare_files_with_tolerance(a, b) is True


def test_exact_match_ratio_identical_trees(tmp_path):
    original = tmp_path / "original"
    restored = tmp_path / "restored"
    for root in (original, restored):
        root.mkdir()
        (root / "CASE.DATA").write_text("RUNSPEC\nTITLE\nX\n", encoding="utf-8")

    manifest_original = build_manifest(original)
    manifest_restored = build_manifest(restored)
    ratio = exact_match_ratio(manifest_original, manifest_restored, original, restored)
    assert ratio == 1.0


def test_exact_match_ratio_missing_file_is_penalized(tmp_path):
    original = tmp_path / "original"
    restored = tmp_path / "restored"
    original.mkdir()
    restored.mkdir()
    (original / "CASE.DATA").write_text("RUNSPEC\n", encoding="utf-8")
    # restored/ is intentionally left empty -- nothing was recovered.

    manifest_original = build_manifest(original)
    manifest_restored = build_manifest(restored)
    ratio = exact_match_ratio(manifest_original, manifest_restored, original, restored)
    assert ratio == 0.0


def test_compression_score_below_1x_is_zero():
    assert compression_score(1.0) == 0.0
    assert compression_score(0.5) == 0.0


def test_compression_score_increases_with_ratio():
    assert compression_score(4.0) < compression_score(8.0)


def test_runtime_score_full_marks_under_threshold():
    assert runtime_score(60.0) == 100.0
    assert runtime_score(120.0) == 100.0


def test_runtime_score_zero_at_and_beyond_one_hour():
    assert runtime_score(3600.0) == 0.0
    assert runtime_score(7200.0) == 0.0
