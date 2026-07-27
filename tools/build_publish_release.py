from __future__ import annotations

import argparse
import shutil
from pathlib import Path

try:
    from tools.build_desktop_exe import (
        build_desktop_exe,
        desktop_staging_dir,
    )
    from tools.build_release import (
        build_release,
        validate_online_version,
    )
    from tools.build_windows_installer import build_windows_installer
except ModuleNotFoundError:
    from build_desktop_exe import build_desktop_exe, desktop_staging_dir
    from build_release import build_release, validate_online_version
    from build_windows_installer import build_windows_installer


def _validated_dist(root: Path) -> Path:
    resolved_root = root.resolve()
    dist = (resolved_root / "dist").resolve()
    if dist.parent != resolved_root or dist.name != "dist":
        raise RuntimeError("发布目录校验失败")
    return dist


def _clear_dist(root: Path) -> Path:
    dist = _validated_dist(root)
    dist.mkdir(parents=True, exist_ok=True)
    for child in tuple(dist.iterdir()):
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
    return dist


def build_publish_release(
    root: str | Path,
    version: str,
    *,
    compiler: str | Path | None = None,
) -> tuple[Path, Path]:
    root_path = Path(root).resolve()
    clean_version = validate_online_version(version)
    dist = _clear_dist(root_path)
    staging = desktop_staging_dir(root_path, clean_version)

    result = build_desktop_exe(
        root_path,
        output_dir=staging,
    )
    if result != 0:
        raise RuntimeError("桌面 EXE 构建失败")

    update = build_release(
        root_path,
        version=clean_version,
        online_update=True,
        artifact_dir=staging,
        output_dir=dist,
    )
    installer = build_windows_installer(
        root_path,
        clean_version,
        compiler=compiler,
        artifact_dir=staging,
        output_dir=dist,
    )
    expected = {update.name, installer.name}
    actual = {path.name for path in dist.iterdir()}
    if actual != expected or not all(path.is_file() for path in dist.iterdir()):
        raise RuntimeError("dist 必须只包含当前在线更新包和完整安装器")
    return update, installer


def main() -> int:
    parser = argparse.ArgumentParser(
        description="构建当前版本的在线更新包和完整安装器",
    )
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    try:
        update, installer = build_publish_release(root, args.version)
    except Exception as exc:
        print(f"[失败] {exc}")
        return 1
    print(f"在线更新包：{update}")
    print(f"完整安装器：{installer}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
