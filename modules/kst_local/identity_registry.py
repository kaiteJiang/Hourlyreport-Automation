from __future__ import annotations

from collections import OrderedDict, defaultdict
from datetime import date
import inspect
from pathlib import Path
import re
import threading
import time
from typing import Any, Callable

from modules.kst_local.backend import (
    build_installation_runtime,
    installation_active,
    installation_ready,
    installation_runtime_state,
    read_installation_promotion_ids,
)
from modules.kst_local.discovery import (
    KstDiscoveryError,
    discover_all_installations,
    most_specific_discovery_error,
)
from modules.kst_local.fingerprint import (
    capture_installation_identity,
    installation_identity_fingerprint,
)
from modules.kst_local.log_source import parse_cached_log_snapshot
from modules.kst_local.models import (
    AutomaticSourceSnapshot,
    KstInstallation,
    KstInstallationLike,
    LegacyKstInstallation,
)
from modules.project_config import (
    build_runtime_config_from_project,
    list_projects,
    load_project_config,
)


class KstIdentityMappingError(RuntimeError):
    """项目与本机快商通身份无法建立安全的一对一映射。"""

    def __init__(
        self,
        message: str,
        *,
        category: str = "identity_mapping",
    ) -> None:
        super().__init__(message)
        self.category = category


_SAFE_FAILURE_DETAILS = {
    "client_not_running": "客户端未运行",
    "client_path_mismatch": "客户端程序与运行进程不匹配",
    "inactive_log": "未检测到活动身份",
    "database_incompatible": "数据库结构不兼容",
    "database_busy_or_timeout": "数据库忙或读取超时",
    "identity_mapping": "快商通身份映射未就绪",
    "installation_root": "快商通客户端目录无效",
    "data_root": "快商通数据目录无效",
    "discovery_failed": "快商通客户端发现失败",
}
_SAFE_LEGACY_SCHEMA_DETAIL = re.compile(
    r"^老版快商通(?:历史库|消息库)缺少必要(?:数据表|字段)："
    r"[A-Za-z0-9_/、]+$"
)


def _safe_failure(error: BaseException) -> tuple[str, str]:
    category = str(
        getattr(error, "category", "identity_mapping")
    ).strip()
    if category not in _SAFE_FAILURE_DETAILS:
        category = "identity_mapping"
    detail = _SAFE_FAILURE_DETAILS[category]
    candidate = str(error or "").strip()
    if (
        category == "database_incompatible"
        and _SAFE_LEGACY_SCHEMA_DETAIL.fullmatch(candidate) is not None
    ):
        detail = candidate
    return category, detail


def _identity_fingerprints_match(
    installation: KstInstallationLike | None,
    baseline: Any,
    current: Any,
) -> bool:
    if (
        isinstance(installation, KstInstallation)
        and isinstance(baseline, tuple)
        and isinstance(current, tuple)
        and len(baseline) >= 5
        and len(current) >= 5
        and baseline[0] == "electron"
        and current[0] == "electron"
    ):
        return baseline[:4] == current[:4]
    return baseline == current


def _call_with_supported_keywords(
    function: Callable[..., Any],
    *args: Any,
    **keywords: Any,
) -> Any:
    try:
        signature = inspect.signature(function)
    except (TypeError, ValueError):
        return function(*args)
    accepts_keywords = any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    )
    supported = {
        key: value
        for key, value in keywords.items()
        if accepts_keywords or key in signature.parameters
    }
    return function(*args, **supported)


def _project_promotion_ids(project: dict[str, Any]) -> set[str]:
    result: set[str] = set()
    accounts = list(project.get("accounts", []) or [])
    for source in project.get("baidu_sources", []) or []:
        if isinstance(source, dict):
            accounts.extend(source.get("accounts", []) or [])
    for account in accounts:
        if not isinstance(account, dict):
            continue
        for value in account.get("kst_ids", []) or []:
            normalized = str(value or "").strip()
            if normalized:
                result.add(normalized)
    return result


def build_project_promotion_index(
    projects: list[dict[str, Any]],
) -> dict[str, str]:
    index: dict[str, str] = {}
    for project in projects:
        project_id = str(project.get("project_id") or "").strip()
        if not project_id:
            continue
        for promotion_id in _project_promotion_ids(project):
            existing = index.get(promotion_id)
            if existing is not None and existing != project_id:
                raise KstIdentityMappingError(
                    f"推广 ID 在项目 {existing} 与 {project_id} 中重复"
                )
            index[promotion_id] = project_id
    return index


