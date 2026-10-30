#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import signal
import tempfile
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from solution_requirements import (
    ensure_restored_model_not_empty,
    require_existing_directory,
    require_existing_file,
    require_solution_entrypoints,
)
from metrics import (
    build_manifest,
    compression_ratio,
    compression_score,
    compute_structure_metrics,
    exact_match_ratio,
    public_score,
    runtime_score,
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
    parser.add_argument("--allow-trusted-local-execution", action="store_true",
                        help="Execute reviewed trusted solution scripts with host-user rights; not a sandbox.")
    parser.add_argument("--command-timeout-seconds", type=float, default=3600,
                        help="Timeout for each trusted local solution command.")
    return parser.parse_args()


def run_command(command: list[str], log_path: Path, timeout_seconds: float = 3600,
                *, trusted: bool = False) -> CommandResult:
    """Run trusted local code with a timeout and bounded in-memory log excerpts.

    This is not a hostile-submission sandbox. Hosted judging needs external isolation.
    """
    if not trusted:
        raise PermissionError("Explicit trusted local execution consent is required; untrusted code needs an OS sandbox")
    if timeout_seconds <= 0:
        raise ValueError("command timeout must be positive")
    start = time.perf_counter()
    env = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR", "TMPDIR", "TEMP", "TMP")
           if key in os.environ}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        process = subprocess.Popen(command, stdout=stdout, stderr=stderr,
                                   stdin=subprocess.DEVNULL, env=env,
                                   start_new_session=os.name == "posix")
        try:
            try:
                process.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired as error:
                raise TimeoutError(f"Command exceeded {timeout_seconds} seconds") from error
        finally:
            if os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            elif process.poll() is None:
                process.kill()
            process.wait()
        stdout.seek(0)
        stderr.seek(0)
        out = stdout.read(1024 * 1024).decode("utf-8", errors="replace")
        err = stderr.read(1024 * 1024).decode("utf-8", errors="replace")
    duration = round(time.perf_counter() - start, 4)
    log_path.write_text(f"COMMAND: {command!r}\nEXIT: {process.returncode}\n"
                        f"STDOUT (up to 1 MiB):\n{out}\nSTDERR (up to 1 MiB):\n{err}",
                        encoding="utf-8")
    return CommandResult(command, process.returncode, out, err, duration)


def _overlaps(left: Path, right: Path) -> bool:
    return left.is_relative_to(right) or right.is_relative_to(left)


