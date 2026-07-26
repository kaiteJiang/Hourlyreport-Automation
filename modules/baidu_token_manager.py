from __future__ import annotations

import hashlib
import hmac
import json
import os
import shutil
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterator


REFRESH_WINDOW = timedelta(minutes=10)
REFRESH_TIMEOUT_SECONDS = 20
REFRESH_TIMESTAMP_HEADER = "X-Baidu-Refresh-Timestamp"
REFRESH_SIGNATURE_HEADER = "X-Baidu-Refresh-Signature"


class BaiduTokenError(RuntimeError):
    def __init__(
        self,
        category: str,
        message: str,
        reauthorization_required: bool = False,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.reauthorization_required = reauthorization_required


def _read_secrets(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise BaiduTokenError("configuration_error", "找不到百度授权配置") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise BaiduTokenError("configuration_error", "百度授权配置无法读取") from exc
    if not isinstance(payload, dict):
        raise BaiduTokenError("configuration_error", "百度授权配置结构无效")
    return payload


def _parse_expiry(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _seconds_until(expiry: datetime | None, now: datetime) -> float | None:
    if expiry is None:
        return None
    comparable_now = now
    if expiry.tzinfo is not None and comparable_now.tzinfo is None:
        comparable_now = comparable_now.replace(tzinfo=expiry.tzinfo)
    elif expiry.tzinfo is None and comparable_now.tzinfo is not None:
        expiry = expiry.replace(tzinfo=comparable_now.tzinfo)
    return (expiry - comparable_now).total_seconds()


def _signature(timestamp: str, payload: dict[str, Any], client_key: str) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    message = "%s\n%s" % (timestamp, canonical)
    return hmac.new(
        client_key.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _post_refresh(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout_seconds: float,
) -> dict[str, Any]:
    request_headers = {"Content-Type": "application/json; charset=utf-8", **headers}
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        headers=request_headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        response_code = ""
        try:
            response_payload = json.loads(exc.read().decode("utf-8"))
            response_code = str(response_payload.get("code") or "") if isinstance(response_payload, dict) else ""
        except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
            pass
        if response_code in {"refresh_failed", "reauthorization_required"}:
            raise BaiduTokenError(
                "reauthorization_required",
                "百度 API 授权已失效，需要重新授权",
                reauthorization_required=True,
            ) from exc
        if response_code in {
            "app_id_mismatch",
            "refresh_signature_mismatch",
            "expired_refresh_request",
            "invalid_refresh_request",
            "invalid_refresh_timestamp",
            "missing_refresh_signature",
            "server_config_error",
        }:
            raise BaiduTokenError("configuration_error", "百度授权令牌刷新配置无效") from exc
        if 500 <= int(exc.code) <= 599:
            raise BaiduTokenError("token_refresh_error", "百度授权令牌刷新服务暂时不可用") from exc
        if int(exc.code) in {400, 401, 403}:
            raise BaiduTokenError("configuration_error", "百度授权令牌刷新请求被拒绝") from exc
        raise BaiduTokenError("refresh_error", "百度授权令牌刷新请求被拒绝") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise BaiduTokenError("token_refresh_error", "百度授权令牌刷新服务暂时不可用") from exc
    except (ValueError, json.JSONDecodeError) as exc:
        raise BaiduTokenError("refresh_error", "百度授权令牌刷新响应无效") from exc
    if not isinstance(result, dict):
        raise BaiduTokenError("refresh_error", "百度授权令牌刷新响应无效")
    return result


def _signed_post(
    url: str,
    payload: dict[str, Any],
    client_key: str,
    current_time: datetime,
    timeout_seconds: float,
    transport: Callable[[str, dict[str, Any], dict[str, str], float], dict[str, Any]],
) -> dict[str, Any]:
    timestamp = str(int(current_time.timestamp()))
    headers = {
        REFRESH_TIMESTAMP_HEADER: timestamp,
        REFRESH_SIGNATURE_HEADER: _signature(timestamp, payload, client_key),
    }
    return transport(url, payload, headers, timeout_seconds)


@contextmanager
def _exclusive_lock(
    path: Path,
    *,
    timeout_seconds: float | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
            os.fsync(handle.fileno())
        handle.seek(0)
        deadline = None if timeout_seconds is None else clock() + max(0.0, float(timeout_seconds))
        try:
            import msvcrt

            if deadline is None:
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            else:
                while True:
                    try:
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError as exc:
                        remaining = deadline - clock()
                        if remaining <= 0:
                            raise TimeoutError("secrets lock timeout") from exc
                        sleep(min(0.05, remaining))
            unlock = lambda: msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except ImportError:
            import fcntl

            if deadline is None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            else:
                while True:
                    try:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError as exc:
                        remaining = deadline - clock()
                        if remaining <= 0:
                            raise TimeoutError("secrets lock timeout") from exc
                        sleep(min(0.05, remaining))
            unlock = lambda: fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        try:
            yield
        finally:
            handle.seek(0)
            unlock()
    finally:
        handle.close()


@contextmanager
def secrets_file_lock(
    credentials_path: Path,
    *,
    timeout_seconds: float | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> Iterator[None]:
    path = Path(credentials_path)
    try:
        with _exclusive_lock(
            path.with_name(path.name + ".lock"),
            timeout_seconds=timeout_seconds,
            clock=clock,
            sleep=sleep,
        ):
            yield
    except TimeoutError as exc:
        raise BaiduTokenError("token_refresh_error", "百度授权配置正被占用，刷新预算已用尽") from exc


def _safe_metadata(api_profile: str, token_refresh: str, expires_time: Any) -> dict[str, Any]:
    return {
        "api_profile": api_profile,
        "token_refresh": token_refresh,
        "expires_time": expires_time,
    }


def _validate_refresh_config(
    secrets: dict[str, Any],
    api_profile: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    profile = (secrets.get("baidu_api") or {}).get(api_profile)
    gateway = secrets.get("baidu_api_gateway")
    if not isinstance(profile, dict):
        raise BaiduTokenError("configuration_error", "当前项目缺少百度 API 授权")
    if not isinstance(gateway, dict):
        raise BaiduTokenError("configuration_error", "当前电脑缺少百度 API 刷新配置")
    required_gateway = ("refresh_url", "client_key", "app_id")
    if any(not str(gateway.get(key) or "").strip() for key in required_gateway):
        raise BaiduTokenError("configuration_error", "当前电脑的百度 API 刷新配置不完整")
    if not str(gateway["refresh_url"]).lower().startswith("https://"):
        raise BaiduTokenError("configuration_error", "百度 API 刷新地址必须使用 HTTPS")
    if str(profile.get("app_id") or "") != str(gateway["app_id"]):
        raise BaiduTokenError("configuration_error", "百度 API 授权与刷新服务应用不匹配")
    return profile, gateway


def _validate_cloud_token_config(
    secrets: dict[str, Any],
    api_profile: str,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    profile = (secrets.get("baidu_api") or {}).get(api_profile)
    gateway = secrets.get("baidu_api_gateway")
    if not isinstance(profile, dict) or not isinstance(gateway, dict):
        return None
    token_url = str(gateway.get("token_url") or "").strip()
    client_key = str(gateway.get("client_key") or "").strip()
    app_id = str(gateway.get("app_id") or "").strip()
    if not token_url:
        return None
    if not token_url.lower().startswith("https://") or not client_key or not app_id:
        raise BaiduTokenError("configuration_error", "baidu cloud token config incomplete")
    if str(profile.get("app_id") or "").strip() != app_id:
        raise BaiduTokenError("configuration_error", "baidu cloud token app mismatch")
    return profile, gateway


def ensure_valid_access_token(
    config: dict[str, Any],
    root: Path,
    api_profile: str,
    now: datetime | None = None,
    transport: Callable[[str, dict[str, Any], dict[str, str], int], dict[str, Any]] | None = None,
    force_refresh: bool = False,
    timeout_seconds: float | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[str, dict[str, Any]]:
    profile_name = str(api_profile or "").strip()
    if not profile_name:
        raise BaiduTokenError("configuration_error", "当前项目未指定百度 API profile")
    root = Path(root)
    credentials_path = Path(config.get("credentials_path", "secrets/secrets.json"))
    if not credentials_path.is_absolute():
        credentials_path = root / credentials_path
    current_time = now or datetime.now().astimezone()
    request_transport = transport or _post_refresh
    operation_deadline = None
    if timeout_seconds is not None:
        operation_deadline = clock() + max(0.0, float(timeout_seconds))

    with secrets_file_lock(
        credentials_path,
        timeout_seconds=timeout_seconds,
        clock=clock,
        sleep=sleep,
    ):
        secrets = _read_secrets(credentials_path)
        profile, gateway = _validate_refresh_config(secrets, profile_name)
        access_token = str(profile.get("access_token") or "").strip()
        expires_time = profile.get("expires_time")
        seconds_left = _seconds_until(_parse_expiry(expires_time), current_time)
        if access_token and not force_refresh and seconds_left is not None and seconds_left > REFRESH_WINDOW.total_seconds():
            return access_token, _safe_metadata(profile_name, "not_needed", expires_time)

        refresh_token = str(profile.get("refresh_token") or "").strip()
        try:
            user_id = int(profile.get("user_id"))
        except (TypeError, ValueError) as exc:
            raise BaiduTokenError("configuration_error", "百度 API 授权用户 ID 无效") from exc
        if not refresh_token or user_id <= 0:
            raise BaiduTokenError(
                "reauthorization_required",
                "百度 API 授权已失效，需要重新授权",
                reauthorization_required=True,
            )
        refresh_seconds_left = _seconds_until(_parse_expiry(profile.get("refresh_expires_time")), current_time)
        if refresh_seconds_left is not None and refresh_seconds_left <= 0:
            raise BaiduTokenError(
                "reauthorization_required",
                "百度 API 授权已过期，需要重新授权",
                reauthorization_required=True,
            )

        payload = {
            "appId": str(gateway["app_id"]),
            "userId": user_id,
            "refreshToken": refresh_token,
        }
        timestamp = str(int(current_time.timestamp()))
        headers = {
            REFRESH_TIMESTAMP_HEADER: timestamp,
            REFRESH_SIGNATURE_HEADER: _signature(timestamp, payload, str(gateway["client_key"])),
        }
        request_timeout = float(REFRESH_TIMEOUT_SECONDS)
        if operation_deadline is not None:
            request_timeout = min(request_timeout, operation_deadline - clock())
        if request_timeout <= 0:
            raise BaiduTokenError("token_refresh_error", "百度授权令牌刷新预算已用尽")
        try:
            response = request_transport(
                str(gateway["refresh_url"]),
                payload,
                headers,
                request_timeout,
            )
        except BaiduTokenError:
            raise
        except Exception as exc:
            raise BaiduTokenError("token_refresh_error", "百度授权令牌刷新服务暂时不可用") from exc

        authorization = response.get("authorization") if isinstance(response, dict) else None
        if response.get("status") != "ok" or not isinstance(authorization, dict):
            if str(response.get("code") or "") in {"refresh_failed", "reauthorization_required"}:
                raise BaiduTokenError(
                    "reauthorization_required",
                    "百度 API 授权已失效，需要重新授权",
                    reauthorization_required=True,
                )
            raise BaiduTokenError("refresh_error", "百度授权令牌刷新失败")
        new_access_token = str(authorization.get("access_token") or "").strip()
        new_refresh_token = str(authorization.get("refresh_token") or "").strip()
        if not new_access_token or not new_refresh_token:
            raise BaiduTokenError("refresh_error", "百度授权令牌刷新结果不完整")

        updated_profile = dict(profile)
        for key in (
            "access_token",
            "refresh_token",
            "open_id",
            "expires_time",
            "refresh_expires_time",
            "expires_in",
            "refresh_expires_in",
        ):
            if authorization.get(key) is not None:
                updated_profile[key] = authorization[key]
        updated_profile["refreshed_at"] = current_time.isoformat(timespec="seconds")
        secrets.setdefault("baidu_api", {})[profile_name] = updated_profile

        backup_dir = root / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_stamp = current_time.strftime("%Y%m%d_%H%M%S_%f")
        backup_name = "secrets_before_token_refresh_%s_%s.json" % (profile_name, backup_stamp)
        backup_path = backup_dir / backup_name
        temp_path = credentials_path.with_suffix(credentials_path.suffix + ".tmp")
        try:
            shutil.copy2(credentials_path, backup_path)
            with temp_path.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(secrets, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, credentials_path)
        except Exception as exc:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise BaiduTokenError("storage_error", "百度授权令牌保存失败") from exc

        return new_access_token, _safe_metadata(
            profile_name,
            "refreshed",
            updated_profile.get("expires_time"),
        )


def ensure_valid_access_token_cloud_first(
    config: dict[str, Any],
    root: Path,
    api_profile: str,
    now: datetime | None = None,
    transport: Callable[[str, dict[str, Any], dict[str, str], int], dict[str, Any]] | None = None,
    force_refresh: bool = False,
    timeout_seconds: float | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[str, dict[str, Any]]:
    profile_name = str(api_profile or "").strip()
    if not profile_name:
        raise BaiduTokenError("configuration_error", "current project missing baidu api profile")
    root = Path(root)
    credentials_path = Path(config.get("credentials_path", "secrets/secrets.json"))
    if not credentials_path.is_absolute():
        credentials_path = root / credentials_path
    current_time = now or datetime.now().astimezone()
    request_transport = transport or _post_refresh

    secrets = _read_secrets(credentials_path)
    cloud_config = _validate_cloud_token_config(secrets, profile_name)
    if cloud_config is None:
        raise BaiduTokenError(
            "configuration_error",
            "当前电脑缺少云端 Token 配置，请导入管理员最新授权配置",
        )
    _profile, gateway = cloud_config
    request_timeout = float(REFRESH_TIMEOUT_SECONDS)
    if timeout_seconds is not None:
        request_timeout = min(request_timeout, max(0.0, float(timeout_seconds)))
    if request_timeout <= 0:
        raise BaiduTokenError("token_refresh_error", "baidu cloud token budget exhausted")

    payload = {"apiProfile": profile_name, "forceRefresh": bool(force_refresh)}
    try:
        response = _signed_post(
            str(gateway["token_url"]),
            payload,
            str(gateway["client_key"]),
            current_time,
            request_timeout,
            request_transport,
        )
    except BaiduTokenError:
        raise
    except Exception as exc:
        raise BaiduTokenError("token_refresh_error", "baidu cloud token service unavailable") from exc

    authorization = response.get("authorization") if isinstance(response, dict) else None
    if response.get("status") != "ok" or not isinstance(authorization, dict):
        if str(response.get("code") or "") in {
            "reauthorization_required",
            "token_profile_missing",
            "cloud_token_refresh_failed",
        }:
            raise BaiduTokenError(
                "reauthorization_required",
                "baidu cloud authorization requires reauthorization",
                reauthorization_required=True,
            )
        raise BaiduTokenError("token_refresh_error", "baidu cloud token request failed")
    access_token = str(authorization.get("access_token") or "").strip()
    if not access_token:
        raise BaiduTokenError("token_refresh_error", "baidu cloud token response incomplete")
    metadata = _safe_metadata(
        profile_name,
        str(authorization.get("token_refresh") or "cloud"),
        authorization.get("expires_time"),
    )
    metadata["token_source"] = "cloud"
    metadata["user_id"] = authorization.get("user_id")
    return access_token, metadata
