#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from contract_checks import (
    ensure_restored_model_not_empty,
    require_existing_directory,
    require_existing_file,
    require_solution_contract,
)
from metrics import (
    build_manifest,
    compression_ratio,
    exact_match_ratio,
    public_score,
    structure_recall,
    total_size_bytes,
)
from tnavigator_check import run_tnavigator_check


@dataclass
class CommandResult:
    command: list[str]
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Локальный публичный оценщик для GeoModel Compression Challenge."
    )
    parser.add_argument("--model", required=True, help="Путь к директории исходной модели.")
    parser.add_argument(
        "--solution-dir",
        help="Путь к директории решения участника, содержащей compress.py и decompress.py.",
    )
    parser.add_argument(
        "--archive",
        help="Путь к заранее подготовленному сжатому артефакту. Необязательно, если указан --solution-dir.",
    )
    parser.add_argument(
        "--restored-model",
        help="Путь к заранее подготовленной директории восстановленной модели. Необязательно, если указан --solution-dir.",
    )
    parser.add_argument(
        "--workdir",
        default=".evaluator-workdir",
        help="Рабочая директория для временных артефактов и логов.",
    )
    parser.add_argument(
        "--json-output",
        help="Необязательный путь к файлу для записи полного результата оценки в JSON.",
    )
    parser.add_argument(
        "--python-bin",
        default=sys.executable,
        help="Интерпретатор Python, используемый для запуска скриптов участника.",
    )
    parser.add_argument(
        "--tnavigator-exe",
        help="Необязательный путь к tNavigator-con.exe для локальной проверки открытия восстановленной модели.",
    )
    parser.add_argument(
        "--tnavigator-mode",
        choices=["nosim", "smoke", "full"],
        default="smoke",
        help="Режим проверки восстановленной модели через tNavigator.",
    )
    parser.add_argument(
        "--tnavigator-model-file",
        help="Необязательный относительный путь к корневому .DATA-файлу внутри восстановленной модели.",
    )
    parser.add_argument(
        "--tnavigator-ignore-lock",
        action="store_true",
        help="Добавить флаг --ignore-lock при запуске tNavigator.",
    )
    parser.add_argument(
        "--tnavigator-timeout-seconds",
        type=int,
        help="Необязательный таймаут проверки tNavigator в секундах.",
    )
    parser.add_argument(
        "--tnavigator-export-flag",
        action="append",
        default=None,
        help="Дополнительный флаг экспорта для tNavigator. Можно указывать несколько раз.",
    )
    return parser.parse_args()


def run_command(command: list[str], log_path: Path) -> CommandResult:
    start = time.perf_counter()
    completed = subprocess.run(command, capture_output=True, text=True)
    duration = time.perf_counter() - start
    log_path.write_text(
        "\n".join(
            [
                f"КОМАНДА: {' '.join(command)}",
                f"КОД_ВОЗВРАТА: {completed.returncode}",
                "",
                "STDOUT:",
                completed.stdout,
                "",
                "STDERR:",
                completed.stderr,
            ]
        ),
        encoding="utf-8",
    )
    return CommandResult(
        command=command,
        exit_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        duration_seconds=round(duration, 4),
    )


