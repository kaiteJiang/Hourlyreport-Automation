from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path


class KstMachineSettingsError(RuntimeError):
    """本机路径设置无效或无法安全读取。"""


@dataclass(frozen=True)
class KstMachineSettings:
    installation_root: Path | None = None
    data_root: Path | None = None


_SETTING_KEYS = frozenset({"installation_root", "data_root"})


def _settings_path(root: str | Path) -> Path:
    return Path(root) / "runtime" / "kst_machine_settings.json"


def _normalise_path(value: str | Path | None, *, field: str) -> Path | None:
    if value is None:
        return None
    if not isinstance(value, (str, Path)):
        raise KstMachineSettingsError(f"本机路径设置的 {field} 必须为路径字符串")
    text = str(value).strip()
    return Path(text) if text else None


def load_kst_machine_settings(root: str | Path) -> KstMachineSettings:
    path = _settings_path(root)
    if not path.exists():
        return KstMachineSettings()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise KstMachineSettingsError("本机路径设置无法读取") from exc
    if not isinstance(payload, dict) or set(payload) - _SETTING_KEYS:
        raise KstMachineSettingsError("本机路径设置格式无效")
    try:
        return KstMachineSettings(
            installation_root=_normalise_path(
                payload.get("installation_root"), field="installation_root"
            ),
            data_root=_normalise_path(payload.get("data_root"), field="data_root"),
        )
    except KstMachineSettingsError:
        raise
    except (TypeError, ValueError) as exc:
        raise KstMachineSettingsError("本机路径设置格式无效") from exc


def save_kst_machine_settings(
    root: str | Path,
    *,
    installation_root: str | Path | None,
    data_root: str | Path | None,
) -> KstMachineSettings:
    settings = KstMachineSettings(
        installation_root=_normalise_path(
            installation_root, field="installation_root"
        ),
        data_root=_normalise_path(data_root, field="data_root"),
    )
    path = _settings_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "installation_root": (
            str(settings.installation_root) if settings.installation_root else None
        ),
        "data_root": str(settings.data_root) if settings.data_root else None,
    }
    temporary = path.with_suffix(".json.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(path)
    except OSError as exc:
        raise KstMachineSettingsError("本机路径设置无法保存") from exc
    return load_kst_machine_settings(root)
