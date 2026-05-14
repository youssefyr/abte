# app/core/settings.py
from __future__ import annotations

from app.data.settings_store import AppSettings


class SettingsManager:
    def __init__(self) -> None:
        self.settings = AppSettings()

    def get(self, key, default=None, type=None):
        return self.settings.get(key, default, type)

    def set(self, key, value) -> None:
        self.settings.set(key, value)

    def sync(self) -> None:
        self.settings.sync()

    def database_path(self):
        return self.settings.database_path()

    def app_data_dir(self):
        return self.settings.app_data_dir()