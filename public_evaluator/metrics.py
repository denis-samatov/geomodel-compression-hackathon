from __future__ import annotations

import hashlib
import math
import posixpath
from dataclasses import dataclass
from pathlib import Path


DECK_TEXT_SUFFIXES = {".data", ".inc", ".grdecl"}

STRUCTURE_ROLE_WEIGHTS = {
    "root_data": 10.0,
    "referenced_include": 8.0,
    "include": 5.0,
    "user": 3.0,
    "root_aux": 2.5,
    "other": 1.5,
    "results": 0.75,
    "snf": 0.5,
    "auxiliary": 0.25,
}

CONTENT_ROLE_WEIGHTS = {
    "root_data": 10.0,
    "referenced_include": 8.5,
    "include": 5.0,
    "user": 3.0,
    "root_aux": 2.0,
    "other": 1.25,
    "results": 0.75,
    "snf": 0.4,
    "auxiliary": 0.1,
}

IGNORED_RESTORED_EXTRA_PREFIXES = (
    "_orchestrator/",
    "logs/",
    "_logs/",
    "_restore_logs/",
    "_restore_artifacts/",
)

IGNORED_RESTORED_EXTRA_FILENAMES = {
    "restore_metadata.json",
    "baseline_restore_metadata.json",
    "decompress.log",
    "restore.log",
}

IGNORED_RESTORED_EXTRA_SUFFIXES = (
    ".metadata.json",
    "_metadata.json",
    "_restore_metadata.json",
)


@dataclass(frozen=True)
class FileRecord:
    relative_path: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class IncludeGraph:
    resolved_edges: frozenset[tuple[str, str]]
    unresolved_edges: tuple[tuple[str, str], ...]
    referenced_targets: frozenset[str]


@dataclass(frozen=True)
class StructureMetricBreakdown:
    recall: float
    precision: float
    path_f1: float
    root_data_score: float
    include_graph_score: float
    tnavigator_score: float | None
    structure_integrity: float


