#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import tarfile
from pathlib import Path


def parse_args() -> argparse.Namespace:
    """Определяет и парсит аргументы командной строки компрессора.
    
    Returns:
        argparse.Namespace: Разобранные аргументы командной строки с путями к аргументам `--input` и `--output`.
    """
    parser = argparse.ArgumentParser(description="Базовый компрессор: упаковывает всё дерево модели в tar.gz.")
    parser.add_argument("--input", required=True, help="Путь к входной директории модели.")
    parser.add_argument("--output", required=True, help="Путь к выходному архиву.")
    return parser.parse_args()


def main() -> int:
    """Основная функция компрессора (создает tar.gz архив).
    
    Считывает аргументы, обходит входную директорию модели, добавляет все файлы
    в tar.gz архив и сохраняет файл статистики `metadata.json` рядом с архивом.
    
    Returns:
        int: Код возврата 0 при успешном завершении (с выходом из скрипта).
    """
    args = parse_args()
    model_dir = Path(args.input).resolve()
    output_path = Path(args.output).resolve()

    if not model_dir.exists() or not model_dir.is_dir():
        raise FileNotFoundError(f"входная директория модели не найдена: {model_dir}")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    file_count = 0
    with tarfile.open(output_path, "w:gz") as archive:
        for path in sorted(model_dir.rglob("*")):
            if path.is_file():
                archive.add(path, arcname=path.relative_to(model_dir))
                file_count += 1

    metadata = {
        "baseline": "tar-gz-full-copy",
        "input_model": str(model_dir),
        "output_archive": str(output_path),
        "file_count": file_count,
        "archive_size_bytes": output_path.stat().st_size,
    }
    metadata_path = output_path.with_suffix(output_path.suffix + ".metadata.json")
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(json.dumps(metadata, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
