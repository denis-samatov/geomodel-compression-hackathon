#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import tarfile
from pathlib import Path, PurePosixPath, PureWindowsPath


def parse_args() -> argparse.Namespace:
    """Определяет и парсит аргументы командной строки декомпрессора.
    
    Returns:
        argparse.Namespace: Разобранные аргументы с путями к аргументам `--input` (архив) и `--output` (католог для распаковки).
    """
    parser = argparse.ArgumentParser(description="Базовый декомпрессор: распаковывает архив tar.gz.")
    parser.add_argument("--input", required=True, help="Путь к сжатому артефакту.")
    parser.add_argument("--output", required=True, help="Путь к директории восстановленной модели.")
    return parser.parse_args()


def extract_regular_files(archive: tarfile.TarFile, output_dir: Path) -> int:
    """Restore baseline file entries without trusting paths or archive links."""
    members = archive.getmembers()
    seen: set[str] = set()

    # Check every entry before writing so an invalid archive leaves no partial model.
    for member in members:
        name = member.name
        parts = name.split("/")
        if (
            not member.isfile()
            or not name
            or "\\" in name
            or PurePosixPath(name).is_absolute()
            or PureWindowsPath(name).drive
            or any(part in {"", ".", ".."} for part in parts)
            or name in seen
        ):
            raise ValueError(f"недопустимая запись архива: {name!r}")
        target = output_dir.joinpath(*parts)
        if not target.resolve().is_relative_to(output_dir):
            raise ValueError(f"путь архива выходит за пределы каталога: {name!r}")
        seen.add(name)

    for member in members:
        target = output_dir.joinpath(*member.name.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        source = archive.extractfile(member)
        if source is None:
            raise ValueError(f"невозможно прочитать файл архива: {member.name!r}")
        with source, target.open("wb") as destination:
            shutil.copyfileobj(source, destination)

    return len(members)


def main() -> int:
    """Основная функция декомпрессора (распаковывает tar.gz архив).
    
    Считывает переданный архив, проверяет все пути и типы записей,
    восстанавливает обычные файлы и сохраняет метаданные модели.
    
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
        file_count = extract_regular_files(archive, output_dir)

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
