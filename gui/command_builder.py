from __future__ import annotations

from pathlib import Path


def python_exe(root: str | Path) -> Path:
    return Path(root) / ".venv" / "Scripts" / "pythonw.exe"


def _main_py(root: str | Path) -> Path:
    return Path(root) / "main.py"


def normalize_period(period: str) -> str:
    value = str(period or "").strip()
    if not value:
        return value
    if value.endswith("点"):
        return value
    digits = "".join(ch for ch in value if ch.isdigit())
    return f"{digits}点" if digits else value


def _append_project(command: list[str], project_id: str | None) -> list[str]:
    if project_id:
        command.extend(["--project", str(project_id)])
    return command


def build_hourly_command(root: str | Path, period: str, project_id: str | None = None) -> list[str]:
    command = [
        str(python_exe(root)),
        "-u",
        str(_main_py(root)),
        "--mode",
        "run",
    ]
    _append_project(command, project_id)
    command.extend(["--period", normalize_period(period), "--yes"])
    return command


def build_daily_command(root: str | Path, date_text: str | None, project_id: str | None = None) -> list[str]:
    command = [
        str(python_exe(root)),
        "-u",
        str(_main_py(root)),
        "--mode",
        "run-daily",
    ]
    _append_project(command, project_id)
    if date_text:
        command.extend(["--date", str(date_text)])
    command.append("--yes")
    return command


def build_word_class_command(root: str | Path, date_text: str | None, project_id: str | None = None) -> list[str]:
    command = [
        str(python_exe(root)),
        "-u",
        str(_main_py(root)),
        "--mode",
        "run-word-class",
    ]
    _append_project(command, project_id)
    if date_text:
        command.extend(["--date", str(date_text)])
    command.append("--yes")
    return command


def _multi_command(root: str | Path, task: str, project_ids: list[str]) -> list[str]:
    selected = [str(project_id).strip() for project_id in project_ids if str(project_id).strip()]
    return [
        str(python_exe(root)),
        "-u",
        str(_main_py(root)),
        "--mode",
        "run-multi",
        "--projects",
        ",".join(selected),
        "--task",
        task,
    ]


def build_multi_hourly_command(root: str | Path, period: str, project_ids: list[str]) -> list[str]:
    command = _multi_command(root, "hourly", project_ids)
    command.extend(["--period", normalize_period(period)])
    return command


def build_multi_daily_command(root: str | Path, date_text: str | None, project_ids: list[str]) -> list[str]:
    command = _multi_command(root, "daily", project_ids)
    if date_text:
        command.extend(["--date", str(date_text)])
    return command


def build_preflight_command(root: str | Path, task: str, project_id: str | None = None) -> list[str]:
    command = [
        str(python_exe(root)),
        "-u",
        str(_main_py(root)),
        "--mode",
        "preflight",
    ]
    _append_project(command, project_id)
    command.extend([
        "--task",
        str(task or "hourly"),
        "--quick",
    ])
    return command
