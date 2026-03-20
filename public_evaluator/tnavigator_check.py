"""
Модуль для проверки геологических моделей через tNavigator.

Этот модуль предоставляет функциональность для запуска tNavigator на геологических моделях
и анализа результатов выполнения. Включает парсинг логов, определение статуса выполнения
и сбор информации об ошибках, предупреждениях и результирующих файлах.
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional


# Флаги экспорта по умолчанию для запуска tNavigator в режиме smoke/full
DEFAULT_EXPORT_FLAGS = [
    "--ecl-egrid",
    "--ecl-init",
    "--ecl-rsm",
    "--ecl-unrst",
    "--ecl-unsmry",
    "--ecl-smspec",
]


# Регулярное выражение для извлечения статуса чтения модели из stdout
# Ищет количество ошибок, проблем и предупреждений в блоке MODEL READ STATUS
MODEL_READ_STATUS_RE = re.compile(
    r"MODEL READ STATUS.*?"
    r"Errors\s+(\d+).*?"
    r"Problems\s+(\d+).*?"
    r"Warnings\s+(\d+)",
    re.DOTALL | re.IGNORECASE,
)

# Регулярное выражение для извлечения общего статуса tNavigator
# Ищет строку в формате "General info: Status=<статус>"
GENERAL_STATUS_RE = re.compile(
    r"General info:\s+Status=([A-Z_]+)",
    re.IGNORECASE,
)

RESULT_LOG_ERROR_SUMMARY_RE = re.compile(
    r"Error summary:\s*"
    r"Warnings\s+(\d+)\s*"
    r"Problems\s+(\d+)\s*"
    r"Errors\s+(\d+)",
    re.IGNORECASE | re.DOTALL,
)

RESULT_LOG_TOTAL_ELAPSED_RE = re.compile(
    r"Total elapsed\s*=\s*([0-9:.]+)",
    re.IGNORECASE,
)

# Регулярное выражение для поиска строк предупреждения в логах
WARN_LINE_RE = re.compile(r"^\s*Warning:", re.IGNORECASE | re.MULTILINE)

# Регулярное выражение для поиска строк ошибок в логах
ERROR_LINE_RE = re.compile(r"^\s*Error:", re.IGNORECASE | re.MULTILINE)


@dataclass
class ModelReadStatus:
    """
    Статус чтения модели из блока MODEL READ STATUS.
    
    Attributes:
        errors (int): Количество ошибок при чтении модели.
        problems (int): Количество проблем при чтении модели.
        warnings (int): Количество предупреждений при чтении модели.
    """
    errors: int
    problems: int
    warnings: int


@dataclass
class TNavigatorCheckSummary:
    """
    Полная сводка по результатам проверки модели через tNavigator.
    
    Attributes:
        timestamp (str): Временная метка выполнения проверки в формате ISO.
        run_mode (str): Режим запуска tNavigator (nosim, smoke, full).
        model_file (str): Полный путь к файлу модели (.data).
        command (list[str]): Полная команда запуска tNavigator.
        cwd (str): Рабочая директория, из которой была запущена команда.
        returncode (Optional[int]): Код возврата процесса (None если timeout).
        model_read_status (Optional[dict[str, int]]): Статус чтения модели в виде словаря.
        general_status (Optional[str]): Общий статус tNavigator (OK, ERROR и т.д.).
        stdout_warning_count (int): Количество строк с предупреждениями в stdout.
        stderr_warning_count (int): Количество строк с предупреждениями в stderr.
        stderr_error_count (int): Количество строк с ошибками в stderr.
        result_files (list[str]): Список путей к результирующим файлам.
        stdout_log (str): Путь к файлу логов stdout.
        stderr_log (str): Путь к файлу логов stderr.
        summary_json (str): Путь к JSON файлу со сводкой.
        final_status (str): Финальный статус выполнения (success, failed и т.д.).
        notes (list[str]): Список примечаний о ходе выполнения проверки.
    """
    timestamp: str
    run_mode: str
    model_file: str
    command: list[str]
    cwd: str
    returncode: Optional[int]
    model_read_status: Optional[dict[str, int]]
    general_status: Optional[str]
    stdout_warning_count: int
    stderr_warning_count: int
    stderr_error_count: int
    result_files: list[str]
    result_log: Optional[dict[str, object]]
    stdout_log: str
    stderr_log: str
    summary_json: str
    final_status: str
    notes: list[str]


@dataclass
class ResultLogSummary:
    log_file: str
    general_status: Optional[str]
    total_elapsed: Optional[str]
    warnings: Optional[int]
    problems: Optional[int]
    errors: Optional[int]
    warning_line_count: int
    error_line_count: int
    final_section_found: bool


def validate_inputs(tnavigator_exe: Path, model_file: Path) -> None:
    """
    Проверяет наличие и корректность входных файлов.
    
    Убеждается, что исполняемый файл tNavigator существует и файл модели существует
    и является обычным файлом (а не директорией).
    
    Args:
        tnavigator_exe (Path): Путь к исполняемому файлу tNavigator.
        model_file (Path): Путь к файлу модели.
        
    Raises:
        FileNotFoundError: Если tNavigator не найден, если модель не найдена или
            если указанный путь к модели является директорией.
    """
    # Проверяем, что исполняемый файл tNavigator существует
    if not tnavigator_exe.exists():
        raise FileNotFoundError(f"исполняемый файл tNavigator не найден: {tnavigator_exe}")
    
    # Проверяем, что файл модели существует
    if not model_file.exists():
        raise FileNotFoundError(f"файл модели не найден: {model_file}")
    
    # Проверяем, что путь к модели действительно является файлом, а не директорией
    if not model_file.is_file():
        raise FileNotFoundError(f"путь к модели не является файлом: {model_file}")


def find_model_file(model_dir: Path, explicit_model_file: Optional[Path] = None) -> Path:
    """
    Находит файл модели (.data) в указанной директории.
    
    Если явно указан файл модели, использует его. В противном случае применяет
    следующую стратегию поиска:
    1. Сначала ищет .data файлы в корне директории.
    2. Если найдено несколько, требует явного указания через параметр.
    3. Если нет, рекурсивно ищет в подпапках.
    4. Требует нахождения ровно одного файла .data.
    
    Args:
        model_dir (Path): Директория модели для поиска.
        explicit_model_file (Optional[Path]): Явно указанный файл модели.
        
    Returns:
        Path: Абсолютный путь к найденному файлу модели.
        
    Raises:
        FileNotFoundError: Если явно указанный файл не существует или если ни один
            файл .data не найден в модели.
        ValueError: Если найдено несколько файлов .data и не указан явно нужный.
    """
    # Если явно указан файл модели, используем его
    if explicit_model_file is not None:
        # Преобразуем относительный путь в абсолютный, если нужно
        candidate = explicit_model_file if explicit_model_file.is_absolute() else model_dir / explicit_model_file
        candidate = candidate.resolve()
        
        # Проверяем, что явно указанный файл существует
        if not candidate.exists():
            raise FileNotFoundError(f"указанный файл модели для tNavigator не найден: {candidate}")
        return candidate

    # Стратегия 1: ищем файлы .data в корне model_dir
    root_candidates = sorted(
        path for path in model_dir.iterdir() if path.is_file() and path.suffix.lower() == ".data"
    )
    
    # Если найден ровно один файл .data в корне, используем его
    if len(root_candidates) == 1:
        return root_candidates[0].resolve()
    
    # Если найдено больше одного файла .data в корне, требуем явного указания
    if len(root_candidates) > 1:
        raise ValueError(
            "в корне модели найдено несколько файлов .DATA; укажите нужный через --tnavigator-model-file"
        )

    # Стратегия 2: рекурсивно ищем файлы .data во всех подпапках
    recursive_candidates = sorted(
        path for path in model_dir.rglob("*") if path.is_file() and path.suffix.lower() == ".data"
    )
    
    # Если не найдено ни одного файла .data
    if not recursive_candidates:
        raise FileNotFoundError(
            f"не удалось найти файл .DATA внутри модели: {model_dir}"
        )
    
    # Если найдено больше одного файла .data в подпапках, требуем явного указания
    if len(recursive_candidates) > 1:
        raise ValueError(
            "в модели найдено несколько файлов .DATA; укажите нужный через --tnavigator-model-file"
        )
    
    # Возвращаем единственный найденный файл .data
    return recursive_candidates[0].resolve()


def build_command(
    tnavigator_exe: Path,
    model_file: Path,
    run_mode: str,
    ignore_lock: bool,
    export_flags: list[str],
) -> list[str]:
    """
    Строит команду для запуска tNavigator с указанными параметрами.
    
    Формирует список аргументов для subprocess.run() в зависимости от режима запуска:
    - nosim: только чтение модели без симуляции
    - smoke: симуляция одного шага с экспортом результатов
    - full: полная симуляция с экспортом результатов
    
    Args:
        tnavigator_exe (Path): Путь к исполняемому файлу tNavigator.
        model_file (Path): Путь к файлу модели.
        run_mode (str): Режим запуска (nosim, smoke, full).
        ignore_lock (bool): Пропустить проверку блокировки файлов.
        export_flags (list[str]): Флаги для экспорта результатов.
        
    Returns:
        list[str]: Список аргументов команды для subprocess.
        
    Raises:
        ValueError: Если указан неподдерживаемый режим запуска.
    """
    # Инициализируем команду с путем к исполняемому файлу
    command = [str(tnavigator_exe)]

    # Добавляем флаги в зависимости от режима запуска
    if run_mode == "nosim":
        # Режим nosim: только чтение модели, без флагов экспорта
        command.append("--nosim")
    elif run_mode == "smoke":
        # Режим smoke: один шаг симуляции с экспортом
        command.append("--stop-step=1")
        command.extend(export_flags)
    elif run_mode == "full":
        # Режим full: полная симуляция с экспортом
        command.extend(export_flags)
    else:
        # Неподдерживаемый режим
        raise ValueError(f"неподдерживаемый режим запуска tNavigator: {run_mode}")

    # Добавляем флаг игнорирования блокировок, если требуется
    if ignore_lock:
        command.append("--ignore-lock")

    # Добавляем путь к файлу модели в конец команды
    command.append(str(model_file))
    return command


def parse_model_read_status(text: str) -> Optional[ModelReadStatus]:
    """
    Парсит статус чтения модели из текста stdout.
    
    Извлекает из stdout блок MODEL READ STATUS с количеством ошибок, проблем
    и предупреждений при чтении модели tNavigator'ом.
    
    Args:
        text (str): Текст stdout от tNavigator.
        
    Returns:
        Optional[ModelReadStatus]: Объект со статусом или None если блок не найден.
    """
    # Ищем блок MODEL READ STATUS в тексте с помощью регулярного выражения
    match = MODEL_READ_STATUS_RE.search(text)
    
    # Если блок не найден, возвращаем None
    if not match:
        return None
    
    # Извлекаем числовые значения из групп регулярного выражения и создаем объект
    return ModelReadStatus(
        errors=int(match.group(1)),
        problems=int(match.group(2)),
        warnings=int(match.group(3)),
    )


def parse_general_status(text: str) -> Optional[str]:
    """
    Парсит общий статус tNavigator из текста stdout.
    
    Извлекает из stdout строку вида "General info: Status=<статус>" и возвращает
    статус в верхнем регистре.
    
    Args:
        text (str): Текст stdout от tNavigator.
        
    Returns:
        Optional[str]: Строка со статусом в верхнем регистре или None если не найдена.
    """
    # Ищем строку с общим статусом с помощью регулярного выражения
    match = GENERAL_STATUS_RE.search(text)
    
    # Возвращаем статус в верхнем регистре или None
    return match.group(1).upper() if match else None


def count_warnings(text: str) -> int:
    """
    Подсчитывает количество строк с предупреждениями в тексте логов.
    
    Считает количество строк, начинающихся со слова "Warning:" (без учета регистра).
    
    Args:
        text (str): Текст логов для анализа.
        
    Returns:
        int: Количество найденных строк с предупреждениями.
    """
    # Находим все совпадения и возвращаем количество
    return len(WARN_LINE_RE.findall(text))


def count_errors(text: str) -> int:
    """
    Подсчитывает количество строк с ошибками в тексте логов.
    
    Считает количество строк, начинающихся со слова "Error:" (без учета регистра).
    
    Args:
        text (str): Текст логов для анализа.
        
    Returns:
        int: Количество найденных строк с ошибками.
    """
    # Находим все совпадения и возвращаем количество
    return len(ERROR_LINE_RE.findall(text))


def find_result_files(case_dir: Path, model_stem: str) -> list[str]:
    """
    Находит результирующие файлы tNavigator в директории RESULTS.
    
    Ищет файлы с расширением .res в директории {case_dir}/RESULTS/{model_stem}
    и возвращает их отсортированные по времени модификации.
    
    Args:
        case_dir (Path): Корневая директория случая (модели).
        model_stem (str): Имя модели без расширения.
        
    Returns:
        list[str]: Список полных путей к найденным результирующим файлам,
                   отсортированный по времени модификации (от старого к новому).
    """
    # Формируем путь к директории с результатами
    results_run_dir = case_dir / "RESULTS" / model_stem
    
    # Если директория с результатами не существует, возвращаем пустой список
    if not results_run_dir.exists():
        return []

    # Ищем все файлы с расширением .res и сортируем по времени модификации
    files = sorted(results_run_dir.glob("result.*"), key=lambda path: path.stat().st_mtime)
    
    # Преобразуем пути в строки и возвращаем
    return [str(path) for path in files]


def find_result_log(case_dir: Path, model_stem: str) -> Optional[Path]:
    """
    Находит основной журнал результатов расчета tNavigator.

    В первую очередь ищет стандартные имена `result.log` и `results.log`
    в каталоге `RESULTS/<model_stem>/`, затем использует рекурсивный поиск
    по каталогу результатов как запасной вариант.

    Args:
        case_dir (Path): Корневая директория модели.
        model_stem (str): Имя модели без расширения.

    Returns:
        Optional[Path]: Путь к найденному журналу или None, если журнал не найден.
    """
    results_run_dir = case_dir / "RESULTS" / model_stem
    direct_candidates = [
        results_run_dir / "result.log",
        results_run_dir / "results.log",
    ]

    for candidate in direct_candidates:
        if candidate.exists() and candidate.is_file():
            return candidate.resolve()

    if results_run_dir.exists():
        recursive_candidates = sorted(
            path
            for path in results_run_dir.rglob("*")
            if path.is_file() and path.name.lower() in {"result.log", "results.log"}
        )
        if recursive_candidates:
            return recursive_candidates[0].resolve()

    return None


def parse_result_log_summary(log_path: Path) -> ResultLogSummary:
    """
    Извлекает ключевые признаки из журнала `result.log`/`results.log`.

    Args:
        log_path (Path): Путь к журналу результатов.

    Returns:
        ResultLogSummary: Разобранная сводка по журналу результатов.
    """
    text = log_path.read_text(encoding="utf-8", errors="ignore")

    general_status_matches = GENERAL_STATUS_RE.findall(text)
    general_status = general_status_matches[-1].upper() if general_status_matches else None

    elapsed_matches = RESULT_LOG_TOTAL_ELAPSED_RE.findall(text)
    total_elapsed = elapsed_matches[-1] if elapsed_matches else None

    error_summary_matches = list(RESULT_LOG_ERROR_SUMMARY_RE.finditer(text))
    last_summary = error_summary_matches[-1] if error_summary_matches else None

    warnings = int(last_summary.group(1)) if last_summary else None
    problems = int(last_summary.group(2)) if last_summary else None
    errors = int(last_summary.group(3)) if last_summary else None

    return ResultLogSummary(
        log_file=str(log_path),
        general_status=general_status,
        total_elapsed=total_elapsed,
        warnings=warnings,
        problems=problems,
        errors=errors,
        warning_line_count=count_warnings(text),
        error_line_count=count_errors(text),
        final_section_found=last_summary is not None,
    )


def derive_final_status(
    returncode: Optional[int],
    model_read_status: Optional[ModelReadStatus],
    general_status: Optional[str],
    stderr_text: str,
    result_log_summary: Optional[ResultLogSummary],
    run_mode: str,
) -> tuple[str, list[str]]:
    """
    Определяет финальный статус выполнения на основе анализа результатов.
    
    Применяет набор правил для определения статуса (success, failed, needs_review и т.д.)
    и возвращает статус вместе со списком примечаний, объясняющих решение.
    
    Правила проверяются в следующем порядке:
    1. Если returncode = None (timeout), статус = failed.
    2. Если returncode != 0, статус = failed.
    3. Если MODEL READ STATUS не найден, статус = needs_review.
    4. Если есть ошибки в MODEL READ STATUS, статус = failed.
    5. Если общий статус != OK, статус = failed.
    6. Если в stderr есть строки с ошибками, статус = failed.
    7. Если есть предупреждения, статус = success_with_warnings.
    8. Иначе, статус = success.
    
    Args:
        returncode (Optional[int]): Код возврата процесса (None если timeout).
        model_read_status (Optional[ModelReadStatus]): Статус чтения модели.
        general_status (Optional[str]): Общий статус tNavigator.
        stderr_text (str): Текст stderr для анализа ошибок и предупреждений.
        result_log_summary (Optional[ResultLogSummary]): Разбор журнала `result.log`.
        run_mode (str): Режим запуска tNavigator.
        
    Returns:
        tuple[str, list[str]]: Кортеж (финальный_статус, список_примечаний).
    """
    # Инициализируем пустой список примечаний
    notes: list[str] = []

    # Проверка 1: процесс не завершился (timeout)
    if returncode is None:
        notes.append("процесс не вернул код завершения")
        return "failed", notes

    # Проверка 2: ненулевой код возврата
    if returncode != 0:
        notes.append(f"ненулевой код завершения: {returncode}")
        return "failed", notes

    # Проверка 3: блок MODEL READ STATUS не найден в stdout
    if model_read_status is None:
        notes.append("блок MODEL READ STATUS не найден в stdout")
        return "needs_review", notes

    # Проверка 4: есть ошибки при чтении модели
    if model_read_status.errors > 0:
        notes.append(f"MODEL READ STATUS errors = {model_read_status.errors}")
        return "failed", notes

    # Проверка 5: общий статус tNavigator не OK
    if general_status and general_status != "OK":
        notes.append(f"общий статус tNavigator не OK: {general_status}")
        return "failed", notes

    # Подсчитываем ошибки и предупреждения в stderr
    stderr_warning_count = count_warnings(stderr_text)
    stderr_error_count = count_errors(stderr_text)

    # Проверка 6: в stderr есть строки с ошибками
    if stderr_error_count > 0:
        notes.append(f"stderr содержит строк с ошибками: {stderr_error_count}")
        return "failed", notes

    if run_mode in {"smoke", "full"} and result_log_summary is None:
        notes.append("после запуска tNavigator не найден файл result.log/results.log")
        return "failed", notes

    if result_log_summary is not None:
        if result_log_summary.general_status and result_log_summary.general_status != "OK":
            notes.append(
                f"статус в result.log не OK: {result_log_summary.general_status}"
            )
            return "failed", notes

        if not result_log_summary.final_section_found and run_mode in {"smoke", "full"}:
            notes.append("в result.log не найден финальный блок Error summary")
            return "failed", notes

        if result_log_summary.errors and result_log_summary.errors > 0:
            notes.append(f"result.log сообщает об ошибках: {result_log_summary.errors}")
            return "failed", notes

        if result_log_summary.problems and result_log_summary.problems > 0:
            notes.append(f"result.log сообщает о проблемах: {result_log_summary.problems}")
            return "failed", notes

    # Проверка 7: есть предупреждения (из MODEL READ STATUS или stderr)
    if (
        model_read_status.warnings > 0
        or stderr_warning_count > 0
        or (
            result_log_summary is not None
            and (
                (result_log_summary.warnings or 0) > 0
                or result_log_summary.warning_line_count > 0
            )
        )
    ):
        notes.append(
            "обнаружены предупреждения: "
            f"model_read={model_read_status.warnings}, "
            f"stderr={stderr_warning_count}, "
            f"result_log={(result_log_summary.warnings if result_log_summary else 0)}"
        )
        return "success_with_warnings", notes

    # Проверка 8: все проверки пройдены успешно
    return "success", notes


def write_text(path: Path, text: str) -> None:
    """
    Записывает текст в файл с кодировкой UTF-8.
    
    Игнорирует ошибки кодирования при работе с текстом, содержащим неправильные символы.
    
    Args:
        path (Path): Путь к файлу для записи.
        text (str): Текст для записи.
    """
    # Записываем текст в файл с кодировкой UTF-8, игнорируя ошибки кодирования
    path.write_text(text, encoding="utf-8", errors="ignore")


def run_tnavigator_check(
    *,
    tnavigator_exe: Path,
    model_dir: Path,
    workdir: Path,
    run_mode: str,
    ignore_lock: bool,
    export_flags: Optional[list[str]] = None,
    timeout_seconds: Optional[int] = None,
    explicit_model_file: Optional[Path] = None,
) -> TNavigatorCheckSummary:
    """
    Запускает проверку модели через tNavigator и собирает результаты.
    
    Главная функция модуля. Выполняет следующие шаги:
    1. Находит файл модели в указанной директории.
    2. Валидирует входные параметры.
    3. Строит команду для запуска tNavigator.
    4. Запускает процесс tNavigator и перехватывает stdout/stderr.
    5. Парсит результаты и определяет финальный статус.
    6. Записывает логи в файлы и сохраняет JSON сводку.
    
    Параметры передаются только ключевыми словами (keyword-only).
    
    Args:
        tnavigator_exe (Path): Путь к исполняемому файлу tNavigator.
        model_dir (Path): Директория с моделью.
        workdir (Path): Директория для вывода логов и результатов.
        run_mode (str): Режим запуска (nosim, smoke, full).
        ignore_lock (bool): Пропустить проверку блокировки файлов.
        export_flags (Optional[list[str]]): Флаги экспорта (используются DEFAULT_EXPORT_FLAGS если не указано).
        timeout_seconds (Optional[int]): Таймаут для процесса в секундах (None = без ограничений).
        explicit_model_file (Optional[Path]): Явно указанный файл модели.
        
    Returns:
        TNavigatorCheckSummary: Объект со всей информацией о выполнении проверки.
        
    Raises:
        RuntimeError: Если процесс выбросил исключение при запуске.
        FileNotFoundError: Если указанные файлы не найдены.
    """
    # Преобразуем пути в абсолютные и нормализуем их
    model_dir = model_dir.resolve()
    workdir = workdir.resolve()
    
    # Создаем рабочую директорию, если её еще нет
    workdir.mkdir(parents=True, exist_ok=True)

    # Находим файл модели в указанной директории
    model_file = find_model_file(model_dir, explicit_model_file)
    
    # Валидируем входные параметры (проверяем наличие файлов)
    validate_inputs(tnavigator_exe.resolve(), model_file)

    # Генерируем временную метку для уникальности файлов логов
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stdout_file = workdir / f"tnavigator_stdout_{timestamp}.log"
    stderr_file = workdir / f"tnavigator_stderr_{timestamp}.log"
    summary_json_file = workdir / f"tnavigator_summary_{timestamp}.json"

    # Используем переданные флаги экспорта или флаги по умолчанию
    flags = export_flags if export_flags is not None else list(DEFAULT_EXPORT_FLAGS)
    
    # Строим команду для запуска tNavigator
    command = build_command(
        tnavigator_exe=tnavigator_exe.resolve(),
        model_file=model_file,
        run_mode=run_mode,
        ignore_lock=ignore_lock,
        export_flags=flags,
    )

    # Блок try-except для обработки различных сценариев запуска процесса
    try:
        # Запускаем процесс tNavigator и перехватываем вывод
        completed = subprocess.run(
            command,
            cwd=str(model_dir),
            capture_output=True,
            text=True,
            shell=False,
            timeout=timeout_seconds,
        )
        # Извлекаем stdout и stderr из результата
        stdout_text = completed.stdout or ""
        stderr_text = completed.stderr or ""
        return_code: Optional[int] = completed.returncode
    except subprocess.TimeoutExpired as exc:
        # Если процесс истёк по таймауту, извлекаем частичный вывод
        stdout_text = exc.stdout or ""
        stderr_text = exc.stderr or ""
        return_code = None
        # Добавляем маркер таймаута в stderr
        stderr_text = stderr_text + "\n\n[TIMEOUT]"
    except Exception as exc:
        # Если произошла другая ошибка, создаем сводку с информацией об ошибке
        summary = TNavigatorCheckSummary(
            timestamp=datetime.now().isoformat(),
            run_mode=run_mode,
            model_file=str(model_file),
            command=command,
            cwd=str(model_dir),
            returncode=None,
            model_read_status=None,
            general_status=None,
            stdout_warning_count=0,
            stderr_warning_count=0,
            stderr_error_count=0,
            result_files=find_result_files(model_dir, model_file.stem),
            result_log=None,
            stdout_log=str(stdout_file),
            stderr_log=str(stderr_file),
            summary_json=str(summary_json_file),
            final_status="failed",
            notes=[f"ошибка запуска tNavigator: {exc}"],
        )
        # Сохраняем сводку в JSON файл для диагностики
        summary_json_file.write_text(
            json.dumps(asdict(summary), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        # Выбрасываем исключение с указанием пути к файлу сводки
        raise RuntimeError(
            f"не удалось запустить проверку tNavigator; подробности: {summary_json_file}"
        ) from exc

    # Записываем логи stdout и stderr в файлы
    write_text(stdout_file, stdout_text)
    write_text(stderr_file, stderr_text)

    # Парсим результаты из stdout
    model_read_status = parse_model_read_status(stdout_text)
    general_status = parse_general_status(stdout_text)
    result_log_path = find_result_log(model_dir, model_file.stem)
    result_log_summary = (
        parse_result_log_summary(result_log_path) if result_log_path is not None else None
    )
    
    # Определяем финальный статус на основе полученной информации
    final_status, notes = derive_final_status(
        returncode=return_code,
        model_read_status=model_read_status,
        general_status=general_status,
        stderr_text=stderr_text,
        result_log_summary=result_log_summary,
        run_mode=run_mode,
    )

    # Создаем полную сводку с результатами проверки
    summary = TNavigatorCheckSummary(
        timestamp=datetime.now().isoformat(),
        run_mode=run_mode,
        model_file=str(model_file),
        command=command,
        cwd=str(model_dir),
        returncode=return_code,
        # Преобразуем ModelReadStatus в словарь или None
        model_read_status=asdict(model_read_status) if model_read_status else None,
        general_status=general_status,
        # Подсчитываем предупреждения и ошибки в обоих логах
        stdout_warning_count=count_warnings(stdout_text),
        stderr_warning_count=count_warnings(stderr_text),
        stderr_error_count=count_errors(stderr_text),
        # Находим результирующие файлы
        result_files=find_result_files(model_dir, model_file.stem),
        result_log=asdict(result_log_summary) if result_log_summary else None,
        stdout_log=str(stdout_file),
        stderr_log=str(stderr_file),
        summary_json=str(summary_json_file),
        final_status=final_status,
        notes=notes,
    )
    
    # Сохраняем полную сводку в JSON файл
    summary_json_file.write_text(
        json.dumps(asdict(summary), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    
    # Возвращаем сводку результатов
    return summary
