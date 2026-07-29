from __future__ import annotations

import argparse
import hashlib
import json
import re
import zipfile
from datetime import date
from pathlib import Path

DEFAULT_VERSION = "hermes_20260710"
EXCLUDE_DIRS = {".venv", ".git", ".claude", ".playwright-cli", ".superpowers", "browser_profile", "runtime", "__pycache__", ".pytest_cache", "build", "cloud", "diagnostics"}
DESKTOP_EXE = "hourlyreport_automation.exe"
DESKTOP_BUILD_MANIFEST = "hourlyreport_automation.build.json"
EXCLUDE_RUNTIME_DIRS = {"reports", "logs", "backups"}
RUNTIME_KEEP_DIRS = {"kst_exports"}
EXCLUDE_SUFFIXES = {".pyc", ".tmp", ".bak", ".lock", ".spec", ".baidu-secrets", ".baidu-auth", ".db", ".cdb", ".pdb"}
EXCLUDE_FILES = {
    "config.json",
    "credentials.local.json",
    ".ignore",
    "nul",
    "_verify_excel.py",
    "build_desktop_exe.bat",
    "requirements-dev.txt",
    "design-qa.md",
}
EXCLUDE_REPORT_FILES = {"menu_task_status.json", "browser_login_state.json", "unknown_baidu_accounts.json"}
LEGACY_ROOT_FILES = {
    "create_config.bat",
    "run_11.bat",
    "run_15.bat",
    "run_18.bat",
    "run_fetch_baidu.bat",
    "run_fetch_baidu_15.bat",
    "run_inspect.bat",
    "run_mock_write.bat",
    "run_parse_kst_export_15.bat",
    "run_test_browser_connect.bat",
    "setup_env.bat",
    "START_HERE.bat",
}

ONLINE_VERSION_PATTERN = re.compile(r"^v?(\d{4})\.(\d{1,2})\.(\d{1,2})\.(\d+)$")


def validate_online_version(version: str) -> str:
    raw = str(version or "").strip()
    match = ONLINE_VERSION_PATTERN.fullmatch(raw)
    if not match:
        raise ValueError("在线版本号必须使用 YYYY.M.D.NNN 格式")

    year, month, day, counter = (int(part) for part in match.groups())
    try:
        date(year, month, day)
    except ValueError as exc:
        raise ValueError(f"在线版本号日期无效：{year}.{month}.{day}") from exc
    if counter < 100:
        raise ValueError("在线版本号的永久累计序号必须从 100 起")
    return f"{year}.{month}.{day}.{counter}"


def next_online_version(latest_version: str, release_date: date | None = None) -> str:
    current = validate_online_version(latest_version)
    counter = int(current.rsplit(".", 1)[1]) + 1
    target_date = release_date or date.today()
    return f"{target_date.year}.{target_date.month}.{target_date.day}.{counter}"


def normalize_version(version: str | None) -> str:
    raw = (version or DEFAULT_VERSION).strip()
    if not raw:
        raw = DEFAULT_VERSION
    if raw.startswith("v") or not re.fullmatch(r"\d+(?:\.\d+)*", raw):
        return raw
    return f"v{raw}"


def release_name(
    version: str | None = None,
    internal: bool = False,
    online_update: bool = False,
    first_install: bool = False,
) -> str:
    if sum((bool(internal), bool(online_update), bool(first_install))) > 1:
        raise ValueError("内部包、首次安装包与在线更新包不能同时生成")
    if first_install:
        clean_version = validate_online_version(version or "")
        return f"Hourlyreport_automation_first_install_v{clean_version}.zip"
    if online_update:
        clean_version = validate_online_version(version or "")
        return f"Hourlyreport_automation_v{clean_version}.zip"
    prefix = "hourly_report_bot_internal" if internal else "hourly_report_bot_release"
    return f"{prefix}_{normalize_version(version)}.zip"


