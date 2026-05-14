# ui/theme.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication

from app.ui.metrics import UiMetrics


@dataclass(frozen=True)
class ThemeSpec:
    name: str
    # Core surfaces
    bg: str
    sidebar: str
    surface: str
    surface_alt: str
    elevated: str
    # Borders
    border: str
    border_strong: str
    # Text
    text: str
    text_muted: str
    text_subtle: str
    # Primary / accent
    primary: str
    primary_hover: str
    primary_soft: str
    primary_glow: str
    # Semantic
    danger: str
    warning: str
    info: str
    # Selection / focus
    selection: str
    focus_ring: str
    # Radii (used by UiMetrics for variants)
    radius_sm: int = 6
    radius_md: int = 10
    radius_lg: int = 14
    radius_xl: int = 20


CALM_FOREST_DARK = ThemeSpec(
    name="forest_focus",  # keep key for settings compatibility
    # Calm Forest tokens from spec
    bg="#0E1512",              # app background
    sidebar="#0E1512",         # keep sidebar flush with bg to avoid seams
    surface="#121C18",         # cards
    surface_alt="#162420",     # raised / hover
    elevated="#1B2D27",        # inputs / overlays
    border="rgba(255,255,255,0.06)",
    border_strong="rgba(255,255,255,0.10)",
    text="#ECF6F1",
    text_muted="#8FA59C",
    text_subtle="#5C7269",
    primary="#3ECF8E",
    primary_hover="#34B87C",
    primary_soft="rgba(62,207,142,0.12)",
    primary_glow="#A8F0C6",
    danger="#F26D6D",
    warning="#E8B454",
    info="#6FB4FF",
    selection="rgba(62,207,142,0.10)",
    focus_ring="rgba(62,207,142,0.55)",
)

# Keep other themes for future use; update later if needed.
PAPER_DAYLIGHT = ThemeSpec(
    name="paper_daylight",
    bg="#F7F8FA",
    sidebar="#E8EBF0",
    surface="#FFFFFF",
    surface_alt="#F1F4F8",
    elevated="#FFFFFF",
    border="#E0E4EC",
    border_strong="rgba(0,0,0,0.10)",
    text="#17202B",
    text_muted="#5F6B7A",
    text_subtle="#9BA3B5",
    primary="#2364AA",
    primary_hover="#2D73C0",
    primary_soft="rgba(35,100,170,0.10)",
    primary_glow="#8BB4EB",
    danger="#C84D4D",
    warning="#F0A10E",
    info="#3A7BFF",
    selection="rgba(35,100,170,0.10)",
    focus_ring="rgba(35,100,170,0.55)",
)

MONO_FOCUS = ThemeSpec(
    name="mono_focus",
    bg="#101010",
    sidebar="#0A0A0A",
    surface="#181818",
    surface_alt="#1F1F1F",
    elevated="#202020",
    border="#2A2A2A",
    border_strong="rgba(255,255,255,0.16)",
    text="#F5F5F5",
    text_muted="#A3A3A3",
    text_subtle="#777777",
    primary="#00C2FF",
    primary_hover="#31D0FF",
    primary_soft="rgba(0,194,255,0.12)",
    primary_glow="#7BE2FF",
    danger="#E45A5A",
    warning="#E8B454",
    info="#6FB4FF",
    selection="rgba(0,194,255,0.10)",
    focus_ring="rgba(0,194,255,0.55)",
)


