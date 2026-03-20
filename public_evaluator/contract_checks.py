from __future__ import annotations

from pathlib import Path


def require_existing_file(path: Path, description: str) -> None:
    """Убеждается, что указанный путь существует и является файлом.
    
    Args:
        path (Path): Путь для проверки.
        description (str): Человекочитаемое описание файла для сообщения об ошибке.
        
    Raises:
        FileNotFoundError: Если файл не найден или является директорией.
    """
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"{description} не найден: {path}")


def require_existing_directory(path: Path, description: str) -> None:
    """Убеждается, что указанный путь существует и является директорией.
    
    Args:
        path (Path): Путь для проверки.
        description (str): Человекочитаемое описание директории для сообщения об ошибке.
        
    Raises:
        FileNotFoundError: Если директория не найдена или является файлом.
    """
    if not path.exists() or not path.is_dir():
        raise FileNotFoundError(f"{description} не найдена: {path}")


def require_solution_contract(solution_dir: Path) -> tuple[Path, Path]:
    """Проверяет наличие обязательных скриптов compress.py и decompress.py в решении.
    
    Args:
        solution_dir (Path): Директория с решением участника.
        
    Returns:
        tuple[Path, Path]: Абсолютные пути к скриптам (compress_script, decompress_script).
        
    Raises:
        FileNotFoundError: Если один из обязательных скриптов отсутствует.
    """
    compress_script = solution_dir / "compress.py"
    decompress_script = solution_dir / "decompress.py"
    require_existing_file(compress_script, "точка входа compress")
    require_existing_file(decompress_script, "точка входа decompress")
    return compress_script, decompress_script


def ensure_restored_model_not_empty(restored_dir: Path) -> None:
    """Убеждается, что восстановленная директория содержит хотя бы один файл.
    
    Args:
        restored_dir (Path): Путь к восстановленной модели.
        
    Raises:
        FileNotFoundError: Если директория не существует.
        ValueError: Если директория пуста.
    """
    require_existing_directory(restored_dir, "директория восстановленной модели")
    if not any(path.is_file() for path in restored_dir.rglob("*")):
        raise ValueError(f"директория восстановленной модели пуста: {restored_dir}")