_SITE_ID_PATTERN = re.compile(r"^(\d+)_")


def installation_site_id(installation: KstInstallationLike) -> str:
    """从快商通身份串提取唯一站点 ID。"""
    identity = str(getattr(installation, "identity", "") or "").strip()
    match = _SITE_ID_PATTERN.match(identity)
    return match.group(1) if match else ""


def _project_site_id(project: dict[str, Any]) -> str:
    kst_config = project.get("kst") or {}
    if not isinstance(kst_config, dict):
        return ""
    site_id = str(kst_config.get("site_id") or "").strip()
    return site_id if site_id.isdigit() else ""


def build_project_site_index(
    projects: list[dict[str, Any]],
) -> dict[str, str]:
    """构建站点 ID 到项目的唯一映射，并拒绝重复配置。"""
    index: dict[str, str] = {}
    for project in projects:
        project_id = str(project.get("project_id") or "").strip()
        site_id = _project_site_id(project)
        if not project_id or not site_id:
            continue
        existing = index.get(site_id)
        if existing is not None and existing != project_id:
            raise KstIdentityMappingError(
                f"站点 ID {site_id} 在项目 {existing} 与 {project_id} 中重复"
            )
        index[site_id] = project_id
    return index


def _load_formal_projects(root: str | Path) -> list[dict[str, Any]]:
    return [
        load_project_config(root, item["project_id"])
        for item in list_projects(root)
    ]


def _required_endpoints_available(
    installation: KstInstallation,
    target_date: str,
) -> bool:
    snapshot = parse_cached_log_snapshot(
        installation.log_dir,
        target_date,
        auth_date=target_date,
    )
    endpoint_names = set(snapshot.auth.endpoints)
    has_visitor_source = bool(
        {"visitor_card", "visitor_info"} & endpoint_names
    )
    return (
        "tag_dictionary" in endpoint_names
        and has_visitor_source
        and bool(snapshot.auth.common_query)
        and bool(snapshot.auth.headers)
    )


def _runtime_input_state(
    installation: KstInstallation,
    _target_date: str,
    snapshot: AutomaticSourceSnapshot,
) -> tuple[Any, ...]:
    database_state: list[tuple[str, int, int]] = []
    for database_path in installation.database_paths:
        related_paths = (
            database_path,
            database_path.with_name(database_path.name + "-wal"),
            database_path.with_name(database_path.name + "-shm"),
            database_path.with_name(database_path.name + "-journal"),
        )
        for path in related_paths:
            try:
                stat = path.stat()
                database_state.append(
                    (str(path), stat.st_size, stat.st_mtime_ns)
                )
            except OSError:
                database_state.append((str(path), -1, -1))
    return (
        str(installation.root),
        installation.identity,
        tuple(str(path) for path in installation.database_paths),
        snapshot,
        tuple(database_state),
    )