def main() -> int:
    args = parse_args()

    model_dir = Path(args.model).resolve()
    require_existing_directory(model_dir, "входная директория модели")

    workdir = Path(args.workdir).resolve()
    if _overlaps(workdir, model_dir):
        raise ValueError("workdir must not overlap the input model")
    if args.command_timeout_seconds <= 0:
        raise ValueError("command timeout must be positive")
    logs_dir = workdir / "logs"

    archive_path = Path(args.archive).resolve() if args.archive else None
    restored_dir = Path(args.restored_model).resolve() if args.restored_model else None
    if restored_dir is not None and _overlaps(restored_dir, model_dir):
        raise ValueError("restored model must not overlap the original input")
    if args.json_output:
        json_output = Path(args.json_output).resolve()
        protected = [model_dir, archive_path, restored_dir]
        if args.solution_dir:
            protected.append(Path(args.solution_dir).resolve())
        if any(source is not None and _overlaps(json_output, source) for source in protected):
            raise ValueError("JSON output must not overlap inputs or solution")
    if not args.solution_dir and restored_dir is not None and _overlaps(workdir, restored_dir):
        raise ValueError("workdir must not overlap the restored input")
    compress_result: CommandResult | None = None
    decompress_result: CommandResult | None = None
    tnavigator_check = None
    validation_notes: list[str] = []

    if args.solution_dir:
        if not args.allow_trusted_local_execution:
            raise PermissionError("Use --allow-trusted-local-execution only for reviewed trusted scripts or inside an OS sandbox")
        solution_dir = Path(args.solution_dir).resolve()
        compress_script, decompress_script = require_solution_entrypoints(solution_dir)
        if _overlaps(workdir, solution_dir):
            raise ValueError("workdir must not overlap the solution")
        for output in (archive_path, restored_dir, Path(args.json_output).resolve() if args.json_output else None):
            if output is not None and any(_overlaps(output, source) for source in (model_dir, solution_dir)):
                raise ValueError("output paths must not overlap model or solution")
        if restored_dir is not None and restored_dir.exists():
            raise FileExistsError("restored output must be a new directory; existing data is preserved")
        if archive_path is not None and archive_path.exists():
            raise FileExistsError("archive output must be a new file")
        logs_dir.mkdir(parents=True, exist_ok=True)

        if archive_path is None:
            archive_path = workdir / "artifact.tar.gz"
        else:
            archive_path.parent.mkdir(parents=True, exist_ok=True)

        if restored_dir is None:
            restored_dir = Path(tempfile.mkdtemp(prefix="restored-", dir=workdir))

        compress_command = [
            args.python_bin,
            str(compress_script),
            "--input",
            str(model_dir),
            "--output",
            str(archive_path),
        ]
        compress_result = run_command(compress_command, logs_dir / "compress.log", args.command_timeout_seconds, trusted=True)
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
        decompress_result = run_command(decompress_command, logs_dir / "decompress.log", args.command_timeout_seconds, trusted=True)
        if decompress_result.exit_code != 0:
            raise RuntimeError("decompress.py завершился с ошибкой. Подробности смотрите в логах оценщика.")
    else:
        if archive_path is None or restored_dir is None:
            raise ValueError(
                "Укажите либо --solution-dir, либо сразу оба аргумента: --archive и --restored-model."
            )

    logs_dir.mkdir(parents=True, exist_ok=True)
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
    structure_metrics = compute_structure_metrics(
        original_manifest,
        restored_manifest,
        model_dir,
        restored_dir,
        tnavigator_final_status=tnavigator_check["final_status"] if tnavigator_check else None,
    )
    structure = structure_metrics.structure_integrity
    if structure_metrics.root_data_score < 1.0 or structure_metrics.include_graph_score < 1.0:
        validation_notes.append("required root DATA / INCLUDE graph structure is not preserved")
    if structure_metrics.recall < 1.0:
        validation_notes.append("required model paths are missing")
    exact = exact_match_ratio(original_manifest, restored_manifest, model_dir, restored_dir)

    compression_seconds = round(compress_result.duration_seconds, 4) if compress_result else None
    decompression_seconds = round(decompress_result.duration_seconds, 4) if decompress_result else None

    runtime_seconds = None
    if compress_result or decompress_result:
        runtime_total = 0.0
        if compress_result:
            runtime_total += compress_result.duration_seconds
        if decompress_result:
            runtime_total += decompress_result.duration_seconds
        runtime_seconds = round(runtime_total, 4)

    score = (
        public_score(
            compression_ratio_value=ratio,
            structure_integrity_value=structure,
            exact_match_ratio_value=exact,
            total_runtime_seconds=runtime_seconds,
        )
        if runtime_seconds is not None
        else None
    )

    result = {
        "valid": not validation_notes,
        "score_spec_version": "2.0-structural-gates",
        "model": str(model_dir),
        "archive": str(archive_path),
        "restored_model": str(restored_dir),
        "metrics": {
            "public_score": score,
            "score_complete": runtime_seconds is not None,
            "compression_ratio": round(ratio, 4),
            "compression_ratio_score": round(compression_score(ratio), 2),
            "structure_recall": round(structure_metrics.recall, 4),
            "structure_precision": round(structure_metrics.precision, 4),
            "structure_path_f1": round(structure_metrics.path_f1, 4),
            "structure_root_data_score": round(structure_metrics.root_data_score, 4),
            "structure_include_graph_score": round(structure_metrics.include_graph_score, 4),
            "tnavigator_structure_score": (
                round(structure_metrics.tnavigator_score, 4)
                if structure_metrics.tnavigator_score is not None
                else None
            ),
            "structure_integrity": round(structure, 4),
            "exact_match_ratio": round(exact, 4),
            "content_accuracy": round(exact, 4),
            "model_size_bytes": model_size,
            "archive_size_bytes": archive_size,
            "file_count_original": len(original_manifest),
            "file_count_restored": len(restored_manifest),
            "compression_seconds": compression_seconds,
            "decompression_seconds": decompression_seconds,
            "runtime_seconds": runtime_seconds,
            "runtime_score": round(runtime_score(runtime_seconds), 2) if runtime_seconds is not None else None,
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
