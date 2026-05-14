from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from app.data.entities import PluginItem

MigrationFn = Callable[[sqlite3.Connection, int], int]


class PluginStorageAPI(Protocol):
    def register_migration(self, plugin_id: str, migrate_fn: MigrationFn) -> None: ...
    def set_task_plugin_value(self, task_id: str, plugin_id: str, key: str, value: Any) -> None: ...
    def get_task_plugin_payload(self, task_id: str, plugin_id: str) -> dict[str, Any]: ...
    def ensure_plugin_table(self, plugin_id: str, create_sql: str) -> None: ...


@dataclass(slots=True)
class PluginRuntime:
    item: PluginItem
    migrate_fn: MigrationFn | None = None


class PluginManager:
    def __init__(self) -> None:
        self._plugins = [
            PluginRuntime(
                item=PluginItem(
                    id="core.demo",
                    name="Demo Plugin",
                    version="0.1.0",
                    description="Example plugin placeholder with database migration support.",
                    enabled=True,
                )
            )
        ]
        self.plugins_root = Path("plugins")
        self._storage_api: PluginStorageAPI | None = None

    def attach_storage(self, storage_api: PluginStorageAPI) -> None:
        self._storage_api = storage_api
        for runtime in self._plugins:
            if runtime.migrate_fn is not None:
                self._storage_api.register_migration(runtime.item.id, runtime.migrate_fn)

    def register_migration(self, plugin_id: str, migrate_fn: MigrationFn) -> None:
        for runtime in self._plugins:
            if runtime.item.id == plugin_id:
                runtime.migrate_fn = migrate_fn
                if self._storage_api is not None:
                    self._storage_api.register_migration(plugin_id, migrate_fn)
                return

    def plugins(self) -> list[PluginItem]:
        return [runtime.item for runtime in self._plugins]

    def plugin_runtimes(self) -> list[PluginRuntime]:
        return list(self._plugins)

    def enable(self, plugin_id: str) -> None:
        for runtime in self._plugins:
            if runtime.item.id == plugin_id:
                runtime.item.enabled = True

    def disable(self, plugin_id: str) -> None:
        for runtime in self._plugins:
            if runtime.item.id == plugin_id:
                runtime.item.enabled = False
