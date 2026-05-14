from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
import shutil
import urllib.request

from PySide6.QtCore import QEvent, Qt, QPropertyAnimation, Signal
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QProgressBar,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
    QPlainTextEdit,
    QTableWidget,
    QHeaderView,
    QTableWidgetItem,
    QMessageBox,
)

from app.ui.animation_core import FadeScaleMixin, GalaxyBackdropWidget
from app.ui.ui_helpers import ToggleSwitch, StepperSpinBox, make_button, make_card, make_label
from app.models.llama_runtime import LlamaRuntimeDetector
from app.core.llama_install_help import LlamaInstallGuideFactory, InstallGuide
from app.services.slm import SlmService
from app.services.gaze_service import GazeService
from app.ui.calibration.gaze_calibration_wizard import GazeCalibrationWizard

DEFAULT_GGUF_URL = (
    "https://huggingface.co/microsoft/Phi-3-mini-4k-instruct-gguf/resolve/main/"
    "Phi-3-mini-4k-instruct-q4.gguf"
)
DEFAULT_FACE_LANDMARKER_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
    "face_landmarker/float16/1/face_landmarker.task"
)

@dataclass(slots=True)
class StartupWizardDialog(QDialog, FadeScaleMixin):
    setup_completed = Signal(dict)

    def __init__(
        self,
        metrics,
        settings: Any,
        repository: Any,
        gaze_service: GazeService | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.metrics = metrics
        self.settings = settings
        self.repository = repository
        self.slm_service = SlmService(settings, repository)
        self.gaze_service = gaze_service

        self.setModal(True)
        self.setObjectName("StartupWizardDialog")
        self.setWindowTitle("ABTE setup")
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowSystemMenuHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.resize(980, 760)
        self.setMinimumSize(780, 620)

        root = QGridLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.overlay_root = QWidget(self)
        self.overlay_root.setObjectName("StartupWizardOverlayRoot")
        self.overlay_root.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.overlay_root.setAutoFillBackground(False)
        root.addWidget(self.overlay_root, 0, 0)

        overlay_layout = QGridLayout(self.overlay_root)
        overlay_layout.setContentsMargins(0, 0, 0, 0)
        overlay_layout.setSpacing(0)

        self.backdrop = GalaxyBackdropWidget(self.overlay_root)
        self.backdrop.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        overlay_layout.addWidget(self.backdrop, 0, 0)

        
        self.center_host = QWidget(self.overlay_root)
        self.center_host.setObjectName("StartupWizardCenterHost")
        self.center_host.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.center_host.setAutoFillBackground(False)
        overlay_layout.addWidget(self.center_host, 0, 0)

        self.shell = QFrame(self.center_host)
        self.shell.setObjectName("WizardShell")
        self.shell.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.shell.setMaximumWidth(980)

        self.shell.setStyleSheet("""
        QFrame#WizardShell {
            background: rgba(9, 14, 28, 212);
            border: 1px solid rgba(130, 180, 255, 48);
            border-radius: 28px;
        }
        """)

        self.setObjectName("StartupWizardOverlay")
        self.setStyleSheet("""
        QDialog#StartupWizardOverlay,
        QWidget#StartupWizardOverlayRoot,
        QWidget#StartupWizardCenterHost {
            background: transparent;
            border: none;
            color: #ECF6F1;
        }
        """)

        center_layout = QVBoxLayout(self.center_host)
        center_layout.setContentsMargins(36, 36, 36, 36)
        center_layout.addStretch(1)
        center_layout.addWidget(self.shell, 0, Qt.AlignmentFlag.AlignHCenter)
        center_layout.addStretch(1)

        self.shell_opacity_effect = QGraphicsOpacityEffect(self.shell)
        self.shell_opacity_effect.setOpacity(1.0)
        self.shell.setGraphicsEffect(self.shell_opacity_effect)

        self.shell_opacity_anim = QPropertyAnimation(self.shell_opacity_effect, b"opacity", self)
        self.shell_opacity_anim.setDuration(180)

        shell_layout = QVBoxLayout(self.shell)
        shell_layout.setContentsMargins(20, 20, 20, 20)
        shell_layout.setSpacing(16)

        header = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(4)

        self.title_label = make_label("Setup ABTE", "pageTitle")
        self.subtitle_label = make_label(
            "Local AI, profile context, and safe productivity defaults.",
            "muted",
            word_wrap=True,
        )
        title_col.addWidget(self.title_label)
        title_col.addWidget(self.subtitle_label)

        self.close_btn = make_button("Close", "ghost")
        self.close_btn.clicked.connect(self.reject)

        header.addLayout(title_col, 1)
        header.addWidget(self.close_btn, 0)
        shell_layout.addLayout(header)

        self.status_label = make_label(
            "The local model stays on-device. This product is not a therapist or clinician.",
            "muted",
            word_wrap=True,
        )
        shell_layout.addWidget(self.status_label)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        shell_layout.addWidget(self.scroll_area, 1)

        self.content = QWidget()
        self.scroll_area.setWidget(self.content)

        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(4, 4, 4, 4)
        self.content_layout.setSpacing(14)

        self._build_sections()

        actions = QHBoxLayout()
        self.download_btn = make_button("Download model", "secondary")
        self.download_btn.clicked.connect(self._download_model)

        self.complete_btn = make_button("Complete setup", "primary")
        self.complete_btn.clicked.connect(self._complete_setup)

        actions.addWidget(self.download_btn)
        actions.addStretch(1)
        actions.addWidget(self.complete_btn)
        shell_layout.addLayout(actions)

        self._drag_offset = None
        self._load_from_settings()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.animate_dialog_in(self)

    def event(self, event):
        if event.type() == QEvent.Type.WindowActivate:
            self._set_shell_focus_opacity(True)
        elif event.type() == QEvent.Type.WindowDeactivate:
            self._set_shell_focus_opacity(False)
        return super().event(event)

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self._set_shell_focus_opacity(True)

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self._set_shell_focus_opacity(False)

    def _set_shell_focus_opacity(self, focused: bool) -> None:
        start = self.shell_opacity_effect.opacity()
        end = 1.0 if focused else 0.72
        self.shell_opacity_anim.stop()
        self.shell_opacity_anim.setStartValue(start)
        self.shell_opacity_anim.setEndValue(end)
        self.shell_opacity_anim.start()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    def _build_sections(self) -> None:
        intro_card, intro_layout = make_card(
            "What this setup does",
            "Configure profile context, local SLM usage, and development helpers.",
            elevated=True,
        )
        intro_layout.addWidget(
            make_label(
                "Focus Coach uses local summaries from your own saved data. "
                "Task decomposition breaks broad goals into smaller tasks when enabled.",
                "muted",
                word_wrap=True,
            )
        )
        self.content_layout.addWidget(intro_card)

        profile_card, profile_layout = make_card("Profile", "Used as lightweight planning context.")
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Preferred name")
        self.goals_input = QLineEdit()
        self.goals_input.setPlaceholderText("Current goals or priorities")

        profile_layout.addWidget(make_label("Name"))
        profile_layout.addWidget(self.name_input)
        profile_layout.addWidget(make_label("Current goals"))
        profile_layout.addWidget(self.goals_input)
        self.content_layout.addWidget(profile_card)

        ai_card, ai_layout = make_card("Local model", "Download the GGUF model and enable local features.")
        self.model_path_input = QLineEdit()
        self.download_url_input = QLineEdit()
        self.download_url_input.setText(DEFAULT_GGUF_URL)

        self.max_tokens = StepperSpinBox()
        self.max_tokens.setMinimum(128)
        self.max_tokens.setMaximum(2048)
        self.max_tokens.setValue(512)

        self.coach_toggle = ToggleSwitch()
        self.decompose_toggle = ToggleSwitch()
        self.binary_status = make_label("", "muted", word_wrap=True)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)

        ai_layout.addWidget(make_label("Model file path"))
        ai_layout.addWidget(self.model_path_input)
        ai_layout.addWidget(make_label("Download URL"))
        ai_layout.addWidget(self.download_url_input)
        ai_layout.addWidget(make_label("Max tokens"))
        ai_layout.addWidget(self.max_tokens)

        coach_row = QHBoxLayout()
        coach_row.addWidget(make_label("Enable Focus Coach weekly reviews"))
        coach_row.addStretch(1)
        coach_row.addWidget(self.coach_toggle)
        ai_layout.addLayout(coach_row)

        decompose_row = QHBoxLayout()
        decompose_row.addWidget(make_label("Enable task decomposition"))
        decompose_row.addStretch(1)
        decompose_row.addWidget(self.decompose_toggle)
        ai_layout.addLayout(decompose_row)

        ai_layout.addWidget(self.progress)
        ai_layout.addWidget(self.binary_status)

        tool_row = QHBoxLayout()
        self.default_path_btn = make_button("Use default path", "ghost")
        self.default_path_btn.clicked.connect(self._fill_default_model_path)

        self.detect_runtime_btn = make_button("Detect llama.cpp", "ghost")
        self.detect_runtime_btn.clicked.connect(self._detect_runtime)

        tool_row.addWidget(self.default_path_btn)
        tool_row.addWidget(self.detect_runtime_btn)
        tool_row.addStretch(1)
        ai_layout.addLayout(tool_row)

        self.install_help_title = make_label("Install help", "cardTitle")
        self.install_help_summary = make_label(
            "OS-specific llama.cpp setup steps will appear here when runtime detection fails.",
            "muted",
            word_wrap=True,
        )
        self.install_help_body = QPlainTextEdit()
        self.install_help_body.setReadOnly(True)
        self.install_help_body.setObjectName("InstallHelpBody")
        self.install_help_body.setMinimumHeight(220)
        self.install_help_body.setPlainText("Press 'Detect llama.cpp' to inspect the runtime and show setup steps.")

        ai_layout.addWidget(self.install_help_title)
        ai_layout.addWidget(self.install_help_summary)
        ai_layout.addWidget(self.install_help_body)
        self.benchmark_summary_label = make_label(
            "Benchmark summary: no runs yet.",
            "muted",
            word_wrap=True,
        )
        self.planner_label = make_label(
            "Planner: not evaluated yet.",
            "muted",
            word_wrap=True,
        )

        benchmark_actions = QHBoxLayout()
        self.run_benchmark_btn = make_button("Run benchmark", "secondary")
        self.run_benchmark_btn.clicked.connect(self._run_benchmark)

        benchmark_actions.addWidget(self.run_benchmark_btn)
        benchmark_actions.addStretch(1)

        self.benchmark_table = QTableWidget(0, 4)
        self.benchmark_table.setHorizontalHeaderLabels(["Target", "Seconds", "Success", "Reason"])
        self.benchmark_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.benchmark_table.verticalHeader().setVisible(False)
        self.benchmark_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.benchmark_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.benchmark_table.setMinimumHeight(180)

        ai_layout.addWidget(self.benchmark_summary_label)
        ai_layout.addWidget(self.planner_label)
        ai_layout.addLayout(benchmark_actions)
        ai_layout.addWidget(self.benchmark_table)

        self.content_layout.addWidget(ai_card)

        gaze_card, gaze_layout = make_card(
            "Vision / Gaze",
            "Download the face-landmarker model and calibrate gaze tracking.",
        )
        self.gaze_enable_toggle = ToggleSwitch()
        self.vision_model_path_input = QLineEdit()
        self.vision_download_url_input = QLineEdit()
        self.vision_download_url_input.setText(DEFAULT_FACE_LANDMARKER_URL)
        self.vision_progress = QProgressBar()
        self.vision_progress.setRange(0, 100)
        self.vision_progress.setValue(0)
        self.gaze_status_label = make_label("Gaze model not checked yet.", "muted", word_wrap=True)

        gaze_toggle_row = QHBoxLayout()
        gaze_toggle_row.addWidget(make_label("Enable gaze tracking"))
        gaze_toggle_row.addStretch(1)
        gaze_toggle_row.addWidget(self.gaze_enable_toggle)
        gaze_layout.addLayout(gaze_toggle_row)

        gaze_layout.addWidget(make_label("Face landmarker model path"))
        gaze_layout.addWidget(self.vision_model_path_input)
        gaze_layout.addWidget(make_label("Download URL"))
        gaze_layout.addWidget(self.vision_download_url_input)
        gaze_layout.addWidget(self.vision_progress)
        gaze_layout.addWidget(self.gaze_status_label)

        gaze_actions = QHBoxLayout()
        self.download_face_model_btn = make_button("Download face model", "secondary")
        self.download_face_model_btn.clicked.connect(self._download_face_model)
        self.calibrate_gaze_btn = make_button("Calibrate gaze", "primary")
        self.calibrate_gaze_btn.clicked.connect(self._run_gaze_calibration)
        gaze_actions.addWidget(self.download_face_model_btn)
        gaze_actions.addWidget(self.calibrate_gaze_btn)
        gaze_actions.addStretch(1)
        gaze_layout.addLayout(gaze_actions)

        self.content_layout.addWidget(gaze_card)

        dev_card, dev_layout = make_card("Development", "Useful flags for onboarding and local testing.")
        self.fake_data_toggle = ToggleSwitch()
        self.force_wizard_toggle = ToggleSwitch()
        self.reset_arm_toggle = ToggleSwitch()

        for text, widget in [
            ("Seed fake demo data", self.fake_data_toggle),
            ("Force setup wizard next launch", self.force_wizard_toggle),
            ("Arm database reset", self.reset_arm_toggle),
        ]:
            row = QHBoxLayout()
            row.addWidget(make_label(text))
            row.addStretch(1)
            row.addWidget(widget)
            dev_layout.addLayout(row)

        self.content_layout.addWidget(dev_card)
        self.content_layout.addStretch(1)

    def _default_model_path(self) -> Path:
        return self.settings.app_data_dir() / "models" / "phi3-mini-4k-instruct-q4.gguf"

    def _default_face_model_path(self) -> Path:
        return self.settings.app_data_dir() / "models" / "face_landmarker.task"

    def _fill_default_model_path(self) -> None:
        self.model_path_input.setText(str(self._default_model_path()))

    def _check_face_model_downloaded(self) -> None:
        target = Path(self.vision_model_path_input.text().strip() or self._default_face_model_path()).expanduser()
        if target.exists() and target.is_file() and target.stat().st_size > 0:
            self.vision_progress.setValue(100)
            self.gaze_status_label.setText(f"Face model ready at {target}.")
            self.download_face_model_btn.setEnabled(False)
            self.download_face_model_btn.setText("Face model downloaded")
        else:
            self.vision_progress.setValue(0)
            self.gaze_status_label.setText("Download the face landmarker model to enable calibration.")
            self.download_face_model_btn.setEnabled(True)
            self.download_face_model_btn.setText("Download face model")

    def _download_face_model(self) -> None:
        target = Path(self.vision_model_path_input.text().strip() or self._default_face_model_path()).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        url = self.vision_download_url_input.text().strip() or DEFAULT_FACE_LANDMARKER_URL
        self.vision_progress.setValue(3)

        def reporthook(block_num: int, block_size: int, total_size: int) -> None:
            if total_size <= 0:
                return
            pct = min(100, int((block_num * block_size * 100) / total_size))
            self.vision_progress.setValue(pct)

        try:
            urllib.request.urlretrieve(url, str(target), reporthook=reporthook)
            self.vision_progress.setValue(100)
            self.vision_model_path_input.setText(str(target))
            self.gaze_status_label.setText("Face landmarker model download completed.")
            self.download_face_model_btn.setEnabled(False)
            self.download_face_model_btn.setText("Face model downloaded")
        except Exception as exc:
            self.vision_progress.setValue(0)
            self.gaze_status_label.setText(f"Face model download failed: {exc}")
            self.download_face_model_btn.setEnabled(True)
            self.download_face_model_btn.setText("Download face model")

    def _run_gaze_calibration(self) -> None:
        if self.gaze_service is None:
            QMessageBox.warning(self, "Gaze calibration", "Gaze service is unavailable in this build.")
            return

        model_path = Path(self.vision_model_path_input.text().strip() or self._default_face_model_path()).expanduser()
        if not model_path.exists():
            QMessageBox.warning(self, "Gaze calibration", "Face landmarker model not found. Download it first.")
            return

        screens = list(QApplication.screens())
        if not screens:
            QMessageBox.warning(self, "Gaze calibration", "No active screens detected.")
            return

        self.calibrate_gaze_btn.setEnabled(False)
        self.gaze_status_label.setText("Starting gaze calibration...")
        self._calibration_screens = screens
        self._calibration_index = 0
        self._resume_gaze_after_calibration = self.gaze_service.is_running()
        if self._resume_gaze_after_calibration:
            self.gaze_service.stop()
        self._start_next_gaze_screen(model_path)

    def _start_next_gaze_screen(self, model_path: Path) -> None:
        if self._calibration_index >= len(self._calibration_screens):
            self._finish_gaze_calibration()
            return

        screen = self._calibration_screens[self._calibration_index]
        self._gaze_wizard = GazeCalibrationWizard(
            screen=screen,
            model_path=str(model_path),
            camera_index=int(self.settings.get("Vision/camera_index", 0) or 0),
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

        self._calibration_index += 1
        model_path = Path(self.vision_model_path_input.text().strip() or self._default_face_model_path()).expanduser()
        self._start_next_gaze_screen(model_path)

    def _on_gaze_calibration_cancelled(self) -> None:
        self.gaze_status_label.setText("Calibration cancelled.")
        self._finish_gaze_calibration()

    def _finish_gaze_calibration(self) -> None:
        self.calibrate_gaze_btn.setEnabled(True)
        if self._resume_gaze_after_calibration and self.gaze_service is not None:
            self.gaze_service.start()
        self.settings.set("Vision/last_calibrated_at", str(datetime.utcnow().isoformat()))
        self.settings.sync()

    def _render_install_guide(self, guide: InstallGuide) -> None:
        self.install_help_title.setText(guide.title)
        self.install_help_summary.setText(guide.summary)

        command_blocks = "\n\n".join(guide.commands)
        notes_block = "\n".join(f"• {note}" for note in guide.notes)

        self.install_help_body.setPlainText(
            f"Commands:\n{command_blocks}\n\nNotes:\n{notes_block}"
        )

    def _detect_runtime(self) -> None:
        status = LlamaRuntimeDetector.detect()

        if status.found and status.executable:
            self.binary_status.setText(
                f"Detected {status.executable_name} at {status.executable} "
                f"on {status.pretty_product_name} ({status.cpu_arch})."
            )
            self.install_help_title.setText("llama.cpp is ready")
            self.install_help_summary.setText("Runtime found. No installation steps needed.")
            self.install_help_body.setPlainText("")
            return

        guide = LlamaInstallGuideFactory.build()
        self.binary_status.setText(
            f"llama.cpp runtime not found on {status.pretty_product_name} "
            f"({status.cpu_arch}). Showing install steps for this system."
        )
        self._render_install_guide(guide)

    def _load_from_settings(self) -> None:
        self.name_input.setText(str(self.settings.get("Profile/display_name", "") or ""))
        self.goals_input.setText(str(self.settings.get("Profile/current_goals", "") or ""))
        self.model_path_input.setText(
            str(self.settings.get("SLM/model_path", str(self._default_model_path())) or "")
        )
        self.max_tokens.setValue(int(self.settings.get("SLM/max_tokens", 512) or 512))
        self.coach_toggle.setChecked(bool(self.settings.get("SLM/coach_enabled", False)))
        self.decompose_toggle.setChecked(bool(self.settings.get("SLM/decomposition_enabled", False)))
        self.fake_data_toggle.setChecked(bool(self.settings.get("Development/dev_fake_data", False)))
        self.force_wizard_toggle.setChecked(bool(self.settings.get("Development/dev_show_startup_wizard", False)))
        self.reset_arm_toggle.setChecked(bool(self.settings.get("Development/dev_reset_database", False)))
        self.gaze_enable_toggle.setChecked(bool(self.settings.get("Vision/enable_gaze", False)))
        self.vision_model_path_input.setText(
            str(self.settings.get("Vision/face_landmarker_model_path", str(self._default_face_model_path())) or "")
        )
        self._check_model_downloaded()
        self._check_face_model_downloaded()
        self._detect_runtime()
        self._refresh_benchmark_ui()

    def _check_model_downloaded(self) -> None:
        target = Path(self.model_path_input.text().strip() or self._default_model_path()).expanduser()
        if target.exists() and target.is_file() and target.stat().st_size > 0:
            self.progress.setValue(100)
            self.status_label.setText(f"Model already downloaded at {target}.")
            self.download_btn.setEnabled(False)
            self.download_btn.setText("Model downloaded")
        else:
            self.progress.setValue(0)
            self.status_label.setText("Click 'Download model' to download the GGUF model.")
            self.download_btn.setEnabled(True)
            self.download_btn.setText("Download model")

    def _download_model(self) -> None:
        target = Path(self.model_path_input.text().strip() or self._default_model_path()).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        url = self.download_url_input.text().strip() or DEFAULT_GGUF_URL
        self.progress.setValue(3)

        def reporthook(block_num: int, block_size: int, total_size: int) -> None:
            if total_size <= 0:
                return
            pct = min(100, int((block_num * block_size * 100) / total_size))
            self.progress.setValue(pct)

        try:
            urllib.request.urlretrieve(url, str(target), reporthook=reporthook)
            self.progress.setValue(100)
            self.model_path_input.setText(str(target))
            self.status_label.setText("Model download completed.")
            self.download_btn.setEnabled(False)
            self.download_btn.setText("Model downloaded")
        except Exception as exc:
            self.progress.setValue(0)
            self.status_label.setText(f"Model download failed: {exc}")
            self.download_btn.setEnabled(True)
            self.download_btn.setText("Download model")

    def _complete_setup(self) -> None:
        payload = {
            "Profile/display_name": self.name_input.text().strip(),
            "Profile/current_goals": self.goals_input.text().strip(),
            "SLM/model_path": str(Path(self.model_path_input.text().strip() or self._default_model_path()).expanduser()),
            "SLM/backend": "llama_cpp",
            "SLM/max_tokens": self.max_tokens.value(),
            "SLM/coach_enabled": self.coach_toggle.isChecked(),
            "SLM/decomposition_enabled": self.decompose_toggle.isChecked(),
            "Startup/first_run_completed": True,
            "Development/dev_fake_data": self.fake_data_toggle.isChecked(),
            "Development/dev_show_startup_wizard": self.force_wizard_toggle.isChecked(),
            "Development/dev_reset_database": self.reset_arm_toggle.isChecked(),
            "SLM/prefer_gpu": True,
            "SLM/benchmark_summary": self.slm_service.describe_benchmark_summary(),
            "Vision/enable_gaze": self.gaze_enable_toggle.isChecked(),
            "Vision/face_landmarker_model_path": str(
                Path(self.vision_model_path_input.text().strip() or self._default_face_model_path()).expanduser()
            ),
            "Vision/camera_index": int(self.settings.get("Vision/camera_index", 0) or 0),
        }

        for key, value in payload.items():
            self.settings.set(key, value)
        self.settings.sync()

        self.setup_completed.emit(payload)
        self.accept()

    def open_over_parent(self) -> None:
        parent = self.parentWidget()
        if parent is not None:
            self.setGeometry(parent.rect())
            self.move(parent.mapToGlobal(parent.rect().topLeft()))
        else:
            self.showFullScreen()

    def _refresh_benchmark_ui(self) -> None:
        info = self.slm_service.planner_explain()
        if not info.get("ready"):
            self.planner_label.setText(str(info.get("reason", "Planner unavailable.")))
            self.benchmark_summary_label.setText(self.slm_service.describe_benchmark_summary())
            self.benchmark_table.setRowCount(0)
            return

        self.planner_label.setText(
            f"Planner target: {info['target']} | "
            f"Estimated latency: {info['estimated_latency_seconds']}s | "
            f"Benchmark weight: {info['benchmark_weight']}"
        )
        self.benchmark_summary_label.setText(self.slm_service.describe_benchmark_summary())

    def _run_benchmark(self) -> None:
        self.run_benchmark_btn.setEnabled(False)
        self.status_label.setText("Running local SLM benchmark. This may take a while.")
        self.progress.setRange(0, 0)

        try:
            results = self.slm_service.benchmark_runtime()
            self.benchmark_table.setRowCount(0)
            for row_idx, item in enumerate(results):
                self.benchmark_table.insertRow(row_idx)
                self.benchmark_table.setItem(row_idx, 0, QTableWidgetItem(str(item.get("target", ""))))
                self.benchmark_table.setItem(row_idx, 1, QTableWidgetItem(str(item.get("duration_seconds", ""))))
                self.benchmark_table.setItem(row_idx, 2, QTableWidgetItem("Yes" if item.get("success") else "No"))
                self.benchmark_table.setItem(row_idx, 3, QTableWidgetItem(str(item.get("reason", ""))))
            self.status_label.setText("Benchmark completed.")
            self._refresh_benchmark_ui()
        except Exception as exc:
            self.status_label.setText(f"Benchmark failed: {exc}")
        finally:
            self.progress.setRange(0, 100)
            if self.progress.value() == 0:
                self.progress.setValue(100)
            self.run_benchmark_btn.setEnabled(True)