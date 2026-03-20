#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import tarfile
from pathlib import Path


def parse_args() -> argparse.Namespace:
    """Определяет и парсит аргументы командной строки декомпрессора.
    
    Returns:
        argparse.Namespace: Разобранные аргументы с путями к аргументам `--input` (архив) и `--output` (католог для распаковки).
    """
    parser = argparse.ArgumentParser(description="Базовый декомпрессор: распаковывает архив tar.gz.")
    parser.add_argument("--input", required=True, help="Путь к сжатому артефакту.")
    parser.add_argument("--output", required=True, help="Путь к директории восстановленной модели.")
    return parser.parse_args()


def main() -> int:
    """Основная функция декомпрессора (распаковывает tar.gz архив).
    
    Считывает переданный архив, безопасно распаковывает его структуру
    в целевую папку (используя filter='data' для современных версий Python) 
    и создает файл с метаданными о восстановленной модели.
    
    Returns:
        int: Код возврата 0 при успешном завершении (с выходом из скрипта).
    """
    args = parse_args()
    archive_path = Path(args.input).resolve()
    output_dir = Path(args.output).resolve()

    if not archive_path.exists() or not archive_path.is_file():
        raise FileNotFoundError(f"архив не найден: {archive_path}")

    output_dir.mkdir(parents=True, exist_ok=True)

    with tarfile.open(archive_path, "r:gz") as archive:
        try:
            archive.extractall(output_dir, filter="data")
        except TypeError:
            archive.extractall(output_dir)

    file_count = len([path for path in output_dir.rglob("*") if path.is_file()])
    metadata = {
        "baseline": "tar-gz-full-copy",
        "input_archive": str(archive_path),
        "restored_model": str(output_dir),
        "file_count": file_count,
    }
    (output_dir / "baseline_restore_metadata.json").write_text(
        json.dumps(metadata, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(metadata, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