class KstIdentityRegistry:
    def __init__(
        self,
        root: str | Path,
        *,
        projects_loader: Callable[[str | Path], list[dict[str, Any]]] = _load_formal_projects,
        installations_loader: Callable[
            [], list[KstInstallationLike]
        ] | None = None,
        promotion_id_reader: Callable[
            [KstInstallationLike], set[str]
        ] = read_installation_promotion_ids,
        project_loader: Callable[[str | Path, str], dict[str, Any]] = load_project_config,
        config_builder: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]] = build_runtime_config_from_project,
        runtime_builder: Callable[..., Any] = build_installation_runtime,
        runtime_state_reader: Callable[
            [
                KstInstallationLike,
                str,
                AutomaticSourceSnapshot | None,
            ],
            Any,
        ] = installation_runtime_state,
        endpoint_checker: Callable[
            [KstInstallationLike, str], bool
        ] = installation_ready,
        liveness_checker: Callable[
            [KstInstallationLike], bool
        ] = installation_active,
        identity_fingerprint_reader: Callable[
            [KstInstallationLike], Any
        ] = installation_identity_fingerprint,
        promotion_cache_ttl_seconds: float = 300,
        runtime_cache_ttl_seconds: float = 60,
        runtime_cache_max_entries: int = 64,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.root = Path(root)
        self._projects_loader = projects_loader
        self._installations_loader = installations_loader or (
            lambda *, cancel_event=None: discover_all_installations(
                self.root,
                require_running_process=True,
                cancel_event=cancel_event,
            )
        )
        self._promotion_id_reader = promotion_id_reader
        self._project_loader = project_loader
        self._config_builder = config_builder
        self._runtime_builder = runtime_builder
        self._runtime_state_reader = runtime_state_reader
        self._endpoint_checker = endpoint_checker
        self._liveness_checker = liveness_checker
        self._identity_fingerprint_reader = identity_fingerprint_reader
        self._promotion_cache_ttl_seconds = max(
            0.0,
            float(promotion_cache_ttl_seconds),
        )
        self._monotonic = monotonic
        self._runtime_cache_ttl_seconds = max(
            0.0,
            float(runtime_cache_ttl_seconds),
        )
        self._runtime_cache_max_entries = max(
            1,
            int(runtime_cache_max_entries),
        )
        self._state_lock = threading.RLock()
        self._refresh_lock = threading.Lock()
        self._runtime_lock = threading.RLock()
        self._runtime_cache: OrderedDict[
            tuple[str, str],
            tuple[float, Any, Any],
        ] = OrderedDict()
        self._promotion_cache: dict[
            tuple[Any, ...],
            tuple[float, frozenset[str]],
        ] = {}
        self._projects: dict[str, dict[str, Any]] = {}
        self._bindings: dict[str, KstInstallationLike] = {}
        self._binding_fingerprints: dict[str, Any] = {}
        self._stale_projects: set[str] = set()
        self._project_errors: dict[str, str] = {}
        self._identity_error_count = 0
        self._identity_count = 0
        self._refreshed = False
        self._last_error_category: str | None = None
        self._last_error_detail: str | None = None

    @staticmethod
    def _installation_cache_key(
        installation: KstInstallationLike,
    ) -> tuple[str, str, str, tuple[str, ...]]:
        if isinstance(installation, LegacyKstInstallation):
            family = installation.client_family
            database_paths = (
                installation.history_db,
                *installation.message_database_paths,
            )
        elif isinstance(installation, KstInstallation):
            family = "electron"
            database_paths = installation.database_paths
        else:
            raise KstDiscoveryError("不支持的快商通客户端结构")
        return (
            family,
            str(installation.root),
            installation.identity,
            tuple(str(path) for path in database_paths),
        )

    def _identity_fingerprint_for(
        self,
        installation: KstInstallationLike,
        *,
        cancel_event: Any = None,
    ) -> Any:
        return _call_with_supported_keywords(
            self._identity_fingerprint_reader,
            installation,
            cancel_event=cancel_event,
        )

    def _capture_identity_for(
        self,
        installation: KstInstallationLike,
        *,
        cancel_event: Any = None,
    ) -> tuple[KstInstallationLike, Any]:
        captured, default_fingerprint = capture_installation_identity(
            installation,
            cancel_event=cancel_event,
        )
        if (
            self._identity_fingerprint_reader
            is installation_identity_fingerprint
        ):
            return captured, default_fingerprint
        return (
            captured,
            self._identity_fingerprint_for(
                captured,
                cancel_event=cancel_event,
            ),
        )

    def _promotion_ids_for(
        self,
        installation: KstInstallationLike,
        *,
        fingerprint: Any,
        target_date: str,
        allowed_ids: set[str],
        cancel_event: Any = None,
    ) -> set[str]:
        key = (
            *self._installation_cache_key(installation),
            fingerprint,
        )
        now = self._monotonic()
        cached = self._promotion_cache.get(key)
        if cached is not None:
            read_at, values = cached
            age = now - read_at
            if 0 <= age < self._promotion_cache_ttl_seconds:
                return set(values)
        values = frozenset(
            _call_with_supported_keywords(
                self._promotion_id_reader,
                installation,
                target_date=target_date,
                allowed_ids=allowed_ids,
                cancel_event=cancel_event,
            )
        )
        self._promotion_cache[key] = (now, values)
        return set(values)

    def refresh(
        self,
        *,
        force: bool = False,
        cancel_event: Any = None,
    ) -> None:
        with self._refresh_lock:
            try:
                if force:
                    self._promotion_cache.clear()
                    with self._runtime_lock:
                        self._runtime_cache.clear()
                self._refresh_unlocked(cancel_event=cancel_event)
            except Exception as exc:
                self._fail_closed(exc)
                raise

    def _fail_closed(self, error: BaseException) -> None:
        error_category, error_detail = _safe_failure(error)
        with self._state_lock:
            self._projects = {}
            self._bindings = {}
            self._binding_fingerprints = {}
            self._stale_projects.clear()
            self._project_errors = {}
            self._identity_error_count = 0
            self._identity_count = 0
            self._refreshed = False
            self._last_error_category = error_category
            self._last_error_detail = error_detail
            self._promotion_cache.clear()
            with self._runtime_lock:
                self._runtime_cache.clear()

    def _refresh_unlocked(self, *, cancel_event: Any = None) -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise KstDiscoveryError(
                "快商通读取已取消",
                category="database_busy_or_timeout",
            )
        projects = self._projects_loader(self.root)
        project_map = {
            str(project.get("project_id") or ""): project
            for project in projects
            if project.get("project_id")
        }
        promotion_index = build_project_promotion_index(projects)
        project_site_index = build_project_site_index(projects)
        site_project_index = {
            project_id: site_id
            for site_id, project_id in project_site_index.items()
        }
        installations = _call_with_supported_keywords(
            self._installations_loader,
            cancel_event=cancel_event,
        )
        failure_diagnostics: list[BaseException] = list(
            getattr(installations, "diagnostics", ()) or ()
        )
        bindings: dict[str, KstInstallationLike] = {}
        binding_fingerprints: dict[str, Any] = {}
        project_errors: dict[str, str] = {}
        identity_error_count = len(failure_diagnostics)

        candidates: dict[
            str,
            list[tuple[KstInstallationLike, Any]],
        ] = defaultdict(list)
        conflicted_projects: set[str] = set()
        unready_projects: set[str] = set()
        target_date = date.today().isoformat()
        for installation in installations:
            if cancel_event is not None and cancel_event.is_set():
                raise KstDiscoveryError(
                    "快商通读取已取消",
                    category="database_busy_or_timeout",
            )
            try:
                installation, fingerprint_before = self._capture_identity_for(
                    installation,
                    cancel_event=cancel_event,
                )
                discovered_fingerprint = getattr(
                    installation,
                    "identity_fingerprint",
                    None,
                )
                if (
                    discovered_fingerprint is not None
                    and not _identity_fingerprints_match(
                        installation,
                        discovered_fingerprint,
                        fingerprint_before,
                    )
                ):
                    raise KstDiscoveryError(
                        "快商通身份数据库在发现期间发生变化",
                        category="database_busy_or_timeout",
                    )
                known_ids = self._promotion_ids_for(
                    installation,
                    fingerprint=fingerprint_before,
                    target_date=target_date,
                    allowed_ids=set(promotion_index),
                    cancel_event=cancel_event,
                )
                matched_projects = {
                    promotion_index[promotion_id]
                    for promotion_id in known_ids
                    if promotion_id in promotion_index
                }
                site_id = installation_site_id(installation)
                site_project = project_site_index.get(site_id)
                if site_project:
                    if matched_projects and site_project not in matched_projects:
                        conflicted_projects.update(
                            matched_projects | {site_project}
                        )
                        matched_projects = set()
                    else:
                        matched_projects = {site_project}
                elif site_id and matched_projects:
                    matched_projects = {
                        project_id
                        for project_id in matched_projects
                        if not site_project_index.get(project_id)
                    }
                if not _call_with_supported_keywords(
                    self._endpoint_checker,
                    installation,
                    target_date,
                    cancel_event=cancel_event,
                ):
                    identity_error_count += 1
                    unready_projects.update(matched_projects)
                    failure_diagnostics.append(
                        KstIdentityMappingError(
                            "快商通必需接口不可用"
                        )
                    )
                    continue
                (
                    installation_after,
                    fingerprint_after,
                ) = self._capture_identity_for(
                    installation,
                    cancel_event=cancel_event,
                )
                if not _identity_fingerprints_match(
                    installation_after,
                    fingerprint_before,
                    fingerprint_after,
                ):
                    raise KstDiscoveryError(
                        "快商通身份数据库读取期间发生变化",
                        category="database_busy_or_timeout",
                    )
                installation = installation_after
                fingerprint = fingerprint_after
            except Exception as exc:
                identity_error_count += 1
                failure_diagnostics.append(exc)
                continue
            if len(matched_projects) == 1:
                candidates[next(iter(matched_projects))].append(
                    (installation, fingerprint)
                )
            elif len(matched_projects) > 1:
                conflicted_projects.update(matched_projects)

        if cancel_event is not None and cancel_event.is_set():
            raise KstDiscoveryError(
                "快商通读取已取消",
                category="database_busy_or_timeout",
            )
        for project_id in sorted(project_map):
            project_candidates = candidates.get(project_id, [])
            if project_id in unready_projects and not project_candidates:
                project_errors[project_id] = "必需接口不可用"
            elif project_id in conflicted_projects:
                project_errors[project_id] = "身份包含多个项目的推广 ID"
            elif len(project_candidates) > 1:
                project_errors[project_id] = "同一项目匹配到多个身份"
            elif len(project_candidates) == 1:
                installation, fingerprint = project_candidates[0]
                bindings[project_id] = installation
                binding_fingerprints[project_id] = fingerprint
            else:
                project_errors[project_id] = "未找到匹配身份"
        failure_diagnostics.extend(
            KstIdentityMappingError(reason)
            for reason in project_errors.values()
        )
        if bindings:
            last_error_category = None
            last_error_detail = None
        else:
            selected_error = most_specific_discovery_error(
                failure_diagnostics,
                fallback_category="identity_mapping",
            )
            (
                last_error_category,
                last_error_detail,
            ) = _safe_failure(selected_error)
        with self._state_lock:
            changed_projects = {
                project_id
                for project_id in set(self._bindings) | set(bindings)
                if (
                    self._bindings.get(project_id)
                    != bindings.get(project_id)
                    or not _identity_fingerprints_match(
                        (
                            bindings.get(project_id)
                            or self._bindings.get(project_id)
                        ),
                        self._binding_fingerprints.get(project_id),
                        binding_fingerprints.get(project_id),
                    )
                )
            }
            self._projects = project_map
            self._identity_count = len(installations)
            self._bindings = bindings
            self._binding_fingerprints = binding_fingerprints
            self._stale_projects.clear()
            self._project_errors = project_errors
            self._identity_error_count = identity_error_count
            self._refreshed = True
            self._last_error_category = last_error_category
            self._last_error_detail = last_error_detail
            if changed_projects:
                with self._runtime_lock:
                    for key in tuple(self._runtime_cache):
                        if key[0] in changed_projects:
                            self._runtime_cache.pop(key, None)

    def _mark_binding_stale_unlocked(
        self,
        project_id: str,
        error: BaseException,
    ) -> tuple[str, str]:
        category, detail = _safe_failure(error)
        self._stale_projects.add(project_id)
        self._last_error_category = category
        self._last_error_detail = detail
        with self._runtime_lock:
            for key in tuple(self._runtime_cache):
                if key[0] == project_id:
                    self._runtime_cache.pop(key, None)
        return category, detail

    def _validate_binding_fingerprint_unlocked(
        self,
        project_id: str,
        installation: KstInstallationLike,
        *,
        cancel_event: Any = None,
    ) -> Any:
        _, fingerprint = self._capture_bound_identity_unlocked(
            project_id,
            installation,
            cancel_event=cancel_event,
        )
        return fingerprint

    def _capture_bound_identity_unlocked(
        self,
        project_id: str,
        installation: KstInstallationLike,
        *,
        cancel_event: Any = None,
    ) -> tuple[KstInstallationLike, Any]:
        if project_id in self._stale_projects:
            raise KstIdentityMappingError(
                "快商通身份数据库已变化，必须重新扫描"
            )
        baseline = self._binding_fingerprints.get(project_id)
        if baseline is None:
            error = KstIdentityMappingError(
                "快商通身份数据库缺少发现基线"
            )
            self._mark_binding_stale_unlocked(project_id, error)
            raise error
        try:
            captured, current = self._capture_identity_for(
                installation,
                cancel_event=cancel_event,
            )
        except Exception as exc:
            category, detail = self._mark_binding_stale_unlocked(
                project_id,
                exc,
            )
            raise KstIdentityMappingError(
                detail,
                category=category,
            ) from None
        if not _identity_fingerprints_match(
            captured,
            baseline,
            current,
        ):
            error = KstIdentityMappingError(
                "快商通身份数据库已变化，必须重新扫描"
            )
            self._mark_binding_stale_unlocked(project_id, error)
            raise error
        return captured, current

    def installation_for(
        self,
        project_id: str,
    ) -> KstInstallationLike:
        with self._state_lock:
            installation = self._installation_for_unlocked(project_id)
            self._validate_binding_fingerprint_unlocked(
                project_id,
                installation,
            )
            return installation

    def _installation_for_unlocked(
        self,
        project_id: str,
    ) -> KstInstallationLike:
        if not self._refreshed:
            raise KstIdentityMappingError("快商通身份注册表尚未刷新")
        installation = self._bindings.get(project_id)
        if installation is None:
            reason = self._project_errors.get(project_id, "项目不存在或未绑定")
            raise KstIdentityMappingError(
                f"项目 {project_id} 无法安全绑定快商通身份：{reason}"
            )
        return installation

    def build_runtime(
        self,
        project_id: str,
        target_date: str,
        *,
        cancel_event: Any = None,
    ) -> Any:
        with self._state_lock:
            return self._build_runtime_unlocked(
                project_id,
                target_date,
                cancel_event=cancel_event,
            )

    def _build_runtime_unlocked(
        self,
        project_id: str,
        target_date: str,
        *,
        cancel_event: Any = None,
    ) -> Any:
        bound_installation = self._installation_for_unlocked(project_id)
        installation, _ = self._capture_bound_identity_unlocked(
            project_id,
            bound_installation,
            cancel_event=cancel_event,
        )
        _call_with_supported_keywords(
            self._liveness_checker,
            installation,
            cancel_event=cancel_event,
        )
        project = self._project_loader(self.root, project_id)
        snapshot: AutomaticSourceSnapshot | None = None
        if isinstance(installation, KstInstallation):
            snapshot = parse_cached_log_snapshot(
                installation.log_dir,
                target_date,
                auth_date=date.today().isoformat(),
            )
        state = (
            project,
            _call_with_supported_keywords(
                self._runtime_state_reader,
                installation,
                target_date,
                snapshot,
                cancel_event=cancel_event,
            ),
        )
        self._capture_bound_identity_unlocked(
            project_id,
            installation,
            cancel_event=cancel_event,
        )
        key = (project_id, target_date)
        now = self._monotonic()
        with self._runtime_lock:
            cached = self._runtime_cache.get(key)
            if cached is not None:
                built_at, cached_state, runtime = cached
                age = now - built_at
                if (
                    0 <= age < self._runtime_cache_ttl_seconds
                    and cached_state == state
                ):
                    self._runtime_cache.move_to_end(key)
                    self._capture_bound_identity_unlocked(
                        project_id,
                        installation,
                        cancel_event=cancel_event,
                    )
                    return runtime
            config = self._config_builder(project, {})
            self._capture_bound_identity_unlocked(
                project_id,
                installation,
                cancel_event=cancel_event,
            )
            runtime = _call_with_supported_keywords(
                self._runtime_builder,
                config,
                target_date,
                installation=installation,
                snapshot=snapshot,
                cancel_event=cancel_event,
            )
            self._runtime_cache[key] = (now, state, runtime)
            self._runtime_cache.move_to_end(key)
            while len(self._runtime_cache) > self._runtime_cache_max_entries:
                self._runtime_cache.popitem(last=False)
            self._capture_bound_identity_unlocked(
                project_id,
                installation,
                cancel_event=cancel_event,
            )
            return runtime

    def health(self, *, cancel_event: Any = None) -> dict[str, Any]:
        with self._state_lock:
            return self._health_unlocked(cancel_event=cancel_event)

    def _health_unlocked(
        self,
        *,
        cancel_event: Any = None,
    ) -> dict[str, Any]:
        all_project_ids = sorted(self._projects)
        ready = (
            self._refreshed
            and bool(self._bindings)
            and not self._stale_projects
        )
        error_category = self._last_error_category
        error_detail = self._last_error_detail
        if ready:
            for project_id, installation in self._bindings.items():
                try:
                    self._validate_binding_fingerprint_unlocked(
                        project_id,
                        installation,
                        cancel_event=cancel_event,
                    )
                    _call_with_supported_keywords(
                        self._liveness_checker,
                        installation,
                        cancel_event=cancel_event,
                    )
                except Exception as exc:
                    ready = False
                    error_category, error_detail = _safe_failure(exc)
                    break
        bound_project_ids = sorted(
            set(self._bindings) - self._stale_projects
        )
        unbound_project_ids = sorted(
            set(all_project_ids) - set(bound_project_ids)
        )
        health = {
            "status": "ok" if ready else "not_ready",
            "required_endpoints_available": ready,
            "project_routing": True,
            "identity_count": self._identity_count,
            "bound_project_ids": bound_project_ids,
            "unbound_project_ids": unbound_project_ids,
            "mapping_error_count": (
                len(self._project_errors) + self._identity_error_count
            ),
        }
        if not ready:
            health["error_category"] = (
                error_category or "identity_mapping"
            )
            health["error_detail"] = (
                error_detail
                or _SAFE_FAILURE_DETAILS["identity_mapping"]
            )
        return health
