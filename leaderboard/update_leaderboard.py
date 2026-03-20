#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import io
import json
from pathlib import Path


def parse_args() -> argparse.Namespace:
    """Определяет и парсит аргументы командной строки.
    
    Returns:
        argparse.Namespace: Разобранные аргументы с путями к CSV и JSON файлам.
    """
    parser = argparse.ArgumentParser(description="Нормализует CSV таблицы результатов и JSON-снимки.")
    parser.add_argument("--csv", required=True, help="Путь к current.csv")
    parser.add_argument("--json", required=True, help="Путь к current.json")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Завершиться с ошибкой, если current.json не совпадает с нормализованным содержимым CSV.",
    )
    return parser.parse_args()


def load_rows(csv_path: Path) -> tuple[list[dict[str, str]], list[str]]:
    """Читает CSV-файл таблицы, сортирует по результату и пересчитывает ранги.
    
    Args:
        csv_path (Path): Путь к файлу `current.csv`.
        
    Returns:
        tuple[list[dict[str, str]], list[str]]: Отсортированные строки и исходные fieldnames.
    """
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames or []
        rows = list(reader)
    for row in rows:
        score = row.get("best_public_score", "").strip()
        row["_score_sort"] = float(score) if score else float("-inf")
    rows.sort(key=lambda row: row["_score_sort"], reverse=True)
    for index, row in enumerate(rows, start=1):
        row["rank"] = str(index)
        row.pop("_score_sort", None)
    return rows, fieldnames


def serialize_csv(fieldnames: list[str], rows: list[dict[str, str]]) -> str:
    """Сериализует нормализованные строки обратно в CSV."""
    if not fieldnames:
        return ""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def main() -> int:
    """Нормализует CSV и синхронизирует (или проверяет синхронизацию) JSON-зеркало.
    
    Returns:
        int: Возвращает 0 при успешном завершении или ошибку SystemExit(1) в случае рассинхрона.
    """
    args = parse_args()
    csv_path = Path(args.csv).resolve()
    json_path = Path(args.json).resolve()

    rows, fieldnames = load_rows(csv_path)
    normalized_csv = serialize_csv(fieldnames, rows)
    normalized_json = json.dumps(rows, indent=2, ensure_ascii=False) + "\n"

    if args.check:
        existing_csv = csv_path.read_text(encoding="utf-8") if csv_path.exists() else ""
        existing = json_path.read_text(encoding="utf-8") if json_path.exists() else ""
        if existing_csv != normalized_csv:
            raise SystemExit("CSV таблицы результатов не нормализован")
        if existing != normalized_json:
            raise SystemExit("JSON таблицы результатов не синхронизирован с CSV")
    else:
        csv_path.write_text(normalized_csv, encoding="utf-8")
        json_path.write_text(normalized_json, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
