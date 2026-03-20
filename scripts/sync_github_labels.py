#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


HEX_COLOR_RE = re.compile(r"^[0-9a-fA-F]{6}$")


def parse_args() -> argparse.Namespace:
    """Определяет и парсит аргументы командной строки.
    
    Returns:
        argparse.Namespace: Разобранные аргументы командной строки.
    """
    parser = argparse.ArgumentParser(
        description="Проверяет и синхронизирует GitHub label'ы из манифеста репозитория."
    )
    parser.add_argument(
        "--labels-file",
        default=".github/labels.json",
        help="Путь к JSON-манифесту label'ов.",
    )
    parser.add_argument(
        "--repo",
        help="Репозиторий GitHub в формате owner/name. Обязателен, если не используется --validate-only.",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN"),
        help="Токен GitHub с правом управлять label'ами.",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Только проверить манифест и вывести сводку без вызовов GitHub API.",
    )
    parser.add_argument(
        "--delete-missing",
        action="store_true",
        help="Удалить удаленные label'ы, которых нет в манифесте.",
    )
    return parser.parse_args()


def load_labels(path: Path) -> list[dict[str, str]]:
    """Читает и валидирует манифест labels.json, возвращая список нормализованных словарей меток.
    
    Args:
        path (Path): Путь к файлу `labels.json`.
        
    Returns:
        list[dict[str, str]]: Список подтвержденных и нормализованных меток.
    """
    labels = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(labels, list) or not labels:
        raise ValueError("манифест label'ов должен быть непустым JSON-массивом")

    seen_names: set[str] = set()
    normalized: list[dict[str, str]] = []
    for item in labels:
        if not isinstance(item, dict):
            raise ValueError("каждая запись label'а должна быть JSON-объектом")

        name = item.get("name", "").strip()
        color = item.get("color", "").strip()
        description = item.get("description", "").strip()

        if not name:
            raise ValueError("каждый label должен иметь непустое имя")
        if name.lower() in seen_names:
            raise ValueError(f"дублирующееся имя label'а: {name}")
        if not HEX_COLOR_RE.fullmatch(color):
            raise ValueError(f"некорректный цвет для label'а {name}: {color}")

        seen_names.add(name.lower())
        normalized.append(
            {
                "name": name,
                "color": color.lower(),
                "description": description,
            }
        )
    return normalized


def api_request(
    method: str,
    url: str,
    token: str,
    payload: dict[str, str] | None = None,
) -> dict | list | None:
    """Выполняет HTTP-запрос к GitHub API и возвращает разобранный JSON-ответ.
    
    Args:
        method (str): HTTP метод, например 'GET', 'POST', 'PATCH', 'DELETE'.
        url (str): Полный URL для API-запроса.
        token (str): Токен авторизации GitHub.
        payload (dict[str, str] | None, optional): Тело запроса для записи. По умолчанию None.
        
    Returns:
        dict | list | None: Разобранный JSON-ответ от API.
    """
    data = None
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request) as response:
            body = response.read().decode("utf-8")
            if not body:
                return None
            return json.loads(body)
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"запрос GitHub API {method} {url} завершился ошибкой: {exc.code} {details}") from exc


def list_labels(repo: str, token: str) -> list[dict]:
    """Получает полный список текущих меток репозитория через GitHub API с учетом пагинации.
    
    Args:
        repo (str): Название репозитория (например 'owner/name').
        token (str): Токен авторизации GitHub.
        
    Returns:
        list[dict]: Список словарей, описывающих существующие метки GitHub.
    """
    page = 1
    labels: list[dict] = []
    while True:
        url = f"https://api.github.com/repos/{repo}/labels?per_page=100&page={page}"
        page_labels = api_request("GET", url, token)
        if not isinstance(page_labels, list):
            raise RuntimeError("неожиданный ответ GitHub API при получении списка label'ов")
        if not page_labels:
            return labels
        labels.extend(page_labels)
        page += 1


def sync_labels(
    repo: str,
    token: str,
    desired_labels: list[dict[str, str]],
    delete_missing: bool,
) -> dict[str, list[str]]:
    """Синхронизирует желаемые метки с репозиторием GitHub (создание, обновление, удаление).
    
    Args:
        repo (str): Название репозитория (например 'owner/name').
        token (str): Токен авторизации GitHub.
        desired_labels (list[dict[str, str]]): Список целевых конфигураций меток из манифеста.
        delete_missing (bool): Флаг для удаления отсутствующих меток, не описанных в манифесте.
        
    Returns:
        dict[str, list[str]]: Отчет об операциях со списками созданных, измененных, удаленных и нетронутых меток.
    """
    existing = {label["name"]: label for label in list_labels(repo, token)}
    desired_by_name = {label["name"]: label for label in desired_labels}

    created: list[str] = []
    updated: list[str] = []
    deleted: list[str] = []
    unchanged: list[str] = []

    for label in desired_labels:
        current = existing.get(label["name"])
        if current is None:
            api_request(
                "POST",
                f"https://api.github.com/repos/{repo}/labels",
                token,
                payload=label,
            )
            created.append(label["name"])
            continue

        if (
            current.get("color", "").lower() == label["color"]
            and (current.get("description") or "") == label["description"]
        ):
            unchanged.append(label["name"])
            continue

        encoded_name = urllib.parse.quote(label["name"], safe="")
        api_request(
            "PATCH",
            f"https://api.github.com/repos/{repo}/labels/{encoded_name}",
            token,
            payload=label,
        )
        updated.append(label["name"])

    if delete_missing:
        for label_name in existing:
            if label_name in desired_by_name:
                continue
            encoded_name = urllib.parse.quote(label_name, safe="")
            api_request(
                "DELETE",
                f"https://api.github.com/repos/{repo}/labels/{encoded_name}",
                token,
            )
            deleted.append(label_name)

    return {
        "created": created,
        "updated": updated,
        "deleted": deleted,
        "unchanged": unchanged,
    }


def main() -> int:
    """Валидирует и применяет изменения меток к репозиторию.
    
    Returns:
        int: Код возврата 0 при успехе.
    """
    args = parse_args()
    labels_file = Path(args.labels_file).resolve()
    labels = load_labels(labels_file)

    if args.validate_only:
        print(
            json.dumps(
                {
                    "valid": True,
                    "labels_file": str(labels_file),
                    "label_count": len(labels),
                    "labels": [label["name"] for label in labels],
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0

    if not args.repo:
        raise SystemExit("аргумент --repo обязателен, если не используется --validate-only")
    if not args.token:
        raise SystemExit("для синхронизации label'ов нужен токен GitHub")

    result = sync_labels(
        repo=args.repo,
        token=args.token,
        desired_labels=labels,
        delete_missing=args.delete_missing,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # pragma: no cover - CLI failure path
        print(json.dumps({"valid": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise
