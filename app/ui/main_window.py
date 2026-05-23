from __future__ import annotations

from typing import Any, cast

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt, QTimer
from PySide6.QtGui import QKeySequence, QResizeEvent, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QLabel,
)

from app.services.notification_service import NotificationService
from app.services.handle_tasks import TaskService
from app.services.focus_session_service import FocusSessionService
from app.services.active_window_service import ActiveWindowService
from app.services.focus_tick_engine import FocusTickEngine
from app.ui.calendar_widget import FlexibleWeekViewWidget
from app.ui.metrics import UiMetrics, build_metrics
from app.ui.navigation import SidebarMenu
from app.ui.nav_config import HEADER_MAP, NAV_PAGE_ORDER, PAGE_ORDER, build_nav_sections
from app.ui.pages.dashboard import DashboardPage
from app.ui.pages.planner_page import PlannerPage
from app.ui.pages.settings_page import SettingsPage
from app.ui.startup_wizard_dialog import StartupWizardDialog
from app.ui.pages.task_editor_page import TaskEditorPage
from app.ui.pages.coach_page import CoachPage
from app.ui.pages.notifications_page import NotificationsPage
from app.ui.pages.account_page import AccountPage
from app.ui.plugins_manager import PluginsManagerWidget
from app.ui.theme import ThemeManager
from app.ui.ui_helpers import DetailOverlay, make_button, make_card, make_label, make_section_header
from app.ui.widgets.avatar_crop_dialog import AvatarCropDialog
from app.ui.icon_manager import icon_manager

AVAILABLE_THEMES = [
    "forest_focus",
    "mono_focus",
    "paper_daylight",
]



class MainWindow(QMainWindow):
    def __init__(
        self,
        repository,
        settings,
        plugin_manager,
        slm_service,
        active_window_service=None,
        gaze_service=None,
        focus_session_service: FocusSessionService | None = None, 
        focus_tick_engine: FocusTickEngine | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        self.repository = repository
        self.settings = settings
        self._plugin_manager = plugin_manager
        self._slmService = slm_service
        self.task_service = TaskService(repository, self._slmService)
        self.notification_service = NotificationService(repository)
        self.notification_service.add_publish_callback(self._show_toast)

        self.focus_session_service = focus_session_service or FocusSessionService(repository)

        self.active_window_service = active_window_service or ActiveWindowService()
        self.gaze_service = gaze_service
        self.focus_tick_engine = focus_tick_engine
        
        from app.data.fact_store import FactStore
        from app.services.fact_service import FactService
        self.fact_store = FactStore(settings.app_data_dir() / "facts")
        self.fact_service = FactService(self.fact_store)

        from app.services.sidebar_template_service import SidebarTemplateService
        self._sidebar_template_service = SidebarTemplateService(
            repository=self.repository,
            settings=self.settings,
            plugin_manager=self._plugin_manager,
            gaze_service=self.gaze_service,
        )

        if self.focus_tick_engine is not None:
            self.focus_tick_engine.set_notification_service(self.notification_service)
            self.focus_tick_engine.set_slm_service(self._slmService)
            self.focus_tick_engine.set_fact_service(self.fact_service)
            # Enable training data collection if a data dir is available
            try:
                if hasattr(self.settings, "app_data_dir"):
                    self.focus_tick_engine.set_data_dir(self.settings.app_data_dir())
            except Exception:
                pass

        saved_theme = self.settings.get("Settings/theme")

        if (isinstance(saved_theme, str) and saved_theme in AVAILABLE_THEMES):
            self._current_theme = saved_theme
        else:
            self._current_theme = "forest_focus"

        if self.focus_tick_engine is not None:
            self.focus_tick_engine.focus_updated.connect(self._on_focus_runtime_updated)


        geom = self.settings.get("MainWindow/geometry")
        if geom:
            self.restoreGeometry(geom)  # type: ignore[arg-type]
        else:
            self.resize(1440, 920)

        self.setMinimumSize(1100, 760)
        self.setWindowTitle("abte")

        app = cast(QApplication, QApplication.instance())
        self._metrics: UiMetrics = build_metrics(max(self.width(), 1280), self)
        self._theme_manager = ThemeManager(app)
        self._theme_manager.apply(self._metrics, self._current_theme)
        self._detail_open = False
        self._overlay_anim: QPropertyAnimation | None = None

        self.root = QWidget(self)
        self.root.setObjectName("AppRoot")
        self.setCentralWidget(self.root)

        self._pages: list[QWidget] = []
        self._page_scrolls: list[QScrollArea] = []
        self._page_index_by_key: dict[str, int] = {}

        self._build_ui()
        self.startup_wizard_dialog = StartupWizardDialog(
            self._metrics,
            settings=self.settings,
            repository=getattr(self, "repository", None),
            gaze_service=self.gaze_service,
            parent=self,
        )
        self.startup_wizard_dialog.setup_completed.connect(self._on_startup_setup_completed)
        self.coach_page.tasksCreated.connect(lambda _: self._refresh_productivity_surfaces())
        self._wire_sidebar()
        self._populate_detail_overlay()
        self._install_shortcuts()
        self._apply_metrics(force=True)
        self._load_settings()
        self._apply_profile_avatar()
        self._apply_gaze_settings(self._collect_gaze_settings())
        self._refresh_nav_badges()

        last_page_val = self.settings.get("MainWindow/last_page", "dashboard")
        self._navigate_request(last_page_val)
        QTimer.singleShot(100, self._maybe_open_startup_setup)

    def closeEvent(self, event) -> None:
        self.settings.set("MainWindow/geometry", self.saveGeometry())
        current_key = (
            PAGE_ORDER[self.page_stack.currentIndex()]
            if 0 <= self.page_stack.currentIndex() < len(PAGE_ORDER)
            else "dashboard"
        )
        self.settings.set("MainWindow/last_page", current_key)

        if self.focus_tick_engine is not None:
            try:
                self.focus_tick_engine.stop()
            except Exception:
                pass

        if self.gaze_service:
            try:
                if hasattr(self.gaze_service, "shutdown"):
                    self.gaze_service.shutdown()
                else:
                    self.gaze_service.stop()
            except Exception:
                pass

        super().closeEvent(event)

    def _show_toast(self, item: Any) -> None:
        from PySide6.QtWidgets import QLabel
        from PySide6.QtCore import QTimer
        
        # Only show toast for important notifications, or all of them if desired
        # The user requested facts/nudges (which are info/coach) to show as toast
        
        toast = QLabel(self.root)
        toast.setText(f"<b>{item.title}</b><br>{item.message}")
        toast.setWordWrap(True)
        toast.setMinimumWidth(300)
        toast.setMaximumWidth(400)
        
        toast.setObjectName("ToastMessage")
        toast.setProperty("level", item.level)
        toast.style().unpolish(toast)
        toast.style().polish(toast)
        
        toast.adjustSize()
        
        # Position at bottom right
        margin = 24
        x = self.root.width() - toast.width() - margin
        y = self.root.height() - toast.height() - margin
        toast.move(x, y)
        toast.show()
        toast.raise_()
        
        # Animate in (optional, keeping it simple for now)
        
        # Auto-dismiss after 5 seconds
        QTimer.singleShot(5000, toast.deleteLater)

    def _build_ui(self) -> None:
        self.root_layout = QGridLayout(self.root)
        self.root_layout.setContentsMargins(self._metrics.page_margin, self._metrics.page_margin, self._metrics.page_margin, self._metrics.page_margin)
        self.root_layout.setHorizontalSpacing(self._metrics.card_gap)
        self.root_layout.setVerticalSpacing(self._metrics.card_gap)

        self.sidebar = SidebarMenu(self._metrics)
        self.sidebar.set_sections(build_nav_sections())
        self.sidebar.setObjectName("SidebarSurface")
        self.sidebar.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

        self.content_shell = QWidget()
        self.content_shell.setObjectName("ContentShell")
        self.content_layout = QVBoxLayout(self.content_shell)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(self._metrics.card_gap)

        self.topbar = self._build_topbar()
        self.content_layout.addWidget(self.topbar, 0)

        self.page_stack = QStackedWidget()
        self.page_stack.setObjectName("PageStack")
        self.page_stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.dashboard_page = DashboardPage(self._metrics, self.repository, self.focus_session_service, self.focus_tick_engine)
        self.calendar_page = FlexibleWeekViewWidget(repository=self.repository)
        self.planner_page = PlannerPage(self._metrics, self.repository)
        self.tasks_page = TaskEditorPage(self._metrics, self.task_service, self.notification_service, self._slmService)
        self.coach_page = CoachPage(self._metrics, self.task_service)
        self.coach_page.set_slm_service(self._slmService)
        self.notifications_page = NotificationsPage(self._metrics, self.notification_service)
        self.plugins_page = PluginsManagerWidget(self._plugin_manager)
        self.settings_page = SettingsPage(
            self._metrics,
            settings=self.settings,
            repository=self.repository,
            active_window_service=self.active_window_service,
            gaze_service=self.gaze_service,
            sidebar_template_service=self._sidebar_template_service,
        )
        self.account_page = AccountPage(
            self._metrics,
            task_service=self.task_service,
            focus_session_service=self.focus_session_service,
            repository=self.repository,
            settings=self.settings,
        )

        page_specs = [
            ("dashboard",     self.dashboard_page),
            ("calendar",      self.calendar_page),
            ("planner",       self.planner_page),
            ("tasks",         self.tasks_page),
            ("coach",         self.coach_page),
            ("account",       self.account_page), 
            ("notifications", self.notifications_page),
            ("plugins",       self.plugins_page),
            ("settings",      self.settings_page),
        ]


        self._pages = [page for _, page in page_specs]
        self._page_index_by_key = {key: idx for idx, (key, _) in enumerate(page_specs)}

        for _key, page in page_specs:
            wrapper = self._wrap_page(page)
            self._page_scrolls.append(wrapper)
            self.page_stack.addWidget(wrapper)

        self.content_layout.addWidget(self.page_stack, 1)

        self.detail_overlay = DetailOverlay("Quick Add", "Smart capture panel.")
        self.detail_overlay.setMaximumWidth(0)
        self.detail_overlay.hide()

        self.root_layout.addWidget(self.sidebar, 0, 0, Qt.AlignmentFlag.AlignLeft)
        self.root_layout.addWidget(self.content_shell, 0, 1)
        self.root_layout.addWidget(self.detail_overlay, 0, 1, Qt.AlignmentFlag.AlignRight)
        self.root_layout.setColumnStretch(1, 1)

    def _wrap_page(self, page: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setObjectName("PageScrollArea")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        scroll.setWidget(page)
        return scroll
    
    def _cycle_theme(self) -> None:
        available = AVAILABLE_THEMES

        # Safety fallback
        if self._current_theme not in available:
            self._current_theme = available[0]

        current_index = available.index(self._current_theme)
        next_index = (current_index + 1) % len(available)
        next_theme = available[next_index]

        self._current_theme = next_theme

        # Persist
        self.settings.set("Settings/theme", next_theme)

        # Apply theme
        self._theme_manager.apply(self._metrics, next_theme)

        # Human readable tooltip
        theme_labels = {
            "forest_focus": "Forest Focus",
            "mono_focus": "Mono Focus",
            "paper_daylight": "Paper Daylight",
        }

        self.theme_toggle_btn.setToolTip(
            f"Theme: {theme_labels.get(next_theme, next_theme)}"
        )

        # Optional: force repaint for stubborn widgets
        self.repaint()
        self.update()


    def _position_notification_badge(self) -> None:
        if not hasattr(self, "_notif_badge"):
            return

        button_rect = self.notify_btn.geometry()

        badge_w = self._notif_badge.width()
        badge_h = self._notif_badge.height()

        x = button_rect.right() - int(badge_w * 0.55)
        y = button_rect.top() - int(badge_h * 0.15)

        self._notif_badge.move(x, y)
        self._notif_badge.raise_()


    def _set_notification_badge(self, count: int) -> None:
        if count <= 0:
            self._notif_badge.hide()
            return

        text = "99+" if count > 99 else str(count)
        self._notif_badge.setText(text)

        fm = self._notif_badge.fontMetrics()

        text_w = fm.horizontalAdvance(text)

        badge_h = 16
        horizontal_padding = 8

        badge_w = max(
            badge_h,
            text_w + horizontal_padding,
        )

        self._notif_badge.setFixedSize(badge_w, badge_h)

        self._position_notification_badge()

        self._notif_badge.show()
        self._notif_badge.raise_()


    def _build_topbar(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("TopbarCard")

        m = self._metrics
        layout = QHBoxLayout(frame)
        margin = max(8, m.card_padding - 4)
        layout.setContentsMargins(m.card_padding, margin, m.card_padding, margin)
        layout.setSpacing(m.card_gap)

        icon_box = m.compact_control_height
        icon_size_sm = max(16, round(icon_box * 0.50))
        icon_size_md = max(18, round(icon_box * 0.56))

        badge_h = max(14, round(icon_box * 0.44))
        badge_w_min = badge_h
        badge_x_nudge = max(1, round(m.border_width * 2))
        badge_y = max(1, round(icon_box * 0.08))

        avatar_box = max(icon_box, m.control_height)
        avatar_icon = max(18, round(avatar_box * 0.56))

        left_host = QWidget()
        left_layout = QHBoxLayout(left_host)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(max(8, m.card_gap - 2))

        self.page_breadcrumb = make_label("Workspace", "meta")
        self.page_title = make_label("Dashboard", "sectionTitle")
        self.page_subtitle = make_label("Today's focus overview and upcoming work.", "muted")

        left_layout.addWidget(self.page_breadcrumb, 0)
        left_layout.addWidget(self.page_title, 0)
        left_layout.addWidget(self.page_subtitle, 0)
        left_layout.addStretch(1)

        self.search = QLineEdit()
        self.search.setObjectName("SearchInput")
        self.search.setPlaceholderText("Search anything...")
        self.search.setMinimumHeight(m.control_height)
        self.search.textChanged.connect(self._on_search_changed)

        self._notify_container = QWidget()
        self._notify_container.setObjectName("TopbarNotifyContainer")
        self._notify_container.setFixedSize(icon_box + 10, icon_box + 10)
        

        self.notify_btn = QToolButton(self._notify_container)
        self.notify_btn.setObjectName("TopbarIconButton")
        self.notify_btn.setToolTip("Notifications")
        self.notify_btn.setFixedSize(icon_box, icon_box)
        self.notify_btn.move(0, 0)
        icon_manager.apply(self.notify_btn, "mdi6.bell-outline", size=icon_size_sm)
        self.notify_btn.clicked.connect(lambda: self._navigate_to_key("notifications"))

        self._notif_badge = QLabel("", self._notify_container)
        self._notif_badge.setObjectName("TopbarBadge")
        self._notif_badge.setMinimumSize(badge_w_min, badge_h)
        self._notif_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._notif_badge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._notif_badge.hide()
        self._position_notification_badge()
        self._notif_badge.raise_()

        self.theme_toggle_btn = QToolButton()
        self.theme_toggle_btn.setObjectName("TopbarIconButton")
        self.theme_toggle_btn.setToolTip(f"Theme: {self._current_theme}")
        self.theme_toggle_btn.setFixedSize(icon_box, icon_box)
        icon_manager.apply(self.theme_toggle_btn, "mdi6.palette-outline", size=icon_size_sm)
        self.theme_toggle_btn.clicked.connect(self._cycle_theme)

        self.quick_add_btn = QToolButton()
        self.quick_add_btn.setObjectName("TopbarPrimaryAction")
        self.quick_add_btn.setToolTip("New task (Ctrl+N)")
        self.quick_add_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.quick_add_btn.setFixedSize(icon_box, icon_box)
        icon_manager.apply(self.quick_add_btn, "mdi6.plus", size=icon_size_md)
        self.quick_add_btn.clicked.connect(self._open_quick_add)

        self.avatar_btn = QToolButton()
        self.avatar_btn.setObjectName("AvatarButton")
        self.avatar_btn.setToolTip("Account")
        self.avatar_btn.setFixedSize(avatar_box, avatar_box)
        icon_manager.apply(self.avatar_btn, "mdi6.account-circle-outline", size=avatar_icon)
        self.avatar_btn.clicked.connect(lambda: self._navigate_to_key("account"))

        self._gaze_dot = QLabel("●", frame)
        self._gaze_dot.setObjectName("GazeDot")
        self._gaze_dot.setProperty("state", "off")
        self._gaze_dot.setToolTip("Gaze tracking: off")
        self._gaze_dot.setFixedSize(16, 16)
        self._gaze_dot.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._gaze_dot.setCursor(Qt.CursorShape.PointingHandCursor)
        self._gaze_dot.mousePressEvent = lambda _: self._navigate_to_key("settings")

        layout.addWidget(left_host, 1)
        layout.addWidget(self.search, 1)
        layout.addWidget(self._gaze_dot, 0)
        layout.addWidget(self._notify_container, 0)
        layout.addWidget(self.theme_toggle_btn, 0)
        layout.addWidget(self.quick_add_btn, 0)
        layout.addWidget(self.avatar_btn, 0)
        return frame

    def _wire_sidebar(self) -> None:
        self.sidebar.pageRequested.connect(self._navigate_request)
        self.sidebar.actionRequested.connect(self._handle_sidebar_action)
        self.sidebar.collapseChanged.connect(self._on_sidebar_collapsed)
        self.sidebar.profileRequested.connect(self._open_profile_avatar_picker)
        if hasattr(self.account_page, "avatarEditRequested"):
            self.account_page.avatarEditRequested.connect(self._open_profile_avatar_picker)
        self.page_stack.currentChanged.connect(self._update_page_header_from_index)
        self.detail_overlay.close_button.clicked.connect(self.hide_detail_overlay)
        self.settings_page.settings_applied.connect(self._on_settings_applied)
        self.settings_page.open_startup_setup.connect(self._open_startup_setup)
        if hasattr(self.repository, "tasks_changed"):
            self.repository.tasks_changed.connect(self._refresh_nav_badges)
        if hasattr(self.repository, "notifications_changed"):
            self.repository.notifications_changed.connect(self._refresh_nav_badges)
        if hasattr(self.repository, "profile_changed"):
            self.repository.profile_changed.connect(self._apply_profile_avatar)
            self.repository.profile_changed.connect(self.dashboard_page.refresh_data)
            self.repository.profile_changed.connect(self.settings_page.load_from_settings)

        # Wire gaze calibration decay + camera status to topbar dot (#8 + #19)
        if self.gaze_service is not None:
            if hasattr(self.gaze_service, "calibration_decay_detected"):
                self.gaze_service.calibration_decay_detected.connect(
                    self._on_calibration_decay
                )
            if hasattr(self.gaze_service, "camera_status"):
                self.gaze_service.camera_status.connect(
                    self._on_camera_status_changed
                )
            if hasattr(self.gaze_service, "gaze_updated"):
                self.gaze_service.gaze_updated.connect(self._on_gaze_updated_topbar)

    def _on_sidebar_collapsed(self, collapsed: bool) -> None:
        if collapsed:
            self.sidebar.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
            self.root_layout.setAlignment(self.sidebar, Qt.AlignmentFlag.AlignLeft)
        else:
            self.sidebar.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
            self.root_layout.setAlignment(self.sidebar, Qt.AlignmentFlag.AlignLeft)

    def _clear_overlay_body(self) -> None:
        for layout in (self.detail_overlay.body_layout, self.detail_overlay.actions_layout):
            while layout.count():
                item = layout.takeAt(0)
                if item is None:
                    continue
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()
                    continue
                child_layout = item.layout()
                if child_layout is None:
                    continue
                while child_layout.count():
                    child_item = child_layout.takeAt(0)
                    if child_item is None:
                        continue
                    child_widget = child_item.widget()
                    if child_widget is not None:
                        child_widget.deleteLater()

    def _populate_detail_overlay(self) -> None:
        self._clear_overlay_body()
        self.detail_overlay.title_label.setText("Task details")
        self.detail_overlay.subtitle_label.setText("Focused editing panel.")
        self.detail_overlay.subtitle_label.show()
        self.detail_overlay.body_layout.addWidget(make_label("Deep work block", "cardTitle"))
        self.detail_overlay.body_layout.addWidget(make_label("Estimated duration: 90 minutes", "muted"))
        self.detail_overlay.body_layout.addWidget(make_label("Suggested slot: today at 09:00", "muted"))
        self.detail_overlay.actions_layout.addWidget(make_button("Accept suggestion", "primary"))
        self.detail_overlay.actions_layout.addWidget(make_button("Pick date", "secondary"))
        self.detail_overlay.actions_layout.addWidget(make_button("Undo", "ghost"))

    def _install_shortcuts(self) -> None:
        self.search_shortcut = QShortcut(QKeySequence("Ctrl+K"), self)
        self.search_shortcut.activated.connect(self._focus_search)
        self.new_task_shortcut = QShortcut(QKeySequence("Ctrl+N"), self)
        self.new_task_shortcut.activated.connect(self._open_quick_add)
        self.close_panel_shortcut = QShortcut(QKeySequence("Escape"), self)
        self.close_panel_shortcut.activated.connect(self.hide_detail_overlay)

    def _load_settings(self) -> None:
        self._theme_manager.apply(self._metrics, self._current_theme)
        if hasattr(self.settings_page, "load_from_settings"):
            self.settings_page.load_from_settings()
        self._apply_profile_avatar()

    def _apply_profile_avatar(self) -> None:
        if hasattr(self.repository, "get_profile"):
            profile = self.repository.get_profile()
            name = profile.display_name
            avatar_path = profile.avatar_path
        else:
            name = str(self.settings.get("Profile/display_name", "abte user") or "abte user")
            avatar_path = str(self.settings.get("Profile/avatar_path", "") or "")
        
        template = str(self.settings.get("Profile/custom_sidebar_text", "PLANNER · DOER") or "PLANNER · DOER")
        resolved_text = self._sidebar_template_service.render(template, name)
        self.sidebar.set_profile_avatar(name, avatar_path, resolved_text)
        if hasattr(self.account_page, "refresh_data"):
            self.account_page.refresh_data()

    def _open_profile_avatar_picker(self) -> None:
        from PySide6.QtWidgets import QFileDialog, QMessageBox

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select profile photo",
            "",
            "Images (*.png *.jpg *.jpeg *.bmp *.webp)",
        )
        if not file_path:
            return
        try:
            dialog = AvatarCropDialog(file_path, self)
        except Exception as exc:
            QMessageBox.warning(self, "Profile photo", f"Could not open image: {exc}")
            return
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        pixmap = dialog.cropped_pixmap()
        profile_dir = self.settings.app_data_dir() / "profile"
        profile_dir.mkdir(parents=True, exist_ok=True)
        target = profile_dir / "avatar.png"
        if not pixmap.save(str(target), "PNG"):
            QMessageBox.warning(self, "Profile photo", "Could not save the cropped image.")
            return
            
        if hasattr(self.repository, "get_profile"):
            profile = self.repository.get_profile()
            profile.avatar_path = str(target)
            self.repository.update_profile(profile)
            
        self.settings.set("Profile/avatar_path", str(target))
        self.settings.sync()
        self._apply_profile_avatar()

    def _collect_gaze_settings(self) -> dict:
        return {
            "Vision/enable_gaze": self.settings.get("Vision/enable_gaze", False),
            "Vision/face_landmarker_model_path": self.settings.get(
                "Vision/face_landmarker_model_path",
                str(self.settings.app_data_dir() / "models" / "face_landmarker.task"),
            ),
            "Vision/camera_index": self.settings.get("Vision/camera_index", 0),
        }

    def _apply_gaze_settings(self, settings: dict) -> None:
        if not self.gaze_service:
            return
        enabled = bool(settings.get("Vision/enable_gaze", False))
        model_path = settings.get("Vision/face_landmarker_model_path", ...)
        camera_index = int(settings.get("Vision/camera_index", 0) or 0)
        self.gaze_service.set_enabled(enabled)           # gates session-start
        self.gaze_service.configure(model_path, camera_index)
        self._apply_profile_avatar()
        self.settings_page.mark_as_applied()

    def _on_settings_applied(self, settings: dict) -> None:
        theme_value = settings.get("Settings/theme")
        if isinstance(theme_value, str) and theme_value:
            self._current_theme = theme_value
            self._theme_manager.apply(self._metrics, self._current_theme)
        self._apply_gaze_settings(settings)
        
        if hasattr(self.repository, "get_profile"):
            new_name = settings.get("Profile/display_name")
            if new_name is not None:
                profile = self.repository.get_profile()
                if new_name != profile.display_name:
                    profile.display_name = new_name
                    self.repository.update_profile(profile)
        self._apply_profile_avatar()
        
        self.settings_page.mark_as_applied()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._apply_metrics()

    def _apply_metrics(self, force: bool = False) -> None:
        new_metrics = build_metrics(max(self.width(), 1100), self)
        if not force and new_metrics == self._metrics:
            self._apply_responsive_state()
            return

        self._metrics = new_metrics
        self._theme_manager.apply(new_metrics, self._current_theme)
        self.root_layout.setContentsMargins(
            new_metrics.page_margin, new_metrics.page_margin,
            new_metrics.page_margin, new_metrics.page_margin,
        )
        self.root_layout.setHorizontalSpacing(new_metrics.card_gap)
        self.root_layout.setVerticalSpacing(new_metrics.card_gap)
        self.content_layout.setSpacing(new_metrics.card_gap)

        topbar_layout = self.topbar.layout()
        if topbar_layout is not None:
            margin = max(8, new_metrics.card_padding - 4)
            topbar_layout.setContentsMargins(margin, margin, margin, margin)
            topbar_layout.setSpacing(new_metrics.card_gap)

        icon_size = max(18, round(new_metrics.control_height * 0.5))
        icon_manager.apply(self.notify_btn, "mdi6.bell-outline", size=icon_size)
        icon_manager.apply(self.theme_toggle_btn, "mdi6.palette-outline", size=icon_size)
        icon_manager.apply(self.avatar_btn, "mdi6.account-circle-outline", size=icon_size + 2)
        icon_manager.apply(self.quick_add_btn, "mdi6.plus", size=icon_size)

        btn_size = new_metrics.control_height
        self.notify_btn.setFixedSize(btn_size, btn_size)
        self._notify_container.setFixedSize(btn_size + 8, btn_size)
        self._notif_badge.move(btn_size - 6, 0)

        self.sidebar.apply_metrics(new_metrics)

        for page in self._pages:
            if hasattr(page, "apply_metrics"):
                page.apply_metrics(new_metrics)  # type: ignore[attr-defined]

        if self._detail_open:
            self.detail_overlay.setMaximumWidth(new_metrics.detail_panel_width)
        else:
            self.detail_overlay.setMaximumWidth(0)

        self._apply_responsive_state()

    def _apply_responsive_state(self) -> None:
        w = self.width()
        compact = w < 1320
        very_compact = w < 1160
        self.page_breadcrumb.setVisible(not very_compact)
        self.page_subtitle.setVisible(w >= 1440)
        self.search.setMinimumWidth(160 if very_compact else 220)
        self.search.setMaximumWidth(260 if compact else 360)
        self.quick_add_btn.setFixedSize(self._metrics.control_height, self._metrics.control_height)

    def _current_page_widget(self) -> QWidget | None:
        wrapper = self.page_stack.currentWidget()
        if isinstance(wrapper, QScrollArea):
            return wrapper.widget()
        return None

    def _navigate_request(self, request: Any) -> None:
        key = self._normalize_route_request(request)
        self._navigate_to_key(key)

    def _normalize_route_request(self, request: Any) -> str:
        if isinstance(request, str):
            req = request.strip().lower()
            if req in self._page_index_by_key:
                return req
            return "dashboard"
        try:
            index = int(request)
        except (TypeError, ValueError):
            return "dashboard"
        if 0 <= index < len(NAV_PAGE_ORDER):
            return NAV_PAGE_ORDER[index]
        if 0 <= index < len(PAGE_ORDER):
            return PAGE_ORDER[index]
        return "dashboard"

    def _navigate_to_key(self, key: str) -> None:
        index = self._page_index_by_key.get(key, 0)
        self.page_stack.setCurrentIndex(index)
        if key in NAV_PAGE_ORDER:
            self.sidebar.set_current_page(key)
        self._update_page_header(key)
        self._on_search_changed(self.search.text())
        current = self._current_page_widget()
        if current is not None:
            current.setFocus(Qt.FocusReason.OtherFocusReason)

    def _update_page_header_from_index(self, index: int) -> None:
        if 0 <= index < len(PAGE_ORDER):
            self._update_page_header(PAGE_ORDER[index])

    def _update_page_header(self, key: str) -> None:
        meta = HEADER_MAP.get(key, {"category": "Workspace", "title": "abte", "subtitle": ""})
        category = str(meta.get("category", "Workspace"))
        title = str(meta.get("title", "abte"))
        subtitle = str(meta.get("subtitle", ""))
        self.page_breadcrumb.setText(f"{category} / {title}")
        self.page_title.setText(title)
        self.page_subtitle.setText(subtitle)

    def _on_search_changed(self, text: str) -> None:
        current_page = self._current_page_widget()
        if current_page is not None and hasattr(current_page, "filter_content"):
            current_page.filter_content(text)  # type: ignore[attr-defined]

    def _handle_sidebar_action(self, action_key: str) -> None:
        if action_key == "quick_add":
            self._open_quick_add()
        elif action_key == "search":
            self._focus_search()
        elif action_key == "weekly_review":
            self._navigate_to_key("coach")

    def _focus_search(self) -> None:
        self.search.setFocus()
        self.search.selectAll()

    def _refresh_productivity_surfaces(self) -> None:
        for page in [self.dashboard_page, self.calendar_page, self.tasks_page, self.coach_page, self.account_page]:
            if hasattr(page, "refresh_data"):
                page.refresh_data()
        self._refresh_nav_badges()

    def _refresh_nav_badges(self) -> None:
        tasks = self.task_service.list_tasks()
        active_count = sum(1 for task in tasks if getattr(task, "status", "todo") not in {"done", "cancelled"})
        summary = self.notification_service.summary(hours=72)
        unread = int(summary.unread)

        task_badge = "" if active_count == 0 else ("99+" if active_count > 99 else str(active_count))
        notif_badge = "" if unread == 0 else ("99+" if unread > 99 else str(unread))

        self.sidebar.set_badge("tasks", task_badge)
        self.sidebar.set_badge("notifications", notif_badge)

        # Update topbar notification badge overlay
        if unread > 0:
            self._notif_badge.setText("99+" if unread > 99 else str(unread))
            self._notif_badge.show()
        else:
            self._notif_badge.hide()
        self._apply_profile_avatar()

    def _open_quick_add(self) -> None:
        self.show_detail_overlay()
        self._clear_overlay_body()
        self.detail_overlay.title_label.setText("Quick Add")
        self.detail_overlay.subtitle_label.setText("Smart capture: title, tags, time, estimate, and priority from one line.")
        self.detail_overlay.subtitle_label.show()

        input_header, _ = make_section_header("Capture", "Write a single line and the parser fills the fields.")
        self.detail_overlay.body_layout.addWidget(input_header)

        smart_input = QLineEdit()
        smart_input.setObjectName("QuickAddInput")
        smart_input.setPlaceholderText("Write report tomorrow 3pm #work !! 90m")

        notes_input = QPlainTextEdit()
        notes_input.setObjectName("QuickAddNotes")
        notes_input.setPlaceholderText("Notes, constraints, or extra details")
        notes_input.setFixedHeight(110)

        preview_card, preview_layout = make_card("Parsed preview", "Fields extracted from the line.", elevated=False)
        preview_label = make_label("", "muted", True)
        preview_layout.addWidget(preview_label)
        examples = make_label("Examples: Pay rent tomorrow 10am #admin ! 15m · Deep work on API today 2pm #work !! 2h", "muted", True)

        def update_preview() -> None:
            text = smart_input.text().strip()
            if not text:
                preview_label.setText("Enter a task line to preview parsed fields.")
                return
            try:
                parsed = self.task_service.parse_quick_add(text, notes_input.toPlainText())
            except Exception as exc:
                preview_label.setText(f"Parse error: {exc}")
                return

            due_value = parsed.get("due_at")
            due_text = due_value.strftime("%Y-%m-%d %H:%M") if due_value else "-"
            preview_label.setText(
                f"Title: {parsed['title'] or '-'}\n"
                f"Tags: {', '.join(parsed['tags']) or '-'}\n"
                f"Priority: {parsed['priority']}\n"
                f"Estimate: {parsed['estimated_minutes']}m\n"
                f"Due: {due_text}"
            )

        def save_task() -> None:
            try:
                self.task_service.create_from_quick_add(smart_input.text(), notes_input.toPlainText())
            except Exception as exc:
                preview_label.setText(f"Save error: {exc}")
                return
            self.hide_detail_overlay()
            self._refresh_productivity_surfaces()

        smart_input.textChanged.connect(update_preview)
        notes_input.textChanged.connect(update_preview)

        save_btn = make_button("Save task", "primary")
        cancel_btn = make_button("Cancel", "ghost")
        save_btn.clicked.connect(save_task)
        cancel_btn.clicked.connect(self.hide_detail_overlay)

        self.detail_overlay.body_layout.addWidget(smart_input)
        self.detail_overlay.body_layout.addWidget(notes_input)
        self.detail_overlay.body_layout.addWidget(preview_card)
        self.detail_overlay.body_layout.addWidget(examples)

        actions_row = QHBoxLayout()
        actions_row.setSpacing(8)
        actions_row.addWidget(save_btn)
        actions_row.addWidget(cancel_btn)
        actions_row.addStretch()
        self.detail_overlay.actions_layout.addLayout(actions_row)

        update_preview()
        smart_input.setFocus()

    def show_detail_overlay(self) -> None:
        if self._detail_open:
            return
        self._detail_open = True
        panel_w = self._metrics.detail_panel_width
        self.detail_overlay.setMaximumWidth(0)
        self.detail_overlay.show()
        self._overlay_anim = QPropertyAnimation(self.detail_overlay, b"maximumWidth", self)
        self._overlay_anim.setDuration(180)
        self._overlay_anim.setStartValue(0)
        self._overlay_anim.setEndValue(panel_w)
        self._overlay_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._overlay_anim.start()

    def hide_detail_overlay(self) -> None:
        if not self._detail_open:
            return
        self._detail_open = False
        start_width = max(0, self.detail_overlay.width())
        self._overlay_anim = QPropertyAnimation(self.detail_overlay, b"maximumWidth", self)
        self._overlay_anim.setDuration(160)
        self._overlay_anim.setStartValue(start_width)
        self._overlay_anim.setEndValue(0)
        self._overlay_anim.setEasingCurve(QEasingCurve.Type.InCubic)

        def _finish_hide() -> None:
            self.detail_overlay.hide()

        self._overlay_anim.finished.connect(_finish_hide)
        self._overlay_anim.start()

    def _open_startup_setup(self) -> None:
        self.startup_wizard_dialog.resize(max(820, int(self.width() * 0.72)), max(620, int(self.height() * 0.82)))
        self.startup_wizard_dialog.show()
        self.startup_wizard_dialog.raise_()
        self.startup_wizard_dialog.activateWindow()

    def _on_startup_setup_completed(self, payload: dict) -> None:
        _ = payload
        if hasattr(self.settings_page, "load_from_settings"):
            self.settings_page.load_from_settings()
        self._apply_gaze_settings(self._collect_gaze_settings())

    def _maybe_open_startup_setup(self) -> None:
        try:
            if bool(self.settings.get("Development/dev_show_startup_wizard", False)):
                self.settings.set("Development/dev_show_startup_wizard", False)
                self.settings.sync()
                self._open_startup_setup()
                return

            if bool(self.settings.get("Startup/first_run_completed", False)):
                return
            if self._slmService.should_show_startup_setup():
                self._open_startup_setup()
        except Exception:
            return
    def _on_focus_runtime_updated(self, snapshot: object) -> None:
        """
        Receives FocusRuntimeSnapshot every model tick (~2 Hz).
        Routes the live update to the dashboard and topbar without a full
        refresh_data() call, keeping the UI lightweight.
        """
        from app.services.focus_tick_engine import FocusRuntimeSnapshot
        if not isinstance(snapshot, FocusRuntimeSnapshot):
            return

        # Push live data to dashboard if it exists and is the active page.
        if hasattr(self, "dashboard_page"):
            try:
                self.dashboard_page.on_live_focus_updated(snapshot)
            except Exception:
                pass

    def _set_gaze_dot_state(self, state: str) -> None:
        if hasattr(self, "_gaze_dot"):
            self._gaze_dot.setProperty("state", state)
            self._gaze_dot.style().unpolish(self._gaze_dot)
            self._gaze_dot.style().polish(self._gaze_dot)

    def _on_calibration_decay(self) -> None:
        """Gaze calibration accuracy has degraded — notify user and turn dot yellow (#8)."""
        self._set_gaze_dot_state("degraded")
        if hasattr(self, "_gaze_dot"):
            self._gaze_dot.setToolTip("Gaze: calibration degraded — click to recalibrate")
        try:
            self.notification_service.publish(
                title="Gaze calibration degraded",
                message="Head pose accuracy has dropped. Click the yellow dot in the topbar to recalibrate.",
                level="warning",
            )
        except Exception:
            pass

    def _on_camera_status_changed(self, status: str, detail: str) -> None:
        """Updates the gaze status dot colour based on camera state (#8 + #19)."""
        if not hasattr(self, "_gaze_dot"):
            return
        if status in {"unavailable", "lost"}:
            self._set_gaze_dot_state("error")
            self._gaze_dot.setToolTip(f"Gaze: camera {status} ({detail}) — click to open settings")
        elif status == "degraded":
            self._set_gaze_dot_state("degraded")
            self._gaze_dot.setToolTip("Gaze: poor lighting or low quality frame — click to open settings")
        elif status == "ok":
            self._set_gaze_dot_state("active")
            self._gaze_dot.setToolTip("Gaze tracking: active")
        else:
            self._set_gaze_dot_state("off")
            self._gaze_dot.setToolTip(f"Gaze: {status}")

    def _on_gaze_updated_topbar(self, result: object) -> None:
        """Keeps the gaze dot green while the camera is live and face is present (#19)."""
        if not hasattr(self, "_gaze_dot"):
            return
        face_present = bool(getattr(result, "face_detected", False))
        zone = getattr(result, "zone", None)
        zone_name = zone.name if hasattr(zone, "name") else str(zone).upper() if zone else ""
        if zone_name == "DEGRADED":
            self._set_gaze_dot_state("degraded")
            self._gaze_dot.setToolTip("Gaze: signal degraded (poor light or blur) — click to open settings")
        elif face_present:
            self._set_gaze_dot_state("active")
            self._gaze_dot.setToolTip("Gaze tracking: active — face detected")
        else:
            self._set_gaze_dot_state("off")
            self._gaze_dot.setToolTip("Gaze: no face detected")