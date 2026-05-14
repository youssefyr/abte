from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from PySide6.QtGui import QGuiApplication, QScreen
from PySide6.QtWidgets import QWidget


class WidthClass(str, Enum):
    COMPACT = "compact"
    MEDIUM = "medium"
    EXPANDED = "expanded"
    LARGE = "large"


@dataclass(frozen=True)
class UiMetrics:
    scale: float
    width_class: WidthClass
    sidebar_width: int
    detail_panel_width: int
    page_margin: int
    card_padding: int
    card_gap: int
    toolbar_height: int
    nav_row_height: int
    control_height: int
    compact_control_height: int
    radius: int
    border_width: int
    title_pt: int
    section_pt: int
    body_pt: int
    meta_pt: int

    @property
    def collapsed_sidebar_extent(self) -> int:
        return self.toolbar_height + (self.card_padding * 2)

    @property
    def collapsed_sidebar_width(self) -> int:
        return max(round(self.toolbar_height * 1.2), self.icon_button_size + (self.card_padding * 2))

    @property
    def icon_button_size(self) -> int:
        return self.toolbar_height

    @property
    def switch_width(self) -> int:
        return max(44, round(self.control_height * 1.2))

    @property
    def switch_height(self) -> int:
        return max(24, round(self.control_height * 0.62))

    @property
    def switch_handle_size(self) -> int:
        return max(18, self.switch_height - max(6, self.border_width * 4))


def _screen_for(widget: QWidget | None = None) -> QScreen | None:
    if widget is not None and widget.windowHandle() is not None:
        return widget.windowHandle().screen()
    return QGuiApplication.primaryScreen()


def _dpi_scale(screen: QScreen | None) -> float:
    if screen is None:
        return 1.0
    dpi = screen.logicalDotsPerInch()
    scale = dpi / 96.0
    return max(0.95, min(scale, 1.35))


def _width_class(width: int) -> WidthClass:
    if width < 1180:
        return WidthClass.COMPACT
    if width < 1480:
        return WidthClass.MEDIUM
    if width < 1800:
        return WidthClass.EXPANDED
    return WidthClass.LARGE


def build_metrics(window_width: int, widget: QWidget | None = None) -> UiMetrics:
    screen = _screen_for(widget)
    scale = _dpi_scale(screen)
    wc = _width_class(window_width)

    if wc == WidthClass.COMPACT:
        base_sidebar = 116
        base_panel = 300
        page_margin = 10
        card_padding = 12
        gap = 10
        toolbar_height = 56
        nav_row = 40
        control = 38
        compact_control = 34
        radius = 10
        border_width = 1
        title_pt = 16
        section_pt = 12
        body_pt = 10
        meta_pt = 9
    elif wc == WidthClass.MEDIUM:
        base_sidebar = 232
        base_panel = 320
        page_margin = 12
        card_padding = 14
        gap = 12
        toolbar_height = 60
        nav_row = 42
        control = 40
        compact_control = 36
        radius = 10
        border_width = 1
        title_pt = 17
        section_pt = 13
        body_pt = 10
        meta_pt = 9
    elif wc == WidthClass.EXPANDED:
        base_sidebar = 248
        base_panel = 340
        page_margin = 14
        card_padding = 16
        gap = 12
        toolbar_height = 62
        nav_row = 44
        control = 42
        compact_control = 36
        radius = 11
        border_width = 1
        title_pt = 18
        section_pt = 13
        body_pt = 10
        meta_pt = 9
    else:
        base_sidebar = 264
        base_panel = 360
        page_margin = 16
        card_padding = 18
        gap = 14
        toolbar_height = 64
        nav_row = 46
        control = 44
        compact_control = 38
        radius = 12
        border_width = 1
        title_pt = 19
        section_pt = 13
        body_pt = 10
        meta_pt = 9

    return UiMetrics(
        scale=scale,
        width_class=wc,
        sidebar_width=round(base_sidebar * scale),
        detail_panel_width=round(base_panel * scale),
        page_margin=round(page_margin * scale),
        card_padding=round(card_padding * scale),
        card_gap=round(gap * scale),
        toolbar_height=round(toolbar_height * scale),
        nav_row_height=round(nav_row * scale),
        control_height=round(control * scale),
        compact_control_height=round(compact_control * scale),
        radius=round(radius * scale),
        border_width=max(1, round(border_width * scale)),
        title_pt=title_pt,
        section_pt=section_pt,
        body_pt=body_pt,
        meta_pt=meta_pt,
    )