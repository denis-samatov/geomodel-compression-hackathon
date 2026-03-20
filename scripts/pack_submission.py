#!/usr/bin/env python3
from __future__ import annotations

import argparse
import tarfile
from pathlib import Path


EXCLUDE_NAMES = {".git", "__pycache__", ".pytest_cache", ".DS_Store"}


def parse_args() -> argparse.Namespace:
    """Определяет и парсит аргументы командной строки.
    
    Returns:
        argparse.Namespace: Разобранные аргументы командной строки.
    """
    parser = argparse.ArgumentParser(description="Упаковывает директорию решения участника в архив tar.gz.")
    parser.add_argument("--input-dir", required=True, help="Путь к директории решения участника.")
    parser.add_argument("--output", required=True, help="Путь к выходному архиву tar.gz.")
    return parser.parse_args()


def should_include(path: Path) -> bool:
    """Проверяет, нужно ли включать файл в архив (исключая кэш, гит и т.д.).
    
    Args:
        path (Path): Путь к проверяемому файлу/директории.
        
    Returns:
        bool: True, если файл следует запаковать, иначе False.
    """
    return not any(part in EXCLUDE_NAMES for part in path.parts)


def main() -> int:
    """Формирует tar.gz архив с решением участника.
    
    Returns:
        int: Возвращает 0 при успехе.
    """
    args = parse_args()
    input_dir = Path(args.input_dir).resolve()
    output_path = Path(args.output).resolve()
    if not input_dir.exists() or not input_dir.is_dir():
        raise FileNotFoundError(f"входная директория не найдена: {input_dir}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output_path, "w:gz") as archive:
        for path in sorted(input_dir.rglob("*")):
            if path.is_file() and should_include(path):
                archive.add(path, arcname=path.relative_to(input_dir))
    print(f"Сабмит упакован: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
