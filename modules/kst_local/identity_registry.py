from __future__ import annotations

from collections import OrderedDict, defaultdict
from datetime import date
from pathlib import Path
import threading
import time
from typing import Any, Callable

from modules.kst_local.backend import (
    build_installation_runtime,
    installation_ready,
    installation_runtime_state,
    read_installation_promotion_ids,
)
from modules.kst_local.discovery import (
    KstDiscoveryError,
    discover_all_installations,
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
    required = {"visitor_info", "visitor_card", "tag_dictionary"}
    return (
        required.issubset(snapshot.auth.endpoints)
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
        promotion_cache_ttl_seconds: float = 300,
        runtime_cache_ttl_seconds: float = 60,
        runtime_cache_max_entries: int = 64,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.root = Path(root)
        self._projects_loader = projects_loader
        self._installations_loader = installations_loader or (
            lambda: discover_all_installations(
                self.root,
                require_running_process=True,
            )
        )
        self._promotion_id_reader = promotion_id_reader
        self._project_loader = project_loader
        self._config_builder = config_builder
        self._runtime_builder = runtime_builder
        self._runtime_state_reader = runtime_state_reader
        self._endpoint_checker = endpoint_checker
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
            tuple[str, str, str, tuple[str, ...]],
            tuple[float, frozenset[str]],
        ] = {}
        self._projects: dict[str, dict[str, Any]] = {}
        self._bindings: dict[str, KstInstallationLike] = {}
        self._project_errors: dict[str, str] = {}
        self._identity_error_count = 0
        self._identity_count = 0
        self._refreshed = False

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

    def _promotion_ids_for(
        self,
        installation: KstInstallationLike,
    ) -> set[str]:
        key = self._installation_cache_key(installation)
        now = self._monotonic()
        cached = self._promotion_cache.get(key)
        if cached is not None:
            read_at, values = cached
            age = now - read_at
            if 0 <= age < self._promotion_cache_ttl_seconds:
                return set(values)
        values = frozenset(self._promotion_id_reader(installation))
        self._promotion_cache[key] = (now, values)
        return set(values)

    def refresh(self) -> None:
        with self._refresh_lock:
            self._refresh_unlocked()

    def _refresh_unlocked(self) -> None:
        projects = self._projects_loader(self.root)
        project_map = {
            str(project.get("project_id") or ""): project
            for project in projects
            if project.get("project_id")
        }
        promotion_index = build_project_promotion_index(projects)
        installations = self._installations_loader()
        bindings: dict[str, KstInstallationLike] = {}
        project_errors: dict[str, str] = {}
        identity_error_count = 0

        candidates: dict[
            str,
            list[KstInstallationLike],
        ] = defaultdict(list)
        conflicted_projects: set[str] = set()
        unready_projects: set[str] = set()
        target_date = date.today().isoformat()
        for installation in installations:
            try:
                known_ids = self._promotion_ids_for(installation)
                matched_projects = {
                    promotion_index[promotion_id]
                    for promotion_id in known_ids
                    if promotion_id in promotion_index
                }
                if not self._endpoint_checker(installation, target_date):
                    identity_error_count += 1
                    unready_projects.update(matched_projects)
                    continue
            except Exception:
                identity_error_count += 1
                continue
            if len(matched_projects) == 1:
                candidates[next(iter(matched_projects))].append(installation)
            elif len(matched_projects) > 1:
                conflicted_projects.update(matched_projects)

        for project_id in sorted(project_map):
            project_candidates = candidates.get(project_id, [])
            if project_id in unready_projects and not project_candidates:
                project_errors[project_id] = "必需接口不可用"
            elif project_id in conflicted_projects:
                project_errors[project_id] = "身份包含多个项目的推广 ID"
            elif len(project_candidates) > 1:
                project_errors[project_id] = "同一项目匹配到多个身份"
            elif len(project_candidates) == 1:
                bindings[project_id] = project_candidates[0]
            else:
                project_errors[project_id] = "未找到匹配身份"
        with self._state_lock:
            changed_projects = {
                project_id
                for project_id in set(self._bindings) | set(bindings)
                if self._bindings.get(project_id) != bindings.get(project_id)
            }
            self._projects = project_map
            self._identity_count = len(installations)
            self._bindings = bindings
            self._project_errors = project_errors
            self._identity_error_count = identity_error_count
            self._refreshed = True
            if changed_projects:
                with self._runtime_lock:
                    for key in tuple(self._runtime_cache):
                        if key[0] in changed_projects:
                            self._runtime_cache.pop(key, None)

    def installation_for(
        self,
        project_id: str,
    ) -> KstInstallationLike:
        with self._state_lock:
            return self._installation_for_unlocked(project_id)

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

    def build_runtime(self, project_id: str, target_date: str) -> Any:
        with self._state_lock:
            return self._build_runtime_unlocked(project_id, target_date)

    def _build_runtime_unlocked(
        self,
        project_id: str,
        target_date: str,
    ) -> Any:
        installation = self._installation_for_unlocked(project_id)
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
            self._runtime_state_reader(
                installation,
                target_date,
                snapshot,
            ),
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
                    return runtime
            config = self._config_builder(project, {})
            runtime = self._runtime_builder(
                config,
                target_date,
                installation=installation,
                snapshot=snapshot,
            )
            self._runtime_cache[key] = (now, state, runtime)
            self._runtime_cache.move_to_end(key)
            while len(self._runtime_cache) > self._runtime_cache_max_entries:
                self._runtime_cache.popitem(last=False)
            return runtime

    def health(self) -> dict[str, Any]:
        with self._state_lock:
            return self._health_unlocked()

    def _health_unlocked(self) -> dict[str, Any]:
        all_project_ids = sorted(self._projects)
        bound_project_ids = sorted(self._bindings)
        unbound_project_ids = sorted(
            set(all_project_ids) - set(bound_project_ids)
        )
        ready = self._refreshed and bool(bound_project_ids)
        return {
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