def should_include_file(
    path: Path,
    internal: bool = False,
    online_update: bool = False,
    first_install: bool = False,
) -> bool:
    parts = path.parts
    if (
        (internal or online_update or first_install)
        and len(parts) == 1
        and path.name.casefold() == DESKTOP_EXE.casefold()
    ):
        return False
    if (
        (internal or online_update or first_install)
        and ".worktrees" in parts
    ):
        return False
    if first_install and path.as_posix() in {
        "configs/current_project.json",
        "configs/multi_project_selection.json",
    }:
        return False
    if online_update and parts and parts[0] in {
        "configs", "secrets", "reports", "logs", "backups", "browser_profile", "kst_exports", "samples", ".venv", "runtime"
    }:
        return False
    if any(part in EXCLUDE_DIRS for part in parts):
        return False
    if parts and parts[0] == "dist":
        return False
    if parts and parts[0] == "tests":
        return False
    if len(parts) >= 2 and parts[0] == "docs" and parts[1] == "superpowers":
        return False
    if parts and parts[0] == "tools" and path.name != "bootstrap_python.ps1":
        return False
    if len(parts) == 1 and path.name in LEGACY_ROOT_FILES:
        return False
    if path.name in EXCLUDE_FILES:
        return False
    if path.suffix.lower() in EXCLUDE_SUFFIXES:
        return False
    # 所有程序发布包都排除真实凭据；授权配置通过独立配置包传递。
    if len(parts) >= 2 and parts[0] == "secrets":
        if path.name == "secrets.example.json":
            return True
        if path.name == "secrets.json":
            return False
        return False
    if parts and parts[0] in EXCLUDE_RUNTIME_DIRS:
        return path.name == ".gitkeep"
    # 运行时目录只保留 .gitkeep
    if parts and parts[0] in RUNTIME_KEEP_DIRS:
        if path.name in EXCLUDE_REPORT_FILES:
            return False
        return path.name == ".gitkeep"
    # samples 只保留 .gitkeep
    if parts and parts[0] == "samples":
        return path.name == ".gitkeep"
    return True


