#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


HEX_COLOR_RE = re.compile(r"^[0-9a-fA-F]{6}$")
PATH_ITEM_RE = re.compile(r'^\s*-\s+["\']?([^"\']+)["\']?\s*$')


def parse_args() -> argparse.Namespace:
    """Определяет и парсит аргументы командной строки.
    
    Returns:
        argparse.Namespace: Разобранные аргументы командной строки.
    """
    parser = argparse.ArgumentParser(
        description="Проверяет внутреннюю согласованность метаданных .github в репозитории."
    )
    parser.add_argument(
        "--repo-root",
        default=".",
        help="Путь к корню репозитория.",
    )
    return parser.parse_args()


def require(condition: bool, message: str, errors: list[str]) -> None:
    """Проверяет условие и, если оно ложно, добавляет сообщение об ошибке в список.
    
    Args:
        condition (bool): Ожидаемое логическое условие.
        message (str): Текст ошибки, если условие ложно.
        errors (list[str]): Коллекция ошибок по ссылке для добавления сообщений.
    """
    if not condition:
        errors.append(message)


def load_labels(labels_path: Path, errors: list[str]) -> dict[str, dict[str, str]]:
    """Читает и валидирует манифест labels.json, записывая найденные ошибки.
    
    Args:
        labels_path (Path): Путь к .github/labels.json.
        errors (list[str]): Коллекция для накопления строк с ошибками.
        
    Returns:
        dict[str, dict[str, str]]: Словарь меток с ключом по названию.
    """
    try:
        raw = json.loads(labels_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        errors.append(f"отсутствует манифест label'ов: {labels_path}")
        return {}

    require(isinstance(raw, list) and raw, "манифест label'ов должен быть непустым JSON-массивом", errors)
    if not isinstance(raw, list):
        return {}

    labels: dict[str, dict[str, str]] = {}
    for entry in raw:
        require(isinstance(entry, dict), "каждая запись label'а должна быть JSON-объектом", errors)
        if not isinstance(entry, dict):
            continue

        name = str(entry.get("name", "")).strip()
        color = str(entry.get("color", "")).strip()
        description = str(entry.get("description", "")).strip()

        require(bool(name), "каждый label должен иметь непустое имя", errors)
        require(bool(HEX_COLOR_RE.fullmatch(color)), f"label '{name or '<пусто>'}' имеет некорректный цвет '{color}'", errors)
        if name in labels:
            errors.append(f"дублирующееся имя label'а в манифесте: {name}")
            continue

        labels[name] = {
            "name": name,
            "color": color.lower(),
            "description": description,
        }
    return labels


def extract_list_items(text: str, start_key: str) -> list[str]:
    """Извлекает элементы списка из YAML-подобного текста по ключу (например, labels:).
    
    Args:
        text (str): Исходный текстовый контент файла шаблона.
        start_key (str): Ключ начала списка для парсинга элементов.
        
    Returns:
        list[str]: Список извлеченных элементов без маркировок.
    """
    items: list[str] = []
    in_block = False
    block_indent = None
    for line in text.splitlines():
        stripped = line.strip()
        if not in_block and stripped == f"{start_key}:":
            in_block = True
            block_indent = len(line) - len(line.lstrip())
            continue
        if not in_block:
            continue

        if not stripped:
            continue

        current_indent = len(line) - len(line.lstrip())
        if current_indent <= (block_indent or 0) and not stripped.startswith("- "):
            break

        match = PATH_ITEM_RE.match(line)
        if match:
            items.append(match.group(1).strip())
            continue

        if current_indent <= (block_indent or 0):
            break
    return items


def validate_issue_forms(
    repo_root: Path,
    labels: dict[str, dict[str, str]],
    errors: list[str],
) -> list[dict[str, object]]:
    """Проверяет валидность и структуру YAML-шаблонов для обращений GitHub.
    
    Args:
        repo_root (Path): Корневая папка репозитория.
        labels (dict[str, dict[str, str]]): Загруженный словарь доступных меток для сверки.
        errors (list[str]): Коллекция накопительных проверок.
        
    Returns:
        list[dict[str, object]]: Метаданные валидации по каждому шаблону.
    """
    issue_dir = repo_root / ".github" / "ISSUE_TEMPLATE"
    form_paths = sorted(path for path in issue_dir.glob("*.yml") if path.name != "config.yml")
    require(bool(form_paths), "в .github/ISSUE_TEMPLATE не найдены формы issue", errors)

    results: list[dict[str, object]] = []
    for path in form_paths:
        text = path.read_text(encoding="utf-8")
        for field in ("name:", "description:", "title:", "labels:", "body:"):
            require(field in text, f"в {path.name} отсутствует обязательное поле '{field[:-1]}'", errors)

        labels_in_form = extract_list_items(text, "labels")
        require(bool(labels_in_form), f"{path.name} должен объявлять хотя бы один label", errors)
        for label in labels_in_form:
            require(label in labels, f"{path.name} ссылается на отсутствующий label '{label}'", errors)

        require(
            "required: true" in text,
            f"{path.name} должен содержать хотя бы одно обязательное поле",
            errors,
        )

        results.append(
            {
                "file": str(path.relative_to(repo_root)),
                "labels": labels_in_form,
            }
        )
    return results


def validate_issue_config(repo_root: Path, errors: list[str]) -> dict[str, object]:
    """Проверяет конфигурацию config.yml для ссылок на обращения.
    
    Args:
        repo_root (Path): Корневая папка репозитория.
        errors (list[str]): Коллекция накопительных ошибок.
        
    Returns:
        dict[str, object]: Результат валидации конфига с метриками.
    """
    config_path = repo_root / ".github" / "ISSUE_TEMPLATE" / "config.yml"
    text = config_path.read_text(encoding="utf-8")
    require("blank_issues_enabled: false" in text, "конфигурация формы обращений должна отключать пустые обращения", errors)
    require("contact_links:" in text, "конфигурация формы обращений должна определять раздел `contact_links`", errors)

    url_count = sum(1 for line in text.splitlines() if line.strip().startswith("url: "))
    require(url_count >= 3, "конфигурация формы обращений должна содержать как минимум три контактные ссылки", errors)

    return {
        "file": str(config_path.relative_to(repo_root)),
        "contact_link_count": url_count,
    }


def validate_codeowners(repo_root: Path, errors: list[str]) -> dict[str, object]:
    """Проверяет наличие обязательных путей в файле CODEOWNERS.
    
    Args:
        repo_root (Path): Корневая папка репозитория.
        errors (list[str]): Коллекция накопительных ошибок.
        
    Returns:
        dict[str, object]: Метаданные о проверке файла контроля владения.
    """
    codeowners_path = repo_root / ".github" / "CODEOWNERS"
    text = codeowners_path.read_text(encoding="utf-8")
    required_entries = [
        "README.md",
        "docs/",
        "baseline/",
        "public_evaluator/",
        "starter-kit/",
        "submission_template/",
        "leaderboard/",
        "scripts/",
        ".github/",
    ]
    missing = [entry for entry in required_entries if entry not in text]
    require(not missing, f"в CODEOWNERS отсутствуют обязательные записи: {', '.join(missing)}", errors)
    return {
        "file": str(codeowners_path.relative_to(repo_root)),
        "entry_count": sum(1 for line in text.splitlines() if line.strip() and not line.strip().startswith("#")),
    }


def validate_pr_template(repo_root: Path, errors: list[str]) -> dict[str, object]:
    """Проверяет наличие обязательных секций в шаблоне запроса на слияние.
    
    Args:
        repo_root (Path): Корневая директория проекта.
        errors (list[str]): Коллекция накопительных ошибок.
        
    Returns:
        dict[str, object]: Результат проверки наличия секций шаблона.
    """
    pr_path = repo_root / ".github" / "pull_request_template.md"
    text = pr_path.read_text(encoding="utf-8")
    for section in ("## Кратко", "## Проверка", "## Чеклист"):
        require(section in text, f"в pull_request_template.md отсутствует раздел '{section}'", errors)
    return {
        "file": str(pr_path.relative_to(repo_root)),
        "checked_sections": ["Кратко", "Проверка", "Чеклист"],
    }


def validate_repo_docs(repo_root: Path, errors: list[str]) -> list[str]:
    """Проверяет, что GITHUB-GUIDE.md и CONTRIBUTING.md не пустые.
    
    Args:
        repo_root (Path): Корневая директория.
        errors (list[str]): Список ошибок и недочётов.
        
    Returns:
        list[str]: Список проверенных файлов документации.
    """
    guide_path = repo_root / ".github" / "GITHUB-GUIDE.md"
    contributing_path = repo_root / ".github" / "CONTRIBUTING.md"
    for path in (guide_path, contributing_path):
        text = path.read_text(encoding="utf-8")
        require(bool(text.strip()), f"{path.name} не должен быть пустым", errors)
    return [
        str(guide_path.relative_to(repo_root)),
        str(contributing_path.relative_to(repo_root)),
    ]


def validate_workflows(repo_root: Path, errors: list[str]) -> list[dict[str, object]]:
    """Проверяет синтаксис рабочих процессов GitHub Actions и ссылки на пути.
    
    Args:
        repo_root (Path): Корень репозитория.
        errors (list[str]): Коллекция ошибок агрегации.
        
    Returns:
        list[dict[str, object]]: Представление метрик проверенных автоматических процессов.
    """
    workflow_dir = repo_root / ".github" / "workflows"
    workflow_paths = sorted(workflow_dir.glob("*.yml"))
    require(bool(workflow_paths), "в .github/workflows не найдены файлы автоматических процессов", errors)

    results: list[dict[str, object]] = []
    for path in workflow_paths:
        text = path.read_text(encoding="utf-8")
        require("actions/checkout@v4" in text, f"{path.name} должен выполнять checkout содержимого репозитория", errors)
        referenced_paths: list[str] = []
        for raw in text.splitlines():
            match = PATH_ITEM_RE.match(raw)
            if not match:
                continue
            candidate = match.group(1).strip()
            if "/" not in candidate or candidate.startswith("actions/"):
                continue
            if any(ch in candidate for ch in "*{}$"):
                referenced_paths.append(candidate)
                continue
            require((repo_root / candidate).exists(), f"{path.name} ссылается на отсутствующий путь '{candidate}'", errors)
            referenced_paths.append(candidate)

        results.append(
            {
                "file": str(path.relative_to(repo_root)),
                "referenced_paths": referenced_paths,
            }
        )
    return results


def validate_absence_of_meta_readme(repo_root: Path, errors: list[str]) -> None:
    """Убеждается, что .github/README.md физически отсутствует.
    
    Args:
        repo_root (Path): Путь к корню проекта.
        errors (list[str]): Коллекция для ошибок валидации.
    """
    require(
        not (repo_root / ".github" / "README.md").exists(),
        ".github/README.md должен отсутствовать, чтобы не перехватить рендер корневого README",
        errors,
    )


def validate_required_files(repo_root: Path, errors: list[str]) -> list[str]:
    """Проверяет физическое наличие всех ключевых файлов структуры .github/ и скриптов.
    
    Args:
        repo_root (Path): Корень репозитория.
        errors (list[str]): Коллекция для ошибок доступности.
        
    Returns:
        list[str]: Список ключевых требуемых путей, которые были проверены.
    """
    required_paths = [
        ".github/CODEOWNERS",
        ".github/CONTRIBUTING.md",
        ".github/GITHUB-GUIDE.md",
        ".github/labels.json",
        ".github/pull_request_template.md",
        ".github/ISSUE_TEMPLATE/bug-report.yml",
        ".github/ISSUE_TEMPLATE/config.yml",
        ".github/ISSUE_TEMPLATE/data-access.yml",
        ".github/ISSUE_TEMPLATE/evaluator-problem.yml",
        ".github/ISSUE_TEMPLATE/question.yml",
        ".github/workflows/sync-labels.yml",
        ".github/workflows/validate-github-meta.yml",
        ".github/workflows/validate-leaderboard.yml",
        "scripts/sync_github_labels.py",
        "scripts/validate_github_meta.py",
    ]
    for relative_path in required_paths:
        require((repo_root / relative_path).exists(), f"отсутствует обязательный файл: {relative_path}", errors)
    return required_paths


def main() -> int:
    """Последовательно запускает все проверки метаданных и выводит JSON-результат.
    
    Returns:
        int: Код возврата выполнения (0 при отсутствии ошибок валидации, иначе 1).
    """
    args = parse_args()
    repo_root = Path(args.repo_root).resolve()
    errors: list[str] = []

    required_files = validate_required_files(repo_root, errors)
    validate_absence_of_meta_readme(repo_root, errors)

    labels = load_labels(repo_root / ".github" / "labels.json", errors)
    issue_forms = validate_issue_forms(repo_root, labels, errors)
    issue_config = validate_issue_config(repo_root, errors)
    codeowners = validate_codeowners(repo_root, errors)
    pr_template = validate_pr_template(repo_root, errors)
    docs = validate_repo_docs(repo_root, errors)
    workflows = validate_workflows(repo_root, errors)

    result = {
        "valid": not errors,
        "repo_root": str(repo_root),
        "required_file_count": len(required_files),
        "label_count": len(labels),
        "issue_forms": issue_forms,
        "issue_config": issue_config,
        "codeowners": codeowners,
        "pr_template": pr_template,
        "docs": docs,
        "workflows": workflows,
        "errors": errors,
    }
    stream = sys.stdout if not errors else sys.stderr
    print(json.dumps(result, indent=2, ensure_ascii=False), file=stream)
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
