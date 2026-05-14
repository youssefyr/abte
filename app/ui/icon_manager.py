from __future__ import annotations

from dataclasses import dataclass
import weakref

from PySide6.QtCore import QSize
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import QAbstractButton, QApplication

import qtawesome as qta



@dataclass(frozen=True)
class IconSpec:
    name: str
    size: int
    color: str | None = None


class IconManager:
    def __init__(self) -> None:
        self._cache: dict[IconSpec, QIcon] = {}
        self._registry: "weakref.WeakKeyDictionary[QAbstractButton, tuple[str, int, str | None]]" = weakref.WeakKeyDictionary()

    def icon(self, name: str, size: int = 20, color: str | None = None) -> QIcon:
        spec = IconSpec(name=name, size=size, color=color)
        if spec in self._cache:
            return self._cache[spec]

        icon = self._build_icon(spec)
        self._cache[spec] = icon
        return icon

    def apply(self, widget: QAbstractButton, name: str, size: int = 20, color: str | None = None) -> None:
        self._registry[widget] = (name, size, color)
        icon = self.icon(name, size=size, color=color)
        if not icon.isNull():
            widget.setIcon(icon)
            widget.setIconSize(QSize(size, size))

    def clear_cache(self) -> None:
        self._cache.clear()

    def refresh(self) -> None:
        self.clear_cache()
        for widget, spec in list(self._registry.items()):
            try:
                name, size, color = spec
                self.apply(widget, name, size=size, color=color)
            except RuntimeError:
                continue

    def _build_icon(self, spec: IconSpec) -> QIcon:
        color = spec.color or self._palette_text_color()
        try:
            return qta.icon(
                spec.name,
                color=color,
                color_active=self._palette_highlight_color(),
                color_disabled=self._palette_disabled_color(),
            )
        except Exception:
            return QIcon()

    def _palette_text_color(self) -> str:
        app = QApplication.instance()
        if not isinstance(app, QApplication):
            return "#ECF6F1"
        color = app.palette().text().color()
        return QColor(color).name()

    def _palette_highlight_color(self) -> str:
        app = QApplication.instance()
        if not isinstance(app, QApplication):
            return "#3ECF8E"
        color = app.palette().highlight().color()
        return QColor(color).name()

    def _palette_disabled_color(self) -> str:
        app = QApplication.instance()
        if not isinstance(app, QApplication):
            return "#5C7269"
        color = app.palette().placeholderText().color()
        return QColor(color).name()


icon_manager = IconManager()