def _validate_first_install_source(
    root: Path,
    artifact_dir: str | Path | None,
) -> None:
    artifact_path = _desktop_artifact_dir(root, artifact_dir)
    required_files = (
        artifact_path / DESKTOP_EXE,
        root / "main.py",
        root / "configs" / "app_config.json",
        root / "install_env.bat",
        root / "requirements-runtime.txt",
    )
    missing = [str(path.relative_to(root)) for path in required_files if not path.is_file()]
    projects_dir = root / "configs" / "projects"
    project_files = [
        path
        for path in projects_dir.glob("*.json")
        if path.name != "project_template.json"
    ] if projects_dir.is_dir() else []
    if not project_files:
        missing.append("configs/projects/*.json")
    if missing:
        raise ValueError("首次安装包源文件不完整：" + "、".join(missing))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_fingerprint(root: Path) -> str:
    candidates = [root / "main.py", root / "menu.py"]
    for folder in (root / "gui", root / "modules"):
        if folder.is_dir():
            candidates.extend(folder.rglob("*.py"))
    kst_resources = root / "modules" / "kst_local" / "resources"
    if kst_resources.is_dir():
        candidates.extend(kst_resources.rglob("*"))
    candidates.extend(
        (root / "assets" / name)
        for name in ("app_icon.ico", "app_icon.png", "app_icon_exe.png")
    )
    candidates.extend(
        root / "tools" / name
        for name in ("build_desktop_exe.py", "hourlyreport_automation.spec")
    )
    fonts_dir = root / "assets" / "fonts"
    if fonts_dir.is_dir():
        candidates.extend(fonts_dir.rglob("*"))
    digest = hashlib.sha256()
    for path in sorted({item for item in candidates if item.is_file()}, key=lambda item: item.relative_to(root).as_posix()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _source_version(root: Path) -> str:
    version_file = root / "gui" / "version.py"
    text = version_file.read_text(encoding="utf-8")
    match = re.search(r'^CURRENT_VERSION\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
    if not match:
        raise ValueError(f"无法读取源码版本：{version_file}")
    return validate_online_version(match.group(1))


def _desktop_artifact_dir(
    root: Path,
    artifact_dir: str | Path | None,
) -> Path:
    if artifact_dir is not None:
        return Path(artifact_dir)
    try:
        version = _source_version(root)
    except (OSError, ValueError):
        version = "unversioned"
    return root / "build" / f"release_{version}_staging"


def _validate_desktop_build(
    root: Path,
    expected_version: str,
    artifact_dir: str | Path | None,
) -> None:
    artifact_path = _desktop_artifact_dir(root, artifact_dir)
    executable = artifact_path / DESKTOP_EXE
    manifest_path = artifact_path / DESKTOP_BUILD_MANIFEST
    if not manifest_path.is_file():
        raise ValueError(f"缺少桌面程序构建清单：{manifest_path.relative_to(root)}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("桌面程序构建清单无效") from exc
    if str(manifest.get("version") or "") != expected_version:
        raise ValueError("桌面程序构建清单版本与发布版本不一致")
    if str(manifest.get("executable") or "").casefold() != DESKTOP_EXE.casefold():
        raise ValueError("桌面程序构建清单的 EXE 名称无效")
    if int(manifest.get("size") or 0) != executable.stat().st_size:
        raise ValueError("桌面程序构建清单大小与 EXE 不一致")
    if str(manifest.get("sha256") or "").lower() != _sha256(executable):
        raise ValueError("桌面程序构建清单 SHA-256 与 EXE 不一致")
    if str(manifest.get("source_sha256") or "").lower() != _source_fingerprint(root):
        raise ValueError("桌面程序构建清单与当前源码不一致，请重新构建 EXE")


def _validate_online_update_source(
    root: Path,
    version: str,
    artifact_dir: str | Path | None,
) -> None:
    artifact_path = _desktop_artifact_dir(root, artifact_dir)
    required_files = (
        artifact_path / DESKTOP_EXE,
        root / "main.py",
        root / "gui" / "version.py",
        artifact_path / DESKTOP_BUILD_MANIFEST,
    )
    missing = [str(path.relative_to(root)) for path in required_files if not path.is_file()]
    if missing:
        raise ValueError("在线更新包源文件不完整：" + "、".join(missing))
    clean_version = validate_online_version(version)
    source_version = _source_version(root)
    if source_version != clean_version:
        raise ValueError(f"源码版本 {source_version} 与发布版本 {clean_version} 不一致")
    _validate_desktop_build(root, clean_version, artifact_path)


def build_release(
    root: str | Path,
    version: str | None = None,
    internal: bool = False,
    online_update: bool = False,
    first_install: bool = False,
    output_dir: str | Path | None = None,
    artifact_dir: str | Path | None = None,
) -> Path:
    if sum((bool(internal), bool(online_update), bool(first_install))) > 1:
        raise ValueError("内部包、首次安装包与在线更新包不能同时生成")
    root_path = Path(root)
    dist_dir = (
        Path(output_dir)
        if output_dir is not None
        else root_path / "build" / "releases"
    )
    dist_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = _desktop_artifact_dir(
        root_path,
        artifact_dir,
    )
    if first_install:
        _validate_first_install_source(root_path, artifact_path)
        clean_version = validate_online_version(version or "")
        source_version = _source_version(root_path)
        if source_version != clean_version:
            raise ValueError(f"源码版本 {source_version} 与发布版本 {clean_version} 不一致")
        _validate_desktop_build(
            root_path,
            clean_version,
            artifact_path,
        )
    if online_update:
        _validate_online_update_source(
            root_path,
            version or "",
            artifact_path,
        )
    release_path = dist_dir / release_name(
        version,
        internal=internal,
        online_update=online_update,
        first_install=first_install,
    )
    if release_path.exists():
        release_path.unlink()

    with zipfile.ZipFile(release_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        executable = artifact_path / DESKTOP_EXE
        if (
            (internal or online_update or first_install)
            and executable.is_file()
        ):
            archive.write(executable, DESKTOP_EXE)
        for path in root_path.rglob("*"):
            if path.is_dir():
                continue
            rel = path.relative_to(root_path)
            if should_include_file(
                rel,
                internal=internal,
                online_update=online_update,
                first_install=first_install,
            ):
                archive_name = rel.as_posix()
                if rel.as_posix() == "configs/app_config.json":
                    app_config = json.loads(path.read_text(encoding="utf-8"))
                    app_config.pop("desktop_pet_position", None)
                    app_config["desktop_pet"] = "clawd"
                    app_config["desktop_pet_scale"] = 1.0
                    archive.writestr(archive_name, json.dumps(app_config, ensure_ascii=False, indent=2) + "\n")
                else:
                    archive.write(path, archive_name)
    return release_path


def main() -> None:
    parser = argparse.ArgumentParser(description="构建百度竞价日报/小时报自动化工具发布包")
    parser.add_argument("--version", default=DEFAULT_VERSION, help="发布版本号，例如 2.0 或 v2.0")
    parser.add_argument("--internal", action="store_true", help="构建内部包（不包含本机账号和授权配置）")
    parser.add_argument("--first-install", action="store_true", help="构建新电脑首次安装包（包含默认配置，不包含真实凭据）")
    parser.add_argument("--online-update", action="store_true", help="构建在线更新包（包含 GUI，排除所有用户配置）")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]

    release_path = build_release(
        root,
        version=args.version,
        internal=args.internal,
        online_update=args.online_update,
        first_install=args.first_install,
        output_dir=root / "dist",
    )
    size_mb = release_path.stat().st_size / 1024 / 1024
    if args.online_update:
        pkg_type = "在线更新包"
    elif args.first_install:
        pkg_type = "首次安装包"
    else:
        pkg_type = "内部包" if args.internal else "普通包"
    print(f"{pkg_type}已生成：{release_path}")
    print(f"大小：{size_mb:.2f} MB")


if __name__ == "__main__":
    main()