def hash_file(path: Path) -> str:
    """Считает SHA-256 хэш файла потоковым чтением."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_manifest(root: Path) -> dict[str, FileRecord]:
    """Строит описание файлов с хэшами и размерами для директории."""
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
    """Считает суммарный физический размер списка файлов в байтах."""
    return sum(path.stat().st_size for path in paths if path.exists() and path.is_file())


def compression_ratio(model_size_bytes: int, archive_size_bytes: int) -> float:
    """Считает коэффициент сжатия."""
    if archive_size_bytes <= 0:
        return 0.0
    return model_size_bytes / archive_size_bytes


def normalize_posix_path(path_str: str) -> str:
    """Нормализует путь в posix-форму без потери скрытых директорий вроде `.snf`."""
    normalized = posixpath.normpath(path_str.replace("\\", "/"))
    if normalized == ".":
        return ""
    if normalized.startswith("./"):
        return normalized[2:]
    return normalized


def file_role(relative_path: str, referenced_includes: set[str] | None = None) -> str:
    """Классифицирует файл по роли в проекте.

    Веса ролей отражают доменную важность файла для корректности модели.
    Маленькие управляющие файлы `.DATA` и реально используемые `INCLUDE`
    важнее крупных производных `RESULTS` и UI-кэша `.snf`.
    """
    rel = normalize_posix_path(relative_path)
    lower = rel.lower()

    if lower.endswith(".bak") or lower.startswith("_orchestrator/"):
        return "auxiliary"
    if "/" not in rel and lower.endswith(".data"):
        return "root_data"
    if referenced_includes and rel in referenced_includes:
        return "referenced_include"
    if lower.startswith("include/"):
        return "include"
    if lower.startswith("user/"):
        return "user"
    if lower.startswith("results/"):
        return "results"
    if ".snf/" in lower or lower.startswith(".snf/"):
        return "snf"
    if "/" not in rel:
        return "root_aux"
    return "other"


def is_ignored_restored_extra_file(relative_path: str) -> bool:
    """Определяет служебные sidecar-артефакты, которые не считаются частью модели.

    Эти файлы допустимо создавать во время `decompress`, и они не должны
    ухудшать структурную метрику, если отсутствовали в исходной модели.
    Функция намеренно узкая: игнорируются только очевидные restore metadata/logs.
    """
    rel = normalize_posix_path(relative_path)
    lower = rel.lower()
    filename = posixpath.basename(lower)

    if any(lower.startswith(prefix) for prefix in IGNORED_RESTORED_EXTRA_PREFIXES):
        return True
    if filename in IGNORED_RESTORED_EXTRA_FILENAMES:
        return True
    if filename.endswith(IGNORED_RESTORED_EXTRA_SUFFIXES):
        return True
    if "/" not in rel and filename.endswith(".log"):
        return True
    return False


def structure_path_weight(relative_path: str, referenced_includes: set[str] | None = None) -> float:
    """Возвращает вес файла для структурной оценки."""
    return STRUCTURE_ROLE_WEIGHTS[file_role(relative_path, referenced_includes)]


def normalized_size_factor(size_bytes: int) -> float:
    """Сглаживает влияние размера файла.

    Используется логарифмическая шкала, чтобы гигантские `RESULTS` не могли
    доминировать над критичными, но небольшими управляющими файлами модели.
    """
    return max(1.0, math.log10(max(size_bytes, 1) + 10))


def content_weight(record: FileRecord, referenced_includes: set[str] | None = None) -> float:
    """Возвращает вес файла для содержательной метрики."""
    role_weight = CONTENT_ROLE_WEIGHTS[file_role(record.relative_path, referenced_includes)]
    return role_weight * normalized_size_factor(record.size_bytes)


def harmonic_mean(precision: float, recall: float) -> float:
    """Считает гармоническое среднее precision/recall."""
    if precision <= 0.0 or recall <= 0.0:
        return 0.0
    return 2.0 * precision * recall / (precision + recall)


def strip_deck_comments(text: str) -> str:
    """Удаляет deck-комментарии `-- ...` построчно."""
    cleaned_lines = []
    for line in text.splitlines():
        cleaned = line.split("--", 1)[0].strip()
        if cleaned:
            cleaned_lines.append(cleaned)
    return "\n".join(cleaned_lines)


def tokenize_text_file(path: Path) -> list[str] | None:
    """Преобразует текстовый файл в токены, игнорируя несущественное форматирование."""
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None

    if path.suffix.lower() in DECK_TEXT_SUFFIXES:
        normalized = strip_deck_comments(text)
    else:
        normalized = "\n".join(line.strip() for line in text.splitlines() if line.strip())

    if not normalized:
        return []
    return normalized.split()


def compare_files_with_tolerance(
    original_path: Path,
    restored_path: Path,
    rel_tol: float = 1e-5,
    abs_tol: float = 1e-8,
) -> bool:
    """Сравнивает текстовые файлы по токенам с допуском на float.

    Проверка устойчивее к форматированию, комментариям и переносам строк,
    чем построчное сравнение, но остается строгой к порядку токенов и строковым значениям.
    """
    original_tokens = tokenize_text_file(original_path)
    restored_tokens = tokenize_text_file(restored_path)
    if original_tokens is None or restored_tokens is None:
        return False
    if len(original_tokens) != len(restored_tokens):
        return False

    for left_token, right_token in zip(original_tokens, restored_tokens):
        if left_token == right_token:
            continue
        try:
            left_value = float(left_token)
            right_value = float(right_token)
        except ValueError:
            return False
        if not math.isclose(left_value, right_value, rel_tol=rel_tol, abs_tol=abs_tol):
            return False
    return True


def resolve_include_target(source_relative_path: str, raw_target: str, available_paths: set[str]) -> str:
    """Разрешает путь в `INCLUDE`, поддерживая model-root и source-relative ссылки."""
    cleaned_target = raw_target.strip().strip("'\"")
    if not cleaned_target:
        return ""

    source_dir = posixpath.dirname(source_relative_path)
    candidates = [
        normalize_posix_path(cleaned_target),
        normalize_posix_path(posixpath.join(source_dir, cleaned_target)),
    ]

    unique_candidates: list[str] = []
    for candidate in candidates:
        if candidate and candidate not in unique_candidates:
            unique_candidates.append(candidate)

    for candidate in unique_candidates:
        if candidate in available_paths:
            return candidate
    return unique_candidates[0] if unique_candidates else ""


def extract_include_targets(tokens: list[str], source_relative_path: str, available_paths: set[str]) -> list[str]:
    """Извлекает цели `INCLUDE` из deck-токенов."""
    targets: list[str] = []
    for index, token in enumerate(tokens):
        if token.upper() != "INCLUDE":
            continue
        if index + 1 >= len(tokens):
            continue
        candidate = tokens[index + 1]
        if candidate == "/":
            continue
        resolved = resolve_include_target(source_relative_path, candidate, available_paths)
        if resolved:
            targets.append(resolved)
    return targets


def build_include_graph(root: Path) -> IncludeGraph:
    """Строит граф связей `source -> target` по директивам `INCLUDE`."""
    available_paths = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
    }

    resolved_edges: set[tuple[str, str]] = set()
    unresolved_edges: list[tuple[str, str]] = []
    referenced_targets: set[str] = set()

    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in DECK_TEXT_SUFFIXES:
            continue

        tokens = tokenize_text_file(path)
        if tokens is None:
            continue

        source_relative_path = path.relative_to(root).as_posix()
        for target in extract_include_targets(tokens, source_relative_path, available_paths):
            edge = (source_relative_path, target)
            if target in available_paths:
                resolved_edges.add(edge)
                referenced_targets.add(target)
            else:
                unresolved_edges.append(edge)

    return IncludeGraph(
        resolved_edges=frozenset(resolved_edges),
        unresolved_edges=tuple(unresolved_edges),
        referenced_targets=frozenset(referenced_targets),
    )


def structure_recall(
    original: dict[str, FileRecord],
    restored: dict[str, FileRecord],
    referenced_includes: set[str] | None = None,
) -> float:
    """Считает weighted recall по путям, а не простой счетчик файлов."""
    if not original:
        return 0.0

    total_weight = sum(
        structure_path_weight(relative_path, referenced_includes)
        for relative_path in original
    )
    matched_weight = sum(
        structure_path_weight(relative_path, referenced_includes)
        for relative_path in original
        if relative_path in restored
    )
    if total_weight <= 0.0:
        return 0.0
    return matched_weight / total_weight


def structure_precision(
    original: dict[str, FileRecord],
    restored: dict[str, FileRecord],
    referenced_includes: set[str] | None = None,
) -> float:
    """Считает weighted precision по путям, чтобы штрафовать лишние файлы."""
    if not restored:
        return 0.0

    effective_restored_paths = [
        relative_path
        for relative_path in restored
        if not (
            relative_path not in original
            and is_ignored_restored_extra_file(relative_path)
        )
    ]

    total_weight = sum(
        structure_path_weight(relative_path, referenced_includes)
        for relative_path in effective_restored_paths
    )
    matched_weight = sum(
        structure_path_weight(relative_path, referenced_includes)
        for relative_path in effective_restored_paths
        if relative_path in original
    )
    if total_weight <= 0.0:
        return 0.0
    return matched_weight / total_weight


def root_data_score(original: dict[str, FileRecord], restored: dict[str, FileRecord]) -> float:
    """Проверяет наличие корневых `.DATA` файлов."""
    original_root_data = {
        relative_path
        for relative_path in original
        if file_role(relative_path) == "root_data"
    }
    if not original_root_data:
        return 0.0

    restored_root_data = {
        relative_path
        for relative_path in restored
        if file_role(relative_path) == "root_data"
    }
    return len(original_root_data & restored_root_data) / len(original_root_data)


def include_edge_weight(edge: tuple[str, str], referenced_includes: set[str]) -> float:
    """Вес связи `INCLUDE` задается важностью целевого файла."""
    _, target = edge
    return structure_path_weight(target, referenced_includes)


def include_graph_score(
    original_graph: IncludeGraph,
    restored_graph: IncludeGraph,
    referenced_includes: set[str],
) -> float:
    """Считает F1 по графу `INCLUDE`, включая штраф за неразрешенные ссылки."""
    if not original_graph.resolved_edges:
        has_restored_include_activity = bool(
            restored_graph.resolved_edges or restored_graph.unresolved_edges
        )
        return 0.0 if has_restored_include_activity else 1.0

    original_total = sum(
        include_edge_weight(edge, referenced_includes)
        for edge in original_graph.resolved_edges
    )
    matched_recall_weight = sum(
        include_edge_weight(edge, referenced_includes)
        for edge in original_graph.resolved_edges
        if edge in restored_graph.resolved_edges
    )
    recall = matched_recall_weight / original_total if original_total > 0.0 else 0.0

    restored_total = sum(
        include_edge_weight(edge, referenced_includes)
        for edge in restored_graph.resolved_edges
    )
    unresolved_penalty = sum(
        structure_path_weight(target, referenced_includes)
        for _, target in restored_graph.unresolved_edges
    )
    matched_precision_weight = sum(
        include_edge_weight(edge, referenced_includes)
        for edge in restored_graph.resolved_edges
        if edge in original_graph.resolved_edges
    )
    precision_denominator = restored_total + unresolved_penalty
    precision = (
        matched_precision_weight / precision_denominator
        if precision_denominator > 0.0
        else 0.0
    )
    return harmonic_mean(precision, recall)


def tnavigator_status_score(final_status: str | None) -> float | None:
    """Преобразует статус tNavigator в числовую оценку структуры."""
    if final_status is None:
        return None
    mapping = {
        "success": 1.0,
        "success_with_warnings": 0.9,
        "needs_review": 0.5,
        "failed": 0.0,
    }
    return mapping.get(final_status, 0.0)


def compute_structure_metrics(
    original: dict[str, FileRecord],
    restored: dict[str, FileRecord],
    original_root: Path,
    restored_root: Path,
    tnavigator_final_status: str | None = None,
) -> StructureMetricBreakdown:
    """Считает составную структуру проекта.

    Структура должна учитывать:
    - важность файлов по ролям;
    - штраф за лишние пути;
    - сохранность корневого `.DATA`;
    - корректность графа `INCLUDE`;
    - при наличии исполняемой проверки — успешность чтения в tNavigator.
    """
    original_graph = build_include_graph(original_root)
    restored_graph = build_include_graph(restored_root)
    referenced_includes = set(original_graph.referenced_targets)

    recall = structure_recall(original, restored, referenced_includes)
    precision = structure_precision(original, restored, referenced_includes)
    path_f1 = harmonic_mean(precision, recall)
    root_score = root_data_score(original, restored)
    include_score = include_graph_score(original_graph, restored_graph, referenced_includes)
    base_structure = (path_f1 * 0.50) + (root_score * 0.20) + (include_score * 0.30)

    tnavigator_score = tnavigator_status_score(tnavigator_final_status)
    structure_integrity_value = (
        (base_structure * 0.70) + (tnavigator_score * 0.30)
        if tnavigator_score is not None
        else base_structure
    )

    return StructureMetricBreakdown(
        recall=recall,
        precision=precision,
        path_f1=path_f1,
        root_data_score=root_score,
        include_graph_score=include_score,
        tnavigator_score=tnavigator_score,
        structure_integrity=structure_integrity_value,
    )


def exact_match_ratio(
    original: dict[str, FileRecord],
    restored: dict[str, FileRecord],
    original_root: Path,
    restored_root: Path,
) -> float:
    """Считает единую метрику точности восстановления содержимого.

    Вклад файла определяется его ролью в проекте и сглаженным размером.
    Это не дает большим `RESULTS` или `.snf` доминировать над критичными
    управляющими файлами `.DATA` и реально используемыми `INCLUDE`.
    """
    if not original:
        return 0.0

    include_graph = build_include_graph(original_root)
    referenced_includes = set(include_graph.referenced_targets)

    matched_weight = 0.0
    total_weight = 0.0
    for relative_path, original_record in original.items():
        weight = content_weight(original_record, referenced_includes)
        total_weight += weight

        restored_record = restored.get(relative_path)
        if restored_record is None:
            continue

        if restored_record.sha256 == original_record.sha256:
            matched_weight += weight
            continue

        original_path = original_root / relative_path
        restored_path = restored_root / relative_path
        if compare_files_with_tolerance(original_path, restored_path):
            matched_weight += weight

    if total_weight <= 0.0:
        return 0.0
    return matched_weight / total_weight


def score_from_ratio(value: float, *, floor: float = 0.0, ceiling: float = 1.0) -> float:
    """Нормализует значение между [floor, ceiling] в диапазон от 0 до 100 баллов."""
    if ceiling <= floor:
        return 0.0
    normalized = (value - floor) / (ceiling - floor)
    normalized = max(0.0, min(1.0, normalized))
    return normalized * 100.0


def compression_score(value: float) -> float:
    """Баллы за сжатие по логарифму коэффициента.

    Каждое удвоение качества сжатия должно давать сопоставимую прибавку,
    поэтому используется логарифмическая шкала. Порог насыщения выбран на уровне `8x`.
    """
    if value <= 1.0:
        return 0.0
    return score_from_ratio(math.log2(value), floor=0.0, ceiling=3.0)


def runtime_score(total_seconds: float) -> float:
    """Баллы за скорость с мягким логарифмическим спадом.

    Для публичной оценки скорость важна, но она не должна доминировать
    над корректностью восстановления. Полный балл дается до 120 секунд,
    ноль — при часу и более.
    """
    if total_seconds <= 120.0:
        return 100.0
    if total_seconds >= 3600.0:
        return 0.0

    min_log = math.log(120.0)
    max_log = math.log(3600.0)
    current_log = math.log(total_seconds)
    normalized = (current_log - min_log) / (max_log - min_log)
    normalized = max(0.0, min(1.0, normalized))
    return 100.0 * (1.0 - normalized)


def public_score(
    *,
    compression_ratio_value: float,
    structure_integrity_value: float,
    exact_match_ratio_value: float,
    total_runtime_seconds: float,
) -> float:
    """Агрегирует public score с приоритетом качества восстановления.

    В публичном контуре качество модели важнее агрессивного сжатия и времени.
    Поэтому структура и точность доминируют, а runtime имеет ограниченный вес.
    """
    return round(
        (compression_score(compression_ratio_value) * 0.20)
        + (structure_integrity_value * 100.0 * 0.35)
        + (exact_match_ratio_value * 100.0 * 0.40)
        + (runtime_score(total_runtime_seconds) * 0.05),
        2,
    )
