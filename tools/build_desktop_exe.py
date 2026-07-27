from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path


APP_NAME = "hourlyreport_automation"
BUILD_MANIFEST_NAME = f"{APP_NAME}.build.json"


def desktop_staging_dir(
    root: str | Path,
    version: str | None = None,
) -> Path:
    root_path = Path(root)
    build_version = str(
        version or read_source_version(root_path)
    ).strip()
    return root_path / "build" / f"release_{build_version}_staging"


def read_source_version(root: str | Path) -> str:
    version_file = Path(root) / "gui" / "version.py"
    text = version_file.read_text(encoding="utf-8")
    match = re.search(r'^CURRENT_VERSION\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
    if not match:
        raise ValueError(f"无法读取程序版本：{version_file}")
    return match.group(1).strip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_fingerprint(root: str | Path) -> str:
    root_path = Path(root)
    candidates = [root_path / "main.py", root_path / "menu.py"]
    for folder in (root_path / "gui", root_path / "modules"):
        if folder.is_dir():
            candidates.extend(folder.rglob("*.py"))
    kst_resources = root_path / "modules" / "kst_local" / "resources"
    if kst_resources.is_dir():
        candidates.extend(kst_resources.rglob("*"))
    candidates.extend(
        (root_path / "assets" / name)
        for name in ("app_icon.ico", "app_icon.png", "app_icon_exe.png")
    )
    candidates.extend(
        root_path / "tools" / name
        for name in ("build_desktop_exe.py", "hourlyreport_automation.spec")
    )
    fonts_dir = root_path / "assets" / "fonts"
    if fonts_dir.is_dir():
        candidates.extend(fonts_dir.rglob("*"))
    digest = hashlib.sha256()
    for path in sorted({item for item in candidates if item.is_file()}, key=lambda item: item.relative_to(root_path).as_posix()):
        relative = path.relative_to(root_path).as_posix().encode("utf-8")
        digest.update(relative)
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def write_build_manifest(root: str | Path, executable: str | Path, version: str | None = None) -> Path:
    root_path = Path(root)
    executable_path = Path(executable)
    build_version = str(version or read_source_version(root_path)).strip()
    manifest_path = executable_path.parent / BUILD_MANIFEST_NAME
    payload = {
        "version": build_version,
        "executable": executable_path.name,
        "size": executable_path.stat().st_size,
        "sha256": _sha256(executable_path),
        "source_sha256": source_fingerprint(root_path),
    }
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest_path


def build_desktop_exe(
    root: str | Path,
    *,
    output_dir: str | Path | None = None,
) -> int:
    root_path = Path(root)
    build_version = read_source_version(root_path)
    target_dir = (
        Path(output_dir)
        if output_dir is not None
        else desktop_staging_dir(root_path, build_version)
    )
    python = root_path / ".venv" / "Scripts" / "python.exe"
    if not python.exists():
        print("[失败] 缺少 .venv\\Scripts\\python.exe，请先运行 install_env.bat")
        return 1
    spec = root_path / "tools" / "hourlyreport_automation.spec"
    command = [
        str(python),
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--distpath",
        str(target_dir),
        "--workpath",
        str(root_path / "build" / APP_NAME),
        str(spec),
    ]
    result = subprocess.run(command, cwd=root_path)
    if result.returncode != 0:
        return int(result.returncode)
    output = target_dir / f"{APP_NAME}.exe"
    if not output.exists() or output.stat().st_size == 0:
        print(f"[失败] PyInstaller 未生成预期文件：{output}")
        return 1
    manifest = write_build_manifest(
        root_path,
        output,
        build_version,
    )
    print(f"[完成] 单文件 GUI：{output}")
    print(f"[完成] 构建清单：{manifest}")
    return 0


def main() -> int:
    return build_desktop_exe(Path(__file__).resolve().parents[1])


if __name__ == "__main__":
    raise SystemExit(main())
