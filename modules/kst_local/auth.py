from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from pathlib import Path


TOKEN_ENV = "KST_LOCAL_API_TOKEN"
TOKEN_FILENAME = "kst_local_api_token"
HEALTH_CHALLENGE = b"hourlyreport-kst-local-api-health-v1"


class KstLocalAuthError(RuntimeError):
    """The local KST API token could not be loaded safely."""


def _token_file(root: str | Path) -> Path:
    return Path(root) / "runtime" / TOKEN_FILENAME


def _valid_token(value: str) -> bool:
    return len(value) >= 32 and not any(character.isspace() for character in value)


def load_or_create_local_token(root: str | Path) -> str:
    configured = os.environ.get(TOKEN_ENV, "").strip()
    if configured:
        if not _valid_token(configured):
            raise KstLocalAuthError("商务通本地 API 环境令牌长度不足")
        return configured

    path = _token_file(root)
    try:
        existing = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        existing = ""
    except OSError as exc:
        raise KstLocalAuthError("无法读取商务通本地 API 令牌") from exc
    if existing:
        if not _valid_token(existing):
            raise KstLocalAuthError("商务通本地 API 令牌文件无效")
        return existing

    path.parent.mkdir(parents=True, exist_ok=True)
    generated = secrets.token_urlsafe(32)
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
    except FileExistsError:
        try:
            concurrent = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise KstLocalAuthError("无法读取商务通本地 API 令牌") from exc
        if not _valid_token(concurrent):
            raise KstLocalAuthError("商务通本地 API 令牌文件无效")
        return concurrent
    except OSError as exc:
        raise KstLocalAuthError("无法创建商务通本地 API 令牌") from exc

    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(generated)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as exc:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise KstLocalAuthError("无法保存商务通本地 API 令牌") from exc
    return generated


def local_health_proof(token: str) -> str:
    if not _valid_token(token):
        raise KstLocalAuthError("商务通本地 API 令牌无效")
    return hmac.new(
        token.encode("utf-8"),
        HEALTH_CHALLENGE,
        hashlib.sha256,
    ).hexdigest()
