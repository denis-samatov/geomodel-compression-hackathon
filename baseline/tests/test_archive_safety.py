"""Baseline archive round-trip and rejection of unsafe tar entries."""

from __future__ import annotations

import io
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
COMPRESS = ROOT / "baseline" / "compress.py"
DECOMPRESS = ROOT / "baseline" / "decompress.py"


def run_script(script: Path, *args: str | Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script), *map(str, args)],
        capture_output=True,
        text=True,
        check=False,
    )


class ArchiveSafetyTests(unittest.TestCase):
    def test_regular_files_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            model = root / "model"
            (model / "INCLUDE").mkdir(parents=True)
            (model / "MODEL.DATA").write_text("INCLUDE 'INCLUDE/grid.inc'\n")
            (model / "INCLUDE" / "grid.inc").write_text("GRID\n")
            archive = root / "model.tar.gz"
            restored = root / "restored"

            compressed = run_script(COMPRESS, "--input", model, "--output", archive)
            self.assertEqual(compressed.returncode, 0, compressed.stderr)
            decompressed = run_script(
                DECOMPRESS, "--input", archive, "--output", restored
            )
            self.assertEqual(decompressed.returncode, 0, decompressed.stderr)
            self.assertEqual(
                (restored / "MODEL.DATA").read_bytes(),
                (model / "MODEL.DATA").read_bytes(),
            )
            self.assertEqual(
                (restored / "INCLUDE" / "grid.inc").read_bytes(),
                (model / "INCLUDE" / "grid.inc").read_bytes(),
            )

    def test_parent_path_is_rejected_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "unsafe.tar.gz"
            with tarfile.open(archive, "w:gz") as tar:
                for name in ("safe.txt", "../outside.txt"):
                    data = b"test"
                    entry = tarfile.TarInfo(name)
                    entry.size = len(data)
                    tar.addfile(entry, io.BytesIO(data))

            restored = root / "restored"
            result = run_script(DECOMPRESS, "--input", archive, "--output", restored)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((root / "outside.txt").exists())
            self.assertFalse((restored / "safe.txt").exists())

    def test_archive_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "link.tar.gz"
            with tarfile.open(archive, "w:gz") as tar:
                entry = tarfile.TarInfo("link")
                entry.type = tarfile.SYMTYPE
                entry.linkname = "../outside.txt"
                tar.addfile(entry)

            result = run_script(
                DECOMPRESS, "--input", archive, "--output", root / "restored"
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((root / "restored" / "link").exists())

    def test_input_symlink_is_rejected_before_archive_creation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            model = root / "model"
            model.mkdir()
            (model / "MODEL.DATA").write_text("DATA\n")
            try:
                (model / "linked.DATA").symlink_to("MODEL.DATA")
            except (OSError, NotImplementedError):
                self.skipTest("symlinks unavailable on this platform")

            archive = root / "model.tar.gz"
            result = run_script(COMPRESS, "--input", model, "--output", archive)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(archive.exists())

    def test_existing_directory_symlink_cannot_redirect_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "model.tar.gz"
            with tarfile.open(archive, "w:gz") as tar:
                data = b"test"
                entry = tarfile.TarInfo("INCLUDE/grid.inc")
                entry.size = len(data)
                tar.addfile(entry, io.BytesIO(data))

            restored = root / "restored"
            outside = root / "outside"
            restored.mkdir()
            outside.mkdir()
            try:
                (restored / "INCLUDE").symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks unavailable on this platform")

            result = run_script(DECOMPRESS, "--input", archive, "--output", restored)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((outside / "grid.inc").exists())


if __name__ == "__main__":
    unittest.main()


class ArchiveBudgetTests(unittest.TestCase):
    def test_conflicting_file_and_directory_is_rejected_before_writing(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("decoder", DECOMPRESS)
        decoder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(decoder)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            data = io.BytesIO()
            with tarfile.open(fileobj=data, mode="w:gz") as archive:
                for name in ("folder", "folder/file"):
                    entry = tarfile.TarInfo(name)
                    entry.size = 1
                    archive.addfile(entry, io.BytesIO(b"x"))
            data.seek(0)
            restored = root / "out"
            restored.mkdir()
            with tarfile.open(fileobj=data, mode="r:gz") as archive:
                with self.assertRaises(ValueError):
                    decoder.extract_regular_files(archive, restored)
            self.assertFalse(any(restored.iterdir()))

    def test_expanded_budget_rejected_before_writing(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("decoder", DECOMPRESS)
        decoder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(decoder)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            data = io.BytesIO()
            with tarfile.open(fileobj=data, mode="w:gz") as archive:
                entry = tarfile.TarInfo("file")
                entry.size = 3
                archive.addfile(entry, io.BytesIO(b"abc"))
            data.seek(0)
            restored = root / "out"
            restored.mkdir()
            with tarfile.open(fileobj=data, mode="r:gz") as archive:
                with self.assertRaises(ValueError):
                    decoder.extract_regular_files(archive, restored, max_bytes=2)
            self.assertFalse(any(restored.iterdir()))

    def test_baseline_archive_bytes_do_not_depend_on_output_name(self):
        import hashlib
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            model = root / "model"
            model.mkdir()
            (model / "CASE.DATA").write_text("RUNSPEC\n")
            hashes = []
            for name in ("first.tar.gz", "second.tar.gz"):
                output = root / name
                result = run_script(COMPRESS, "--input", model, "--output", output)
                self.assertEqual(result.returncode, 0, result.stderr)
                hashes.append(hashlib.sha256(output.read_bytes()).hexdigest())
            self.assertEqual(hashes[0], hashes[1])
