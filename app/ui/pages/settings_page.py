from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QPlainTextEdit,
    QMessageBox,
    QWidget,
)

from app.ui.pages.base_page import BasePage
from app.ui.ui_helpers import StepperSpinBox, ToggleSwitch, make_card, make_label
from app.ui.slm_model_selector import ModelSelectorWidget
from app.services.slm import SlmService
from app.services.gaze_service import GazeService
from app.services.fake_data_service import FakeDataService
from app.ui.calibration.gaze_calibration_wizard import GazeCalibrationWizard


class SettingsPage(BasePage):
    settings_applied = Signal(dict)
    open_startup_setup = Signal()

    def __init__(
        self,
        metrics,
        settings: Any,
        repository: Any,
        active_window_service=None,
        gaze_service: GazeService | None = None,
        sidebar_template_service=None,
        parent=None,
    ):
        super().__init__(metrics, parent)
        self.settings = settings
        self.repository = repository
        self.slm_service = SlmService(settings, repository)
        self.active_window_service = active_window_service
        self.gaze_service = gaze_service
        self.sidebar_template_service = sidebar_template_service

        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(12)

        self.labels_to_search: list[Any] = []
        self._suppress_changes = False
        self._saved_settings: dict[str, Any] = {}
        self._dynamic_widgets: dict[str, dict[str, Any]] = {}
        self._defaults: dict[str, Any] = {}

        main_layout = QHBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.addLayout(main_layout)

        self.nav_card, self.nav_layout = make_card(
            "Settings sections",
            "Basic, profile, advanced, AI, and development.",
        )
        self.nav_layout.addStretch()

        # Flat right column widget and layout
        self.right_widget = QWidget()
        self.right_layout = QVBoxLayout(self.right_widget)
        self.right_layout.setContentsMargins(0, 0, 0, 0)
        self.right_layout.setSpacing(14)

        self.body_card, self.body_layout = make_card("Settings", "Configuration options.", elevated=False)
        self.body_layout.addStretch()

        self.apply_btn = QPushButton("Apply")
        self.apply_btn.setObjectName("PrimaryButton")
        self.apply_btn.clicked.connect(self._apply_settings)
        self.apply_btn.setEnabled(False)

        main_layout.addWidget(self.nav_card, 1)
        main_layout.addWidget(self.right_widget, 3)

        self.right_layout.addWidget(self.body_card)

        self.open_setup_btn = QPushButton("Open startup setup")
        self.open_setup_btn.setObjectName("SecondaryButton")
        self.open_setup_btn.clicked.connect(self.open_startup_setup.emit)

        apply_container = QHBoxLayout()
        apply_container.addWidget(self.open_setup_btn)
        apply_container.addStretch()
        apply_container.addWidget(self.apply_btn)
        self.body_layout.addLayout(apply_container)

        self._load_schema()
        self.load_from_settings()
        self._build_model_selector_panel()
        self._build_extension_panel()
        self._build_gaze_diagnostics_panel()
        self._build_slm_diagnostics_panel()
        self._build_fake_data_panel()
        self.right_layout.addStretch()
        self._refresh_extension_panel()
        self._refresh_gaze_diagnostics()
        self._refresh_slm_diagnostics()

    def _load_schema(self):
        schema = {
            "Basic": [
                {
                    "key": "Settings/theme",
                    "label": "Theme",
                    "type": "select",
                    "options": [
                        {"label": "Forest Focus", "value": "forest_focus"},
                        {"label": "Paper Daylight", "value": "paper_daylight"},
                        {"label": "Mono Focus", "value": "mono_focus"},
                    ],
                    "default": "forest_focus",
                },
                {
                    "key": "Notifications/enabled",
                    "label": "Enable notifications",
                    "type": "boolean",
                    "default": True,
                },
                {
                    "key": "Storage/autosave_interval",
                    "label": "Autosave Interval (min)",
                    "type": "number",
                    "min": 1,
                    "max": 60,
                    "default": 5,
                },
            ],
            "Profile": [
                {
                    "key": "Profile/display_name",
                    "label": "Preferred name",
                    "type": "text",
                    "default": "",
                },
                {
                    "key": "Profile/current_goals",
                    "label": "Current focus goals",
                    "type": "text",
                    "default": "",
                },
                {
                    "key": "Profile/custom_sidebar_text",
                    "label": "Custom sidebar subtitle text",
                    "type": "text",
                    "default": "PLANNER · DOER",
                },
            ],
            "Advanced": [
                {
                    "key": "Storage/database_path",
                    "label": "Database path",
                    "type": "text",
                    "default": str(self.settings.database_path()),
                },
                {
                    "key": "Storage/max_history_days",
                    "label": "Behavior log retention (days)",
                    "type": "number",
                    "min": 7,
                    "max": 3650,
                    "default": 365,
                },
                {
                    "key": "Planner/smart_reschedule_enabled",
                    "label": "Enable smart reschedule learning",
                    "type": "boolean",
                    "default": True,
                },
            ],
            "AI / Setup": [
                {
                    "key": "SLM/backend",
                    "label": "SLM backend",
                    "type": "select",
                    "options": [
                        {"label": "llama.cpp", "value": "llama_cpp"},
                        {"label": "ONNX Runtime (experimental)", "value": "onnx_runtime"},
                    ],
                    "default": "llama_cpp",
                },
                {
                    "key": "SLM/max_tokens",
                    "label": "SLM max tokens per reply",
                    "type": "number",
                    "min": 128,
                    "max": 2048,
                    "default": 512,
                },
                {
                    "key": "SLM/coach_enabled",
                    "label": "Enable Focus Coach weekly reviews",
                    "type": "boolean",
                    "default": False,
                },
                {
                    "key": "SLM/decomposition_enabled",
                    "label": "Enable task decomposition",
                    "type": "boolean",
                    "default": False,
                },
                {
                    "key": "SLM/prefer_gpu",
                    "label": "Prefer GPU when beneficial",
                    "type": "boolean",
                    "default": True,
                },
                {
                    "key": "SLM/gpu_memory_reserve_mb",
                    "label": "GPU reserve memory (MB)",
                    "type": "number",
                    "min": 256,
                    "max": 32768,
                    "default": 1024,
                },
                {
                    "key": "SLM/cpu_memory_reserve_mb",
                    "label": "CPU reserve memory (MB)",
                    "type": "number",
                    "min": 256,
                    "max": 65536,
                    "default": 1024,
                },
                {
                    "key": "SLM/planner_timeout_ms",
                    "label": "Planner timeout (ms)",
                    "type": "number",
                    "min": 50,
                    "max": 5000,
                    "default": 250,
                },
            ],
            "Vision / Gaze": [
                {
                    "key": "Vision/enable_gaze",
                    "label": "Enable gaze tracking",
                    "type": "boolean",
                    "default": False,
                },
                {
                    "key": "Vision/face_landmarker_model_path",
                    "label": "Face landmarker model path",
                    "type": "text",
                    "default": str(self.settings.app_data_dir() / "models" / "face_landmarker.task"),
                },
                {
                    "key": "Vision/camera_index",
                    "label": "Camera index",
                    "type": "number",
                    "min": 0,
                    "max": 8,
                    "default": 0,
                },
            ],
            "Development": [
                {
                    "key": "Development/dev_verbose_logs",
                    "label": "Verbose logs",
                    "type": "boolean",
                    "default": False,
                },
                {
                    "key": "Development/dev_plugin_reload",
                    "label": "Enable plugin reload",
                    "type": "boolean",
                    "default": False,
                },
                {
                    "key": "Development/dev_reset_database",
                    "label": "Arm data reset (requires explicit action)",
                    "type": "boolean",
                    "default": False,
                },
                {
                    "key": "Development/dev_show_startup_wizard",
                    "label": "Force startup wizard on next launch",
                    "type": "boolean",
                    "default": False,
                },
            ],
        }

        for section_name, fields in schema.items():
            nav_lbl = make_label(section_name, "secondary")
            self.nav_layout.insertWidget(self.nav_layout.count() - 1, nav_lbl)

            section_header = make_label(section_name, "cardTitle")
            self.labels_to_search.append(section_header)
            self.body_layout.insertWidget(self.body_layout.count() - 2, section_header)

            for field in fields:
                self._defaults[field["key"]] = field.get("default")
                row = QFrame()
                row.setObjectName("ListRow")
                row_layout = QHBoxLayout(row)
                row_layout.setContentsMargins(12, 12, 12, 12)
                row_layout.setSpacing(12)

                lbl = make_label(field["label"])
                self.labels_to_search.append(lbl)
                row_layout.addWidget(lbl)

                if field["key"] == "Profile/custom_sidebar_text":
                    info_btn = QPushButton("ⓘ")
                    info_btn.setObjectName("SidebarTextInfoBtn")
                    info_btn.setFixedSize(22, 22)
                    info_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                    info_btn.clicked.connect(self._show_sidebar_help)
                    row_layout.addWidget(info_btn, 0, Qt.AlignmentFlag.AlignVCenter)

                row_layout.addStretch()

                widget = None
                key = field["key"]
                ftype = field["type"]

                if ftype == "select":
                    widget = QComboBox()
                    for opt in field["options"]:
                        widget.addItem(opt["label"], opt["value"])
                    widget.currentIndexChanged.connect(self._on_setting_changed)
                elif ftype == "boolean":
                    widget = ToggleSwitch()
                    widget.toggled.connect(self._on_setting_changed)
                elif ftype == "number":
                    widget = StepperSpinBox()
                    if "min" in field:
                        widget.setMinimum(field["min"])
                    if "max" in field:
                        widget.setMaximum(field["max"])
                    widget.valueChanged.connect(self._on_setting_changed)
                elif ftype == "text":
                    widget = QLineEdit()
                    widget.textChanged.connect(self._on_setting_changed)

                if widget is not None:
                    row_layout.addWidget(widget)
                    self._dynamic_widgets[key] = {"widget": widget, "type": ftype}

                self.body_layout.insertWidget(self.body_layout.count() - 2, row)

    def _show_sidebar_help(self):
        if not self.sidebar_template_service:
            return

        placeholders = self.sidebar_template_service.get_placeholders_metadata()

        from app.ui.ui_helpers import TemplateGuidePopup
        popup = TemplateGuidePopup(placeholders, self)
        widget_data = self._dynamic_widgets.get("Profile/custom_sidebar_text")
        if widget_data and "widget" in widget_data:
            popup.show_at_widget(widget_data["widget"])
        else:
            popup.show()

    def _default_for_key(self, key: str):
        return self._defaults.get(key)

    def load_from_settings(self) -> None:
        values: dict[str, Any] = {}
        for key in self._dynamic_widgets.keys():
            if key == "Profile/display_name" and getattr(self, "repository", None) and hasattr(self.repository, "get_profile"):
                values[key] = self.repository.get_profile().display_name
            else:
                values[key] = self.settings.get(key, self._default_for_key(key))
        self.set_all_settings(values)

    def _on_setting_changed(self, *args):
        if self._suppress_changes:
            return
        current = self._collect_current_settings()
        self.apply_btn.setEnabled(current != self._saved_settings)

    def _collect_current_settings(self):
        settings = {}
        for key, data in self._dynamic_widgets.items():
            widget = data["widget"]
            ftype = data["type"]
            if ftype == "select":
                settings[key] = widget.currentData()
            elif ftype == "boolean":
                settings[key] = widget.isChecked()
            elif ftype == "number":
                settings[key] = widget.value()
            elif ftype == "text":
                settings[key] = widget.text().strip()
        return settings

    def _apply_settings(self):
        settings = self._collect_current_settings()

        db_path_value = settings.get("Storage/database_path")
        if db_path_value:
            normalized = str(Path(db_path_value).expanduser())
            settings["Storage/database_path"] = normalized

        model_path_value = settings.get("SLM/model_path")
        if model_path_value:
            normalized_model = str(Path(model_path_value).expanduser())
            settings["SLM/model_path"] = normalized_model

        face_model_path_value = settings.get("Vision/face_landmarker_model_path")
        if face_model_path_value:
            normalized_face = str(Path(face_model_path_value).expanduser())
            settings["Vision/face_landmarker_model_path"] = normalized_face

        for key, value in settings.items():
            self.settings.set(key, value)
        self.settings.sync()
        self._refresh_slm_diagnostics()

        self._saved_settings = settings.copy()
        self.apply_btn.setEnabled(False)
        self.settings_applied.emit(settings)

    def set_all_settings(self, settings_dict):
        self._suppress_changes = True
        try:
            for key, val in settings_dict.items():
                if key not in self._dynamic_widgets:
                    continue
                widget = self._dynamic_widgets[key]["widget"]
                ftype = self._dynamic_widgets[key]["type"]
                if ftype == "select":
                    idx = widget.findData(val)
                    if idx >= 0:
                        widget.setCurrentIndex(idx)
                elif ftype == "boolean":
                    widget.setChecked(bool(val))
                elif ftype == "number":
                    widget.setValue(int(val))
                elif ftype == "text":
                    widget.setText(str(val))
        finally:
            self._suppress_changes = False
            self._saved_settings = settings_dict.copy()
            self.apply_btn.setEnabled(False)

    def mark_as_applied(self):
        self._saved_settings = self._collect_current_settings()
        self.apply_btn.setEnabled(False)

    def filter_content(self, text: str):
        text = text.lower()
        if not text:
            for lbl in self.labels_to_search:
                lbl.setProperty("highlighted", False)
                lbl.style().unpolish(lbl)
                lbl.style().polish(lbl)
            return

        for lbl in self.labels_to_search:
            if text in lbl.text().lower():
                lbl.setProperty("highlighted", True)
            else:
                lbl.setProperty("highlighted", False)
            lbl.style().unpolish(lbl)
            lbl.style().polish(lbl)

    def _build_model_selector_panel(self) -> None:
        model_card, model_layout = make_card(
            "Model library",
            "Browse, download, and switch between local GGUF models. Existing files are never deleted.",
            elevated=False,
        )

        self._model_selector_widget = ModelSelectorWidget(self.settings, self)
        self._model_selector_widget.model_path_changed.connect(self._on_catalog_model_selected)
        model_layout.addWidget(self._model_selector_widget)

        self.right_layout.addWidget(model_card)

    def _on_catalog_model_selected(self, model_path: str) -> None:
        """Refresh SLM diagnostics and emit setting change when a catalog model is selected."""
        self._refresh_slm_diagnostics()
        self.settings_applied.emit({"SLM/model_path": model_path})

    def _build_extension_panel(self) -> None:
        self.extension_card, extension_layout = make_card(
            "Browser extension setup",
            "Smart guide for installing the browser extension and verifying telemetry.",
            elevated=False,
        )

        self.extension_status_label = make_label("Extension status: unknown.", "muted", word_wrap=True)
        self.extension_guide_label = make_label("Open setup wizard to install the browser extension.", "muted", word_wrap=True)
        self.current_window_label = make_label("Current window: unknown.", "muted", word_wrap=True)
        self.current_tab_label = make_label("Current tab: waiting for browser data.", "muted", word_wrap=True)
        self.window_reason_label = make_label("Decision: awaiting telemetry.", "muted", word_wrap=True)

        self.extension_refresh_btn = QPushButton("Refresh extension status")
        self.extension_refresh_btn.setObjectName("SecondaryButton")
        self.extension_refresh_btn.clicked.connect(self._refresh_extension_panel)

        self.extension_setup_btn = QPushButton("Open setup wizard")
        self.extension_setup_btn.setObjectName("SecondaryButton")
        self.extension_setup_btn.clicked.connect(self.open_startup_setup.emit)

        actions = QHBoxLayout()
        actions.addWidget(self.extension_refresh_btn)
        actions.addWidget(self.extension_setup_btn)
        actions.addStretch(1)

        self.window_stats_label = make_label("Active window telemetry: awaiting samples.", "muted", word_wrap=True)

        self.window_process_table = QTableWidget(0, 2)
        self.window_process_table.setHorizontalHeaderLabels(["Process", "Count"])
        self.window_process_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.window_process_table.verticalHeader().setVisible(False)
        self.window_process_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.window_process_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.window_process_table.setMinimumHeight(180)

        self.window_title_table = QTableWidget(0, 2)
        self.window_title_table.setHorizontalHeaderLabels(["Window title", "Count"])
        self.window_title_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.window_title_table.verticalHeader().setVisible(False)
        self.window_title_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.window_title_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.window_title_table.setMinimumHeight(180)

        self.extension_diag_text = QPlainTextEdit()
        self.extension_diag_text.setReadOnly(True)
        self.extension_diag_text.setPlaceholderText("No telemetry diagnostics yet.")
        self.extension_diag_text.setMaximumBlockCount(300)
        self.extension_diag_text.setMinimumHeight(220)
        self.extension_diag_text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)

        extension_layout.addLayout(actions)
        extension_layout.addWidget(self.extension_status_label)
        extension_layout.addWidget(self.extension_guide_label)
        extension_layout.addWidget(self.current_window_label)
        extension_layout.addWidget(self.current_tab_label)
        extension_layout.addWidget(self.window_reason_label)
        extension_layout.addWidget(self.window_stats_label)
        extension_layout.addWidget(self.window_process_table)
        extension_layout.addWidget(self.window_title_table)
        extension_layout.addWidget(self.extension_diag_text)

        self.right_layout.addWidget(self.extension_card)

    def _build_gaze_diagnostics_panel(self) -> None:
        self.gaze_card, gaze_layout = make_card(
            "Gaze diagnostics",
            "Status, calibration health, and last gaze sample.",
            elevated=False,
        )

        self.gaze_refresh_btn = QPushButton("Refresh gaze status")
        self.gaze_refresh_btn.setObjectName("SecondaryButton")
        self.gaze_refresh_btn.clicked.connect(self._refresh_gaze_diagnostics)

        self.gaze_calibrate_btn = QPushButton("Calibrate gaze")
        self.gaze_calibrate_btn.setObjectName("SecondaryButton")
        self.gaze_calibrate_btn.clicked.connect(self._start_gaze_calibration)

        actions = QHBoxLayout()
        actions.addWidget(self.gaze_refresh_btn)
        actions.addWidget(self.gaze_calibrate_btn)
        actions.addStretch(1)

        self.gaze_status_label = make_label("Gaze status: unknown.", "muted", word_wrap=True)
        self.gaze_detail_label = make_label("No gaze samples yet.", "muted", word_wrap=True)

        self.gaze_diag_text = QPlainTextEdit()
        self.gaze_diag_text.setReadOnly(True)
        self.gaze_diag_text.setPlaceholderText("No gaze diagnostics yet.")
        self.gaze_diag_text.setMaximumBlockCount(200)
        self.gaze_diag_text.setMinimumHeight(160)
        self.gaze_diag_text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)

        gaze_layout.addLayout(actions)
        gaze_layout.addWidget(self.gaze_status_label)
        gaze_layout.addWidget(self.gaze_detail_label)
        gaze_layout.addWidget(self.gaze_diag_text)

        self.right_layout.addWidget(self.gaze_card)

    def _refresh_gaze_diagnostics(self) -> None:
        if not self.gaze_service:
            self.gaze_status_label.setText("Gaze status: gaze service unavailable.")
            self.gaze_detail_label.setText("Enable gaze service in this build to collect data.")
            self.gaze_diag_text.setPlainText("gaze_service = None")
            return

        presence = self.gaze_service.read_presence()
        last = self.gaze_service.get_last_result()
        running = self.gaze_service.is_running()
        calibrated = self.gaze_service.is_calibrated()

        last_seen = presence.get("last_seen")
        if last_seen:
            last_seen_text = datetime.fromtimestamp(last_seen).strftime("%H:%M:%S")
        else:
            last_seen_text = "never"

        self.gaze_status_label.setText(
            f"Gaze status: running={running} calibrated={calibrated} last_seen={last_seen_text}."
        )

        if last is None:
            self.gaze_detail_label.setText("No gaze samples available yet.")
        else:
            self.gaze_detail_label.setText(
                f"Zone={last.zone.value} confidence={last.confidence:.2f} "
                f"yaw={last.yaw_deg:.1f} pitch={last.pitch_deg:.1f}."
            )

        lines = [
            "Gaze Diagnostics",
            "=" * 64,
            f"present: {presence.get('present')}",
            f"drift_rate_5m: {presence.get('drift_rate_5m')}",
            f"last_zone: {presence.get('last_zone')}",
            f"calibrated: {presence.get('calibrated')}",
        ]

        if last is not None:
            lines.extend(
                [
                    "",
                    "Last Gaze Result",
                    "-" * 64,
                    f"screen_id: {last.screen_id}",
                    f"gaze_x_norm: {last.gaze_x_norm:.3f}",
                    f"gaze_y_norm: {last.gaze_y_norm:.3f}",
                    f"gaze_x_px: {last.gaze_x_px}",
                    f"gaze_y_px: {last.gaze_y_px}",
                    f"face_detected: {last.face_detected}",
                    f"eye_open_avg: {last.eye_open_avg:.3f}",
                    f"is_blinking: {last.is_blinking}",
                    f"is_low_light: {last.is_low_light}",
                    f"is_blurry: {last.is_blurry}",
                ]
            )

        self.gaze_diag_text.setPlainText("\n".join(lines))

    def _current_gaze_model_path(self) -> Path:
        widget_data = self._dynamic_widgets.get("Vision/face_landmarker_model_path")
        if widget_data:
            widget = widget_data["widget"]
            return Path(widget.text().strip() or self.settings.get("Vision/face_landmarker_model_path", "")).expanduser()
        return Path(self.settings.get("Vision/face_landmarker_model_path", "")).expanduser()

    def _current_camera_index(self) -> int:
        widget_data = self._dynamic_widgets.get("Vision/camera_index")
        if widget_data:
            widget = widget_data["widget"]
            return int(widget.value())
        return int(self.settings.get("Vision/camera_index", 0) or 0)

    def _start_gaze_calibration(self) -> None:
        if self.gaze_service is None:
            QMessageBox.warning(self, "Gaze calibration", "Gaze service is unavailable in this build.")
            return

        model_path = self._current_gaze_model_path()
        if not model_path.exists():
            QMessageBox.warning(self, "Gaze calibration", "Face landmarker model not found. Download it first.")
            return

        screens = list(QApplication.screens())
        if not screens:
            QMessageBox.warning(self, "Gaze calibration", "No active screens detected.")
            return

        self.gaze_calibrate_btn.setEnabled(False)
        self.gaze_status_label.setText("Starting gaze calibration...")
        self._gaze_calibration_screens = screens
        self._gaze_calibration_index = 0
        self._resume_gaze_after_calibration = self.gaze_service.is_running()
        if self._resume_gaze_after_calibration:
            self.gaze_service.stop()
        self._start_next_gaze_screen(model_path)

    def _start_next_gaze_screen(self, model_path: Path) -> None:
        if self._gaze_calibration_index >= len(self._gaze_calibration_screens):
            self._finish_gaze_calibration()
            return

        screen = self._gaze_calibration_screens[self._gaze_calibration_index]
        self._gaze_wizard = GazeCalibrationWizard(
            screen=screen,
            model_path=str(model_path),
            camera_index=self._current_camera_index(),
            parent=None,
        )
        self._gaze_wizard.calibration_finished.connect(self._on_gaze_calibration_finished)
        self._gaze_wizard.calibration_cancelled.connect(self._on_gaze_calibration_cancelled)
        self._gaze_wizard.showFullScreen()

    def _on_gaze_calibration_finished(self, payload: dict) -> None:
        if self.gaze_service is None:
            return
        try:
            self.gaze_service.apply_calibration_result(
                screen_id=payload.get("screen_id", "primary"),
                iris_xs=payload.get("iris_xs", []),
                iris_ys=payload.get("iris_ys", []),
                yaws=payload.get("yaws", []),
                pitches=payload.get("pitches", []),
                screen_xs_norm=payload.get("screen_xs_norm", []),
                screen_ys_norm=payload.get("screen_ys_norm", []),
                natural_gaze=payload.get("natural_gaze", {}),
            )
        except Exception as exc:
            self.gaze_status_label.setText(f"Calibration failed: {exc}")

        self._gaze_calibration_index += 1
        model_path = self._current_gaze_model_path()
        self._start_next_gaze_screen(model_path)

    def _on_gaze_calibration_cancelled(self) -> None:
        self.gaze_status_label.setText("Calibration cancelled.")
        self._finish_gaze_calibration()

    def _finish_gaze_calibration(self) -> None:
        self.gaze_calibrate_btn.setEnabled(True)
        if self._resume_gaze_after_calibration and self.gaze_service is not None:
            self.gaze_service.start()
        self.settings.set("Vision/last_calibrated_at", str(datetime.utcnow().isoformat()))
        self.settings.sync()
        self._refresh_gaze_diagnostics()

    def _refresh_extension_panel(self) -> None:
        if not self.active_window_service:
            self.extension_status_label.setText("Extension status: active window service unavailable.")
            self.extension_guide_label.setText("Restart the app with telemetry services enabled.")
            self.current_window_label.setText("Current window: unavailable.")
            self.current_tab_label.setText("Current tab: unavailable.")
            self.window_reason_label.setText("Decision: active window service is missing.")
            self.extension_diag_text.setPlainText("active_window_service = None")
            return

        read_result = self.active_window_service.read_active_window()
        os_window = self.active_window_service.get_last_os_window()
        ext_data = self.active_window_service.get_last_extension_data()
        stats = self.active_window_service.get_recent_window_stats(60)

        last_seen = stats.get("last_seen")
        if last_seen:
            last_seen_text = datetime.fromtimestamp(last_seen).strftime("%H:%M:%S")
        else:
            last_seen_text = "never"

        browser_family = stats.get("browser_family", "unknown")
        browser_label = self._browser_family_label(browser_family)
        extension_state = self._classify_extension_state(os_window=os_window, ext_data=ext_data, browser_family=browser_family)

        self.extension_status_label.setText(
            f"Extension status: {extension_state['headline']} Last window sample: {last_seen_text}."
        )
        self.extension_guide_label.setText(self._build_extension_guide(browser_family, extension_state["code"]))

        if os_window is None:
            self.current_window_label.setText("Current window: unavailable (native tracker returned no state).")
        else:
            browser_flag = "browser" if os_window.is_browser else "app"
            shown_title = os_window.title.strip() if os_window.title else "untitled"
            shown_process = os_window.process.strip() if os_window.process else "unknown"
            self.current_window_label.setText(
                f"Current window: {shown_title} ({shown_process}) [{browser_flag}]"
            )

        native_known = False
        global_supported = bool(read_result.get("global_foreground_supported", True))

        if os_window is not None and global_supported:
            native_title = (getattr(os_window, "title", "") or "").strip()
            native_process = (getattr(os_window, "process", "") or "").strip().lower()
            native_known = bool(native_title or (native_process and native_process != "unknown"))
        else:
            native_known = False

        if os_window and os_window.is_browser:
            if ext_data:
                tab_title = (ext_data.active_tab_title or "untitled").strip()
                tab_url = (ext_data.active_tab_url or "").strip()
                productive = ext_data.is_productive
                self.current_tab_label.setText(
                    f"Current tab: {tab_title} | {tab_url or 'no-url'} | productive={productive}"
                )
            else:
                self.current_tab_label.setText(
                    "Current tab: browser is active, but extension telemetry is missing or stale."
                )
        elif ext_data and not native_known:
            tab_title = (ext_data.active_tab_title or "untitled").strip()
            tab_url = (ext_data.active_tab_url or "").strip()
            self.current_tab_label.setText(
                f"Recent browser telemetry: {tab_title} | {tab_url or 'no-url'}"
            )
        else:
            self.current_tab_label.setText(
                "Current tab: suppressed because the active foreground window is not a browser."
            )

        self.window_reason_label.setText(f"Decision: {extension_state['reason']}")
        self.extension_diag_text.setPlainText(
            self._build_extension_debug_text(
                os_window=os_window,
                ext_data=ext_data,
                stats=stats,
                read_result=read_result,
                browser_label=browser_label,
                extension_state=extension_state,
            )
        )

        self._refresh_window_tables(stats)

    def _browser_family_label(self, browser_family: str) -> str:
        if browser_family == "firefox":
            return "Firefox-based"
        if browser_family == "chromium":
            return "Chromium-based"
        return "Unknown"

    def _classify_extension_state(self, os_window: Any, ext_data: Any, browser_family: str) -> dict[str, str]:
        if os_window is None:
            return {
                "code": "native_unavailable",
                "headline": "native window tracker unavailable.",
                "reason": "The page could not obtain a foreground-window snapshot from the OS tracker.",
            }

        process = (getattr(os_window, "process", "") or "").lower().strip()
        title = (getattr(os_window, "title", "") or "").strip()
        is_browser = bool(getattr(os_window, "is_browser", False))
        native_known = bool(title or (process and process != "unknown"))

        service = self.active_window_service
        wayland_env = service.is_wayland_session() if service is not None else False

        if is_browser and ext_data:
            return {
                "code": "browser_live",
                "headline": f"{self._browser_family_label(browser_family)} browser active with live tab telemetry.",
                "reason": "Foreground focus is confirmed as a browser and fresh extension telemetry is available.",
            }

        if is_browser and not ext_data:
            if wayland_env and browser_family == "unknown":
                return {
                    "code": "browser_no_ext_wayland",
                    "headline": "browser active, extension telemetry missing, native platform path may be limited.",
                    "reason": "The browser appears active, but no fresh extension payload is available. On Wayland, native global window inspection is often restricted by compositor policy.",
                }
            return {
                "code": "browser_no_ext",
                "headline": f"{self._browser_family_label(browser_family)} browser active, but tab telemetry is missing.",
                "reason": "Foreground focus is a browser, but no fresh extension payload was available within the validity window.",
            }

        if (not is_browser) and ext_data and (not native_known):
            return {
                "code": "browser_telemetry_only",
                "headline": "browser telemetry is live, but foreground window is unresolved.",
                "reason": "Fresh extension data exists, but the OS tracker did not confirm the active foreground window. Browser data is treated as recent telemetry, not proof of current focus.",
            }

        if not is_browser and ext_data:
            return {
                "code": "non_browser_with_stale_ext",
                "headline": "non-browser app active; cached browser telemetry present but suppressed.",
                "reason": "The extension still has recent browser tab data, but the current foreground window is not a browser, so the tab line is intentionally hidden.",
            }

        if wayland_env and process == "unknown" and not title:
            return {
                "code": "wayland_limited",
                "headline": "foreground window unresolved; compositor restrictions likely.",
                "reason": "Wayland often blocks generic global active-window inspection unless compositor-specific integration exists.",
            }

        if process == "unknown" and not title:
            return {
                "code": "native_unknown",
                "headline": "foreground window unresolved by native tracker.",
                "reason": "The native tracker returned no reliable title or process for the active window.",
            }

        return {
            "code": "non_browser_ok",
            "headline": "non-browser application active.",
            "reason": "Foreground focus is currently an application window, so browser tab telemetry is not relevant.",
        }

    def _build_extension_guide(self, browser_family: str, state_code: str) -> str:
        if state_code == "browser_live":
            steps = [
                "Live browser telemetry is working.",
                "Keep the browser focused to inspect current tab metadata.",
                "Switch to Abte or another app to verify tab suppression logic.",
            ]
        elif state_code == "non_browser_with_stale_ext":
            steps = [
                "The active foreground window is not a browser.",
                "The panel is correctly suppressing stale browser tab data.",
                "Focus the browser again to verify live tab updates.",
            ]
        elif state_code in {"browser_no_ext", "browser_no_ext_wayland"}:
            if browser_family == "firefox":
                steps = [
                    "1) Open the setup wizard and choose Firefox-based install.",
                    "2) Confirm the extension is loaded and native messaging permission was granted.",
                    "3) Keep a browser tab focused for several seconds, then refresh.",
                    "4) Check the diagnostics panel for stale-or-missing extension state.",
                ]
            elif browser_family == "chromium":
                steps = [
                    "1) Chromium browser was detected.",
                    "2) Verify the native host manifest exists under the browser NativeMessagingHosts directory.",
                    "3) Verify the extension is installed and actively pushing state.",
                    "4) Refresh while the browser remains foreground.",
                ]
            else:
                steps = [
                    "1) Open the setup wizard to install the browser extension.",
                    "2) Focus the browser for several seconds.",
                    "3) Refresh and inspect the diagnostics text for tracker or extension failures.",
                ]
        elif state_code == "wayland_limited":
            steps = [
                "1) Native foreground tracking appears limited on this Wayland session.",
                "2) Use compositor-specific integration or browser extension telemetry where available.",
                "3) Inspect diagnostics to determine whether GNOME/KDE/wlroots support is missing.",
            ]
        else:
            steps = [
                "1) Open the setup wizard to detect your browser.",
                "2) Activate the target browser window for a few seconds.",
                "3) Refresh this panel and inspect the diagnostics text below.",
            ]
        return "\n".join(steps)

    def _build_extension_debug_text(
        self,
        os_window: Any,
        ext_data: Any,
        stats: dict,
        read_result: dict,
        browser_label: str,
        extension_state: dict[str, str],
    ) -> str:
        lines: list[str] = []

        tracker = getattr(self.active_window_service, "_os_tracker", None)
        gateway = getattr(self.active_window_service, "_browser_gateway", None)

        is_wayland = bool(getattr(tracker, "is_wayland", False)) if tracker else False
        tracker_cls = tracker.__class__.__name__ if tracker else "None"
        gateway_cls = gateway.__class__.__name__ if gateway else "None"

        lines.append("Active Window Diagnostics")
        lines.append("=" * 72)
        lines.append(f"Decision code: {extension_state.get('code', 'unknown')}")
        lines.append(f"Decision reason: {extension_state.get('reason', '')}")
        lines.append("")

        lines.append("Tracker")
        lines.append("-" * 72)
        lines.append(f"OS tracker class: {tracker_cls}")
        lines.append(f"Browser gateway class: {gateway_cls}")
        lines.append(f"Wayland session detected: {is_wayland}")
        lines.append(f"Browser family from samples: {browser_label}")
        lines.append("")

        lines.append("Foreground window snapshot")
        lines.append("-" * 72)
        if os_window is None:
            lines.append("os_window: None")
        else:
            lines.append(f"title: {repr(getattr(os_window, 'title', ''))}")
            lines.append(f"process: {repr(getattr(os_window, 'process', ''))}")
            lines.append(f"is_browser: {bool(getattr(os_window, 'is_browser', False))}")
        lines.append("")

        lines.append("Extension snapshot")
        lines.append("-" * 72)
        if ext_data is None:
            lines.append("ext_data: None")
        else:
            lines.append(f"active_tab_title: {repr(getattr(ext_data, 'active_tab_title', ''))}")
            lines.append(f"active_tab_url: {repr(getattr(ext_data, 'active_tab_url', ''))}")
            lines.append(f"is_productive: {bool(getattr(ext_data, 'is_productive', False))}")
            lines.append(f"tab_switch_count_5m: {int(getattr(ext_data, 'tab_switch_count_5m', 0))}")
            lines.append(f"focus_score_hint: {float(getattr(ext_data, 'focus_score_hint', 0.0))}")
        lines.append("")

        lines.append("Read result")
        lines.append("-" * 72)
        for key in [
            "title",
            "process",
            "process_tags",
            "tab_switch_frequency_5m",
            "app_switch_frequency_5m",
            "idle_seconds",
            "focus_score_window_5m",
            "focus_score_5m",
            "productive_keyword_hit",
            "user_override_hit",
        ]:
            lines.append(f"{key}: {repr(read_result.get(key))}")
        lines.append("")

        lines.append("Recent samples")
        lines.append("-" * 72)
        lines.append(f"samples_60s: {stats.get('samples', 0)}")
        lines.append(f"last_seen: {stats.get('last_seen')}")
        lines.append(f"by_process: {repr(stats.get('by_process', []))}")
        lines.append(f"top_titles: {repr(stats.get('top_titles', []))}")
        lines.append("")

        if gateway is not None:
            state_file = getattr(gateway, "_state_file", None)
            cache = getattr(gateway, "_cache", None)
            last_fetch_time = getattr(gateway, "_last_fetch_time", 0.0)

            lines.append("Browser gateway internals")
            lines.append("-" * 72)
            lines.append(f"state_file: {state_file}")
            if state_file and Path(state_file).exists():
                try:
                    raw = Path(state_file).read_text(encoding="utf-8")
                    lines.append(f"state_file_exists: True")
                    lines.append(f"state_file_mtime: {datetime.fromtimestamp(Path(state_file).stat().st_mtime).isoformat()}")
                    lines.append("state_file_raw:")
                    lines.append(raw[:4000])
                except Exception as exc:
                    lines.append(f"state_file_read_error: {exc}")
            else:
                lines.append("state_file_exists: False")
            lines.append(f"cache_present: {cache is not None}")
            lines.append(f"last_fetch_time: {last_fetch_time}")
            lines.append("")

        lines.append("Interpretation")
        lines.append("-" * 72)
        native_known = False
        if os_window is not None:
            native_title = (getattr(os_window, "title", "") or "").strip()
            native_process = (getattr(os_window, "process", "") or "").strip().lower()
            native_known = bool(native_title or (native_process and native_process != "unknown"))

            if os_window is None:
                lines.append("The settings page did not receive an OS foreground window snapshot.")
            elif os_window.is_browser and ext_data is None:
                lines.append("A browser is confirmed as foreground, but extension telemetry is missing or stale.")
            elif os_window.is_browser and ext_data is not None:
                lines.append("Foreground browser state and extension telemetry are aligned.")
            elif (not os_window.is_browser) and ext_data is not None and not native_known:
                lines.append("Fresh browser telemetry exists, but the OS foreground window is unresolved. Browser data is auxiliary only.")
            elif (not os_window.is_browser) and ext_data is not None:
                lines.append("Browser tab telemetry exists, but it is intentionally hidden because a non-browser app is foreground.")
            else:
                lines.append("Foreground is a non-browser app and no live browser telemetry is relevant.")
        return "\n".join(lines)

    def _refresh_window_tables(self, stats: dict) -> None:
        samples = stats.get("samples", 0)
        self.window_stats_label.setText(f"Active window telemetry: {samples} samples in the last 60s.")

        processes = stats.get("by_process", [])
        titles = stats.get("top_titles", [])

        self.window_process_table.setRowCount(0)
        for row_idx, (process, count) in enumerate(processes):
            self.window_process_table.insertRow(row_idx)
            self.window_process_table.setItem(row_idx, 0, QTableWidgetItem(str(process)))
            self.window_process_table.setItem(row_idx, 1, QTableWidgetItem(str(count)))

        self.window_title_table.setRowCount(0)
        for row_idx, (title, count) in enumerate(titles):
            self.window_title_table.insertRow(row_idx)
            self.window_title_table.setItem(row_idx, 0, QTableWidgetItem(str(title)))
            self.window_title_table.setItem(row_idx, 1, QTableWidgetItem(str(count)))

    def _build_slm_diagnostics_panel(self) -> None:
        self.diagnostics_card, diagnostics_layout = make_card(
            "SLM diagnostics",
            "Planner decision, benchmark summary, and cached runtime samples.",
            elevated=False,
        )

        self.refresh_diag_btn = QPushButton("Refresh diagnostics")
        self.refresh_diag_btn.setObjectName("SecondaryButton")
        self.refresh_diag_btn.clicked.connect(self._refresh_slm_diagnostics)

        self.run_diag_benchmark_btn = QPushButton("Run benchmark")
        self.run_diag_benchmark_btn.setObjectName("SecondaryButton")
        self.run_diag_benchmark_btn.clicked.connect(self._run_slm_benchmark_from_settings)

        self.diag_label = make_label("Planner diagnostics unavailable.", "muted", word_wrap=True)
        self.summary_label = make_label("No benchmark history yet.", "muted", word_wrap=True)

        actions = QHBoxLayout()
        actions.addWidget(self.refresh_diag_btn)
        actions.addWidget(self.run_diag_benchmark_btn)
        actions.addStretch(1)

        self.benchmark_table = QTableWidget(0, 5)
        self.benchmark_table.setHorizontalHeaderLabels(["Created", "Target", "Seconds", "Success", "Model"])
        self.benchmark_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.benchmark_table.verticalHeader().setVisible(False)
        self.benchmark_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.benchmark_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.benchmark_table.setMinimumHeight(220)

        diagnostics_layout.addLayout(actions)
        diagnostics_layout.addWidget(self.diag_label)
        diagnostics_layout.addWidget(self.summary_label)
        diagnostics_layout.addWidget(self.benchmark_table)

        self.right_layout.addWidget(self.diagnostics_card)

    def _refresh_slm_diagnostics(self) -> None:
        info = self.slm_service.planner_explain()
        if not info.get("ready"):
            self.diag_label.setText(str(info.get("reason", "Planner unavailable.")))
        else:
            self.diag_label.setText(
                f"Target: {info['target']} | "
                f"Estimated latency: {info['estimated_latency_seconds']}s | "
                f"Heuristic weight: {info['heuristic_weight']} | "
                f"Benchmark weight: {info['benchmark_weight']}"
            )
        self.summary_label.setText(self.slm_service.describe_benchmark_summary())

        records = list(reversed(self.slm_service.benchmark_store().all_records()))[:20]
        self.benchmark_table.setRowCount(0)
        for row_idx, record in enumerate(records):
            self.benchmark_table.insertRow(row_idx)
            self.benchmark_table.setItem(row_idx, 0, QTableWidgetItem(record.created_at))
            self.benchmark_table.setItem(row_idx, 1, QTableWidgetItem(record.target))
            self.benchmark_table.setItem(row_idx, 2, QTableWidgetItem(str(record.duration_seconds)))
            self.benchmark_table.setItem(row_idx, 3, QTableWidgetItem("Yes" if record.success else "No"))
            self.benchmark_table.setItem(row_idx, 4, QTableWidgetItem(Path(record.model_path).name))

    def _run_slm_benchmark_from_settings(self) -> None:
        self.run_diag_benchmark_btn.setEnabled(False)
        try:
            self.slm_service.benchmark_runtime()
            self._refresh_slm_diagnostics()
            current = self._collect_current_settings()
            current["SLM/benchmark_summary"] = self.slm_service.describe_benchmark_summary()
            self.set_all_settings(current)
            self.mark_as_applied()
        finally:
            self.run_diag_benchmark_btn.setEnabled(True)

    def _build_fake_data_panel(self) -> None:
        self.fake_data_card, layout = make_card(
            "Fake Data Generation",
            "Generate expansive mock data for development and testing.",
            elevated=False,
        )

        self.fake_tasks_spin = StepperSpinBox()
        self.fake_tasks_spin.setRange(0, 1000)
        self.fake_tasks_spin.setValue(50)

        self.fake_sessions_spin = StepperSpinBox()
        self.fake_sessions_spin.setRange(0, 1000)
        self.fake_sessions_spin.setValue(20)

        self.fake_days_spin = StepperSpinBox()
        self.fake_days_spin.setRange(1, 365)
        self.fake_days_spin.setValue(30)

        row_tasks = QHBoxLayout()
        row_tasks.addWidget(make_label("Tasks to generate"))
        row_tasks.addStretch(1)
        row_tasks.addWidget(self.fake_tasks_spin)
        layout.addLayout(row_tasks)

        row_sessions = QHBoxLayout()
        row_sessions.addWidget(make_label("Sessions to generate"))
        row_sessions.addStretch(1)
        row_sessions.addWidget(self.fake_sessions_spin)
        layout.addLayout(row_sessions)

        row_days = QHBoxLayout()
        row_days.addWidget(make_label("Days back to simulate"))
        row_days.addStretch(1)
        row_days.addWidget(self.fake_days_spin)
        layout.addLayout(row_days)

        self.generate_fake_data_btn = QPushButton("Generate Fake Data")
        self.generate_fake_data_btn.setObjectName("SecondaryButton")
        self.generate_fake_data_btn.clicked.connect(self._generate_fake_data)

        actions = QHBoxLayout()
        actions.addWidget(self.generate_fake_data_btn)
        actions.addStretch(1)
        layout.addLayout(actions)

        self.right_layout.addWidget(self.fake_data_card)

    def _generate_fake_data(self) -> None:
        if not self.repository:
            return
        tasks = self.fake_tasks_spin.value()
        sessions = self.fake_sessions_spin.value()
        days = self.fake_days_spin.value()

        self.generate_fake_data_btn.setEnabled(False)
        self.generate_fake_data_btn.setText("Generating...")
        QApplication.processEvents()

        try:
            svc = FakeDataService(self.repository)
            svc.generate_all(tasks_count=tasks, sessions_count=sessions, days_back=days)
            QMessageBox.information(self, "Fake Data", f"Successfully generated {tasks} tasks and {sessions} sessions over {days} days.")
        except Exception as exc:
            QMessageBox.warning(self, "Fake Data Error", f"Failed to generate fake data:\n{exc}")
        finally:
            self.generate_fake_data_btn.setEnabled(True)
            self.generate_fake_data_btn.setText("Generate Fake Data")