class ThemeManager:
    THEMES = {
        "forest_focus": CALM_FOREST_DARK,
        "paper_daylight": PAPER_DAYLIGHT,
        "mono_focus": MONO_FOCUS,
    }

    def __init__(self, app: QApplication) -> None:
        self.app = app
        self._fonts_loaded = False

    def apply(self, metrics: UiMetrics, theme_name: str = "forest_focus") -> None:
        theme = self.THEMES.get(theme_name, CALM_FOREST_DARK)

        # Fonts: Space Grotesk (headings) + DM Sans (body) + JetBrains Mono (numerics)
        self._ensure_fonts_loaded()
        body_font = QFont("DM Sans")
        if not body_font.family():
            body_font = QFont("Inter")
        body_font.setPointSize(metrics.body_pt)

        self.app.setStyle("Fusion")
        self.app.setPalette(self._build_palette(theme))
        self.app.setFont(body_font)
        self.app.setStyleSheet(self._build_qss(theme, metrics))
        try:
            from app.ui.icon_manager import icon_manager

            icon_manager.refresh()
        except Exception:
            pass

    def _ensure_fonts_loaded(self) -> None:
        if self._fonts_loaded:
            return
        db = QFontDatabase()
        # If bundled via resources, replace with qrc paths.
        font_paths: list[Path] = []
        for path in font_paths:
            try:
                db.addApplicationFont(str(path))
            except Exception:
                continue
        self._fonts_loaded = True

    def _build_palette(self, t: ThemeSpec) -> QPalette:
        p = QPalette()
        p.setColor(QPalette.ColorRole.Window, QColor(t.bg))
        p.setColor(QPalette.ColorRole.WindowText, QColor(t.text))
        p.setColor(QPalette.ColorRole.Base, QColor(t.surface))
        p.setColor(QPalette.ColorRole.AlternateBase, QColor(t.surface_alt))
        p.setColor(QPalette.ColorRole.ToolTipBase, QColor(t.elevated))
        p.setColor(QPalette.ColorRole.ToolTipText, QColor(t.text))
        p.setColor(QPalette.ColorRole.Text, QColor(t.text))
        p.setColor(QPalette.ColorRole.Button, QColor(t.surface))
        p.setColor(QPalette.ColorRole.ButtonText, QColor(t.text))
        p.setColor(QPalette.ColorRole.PlaceholderText, QColor(t.text_subtle))
        p.setColor(QPalette.ColorRole.Highlight, QColor(t.primary))
        p.setColor(QPalette.ColorRole.HighlightedText, QColor("#09110D"))
        return p

    def _build_qss(self, t: ThemeSpec, m: UiMetrics) -> str:
        # Single radius used here; pages can differentiate via UiMetrics if needed.
        r = f"{m.radius}px"
        pad = f"{m.card_padding}px"
        gap = f"{m.card_gap}px"
        nav_h = f"{m.nav_row_height}px"
        ctl_h = f"{m.control_height}px"
        compact_h = f"{m.compact_control_height}px"
        toolbar_h = f"{m.toolbar_height}px"
        topbar_h = f"{max(m.compact_control_height + 8, m.control_height + 4)}px"

        kpi_r = f"{m.radius}px"
        avatar_r = f"{max(m.radius + 18, m.radius * 2)}px"
        badge_r = f"{max(6, m.radius - 2)}px"
        progress_r = f"{max(3, m.radius // 2)}px"
        divider_h = "1px"

        kpi_value_pt = f"{m.title_pt + 6}pt"
        label_pt = f"{max(9, m.meta_pt)}pt"
        body_sm_pt = f"{m.body_pt}pt"
        meta_pt = f"{max(9, m.meta_pt)}pt"
        badge_pt = f"{max(8, m.meta_pt - 1)}pt"
        avatar_pt = f"{m.title_pt + 8}pt"
        badge_pad_v = "0px"
        badge_pad_h = f"{max(2, m.border_width // 2)}px"

        return f"""
        QWidget {{
            background: transparent;
            color: {t.text};
            font-size: {m.body_pt}pt;
            border: none;
        }}

        QMainWindow {{
            background: {t.bg};
        }}

        QWidget#AppRoot {{
            background: {t.bg};
        }}

        QScrollArea {{
            background: transparent;
        }}

        QLabel {{
            background: transparent;
        }}

        QLabel[role="pageTitle"] {{
            font-size: {m.title_pt + 4}pt;
            font-weight: 700;
            font-family: "Space Grotesk", "DM Sans", "Inter";
            letter-spacing: -0.02em;
        }}

        QLabel[role="cardTitle"], QLabel[role="sectionTitle"] {{
            font-size: {m.section_pt + 1}pt;
            font-weight: 650;
            font-family: "Space Grotesk", "DM Sans", "Inter";
            letter-spacing: -0.015em;
        }}

        QLabel[role="muted"] {{
            color: {t.text_muted};
            font-size: {m.body_pt}pt;
        }}

        QLabel[role="meta"] {{
            color: {t.text_subtle};
            font-size: {m.meta_pt}pt;
            letter-spacing: 0.3px;
            text-transform: uppercase;
        }}

        QLabel[role="mono"] {{
            font-family: "JetBrains Mono", "DM Sans", "Inter";
        }}

        QFrame#SidebarSurface {{
            background: {t.sidebar};
            border: 1px solid {t.border};
            border-radius: {r};
        }}

        QToolButton#SidebarNavItem {{
            min-height: {nav_h};
            padding: 0 14px;
            margin: 0;
            border-radius: {r};
            color: {t.text_muted};
            background: transparent;
            border: 1px solid transparent;
            text-align: left;
        }}

        QToolButton#SidebarNavItem:hover {{
            background: {t.surface_alt};
            color: {t.text};
        }}

        QToolButton#SidebarNavItem:pressed {{
            background: {t.elevated};
            border: 1px solid {t.border_strong};
            color: {t.text};
        }}

        QToolButton#SidebarNavItem:checked {{
            background: {t.primary_soft};
            color: {t.text};
            border: 1px solid {t.border_strong};
        }}

        QToolButton#SidebarNavItem[minimized="true"] {{
            padding: 0;
            margin: 0 4px;
            border-radius: {r};
            border: 1px solid transparent;
            text-align: center;
        }}

        QToolButton#SidebarNavItem[minimized="true"]:hover {{
            background: {t.surface_alt};
            color: {t.text};
        }}

        QToolButton#SidebarNavItem[minimized="true"]:checked {{
            background: {t.primary_soft};
            border: 1px solid {t.border_strong};
            color: {t.text};
        }}

        QToolButton#SidebarPrimaryAction {{
            min-height: {ctl_h};
            background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                                       stop:0 {t.primary},
                                       stop:1 {t.primary_hover});
            color: #020807;
            border: 1px solid {t.primary};
            border-radius: {r};
            font-weight: 650;
            padding: 0 16px;
        }}

        QToolButton#SidebarPrimaryAction:hover {{
            background: {t.primary_hover};
            border: 1px solid {t.primary_hover};
        }}

        QToolButton#SidebarGhostAction {{
            min-height: {compact_h};
            background: transparent;
            color: {t.text_muted};
            border: 1px solid {t.border};
            border-radius: {r};
            padding: 0 12px;
        }}

        QToolButton#SidebarGhostAction:hover {{
            background: {t.surface_alt};
            color: {t.text};
        }}

        QToolButton#SidebarToggle {{
            background: transparent;
            border: none;
            color: {t.text};
            padding: 4px;
        }}

        QToolButton#SidebarToggle:hover {{
            background: {t.surface_alt};
        }}

        QFrame#Card,
        QFrame#CardElevated,
        QFrame#ToolbarCard,
        QFrame#TaskBlock,
        QFrame#ListRow,
        QFrame#DetailOverlay {{
            border-radius: {r};
            border: 1px solid {t.border};
        }}

        QFrame#SessionCard[active="true"] {{
            border: 1px solid {t.primary_glow};
            background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                                       stop:0 {t.surface_alt},
                                       stop:1 {t.surface});
        }}

        QFrame#Card,
        QFrame#ToolbarCard,
        QFrame#ListRow {{
            background: {t.surface};
        }}

        QFrame#CardElevated,
        QFrame#TaskBlock,
        QFrame#DetailOverlay {{
            background: {t.surface_alt};
        }}

        QFrame#ToolbarCard {{
            min-height: {toolbar_h};
        }}

        QFrame#TopbarCard {{
            min-height: {topbar_h};
            background: {t.surface};
            border: 1px solid {t.border};
            border-radius: {r};
        }}

        QLineEdit,
        QPushButton,
        QToolButton,
        QPlainTextEdit,
        QTextEdit,
        QSpinBox,
        QComboBox,
        QDateTimeEdit {{
            min-height: {ctl_h};
            border-radius: {r};
            background: {t.surface_alt};
            border: 1px solid {t.border};
            padding: 0 12px;
        }}

        QComboBox {{
            padding-right: 30px;
        }}

        QComboBox::drop-down {{
            subcontrol-origin: padding;
            subcontrol-position: top right;
            width: 26px;
            border-left: 1px solid {t.border};
            background: {t.surface};
            border-top-right-radius: {r};
            border-bottom-right-radius: {r};
        }}

        QComboBox::down-arrow {{
            width: 10px;
            height: 10px;
            image: none;
            border: 2px solid {t.text_muted};
            border-top: none;
            border-left: none;
            margin-right: 10px;
            margin-top: 2px;
        }}

        QComboBox QAbstractItemView {{
            background: {t.surface};
            color: {t.text};
            border: 1px solid {t.border_strong};
            selection-background-color: {t.primary_soft};
            selection-color: {t.text};
            outline: 0;
        }}

        QSpinBox {{
            padding-right: 30px;
        }}

        QAbstractSpinBox::up-button,
        QAbstractSpinBox::down-button {{
            subcontrol-origin: padding;
            width: 26px;
            border-left: 1px solid {t.border};
            background: {t.surface};
        }}

        QAbstractSpinBox::up-button {{
            subcontrol-position: top right;
            border-top-right-radius: {r};
        }}

        QAbstractSpinBox::down-button {{
            subcontrol-position: bottom right;
            border-bottom-right-radius: {r};
        }}

        QSpinBox::up-arrow,
        QSpinBox::down-arrow {{
            width: 9px;
            height: 9px;
            image: none;
            border: 2px solid {t.text_muted};
        }}

        QSpinBox::up-arrow {{
            border-bottom: none;
            border-left: none;
            margin-top: 2px;
        }}

        QSpinBox::down-arrow {{
            border-top: none;
            border-left: none;
            margin-bottom: 2px;
        }}

        QLineEdit#SearchInput {{
            background: {t.elevated};
            border: 1px solid {t.border};
            padding: 0 14px;
        }}

        QLineEdit#TaskSearchInput {{
            background: {t.elevated};
            border: 1px solid {t.border};
            padding: 0 14px;
        }}

        QToolButton#FilterChip {{
            min-height: {compact_h};
            padding: 0 10px;
            border-radius: {r};
            background: {t.surface};
            color: {t.text_muted};
            border: 1px solid {t.border};
        }}

        QToolButton#FilterChip:checked {{
            background: {t.primary_soft};
            color: {t.text};
            border: 1px solid {t.border_strong};
        }}

        QToolButton#SegmentButton {{
            min-height: {compact_h};
            padding: 0 12px;
            border-radius: {r};
            background: {t.surface};
            color: {t.text_muted};
            border: 1px solid {t.border};
        }}

        QToolButton#SegmentButton:checked {{
            background: {t.primary_soft};
            color: {t.text};
            border: 1px solid {t.border_strong};
        }}

        QToolButton#TopbarIconButton {{
            min-width: {compact_h};
            min-height: {compact_h};
            padding: 0;
            border-radius: {r};
            background: {t.surface_alt};
            color: {t.text_muted};
            border: 1px solid {t.border};
        }}

        QToolButton#TopbarIconButton:hover {{
            color: {t.text};
            background: {t.surface};
            border: 1px solid {t.border_strong};
        }}

        QToolButton#TopbarIconButton:pressed {{
            background: {t.elevated};
            border: 1px solid {t.border_strong};
            color: {t.text};
        }}

        QToolButton#AvatarButton {{
            min-width: {compact_h};
            min-height: {compact_h};
            padding: 0;
            border-radius: {m.radius}px;
            background: {t.primary_soft};
            color: {t.text};
            border: 1px solid {t.border_strong};
            font-weight: 650;
        }}

        QToolButton#TopbarPrimaryAction {{
            min-width: {compact_h};
            min-height: {compact_h};
            padding: 0;
            border-radius: {r};
            background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                                       stop:0 {t.primary},
                                       stop:1 {t.primary_hover});
            color: #020807;
            border: 1px solid {t.primary};
            font-weight: 650;
        }}

        QToolButton#TopbarPrimaryAction:hover {{
            background: {t.primary_hover};
            border: 1px solid {t.primary_hover};
        }}

        QToolButton#TopbarPrimaryAction:pressed {{
            background: {t.primary};
            border: 1px solid {t.primary_glow};
        }}

        QToolButton#TaskActionButton {{
            min-width: {compact_h};
            min-height: {compact_h};
            padding: 0;
            border-radius: {r};
            background: {t.surface};
            color: {t.text_muted};
            border: 1px solid {t.border};
        }}

        QToolButton#TaskActionButton:hover {{
            background: {t.surface_alt};
            color: {t.text};
            border: 1px solid {t.border_strong};
        }}

        QToolButton#TaskActionButton:pressed {{
            background: {t.elevated};
            color: {t.text};
            border: 1px solid {t.border_strong};
        }}

        QLineEdit:focus,
        QPlainTextEdit:focus,
        QComboBox:focus,
        QSpinBox:focus,
        QDateTimeEdit:focus {{
            border: 1px solid {t.primary_soft};
        }}

        QLineEdit:hover,
        QPushButton:hover,
        QToolButton:hover,
        QPlainTextEdit:hover,
        QTextEdit:hover,
        QSpinBox:hover,
        QComboBox:hover,
        QDateTimeEdit:hover {{
            background: {t.surface_alt};
        }}

        QPushButton:pressed,
        QToolButton:pressed {{
            background: {t.elevated};
            border: 1px solid {t.border_strong};
            color: {t.text};
        }}

        QPushButton#PrimaryButton {{
            background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                                       stop:0 {t.primary},
                                       stop:1 {t.primary_hover});
            color: #020807;
            border: 1px solid {t.primary};
            font-weight: 650;
        }}

        QPushButton#PrimaryButton:hover {{
            background: {t.primary_hover};
            border: 1px solid {t.primary_hover};
        }}

        QPushButton#PrimaryButton:pressed {{
            background: {t.primary};
            border: 1px solid {t.primary_glow};
        }}

        QPushButton#PrimaryButton:disabled {{
            background: {t.surface_alt};
            color: {t.text_subtle};
            border: 1px solid {t.border};
        }}

        QPushButton#SecondaryButton {{
            background: {t.surface};
            color: {t.text};
        }}

        QPushButton#SecondaryButton:pressed {{
            background: {t.surface_alt};
            border: 1px solid {t.border_strong};
        }}

        QPushButton#GhostButton {{
            background: transparent;
            color: {t.text_muted};
        }}

        QPushButton#GhostButton:hover {{
            background: {t.surface_alt};
            color: {t.text};
        }}

        QPushButton#GhostButton:pressed {{
            background: {t.surface_alt};
            border: 1px solid {t.border_strong};
            color: {t.text};
        }}

        QPushButton#SessionPrimaryButton {{
            background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                                       stop:0 {t.primary},
                                       stop:1 {t.primary_hover});
            color: #020807;
            border: 1px solid {t.primary};
            font-weight: 650;
        }}

        QPushButton#SessionPrimaryButton:hover {{
            background: {t.primary_hover};
            border: 1px solid {t.primary_hover};
        }}

        QPushButton#SessionPrimaryButton:pressed {{
            background: {t.primary};
            border: 1px solid {t.primary_glow};
        }}

        QPushButton#SessionSecondaryButton {{
            background: {t.primary_soft};
            color: {t.text};
            border: 1px solid {t.border_strong};
        }}

        QPushButton#SessionSecondaryButton:hover {{
            background: {t.surface_alt};
            border: 1px solid {t.primary_soft};
        }}

        QPushButton#SessionSecondaryButton:pressed {{
            background: {t.elevated};
            border: 1px solid {t.border_strong};
        }}

        QPushButton#SessionGhostButton {{
            background: transparent;
            color: {t.text_muted};
            border: 1px dashed {t.border};
        }}

        QPushButton#SessionGhostButton:hover {{
            background: {t.surface_alt};
            color: {t.text};
            border: 1px solid {t.border_strong};
        }}

        QPushButton#SessionGhostButton:pressed {{
            background: {t.elevated};
            color: {t.text};
            border: 1px solid {t.border_strong};
        }}

        QLabel#Pill,
        QLabel#PillGood,
        QLabel#PillAccent,
        QLabel#PillDanger {{
            border-radius: {r};
            padding: 4px 10px;
            font-weight: 650;
            font-size: {m.meta_pt}pt;
        }}

        QLabel#Pill {{
            background: {t.surface_alt};
            border: 1px solid {t.border};
            color: {t.text_muted};
        }}

        QLabel#PillGood {{
            background: {t.primary_soft};
            border: 1px solid {t.border_strong};
            color: {t.primary};
        }}

        QLabel#PillAccent {{
            background: {t.primary_soft};
            border: 1px solid {t.border_strong};
            color: {t.primary};
        }}

        QLabel#PillDanger {{
            background: rgba(242,109,109,0.10);
            border: 1px solid rgba(242,109,109,0.30);
            color: {t.danger};
        }}

        QLabel#SidebarCountBadge {{
            background: {t.surface_alt};
            border: 1px solid {t.border};
            color: {t.text_muted};
            border-radius: {r};
            padding: 2px 6px;
        }}

        QLabel#SidebarBrandMark {{
            background: {t.surface_alt};
            border: 1px solid {t.border_strong};
            border-radius: {m.radius}px;
        }}

        QLabel#ProfileAvatar {{
            background: {t.surface_alt};
            border: 1px solid {t.border_strong};
            border-radius: {max(m.radius + 14, m.radius * 2)}px;
        }}

        QLabel#SidebarSectionLabel {{
            letter-spacing: 1px;
        }}

        QProgressBar#FocusBar {{
            background: {t.surface_alt};
            border: 1px solid {t.border};
            border-radius: {r};
            min-height: 10px;
            max-height: 10px;
            color: transparent;
        }}

        QProgressBar#FocusBar::chunk {{
            background: {t.primary};
            border-radius: {r};
        }}

        QProgressBar {{
            background: {t.surface_alt};
            border: 1px solid {t.border};
            border-radius: {r};
            min-height: 12px;
            text-align: center;
            color: {t.text_muted};
        }}

        QProgressBar::chunk {{
            background: {t.primary};
            border-radius: {r};
        }}

        QCheckBox {{
            spacing: 10px;
            color: {t.text};
        }}

        QCheckBox::indicator {{
            width: 18px;
            height: 18px;
            border-radius: 4px;
            background: {t.surface};
            border: 1px solid {t.border};
        }}

        QCheckBox::indicator:checked {{
            background: {t.primary};
            border: 1px solid {t.primary};
        }}

        QCheckBox::indicator:disabled {{
            background: {t.surface_alt};
            border: 1px solid {t.border};
        }}

        QListWidget {{
            background: {t.surface};
            border: 1px solid {t.border};
            border-radius: {r};
            padding: 6px;
        }}

        QListWidget::item {{
            padding: 8px 10px;
            border-radius: {r};
            color: {t.text_muted};
        }}

        QListWidget::item:selected {{
            background: {t.primary_soft};
            color: {t.text};
        }}

        QListWidget::item:hover {{
            background: {t.surface_alt};
            color: {t.text};
        }}

        QTableWidget {{
            background: {t.surface};
            border: 1px solid {t.border};
            border-radius: {r};
            gridline-color: {t.border};
            selection-background-color: {t.primary_soft};
            selection-color: {t.text};
        }}

        QHeaderView::section {{
            background: {t.surface_alt};
            color: {t.text_muted};
            border: 1px solid {t.border};
            padding: 6px 10px;
        }}

        QTableWidget::item {{
            padding: 6px 8px;
        }}

        QTextEdit {{
            background: {t.surface_alt};
            border: 1px solid {t.border};
            border-radius: {r};
            padding: 8px 10px;
        }}

        QDialog {{
            background: {t.bg};
        }}

        QSplitter::handle {{
            background: transparent;
        }}

        QScrollBar:vertical {{
            width: 10px;
            background: transparent;
            margin: 4px;
        }}

        QScrollBar::handle:vertical {{
            background: {t.border};
            border-radius: {r};
            min-height: 24px;
        }}

        QScrollBar::add-line:vertical,
        QScrollBar::sub-line:vertical {{
            background: transparent;
        }}

        QScrollBar:horizontal {{
            height: 10px;
            background: transparent;
            margin: 4px;
        }}

        QScrollBar::handle:horizontal {{
            background: {t.border};
            border-radius: {r};
            min-width: 24px;
        }}

        QScrollBar::add-line:horizontal,
        QScrollBar::sub-line:horizontal {{
            background: transparent;
        }}
        QFrame#KpiTile {{
            background: {t.elevated};
            border: 1px solid {t.border};
            border-radius: {kpi_r};
            padding: {pad};
        }}

        QLabel#KpiValue {{
            font-family: "Space Grotesk";
            font-size: {kpi_value_pt};
            font-weight: 700;
            color: {t.text};
        }}

        QLabel#KpiLabel {{
            font-family: "DM Sans";
            font-size: {label_pt};
            color: {t.text_muted};
        }}

        QLabel#KpiDeltaPositive {{
            font-family: "DM Sans";
            font-size: {label_pt};
            color: {t.primary};
        }}

        QLabel#KpiDeltaNegative {{
            font-family: "DM Sans";
            font-size: {label_pt};
            color: {t.danger};
        }}

        QLabel#ProfileAvatar {{
            min-width: {topbar_h};
            max-width: {topbar_h};
            min-height: {topbar_h};
            max-height: {topbar_h};
            font-size: {avatar_pt};
            color: {t.primary};
            background: {t.primary_soft};
            border-radius: {avatar_r};
            qproperty-alignment: 'AlignCenter';
        }}

        QLabel#TopbarBadge {{
            background: {t.danger};
            color: white;

            font-family: "DM Sans";
            font-size: {badge_pt};
            font-weight: 700;

            border: none;
        }}

        QProgressBar#WeekProgressBar {{
            background: {t.surface_alt};
            border: none;
            border-radius: {progress_r};
            min-height: {compact_h};
            max-height: {compact_h};
            color: transparent;
        }}

        QProgressBar#WeekProgressBar::chunk {{
            background: {t.primary};
            border-radius: {progress_r};
        }}

        QLabel#ActivityText {{
            font-family: "DM Sans";
            font-size: {body_sm_pt};
            color: {t.text};
        }}

        QLabel#ActivityMeta {{
            font-family: "DM Sans";
            font-size: {meta_pt};
            color: {t.text_subtle};
        }}

        QLabel#WeekDayLabel,
        QLabel#WeekMinLabel {{
            font-family: "DM Sans";
            font-size: {meta_pt};
            color: {t.text_muted};
        }}

        QFrame#HairlineDivider {{
            background: {t.border};
            border: none;
            min-height: {divider_h};
            max-height: {divider_h};
        }}
        
        """