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


def fluid_scale(val_min: float, val_max: float, width: int, min_w: int = 1000, max_w: int = 1920) -> float:
    """
    Interpolates smoothly between val_min and val_max using a smoothstep (S-curve)
    function, as the window width scales from min_w to max_w.
    """
    t = (width - min_w) / (max_w - min_w)
    t = max(0.0, min(1.0, t))
    # Smooth step (S-curve) for organic transition
    t_smooth = t * t * (3.0 - 2.0 * t)
    return val_min + t_smooth * (val_max - val_min)


def build_metrics(window_width: int, widget: QWidget | None = None) -> UiMetrics:
    screen = _screen_for(widget)
    scale = _dpi_scale(screen)
    wc = _width_class(window_width)

    # 1. Continuous Fluid layout values (pixels, pre-scaled)
    # If in compact state, non-collapsed sidebar width starts smaller (e.g. 200px)
    base_sidebar = fluid_scale(200, 264, window_width)
    base_panel = fluid_scale(290, 360, window_width)
    
    page_margin = fluid_scale(10, 16, window_width)
    card_padding = fluid_scale(12, 18, window_width)
    gap = fluid_scale(10, 14, window_width)
    
    toolbar_height = fluid_scale(54, 64, window_width)
    nav_row = fluid_scale(38, 46, window_width)
    control = fluid_scale(36, 44, window_width)
    compact_control = fluid_scale(32, 38, window_width)
    
    radius = fluid_scale(8, 12, window_width)
    border_width = fluid_scale(1, 1.2, window_width)

    # Override for COMPACT sidebar to guarantee compatibility with layout
    if wc == WidthClass.COMPACT:
        base_sidebar = 116

    # 2. Continuous Fluid typography values (pt, DPI scaled natively by Qt)
    title_pt = round(fluid_scale(15, 19, window_width))
    section_pt = round(fluid_scale(11.5, 13, window_width))
    body_pt = round(fluid_scale(9.5, 10.5, window_width))
    meta_pt = round(fluid_scale(8, 9, window_width))

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