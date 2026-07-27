from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

from modules.kst_local.models import KstAuthContext


class KstApiError(RuntimeError):
    """商务通只读接口调用失败。"""


Transport = Callable[[str, dict[str, Any], dict[str, str], int], Any]


def _default_transport(
    url: str,
    params: dict[str, Any],
    headers: dict[str, str],
    timeout: int,
) -> Any:
    request = urllib.request.Request(
        f"{url}?{urllib.parse.urlencode(params)}",
        data=b"",
        method="POST",
        headers={"Content-Type": "application/json", **headers},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
        status = getattr(exc, "code", "network")
        raise RuntimeError(f"HTTP {status}") from None
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError("response is not JSON") from exc


def _find_tag_pairs(value: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    if isinstance(value, dict):
        type_id = value.get("typeid", value.get("typeId"))
        type_name = value.get("typename", value.get("typeName"))
        if type_id not in (None, "") and type_name not in (None, ""):
            result[str(type_id)] = str(type_name)
        for item in value.values():
            result.update(_find_tag_pairs(item))
    elif isinstance(value, list):
        for item in value:
            result.update(_find_tag_pairs(item))
    return result


class KstApiClient:
    def __init__(
        self,
        auth: KstAuthContext,
        *,
        transport: Transport = _default_transport,
        timeout: int = 30,
    ) -> None:
        self._auth = auth
        self._transport = transport
        self._timeout = timeout

    def _request(self, endpoint_name: str, params: dict[str, Any]) -> Any:
        endpoint = self._auth.endpoints.get(endpoint_name)
        if not endpoint:
            raise KstApiError(f"缺少商务通接口地址：{endpoint_name}")
        query = dict(self._auth.common_query)
        query.update(params)
        try:
            payload = self._transport(
                endpoint,
                query,
                dict(self._auth.headers),
                self._timeout,
            )
        except Exception as exc:
            raise KstApiError(f"商务通只读接口失败：{endpoint_name}") from None
        if not isinstance(payload, dict):
            raise KstApiError(f"商务通接口响应结构不兼容：{endpoint_name}")
        code = payload.get("code")
        if code not in (None, 0, "0", 8, "8"):
            raise KstApiError(f"商务通接口返回失败状态：{endpoint_name}")
        return payload

    def load_visitor(self, rec_id: str) -> dict[str, Any]:
        payload = self._request(
            "visitor_info",
            {
                "channelType": "online",
                "recId": rec_id,
                "time": "",
            },
        )
        bean = payload.get("bean")
        if not isinstance(bean, dict) or not bean:
            raise KstApiError(f"商务通会话信息为空：recId={rec_id}")
        return bean

    def load_card(self, visitor_id: str) -> dict[str, Any]:
        payload = self._request(
            "visitor_card",
            {
                "visitorId": visitor_id,
                "channelType": 1,
            },
        )
        bean = payload.get("bean")
        if not isinstance(bean, dict):
            raise KstApiError("商务通名片响应为空：visitor_card")
        return bean

    def load_tag_dictionary(self) -> dict[str, str]:
        payload = self._request("tag_dictionary", {})
        return _find_tag_pairs(payload)
