from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FileRecord:
    relative_path: str
    size_bytes: int
    sha256: str


def hash_file(path: Path) -> str:
    """Считает SHA-256 хэш файла потоковым чтением.
    
    Args:
        path (Path): Путь к файлу.
        
    Returns:
        str: Шестнадцатеричное представление хэша файла.
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_manifest(root: Path) -> dict[str, FileRecord]:
    """Строит дерево файлов (манифест) с хэшами и размерами для директории.
    
    Args:
        root (Path): Корневая папка модели.
        
    Returns:
        dict[str, FileRecord]: Словарь, где ключ - относительный путь, а значение - FileRecord.
    """
    manifest: dict[str, FileRecord] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            relative = path.relative_to(root).as_posix()
            manifest[relative] = FileRecord(
                relative_path=relative,
                size_bytes=path.stat().st_size,
                sha256=hash_file(path),
            )
    return manifest


def total_size_bytes(paths: list[Path]) -> int:
    """Считает суммарный физический размер списка файлов в байтах.
    
    Args:
        paths (list[Path]): Список файлов.
        
    Returns:
        int: Общий размер в байтах.
    """
    return sum(path.stat().st_size for path in paths if path.exists() and path.is_file())


def compression_ratio(model_size_bytes: int, archive_size_bytes: int) -> float:
    """Считает коэффициент сжатия (Ratio).
    
    Args:
        model_size_bytes (int): Исходный размер модели.
        archive_size_bytes (int): Размер артефакта после сжатия.
        
    Returns:
        float: Коэффициент (>= 1.0 при успешном сжатии).
    """
    if archive_size_bytes <= 0:
        return 0.0
    return model_size_bytes / archive_size_bytes


def structure_recall(original: dict[str, FileRecord], restored: dict[str, FileRecord]) -> float:
    """Считает долю успешно восстановленных файлов (по путям).
    
    Args:
        original (dict[str, FileRecord]): Манифест оригинальной модели.
        restored (dict[str, FileRecord]): Манифест восстановленной модели.
        
    Returns:
        float: Доля (от 0.0 до 1.0).
    """
    if not original:
        return 0.0
    original_paths = set(original)
    restored_paths = set(restored)
    return len(original_paths & restored_paths) / len(original_paths)


def compare_files_with_tolerance(original_path: Path, restored_path: Path, rel_tol: float = 1e-5) -> bool:
    """Сравнивает два файла пословно. Если оба токена являются числами, сравнивает их с допуском."""
    try:
        with original_path.open("r", encoding="utf-8") as f1, restored_path.open("r", encoding="utf-8") as f2:
            for line1, line2 in zip(f1, f2):
                words1 = line1.split()
                words2 = line2.split()
                if len(words1) != len(words2):
                    return False
                for w1, w2 in zip(words1, words2):
                    if w1 == w2:
                        continue
                    try:
                        f_w1 = float(w1)
                        f_w2 = float(w2)
                        if not math.isclose(f_w1, f_w2, rel_tol=rel_tol):
                            return False
                    except ValueError:
                        return False
            if any(f1) or any(f2):
                return False
        return True
    except UnicodeDecodeError:
        return False


def exact_match_ratio(
    original: dict[str, FileRecord],
    restored: dict[str, FileRecord],
    original_root: Path,
    restored_root: Path
) -> float:
    """Считает взвешенную (по байтам) долю идеально восстановленных файлов по SHA-256 или по float-tolerance.
    
    Args:
        original (dict[str, FileRecord]): Манифест оригинала.
        restored (dict[str, FileRecord]): Манифест после декомпрессора.
        original_root (Path): Корневая папка оригинальной модели.
        restored_root (Path): Корневая папка восстановленной модели.
        
    Returns:
        float: Взвешенная доля совпадения (от 0.0 до 1.0).
    """
    if not original:
        return 0.0
    matched_bytes = 0
    total_bytes = sum(record.size_bytes for record in original.values())
    for relative_path, original_record in original.items():
        restored_record = restored.get(relative_path)
        if restored_record:
            if restored_record.sha256 == original_record.sha256:
                matched_bytes += original_record.size_bytes
            else:
                original_path = original_root / relative_path
                restored_path = restored_root / relative_path
                if compare_files_with_tolerance(original_path, restored_path):
                    matched_bytes += original_record.size_bytes
    if total_bytes <= 0:
        return 0.0
    return matched_bytes / total_bytes


def score_from_ratio(value: float, *, floor: float = 0.0, ceiling: float = 1.0) -> float:
    """Нормализует значение между [floor, ceiling] в диапазон от 0 до 100 баллов.
    
    Args:
        value (float): Нормализуемое значение.
        floor (float, optional): Нижняя граница (0 баллов). Defaults to 0.0.
        ceiling (float, optional): Верхняя граница (100 баллов). Defaults to 1.0.
        
    Returns:
        float: Начисленные баллы [0.0, 100.0].
    """
    if ceiling <= floor:
        return 0.0
    normalized = (value - floor) / (ceiling - floor)
    normalized = max(0.0, min(1.0, normalized))
    return normalized * 100.0


def compression_score(value: float) -> float:
    """Баллы за сжатие: от 1.0 (0 баллов) до 10.0 (100 баллов).
    
    Args:
        value (float): Коэффициент сжатия.
        
    Returns:
        float: Баллы от 0 до 100.
    """
    # 1x означает отсутствие выгоды от сжатия, 10x и выше дают максимум баллов.
    return score_from_ratio(value, floor=1.0, ceiling=10.0)


def runtime_score(total_seconds: float) -> float:
    """Баллы за скорость: 100 баллов до 60 сек, линейно спадает к 0 при 600 сек.
    
    Args:
        total_seconds (float): Общее время компрессии и декомпресии.
        
    Returns:
        float: Начисленные баллы скорости от 0 до 100.
    """
    # Максимум баллов до 60 секунд, затем линейное снижение до нуля к 600 секундам.
    if total_seconds <= 60.0:
        return 100.0
    if total_seconds >= 600.0:
        return 0.0
    return 100.0 * (1.0 - ((total_seconds - 60.0) / 540.0))


def public_score(
    *,
    compression_ratio_value: float,
    structure_recall_value: float,
    exact_match_ratio_value: float,
    total_runtime_seconds: float,
) -> float:
    """Агрегирует все компоненты (сжатие 40%, структура 25%, байты 25%, скорость 10%).
    
    Args:
        compression_ratio_value (float): Соотношение размеров.
        structure_recall_value (float): Доля восстановленных путей.
        exact_match_ratio_value (float): Доля побитово совпавших файлов.
        total_runtime_seconds (float): Общее время выполнения скриптов.
        
    Returns:
        float: Итоговая комбинированная оценка (Public Score) от 0 до 100 баллов.
    """
    return round(
        (compression_score(compression_ratio_value) * 0.40)
        + (structure_recall_value * 100.0 * 0.25)
        + (exact_match_ratio_value * 100.0 * 0.25)
        + (runtime_score(total_runtime_seconds) * 0.10),
        2,
    )
