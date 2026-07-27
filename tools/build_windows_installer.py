from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

try:
    from tools.build_release import build_release, validate_online_version
except ModuleNotFoundError:  # Direct execution places tools/ first on sys.path.
    from build_release import build_release, validate_online_version


INSTALLER_SCRIPT = "hourlyreport_automation_installer.iss"


def installer_name(version: str) -> str:
    clean_version = validate_online_version(version)
    return f"Hourlyreport_automation_setup_v{clean_version}.exe"


def find_inno_compiler() -> Path | None:
    configured = os.environ.get("INNO_SETUP_COMPILER")
    candidates = [
        Path(configured) if configured else None,
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe",
        Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
        Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
    ]
    command = shutil.which("ISCC.exe") or shutil.which("iscc.exe")
    if command:
        candidates.insert(0, Path(command))
    return next((path for path in candidates if path is not None and path.is_file()), None)


def build_windows_installer(
    root: str | Path,
    version: str,
    *,
    compiler: str | Path | None = None,
    artifact_dir: str | Path | None = None,
    output_dir: str | Path | None = None,
) -> Path:
    root_path = Path(root).resolve()
    clean_version = validate_online_version(version)
    compiler_path = Path(compiler) if compiler else find_inno_compiler()
    if compiler_path is None or not compiler_path.is_file():
        raise FileNotFoundError("未找到 Inno Setup 6 编译器 ISCC.exe")

    script = root_path / "tools" / INSTALLER_SCRIPT
    if not script.is_file():
        raise FileNotFoundError(f"缺少安装器定义：{script}")
    installer_output_dir = (
        Path(output_dir)
        if output_dir is not None
        else root_path / "dist"
    )
    installer_output_dir.mkdir(parents=True, exist_ok=True)
    expected_output = installer_output_dir / installer_name(clean_version)
    expected_output.unlink(missing_ok=True)

    build_dir = root_path / "build"
    build_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="installer_", dir=build_dir) as temp_dir:
        temp_root = Path(temp_dir)
        package = build_release(
            root_path,
            version=clean_version,
            first_install=True,
            output_dir=temp_root,
            artifact_dir=artifact_dir,
        )
        payload = temp_root / "payload"
        payload.mkdir()
        with zipfile.ZipFile(package) as archive:
            archive.extractall(payload)

        command = [
            str(compiler_path),
            f"/DAppVersion={clean_version}",
            f"/DPayloadDir={payload}",
            f"/DInstallerOutput={installer_output_dir}",
            str(script),
        ]
        result = subprocess.run(command, cwd=root_path)
        if result.returncode != 0:
            raise RuntimeError(f"Inno Setup 构建失败，退出码 {result.returncode}")

    if not expected_output.is_file() or expected_output.stat().st_size <= 0:
        raise RuntimeError(f"安装器未生成：{expected_output}")
    return expected_output


def main() -> int:
    parser = argparse.ArgumentParser(description="构建可选择安装目录的 Windows 完整安装程序")
    parser.add_argument("--version", required=True, help="版本号，例如 2026.7.19.106")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    try:
        output = build_windows_installer(root, args.version)
    except Exception as exc:
        print(f"[失败] {exc}")
        return 1
    print(f"Windows 完整安装器已生成：{output}")
    print(f"大小：{output.stat().st_size / 1024 / 1024:.2f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