def main() -> int:
    args = parse_args()

    model_dir = Path(args.model).resolve()
    require_existing_directory(model_dir, "входная директория модели")

    workdir = Path(args.workdir).resolve()
    logs_dir = workdir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    archive_path = Path(args.archive).resolve() if args.archive else None
    restored_dir = Path(args.restored_model).resolve() if args.restored_model else None
    compress_result: CommandResult | None = None
    decompress_result: CommandResult | None = None
    tnavigator_check = None
    validation_notes: list[str] = []

    if args.solution_dir:
        solution_dir = Path(args.solution_dir).resolve()
        compress_script, decompress_script = require_solution_contract(solution_dir)

        if archive_path is None:
            archive_path = workdir / "artifact.tar.gz"
        else:
            archive_path.parent.mkdir(parents=True, exist_ok=True)

        if restored_dir is None:
            restored_dir = workdir / "restored_model"

        if restored_dir.exists():
            shutil.rmtree(restored_dir)

        compress_command = [
            args.python_bin,
            str(compress_script),
            "--input",
            str(model_dir),
            "--output",
            str(archive_path),
        ]
        compress_result = run_command(compress_command, logs_dir / "compress.log")
        if compress_result.exit_code != 0:
            raise RuntimeError("compress.py завершился с ошибкой. Подробности смотрите в логах оценщика.")

        decompress_command = [
            args.python_bin,
            str(decompress_script),
            "--input",
            str(archive_path),
            "--output",
            str(restored_dir),
        ]
        decompress_result = run_command(decompress_command, logs_dir / "decompress.log")
        if decompress_result.exit_code != 0:
            raise RuntimeError("decompress.py завершился с ошибкой. Подробности смотрите в логах оценщика.")
    else:
        if archive_path is None or restored_dir is None:
            raise ValueError(
                "Укажите либо --solution-dir, либо сразу оба аргумента: --archive и --restored-model."
            )

    require_existing_file(archive_path, "сжатый артефакт")
    ensure_restored_model_not_empty(restored_dir)

    if args.tnavigator_exe:
        tnavigator_workdir = workdir / "tnavigator_check"
        tnavigator_check = asdict(
            run_tnavigator_check(
                tnavigator_exe=Path(args.tnavigator_exe).resolve(),
                model_dir=restored_dir,
                workdir=tnavigator_workdir,
                run_mode=args.tnavigator_mode,
                ignore_lock=args.tnavigator_ignore_lock,
                export_flags=args.tnavigator_export_flag,
                timeout_seconds=args.tnavigator_timeout_seconds,
                explicit_model_file=Path(args.tnavigator_model_file) if args.tnavigator_model_file else None,
            )
        )
        if tnavigator_check["final_status"] not in {"success", "success_with_warnings"}:
            validation_notes.append(
                f"проверка tNavigator завершилась со статусом {tnavigator_check['final_status']}"
            )

    original_manifest = build_manifest(model_dir)
    restored_manifest = build_manifest(restored_dir)

    model_size = total_size_bytes([path for path in model_dir.rglob("*")])
    archive_size = archive_path.stat().st_size
    ratio = compression_ratio(model_size, archive_size)
    structure = structure_recall(original_manifest, restored_manifest)
    exact = exact_match_ratio(original_manifest, restored_manifest, model_dir, restored_dir)

    total_runtime = 0.0
    if compress_result:
        total_runtime += compress_result.duration_seconds
    if decompress_result:
        total_runtime += decompress_result.duration_seconds

    score = public_score(
        compression_ratio_value=ratio,
        structure_recall_value=structure,
        exact_match_ratio_value=exact,
        total_runtime_seconds=total_runtime,
    )

    result = {
        "valid": not validation_notes,
        "model": str(model_dir),
        "archive": str(archive_path),
        "restored_model": str(restored_dir),
        "metrics": {
            "public_score": score,
            "compression_ratio": round(ratio, 4),
            "compression_ratio_score": round(min(max((ratio - 1.0) / 9.0, 0.0), 1.0) * 100.0, 2),
            "structure_recall": round(structure, 4),
            "exact_match_ratio": round(exact, 4),
            "model_size_bytes": model_size,
            "archive_size_bytes": archive_size,
            "file_count_original": len(original_manifest),
            "file_count_restored": len(restored_manifest),
            "runtime_seconds": round(total_runtime, 4),
        },
        "commands": {
            "compress": asdict(compress_result) if compress_result else None,
            "decompress": asdict(decompress_result) if decompress_result else None,
        },
        "logs_directory": str(logs_dir),
        "tnavigator_check": tnavigator_check,
        "validation_notes": validation_notes,
    }

    if args.json_output:
        Path(args.json_output).write_text(json.dumps(result, indent=2), encoding="utf-8")

    print(json.dumps(result, indent=2))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # pragma: no cover - CLI failure path
        error_result = {"valid": False, "error": str(exc)}
        print(json.dumps(error_result, indent=2), file=sys.stderr)
        raise
