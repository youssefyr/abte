from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings, QStandardPaths


class AppSettings:
    def __init__(self) -> None:
        self._settings = QSettings("Zyro", "abteDesktop")

    def get(self, key: str, default=None, type=None):
        if type is not None:
            return self._settings.value(key, default, type)
        return self._settings.value(key, default)

    def set(self, key: str, value) -> None:
        self._settings.setValue(key, value)

    def sync(self) -> None:
        self._settings.sync()

    def app_data_dir(self) -> Path:
        configured = self.get("Storage/app_data_dir")
        if configured:
            path = Path(str(configured)).expanduser()
        else:
            base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
            path = Path(base) / "abte"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def database_path(self) -> Path:
        configured = self.get("Storage/database_path")
        if configured:
            path = Path(str(configured)).expanduser()
        else:
            path = self.app_data_dir() / "abte.db"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path
