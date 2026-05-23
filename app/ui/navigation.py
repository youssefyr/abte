from __future__ import annotations

from PySide6.QtCore import QEasingCurve, Property, QPropertyAnimation, QSize, Qt, Signal
from PySide6.QtGui import QPainter, QPaintEvent, QPen, QColor, QIcon
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.ui.icon_manager import icon_manager
from app.ui.metrics import UiMetrics
from app.ui.nav_config import NavItem, NavSection, build_nav_sections
from app.ui.ui_helpers import make_label, build_initials_avatar, load_avatar_pixmap


class SidebarRailButton(QToolButton):
    def __init__(self, item: NavItem, metrics: UiMetrics, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.item = item
        self._metrics = metrics
        self.setObjectName("SidebarNavItem")
        self.setCheckable(True)
        self.setAutoExclusive(False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._badge_label: QLabel | None = None

        if item.icon_key:
            muted = self.palette().placeholderText().color().name()
            icon_manager.apply(self, item.icon_key, size=max(18, round(metrics.control_height * 0.48)), color=muted)
        self.refresh(collapsed=False)

    def set_metrics(self, metrics: UiMetrics) -> None:
        self._metrics = metrics
        icon_size = max(18, round(metrics.control_height * 0.48))
        if self.item.icon_key:
            muted = self.palette().placeholderText().color().name()
            icon_manager.apply(self, self.item.icon_key, size=icon_size, color=muted)
        elif not self.icon().isNull():
            self.setIconSize(QSize(icon_size, icon_size))
        self.refresh(collapsed=self.property("minimized") == "true")

    def attach_badge(self, label: QLabel) -> None:
        self._badge_label = label

    def refresh(self, collapsed: bool) -> None:
        self.setProperty("minimized", "true" if collapsed else "false")
        self.setEnabled(self.item.enabled)
        self.setVisible(self.item.visible)
        self.setToolTip(self.item.tooltip or self.item.title)
        self.setAccessibleName(self.item.title)

        icon_size = max(18, round(self._metrics.control_height * 0.48))
        self.setIconSize(QSize(icon_size, icon_size))

        if collapsed:
            size = self._metrics.icon_button_size
            self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            self.setText("")
            self.setFixedSize(size, size)
        else:
            self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            self.setText(self.item.title)
            self.setFixedHeight(max(44, self._metrics.nav_row_height))
            self.setMinimumWidth(0)
            self.setMaximumWidth(16777215)
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        if self._badge_label is not None:
            self._badge_label.setVisible((not collapsed) and bool(self.item.badge))
            self._badge_label.setText(self.item.badge)

        self.style().unpolish(self)
        self.style().polish(self)


class SidebarMenu(QFrame):
    pageRequested = Signal(int)
    actionRequested = Signal(str)
    collapseChanged = Signal(bool)
    profileRequested = Signal()

    def __init__(self, metrics: UiMetrics, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SidebarSurface")
        self._metrics = metrics
        self._collapsed = False
        self._sidebar_width = metrics.sidebar_width
        self._sections: list[NavSection] = []
        self._buttons_by_key: dict[str, SidebarRailButton] = {}
        self._items_by_key: dict[str, NavItem] = {}
        self._current_key: str | None = None
        self._anim: QPropertyAnimation | None = None

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)

        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(metrics.card_padding, metrics.card_padding, metrics.card_padding, metrics.card_padding)
        self.root.setSpacing(max(8, metrics.card_gap - 4))

        self.brand_row = QHBoxLayout()
        self.brand_row.setContentsMargins(0, 0, 0, 0)
        self.brand_row.setSpacing(max(10, metrics.card_gap // 2))

        self.brand_mark = QToolButton()
        self.brand_mark.setObjectName("SidebarBrandMark")
        self.brand_mark.setFixedSize(48, 48)
        self.brand_mark.setCursor(Qt.CursorShape.PointingHandCursor)
        brand_icon = icon_manager.icon("mdi6.leaf", size=32)
        if not brand_icon.isNull():
            self.brand_mark.setIcon(brand_icon)
            self.brand_mark.setIconSize(QSize(32, 32))
        self.brand_mark.setToolTip("Profile photo")
        self.brand_mark.clicked.connect(self.profileRequested.emit)
        self.brand_row.addWidget(self.brand_mark, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        self.brand_col = QVBoxLayout()
        self.brand_col.setContentsMargins(0, 0, 0, 0)
        self.brand_col.setSpacing(2)
        self.app_title = QLabel("abte")
        self.app_title.setObjectName("SidebarAppTitle")
        self.app_title.setProperty("role", "sectionTitle")
        self.app_subtitle = make_label("PLANNER · DOER", "meta")
        self.brand_col.addWidget(self.app_title)
        self.brand_col.addWidget(self.app_subtitle)
        self.brand_row.addLayout(self.brand_col, 1)
        self.root.addLayout(self.brand_row)

        self.primary_action = QToolButton(self)
        self.primary_action.setObjectName("SidebarPrimaryAction")
        self.primary_action.setText("+ New task")
        self.primary_action.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.primary_action.setMinimumHeight(metrics.control_height)
        self.primary_action.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.primary_action.setToolTip("New task (Ctrl+N)")
        icon_manager.apply(self.primary_action, "mdi6.plus", size=max(18, round(metrics.control_height * 0.48)))
        self.primary_action.clicked.connect(lambda: self.actionRequested.emit("quick_add"))
        self.root.addWidget(self.primary_action)

        self.nav_scroll = QScrollArea()
        self.nav_scroll.setWidgetResizable(True)
        self.nav_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.nav_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.nav_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.nav_host = QWidget()
        self.nav_host.setObjectName("NavHost")
        self.nav_layout = QVBoxLayout(self.nav_host)
        self.nav_layout.setContentsMargins(0, 0, 0, 0)
        self.nav_layout.setSpacing(max(4, metrics.card_gap // 2))
        self.nav_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.nav_scroll.setWidget(self.nav_host)
        self.root.addWidget(self.nav_scroll, 1)

        self.bottom_row = QHBoxLayout()
        self.bottom_row.setContentsMargins(0, 0, 0, 0)
        self.bottom_row.setSpacing(8)

        self.collapse_button = QToolButton(self)
        self.collapse_button.setObjectName("SidebarGhostAction")
        self.collapse_button.setToolTip("Collapse sidebar")
        self.collapse_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        icon_manager.apply(self.collapse_button, "mdi6.chevron-left", size=max(18, round(metrics.compact_control_height * 0.55)))
        self.collapse_button.clicked.connect(self.toggle_minimize)
        self.bottom_row.addWidget(self.collapse_button)
        self.root.addLayout(self.bottom_row)

        self.set_sections(build_nav_sections())
        self._set_sidebar_width(metrics.sidebar_width)
        self._profile_name = ""
        self._profile_avatar_path = ""

    def set_profile_avatar(self, name: str, avatar_path: str, custom_text: str = "") -> None:
        self._profile_name = name
        self._profile_avatar_path = avatar_path
        size = 48
        pixmap = load_avatar_pixmap(avatar_path, size, shape="circle")
        if pixmap is None:
            pixmap = build_initials_avatar(name or "A", size, shape="circle")
        self.brand_mark.setIcon(QIcon(pixmap))
        self.brand_mark.setIconSize(QSize(size, size))
        self.app_title.setText(name or "abte user")
        self.app_subtitle.setText(custom_text or "PLANNER · DOER")

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        if self._collapsed:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        line_color = QColor(255, 255, 255, 16)
        painter.setPen(QPen(line_color, 1))
        y = self.height() - max(42, self._metrics.card_padding + 18)
        painter.drawLine(self._metrics.card_padding, y, self.width() - self._metrics.card_padding, y)
        painter.end()

    def _get_sidebar_width(self) -> int:
        return self._sidebar_width

    def _set_sidebar_width(self, value: int) -> None:
        self._sidebar_width = int(value)
        self.setFixedWidth(self._sidebar_width)

    sidebarWidth = Property(int, _get_sidebar_width, _set_sidebar_width)

    def set_sections(self, sections: list[NavSection]) -> None:
        self._sections = sections
        self._items_by_key.clear()
        for section in sections:
            for item in section.items:
                self._items_by_key[item.key] = item
        self._rebuild()

    def set_badge(self, key: str, value: str) -> None:
        item = self._items_by_key.get(key)
        if not item:
            return
        item.badge = value
        btn = self._buttons_by_key.get(key)
        if btn:
            btn.refresh(self._collapsed)

    def set_item_enabled(self, key: str, enabled: bool) -> None:
        item = self._items_by_key.get(key)
        if not item:
            return
        item.enabled = enabled
        btn = self._buttons_by_key.get(key)
        if btn:
            btn.refresh(self._collapsed)

    def set_current_page(self, key: str) -> None:
        self._current_key = key
        for btn_key, btn in self._buttons_by_key.items():
            btn.setChecked(btn_key == key)

    def current_key(self) -> str | None:
        return self._current_key

    def toggle_minimize(self) -> None:
        self.set_collapsed(not self._collapsed)

    def set_collapsed(self, collapsed: bool) -> None:
        if self._collapsed == collapsed:
            return

        self._collapsed = collapsed
        target = self._metrics.collapsed_sidebar_width if collapsed else self._metrics.sidebar_width
        self._animate_sidebar_width(target)

        if collapsed:
            self.brand_mark.setFixedSize(self._metrics.icon_button_size, self._metrics.icon_button_size)
            self.brand_row.setSpacing(0)
        else:
            self.brand_mark.setFixedSize(48, 48)
            self.brand_row.setSpacing(max(10, self._metrics.card_gap // 2))
            
        self.brand_row.setAlignment(self.brand_mark, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        self.app_title.setVisible(not collapsed)
        self.app_subtitle.setVisible(not collapsed)
        self.primary_action.setVisible(not collapsed)
        self.nav_scroll.setVisible(True)
        self.collapse_button.setFixedWidth(self._metrics.icon_button_size)
        chevron = "mdi6.chevron-right" if collapsed else "mdi6.chevron-left"
        icon_manager.apply(self.collapse_button, chevron, size=max(18, round(self._metrics.compact_control_height * 0.55)))

        for btn in self._buttons_by_key.values():
            btn.refresh(collapsed)

        for i in range(self.nav_layout.count()):
            item = self.nav_layout.itemAt(i)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None and widget.property("role") == "meta":
                widget.setVisible(not collapsed)

        self.collapseChanged.emit(collapsed)
        self.update()

    def is_collapsed(self) -> bool:
        return self._collapsed

    def _animate_sidebar_width(self, target: int) -> None:
        self._anim = QPropertyAnimation(self, b"sidebarWidth", self)
        self._anim.setDuration(180)
        self._anim.setStartValue(self.width())
        self._anim.setEndValue(target)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.start()

    def _clear_layout(self, layout: QVBoxLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            child_layout = item.layout()
            if widget is not None:
                widget.deleteLater()
            elif child_layout is not None:
                while child_layout.count():
                    sub_item = child_layout.takeAt(0)
                    if sub_item is None:
                        continue
                    sub_widget = sub_item.widget()
                    if sub_widget is not None:
                        sub_widget.deleteLater()

    def _make_nav_row(self, item: NavItem, group_id: int) -> QWidget:
        row = QWidget(self)
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(8)

        btn = SidebarRailButton(item, self._metrics, self)
        self._buttons_by_key[item.key] = btn
        self._group.addButton(btn, group_id)
        btn.clicked.connect(lambda checked=False, key=item.key: self._dispatch_item(key))
        row_layout.addWidget(btn, 1)

        badge = QLabel(item.badge)
        badge.setObjectName("SidebarCountBadge")
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setVisible(bool(item.badge) and not self._collapsed)
        badge.setFixedHeight(20)
        badge.setMinimumWidth(24)
        btn.attach_badge(badge)
        row_layout.addWidget(badge, 0, Qt.AlignmentFlag.AlignVCenter)

        return row

    def _rebuild(self) -> None:
        self._clear_layout(self.nav_layout)
        self._buttons_by_key.clear()
        for btn in self._group.buttons():
            self._group.removeButton(btn)

        group_id = 0
        for section in self._sections:
            label = make_label(section.title.upper(), "meta")
            label.setObjectName("SidebarSectionLabel")
            label.setVisible(not self._collapsed)
            self.nav_layout.addWidget(label)

            for item in section.items:
                row = self._make_nav_row(item, group_id)
                self.nav_layout.addWidget(row)
                group_id += 1

        self.nav_layout.addStretch(1)

        if self._current_key and self._current_key in self._buttons_by_key:
            self._buttons_by_key[self._current_key].setChecked(True)
        else:
            first_page_key = next((item.key for section in self._sections for item in section.items if item.page_index >= 0), None)
            if first_page_key:
                self.set_current_page(first_page_key)

    def _dispatch_item(self, key: str) -> None:
        item = self._items_by_key.get(key)
        if not item:
            return
        if item.page_index >= 0:
            self._current_key = key
            self.pageRequested.emit(item.page_index)
            return
        if item.action_key:
            current = self._current_key
            if current and current in self._buttons_by_key:
                self._buttons_by_key[current].setChecked(True)
            self.actionRequested.emit(item.action_key)

    def apply_metrics(self, metrics: UiMetrics) -> None:
        self._metrics = metrics
        self.root.setContentsMargins(metrics.card_padding, metrics.card_padding, metrics.card_padding, metrics.card_padding)
        self.root.setSpacing(max(8, metrics.card_gap - 4))
        self.brand_row.setSpacing(max(10, metrics.card_gap // 2))
        self.nav_layout.setSpacing(max(4, metrics.card_gap // 2))
        self.brand_mark.setFixedSize(48, 48)
        if self._profile_avatar_path or self._profile_name:
            self.set_profile_avatar(self._profile_name, self._profile_avatar_path)
        else:
            brand_icon = icon_manager.icon("mdi6.leaf", size=32)
            if not brand_icon.isNull():
                self.brand_mark.setIcon(brand_icon)
                self.brand_mark.setIconSize(QSize(32, 32))
        self.primary_action.setMinimumHeight(metrics.control_height)
        self.collapse_button.setFixedHeight(metrics.compact_control_height)
        icon_manager.apply(self.primary_action, "mdi6.plus", size=max(18, round(metrics.control_height * 0.48)))

        if not self._collapsed:
            self._set_sidebar_width(metrics.sidebar_width)
        else:
            self._set_sidebar_width(metrics.collapsed_sidebar_width)
            self.setMinimumHeight(0)
            self.setMaximumHeight(16777215)

        for btn in self._buttons_by_key.values():
            btn.set_metrics(metrics)