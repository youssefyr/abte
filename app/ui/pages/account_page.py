# app/ui/pages/account_page.py
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Iterable

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.ui.metrics import UiMetrics
from app.ui.ui_helpers import make_button, make_card, make_label, make_section_header, build_initials_avatar, load_avatar_pixmap, FadeScaleMixin

if TYPE_CHECKING:
    from app.services.handle_tasks import TaskService
    from app.services.focus_session_service import FocusSessionService

logger = logging.getLogger(__name__)


def _format_duration(minutes: float) -> str:
    if minutes < 1:
        return "0m"
    total = int(minutes)
    hours = total // 60
    mins = total % 60
    if hours == 0:
        return f"{mins}m"
    if mins == 0:
        return f"{hours}h"
    return f"{hours}h {mins}m"


class ProfileEditDialog(QDialog, FadeScaleMixin):
    def __init__(self, current_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit Profile")
        self.setModal(True)
        self.setObjectName("ElevatedDialog")
        self.setFixedSize(360, 180)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        
        header = make_label("Edit profile name", "dialogTitle")
        
        self.name_input = QLineEdit(current_name)
        self.name_input.setPlaceholderText("Enter your name")
        
        cancel_btn = make_button("Cancel", "ghost")
        cancel_btn.clicked.connect(self.reject)
        
        save_btn = make_button("Save", "primary")
        save_btn.clicked.connect(self.accept)
        
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_layout.addWidget(cancel_btn)
        btn_layout.addWidget(save_btn)

        layout.addWidget(header)
        layout.addWidget(self.name_input)
        layout.addStretch()
        layout.addLayout(btn_layout)
        
        self.animate_dialog_in(self)

    def get_name(self) -> str:
        return self.name_input.text().strip()


class InteractiveKpiTile(QFrame):
    def __init__(self, label: str, value: str, tooltip: str = "", delta: str = "", delta_positive: bool = True, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("CardElevated")
        self.setToolTip(tooltip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(4)

        self.val_label = QLabel(value)
        self.val_label.setObjectName("KpiValue")
        self.val_label.setAlignment(Qt.AlignmentFlag.AlignLeft)

        self.lbl_label = QLabel(label)
        self.lbl_label.setObjectName("KpiLabel")
        self.lbl_label.setAlignment(Qt.AlignmentFlag.AlignLeft)

        layout.addWidget(self.val_label)
        layout.addWidget(self.lbl_label)

        if delta:
            delta_label = QLabel(delta)
            delta_label.setObjectName("KpiDeltaPositive" if delta_positive else "KpiDeltaNegative")
            layout.addWidget(delta_label)


class InteractiveActivityRow(QFrame):
    def __init__(self, text: str, meta: str, dot_color: str = "#3ECF8E", parent=None):
        super().__init__(parent)
        self.setObjectName("InteractiveActivityRow")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("""
            #InteractiveActivityRow {
                background-color: transparent;
                border-radius: 6px;
            }
            #InteractiveActivityRow:hover {
                background-color: rgba(255, 255, 255, 0.05);
            }
        """)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(10)

        dot = QLabel("●")
        dot.setFixedWidth(14)
        dot.setStyleSheet(f"color: {dot_color}; font-size: 10px;")

        text_label = QLabel(text)
        text_label.setObjectName("ActivityText")
        text_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        meta_label = QLabel(meta)
        meta_label.setObjectName("ActivityMeta")
        meta_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        layout.addWidget(dot)
        layout.addWidget(text_label, 1)
        layout.addWidget(meta_label)

def _make_activity_row_wrapper(text: str, meta: str, dot_color: str = "#3ECF8E") -> QFrame:
    row = InteractiveActivityRow(text, meta, dot_color)
    
    divider = QFrame()
    divider.setObjectName("HairlineDivider")
    divider.setFrameShape(QFrame.Shape.HLine)
    divider.setFixedHeight(1)

    wrapper = QFrame()
    wrapper.setObjectName("ActivityRowWrapper")
    vl = QVBoxLayout(wrapper)
    vl.setContentsMargins(0, 0, 0, 0)
    vl.setSpacing(0)
    vl.addWidget(row)
    vl.addWidget(divider)
    return wrapper


class AccountPage(QWidget):
    avatarEditRequested = Signal()
    """
    User account & stats page.
    Shows productivity stats, focus history, streaks, and app metadata.
    Not a diagnostic or clinical tool — numbers reflect usage patterns only.
    """

    def __init__(
        self,
        metrics: UiMetrics,
        task_service: "TaskService",
        focus_session_service: "FocusSessionService",
        repository=None,
        settings=None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._metrics = metrics
        self._task_service = task_service
        self._focus_session_service = focus_session_service
        self._repository = repository
        self._settings = settings

        self._build_ui()
        self.refresh_data()

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(60_000)
        self._refresh_timer.timeout.connect(self.refresh_data)
        self._refresh_timer.start()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(
            self._metrics.card_padding,
            self._metrics.card_padding,
            self._metrics.card_padding,
            self._metrics.card_padding,
        )
        root.setSpacing(self._metrics.card_gap)

        # ── Profile hero ──────────────────────────────────────────────────
        profile_header = QWidget()
        profile_header.setObjectName("ProfileHeader")
        profile_layout = QHBoxLayout(profile_header)
        profile_layout.setContentsMargins(0, 0, 0, 16)
        profile_layout.setSpacing(24)

        self._avatar_label = QLabel("")
        self._avatar_label.setObjectName("ProfileAvatar")
        self._avatar_label.setFixedSize(80, 80)
        self._avatar_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        name_col = QVBoxLayout()
        name_col.setContentsMargins(0, 0, 0, 0)
        name_col.setSpacing(4)
        
        name_row = QHBoxLayout()
        name_row.setContentsMargins(0, 0, 0, 0)
        name_row.setSpacing(8)
        self._name_label = make_label("abte user", "sectionTitle")
        self._name_label.setStyleSheet("font-size: 24px;")
        
        self._edit_name_btn = QPushButton("✎")
        self._edit_name_btn.setObjectName("GhostButton")
        self._edit_name_btn.setFixedSize(32, 32)
        self._edit_name_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._edit_name_btn.clicked.connect(self._on_edit_name_clicked)
        self._edit_name_btn.setToolTip("Edit profile name")
        
        name_row.addWidget(self._name_label)
        name_row.addWidget(self._edit_name_btn)
        name_row.addStretch()
        
        self._joined_label = make_label("Tracking since —", "muted")
        
        name_col.addLayout(name_row)
        name_col.addWidget(self._joined_label)
        name_col.addStretch()

        profile_layout.addWidget(self._avatar_label)
        profile_layout.addLayout(name_col, 1)

        action_col = QVBoxLayout()
        action_col.setContentsMargins(0, 0, 0, 0)
        self._avatar_edit_btn = make_button("Change photo", "ghost")
        self._avatar_edit_btn.clicked.connect(self.avatarEditRequested.emit)
        action_col.addWidget(self._avatar_edit_btn)
        action_col.addStretch()

        profile_layout.addLayout(action_col, 0)
        root.addWidget(profile_header)

        # ── KPI grid ──────────────────────────────────────────────────────
        kpi_header, _ = make_section_header("Overview", "Your productivity at a glance.")
        root.addWidget(kpi_header)

        self._kpi_grid = QGridLayout()
        self._kpi_grid.setSpacing(self._metrics.card_gap)
        self._kpi_tiles: list[QFrame] = []

        kpi_names = [
            ("Tasks completed", "0", "Total tasks marked as done", "", True),
            ("Focus time", "0h", "Total minutes spent in focus sessions", "", True),
            ("Active streak", "0 days", "Consecutive days with activity", "", True),
            ("Tasks created", "0", "Total number of tasks created", "", True),
            ("Avg session", "0m", "Average duration of your focus sessions", "", True),
            ("Productivity score", "—", "Score based on task completion and focus time", "", True),
        ]
        cols = 3
        for i, (lbl, val, tooltip, delta, pos) in enumerate(kpi_names):
            tile = InteractiveKpiTile(lbl, val, tooltip, delta, pos)
            self._kpi_tiles.append(tile)
            self._kpi_grid.addWidget(tile, i // cols, i % cols)

        kpi_wrapper = QWidget()
        kpi_wrapper.setLayout(self._kpi_grid)
        root.addWidget(kpi_wrapper)

        # ── Weekly progress bar ───────────────────────────────────────────
        week_header, week_layout = make_section_header(
            "This week", "Focus minutes per day, target 120 min/day."
        )
        root.addWidget(week_header)

        week_card, week_body = make_card("", "", elevated=False)
        self._week_bars: list[tuple[QLabel, QProgressBar, QLabel]] = []
        day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

        week_grid = QGridLayout()
        week_grid.setSpacing(6)
        for col, day in enumerate(day_names):
            day_lbl = QLabel(day)
            day_lbl.setObjectName("WeekDayLabel")
            day_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

            bar = QProgressBar()
            bar.setObjectName("WeekProgressBar")
            bar.setOrientation(Qt.Orientation.Vertical)
            bar.setRange(0, 120)
            bar.setValue(0)
            bar.setTextVisible(False)
            bar.setFixedWidth(28)
            bar.setMinimumHeight(72)

            val_lbl = QLabel("0")
            val_lbl.setObjectName("WeekMinLabel")
            val_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

            week_grid.addWidget(day_lbl, 0, col, Qt.AlignmentFlag.AlignHCenter)
            week_grid.addWidget(bar, 1, col, Qt.AlignmentFlag.AlignHCenter)
            week_grid.addWidget(val_lbl, 2, col, Qt.AlignmentFlag.AlignHCenter)
            self._week_bars.append((day_lbl, bar, val_lbl))

        week_body.addLayout(week_grid)
        root.addWidget(week_card)

        # ── Recent activity ───────────────────────────────────────────────
        activity_header, _ = make_section_header(
            "Recent activity", "Last 10 completed tasks and sessions."
        )
        root.addWidget(activity_header)

        activity_card, activity_body = make_card("", "", elevated=False)
        self._activity_body = activity_body
        self._activity_card = activity_card
        root.addWidget(activity_card)

        # ── App info + data section ───────────────────────────────────────
        info_header, _ = make_section_header("App", "Data and application information.")
        root.addWidget(info_header)

        info_card, info_body = make_card("", "", elevated=False)
        info_body.setSpacing(10)

        self._data_dir_label = make_label("Data directory: —", "muted", word_wrap=True)
        self._app_version_label = make_label("Version: abte 1.0", "muted")
        export_btn = make_button("Export all data (JSON)", "secondary")
        export_btn.setMaximumWidth(240)
        export_btn.clicked.connect(self._on_export_data)

        clear_btn = make_button("Clear focus session history", "ghost")
        clear_btn.setMaximumWidth(240)
        clear_btn.clicked.connect(self._on_clear_sessions)

        info_body.addWidget(self._app_version_label)
        info_body.addWidget(self._data_dir_label)
        info_body.addWidget(export_btn)
        info_body.addWidget(clear_btn)

        root.addWidget(info_card)
        root.addStretch(1)

    def _on_edit_name_clicked(self) -> None:
        if not self._repository:
            return
        profile = self._repository.get_profile()
        current_name = profile.display_name

        dialog = ProfileEditDialog(current_name, self)
        if dialog.exec() == int(QDialog.DialogCode.Accepted):
            new_name = dialog.get_name()
            if new_name and new_name != current_name:
                profile.display_name = new_name
                self._repository.update_profile(profile)
                if self._settings:
                    self._settings.set("Profile/display_name", new_name)
                    self._settings.sync()
                self.refresh_data()

    def apply_metrics(self, metrics: UiMetrics) -> None:
        self._metrics = metrics

    def refresh_data(self) -> None:
        try:
            self._refresh_avatar()
            self._refresh_stats()
            self._refresh_activity()
            self._refresh_app_info()
        except Exception as exc:
            logger.warning(f"AccountPage.refresh_data failed: {exc}")

    def _refresh_avatar(self) -> None:
        name = "abte user"
        avatar_path = ""
        if self._repository:
            profile = self._repository.get_profile()
            name = profile.display_name
            avatar_path = profile.avatar_path
            self._name_label.setText(name)
        elif self._settings is not None:
            name = str(self._settings.get("Profile/display_name", name) or name)
            avatar_path = str(self._settings.get("Profile/avatar_path", "") or "")
            self._name_label.setText(name)
            
        size = self._avatar_label.width() or 80
        pixmap = load_avatar_pixmap(avatar_path, size, shape="circle")
        if pixmap is None:
            pixmap = build_initials_avatar(name, size, shape="circle")
        self._avatar_label.setPixmap(pixmap)
    
    def _get_sessions(self) -> list[Any]:
        """
        Probe FocusSessionService for a sessions accessor.
        Only convert to list after proving the result is iterable.
        """
        svc = self._focus_session_service
        for name in ("list_sessions", "get_sessions", "all_sessions", "sessions"):
            attr = getattr(svc, name, None)
            if attr is None:
                continue
            try:
                result: Any = attr() if callable(attr) else attr
                if result is None:
                    return []
                if isinstance(result, list):
                    return result
                if isinstance(result, Iterable) and not isinstance(result, (str, bytes, dict)):
                    return list(result)
                logger.debug(
                    "AccountPage._get_sessions: accessor '%s' returned non-iterable %s",
                    name,
                    type(result).__name__,
                )
                return []
            except Exception as exc:
                logger.debug(f"AccountPage._get_sessions via '{name}' failed: {exc}")
                return []
        logger.debug("AccountPage: FocusSessionService has no known sessions accessor.")
        return []

    def _refresh_stats(self) -> None:
        # ── Task counts ───────────────────────────────────────────────────
        try:
            all_tasks = self._task_service.list_tasks()
        except Exception:
            all_tasks = []

        completed = [t for t in all_tasks if getattr(t, "status", "") == "done"]
        created = len(all_tasks)
        done_count = len(completed)

        # ── Focus sessions ────────────────────────────────────────────────
        total_focus_minutes = 0.0
        session_durations: list[float] = []
        streak_days = 0
        day_minutes: dict[int, float] = {i: 0.0 for i in range(7)}

        try:
            sessions = self._get_sessions()         
            now = datetime.now()
            week_start = (now - timedelta(days=now.weekday())).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            active_days: set[str] = set()

            for s in sessions:
                start_dt = getattr(s, "started_at", None)
                dur = float(getattr(s, "duration_minutes", 0) or 0)
                total_focus_minutes += dur
                session_durations.append(dur)

                if start_dt:
                    if isinstance(start_dt, (int, float)):
                        start_dt = datetime.fromtimestamp(start_dt)
                    if start_dt >= week_start:
                        weekday = start_dt.weekday()
                        day_minutes[weekday] = day_minutes.get(weekday, 0.0) + dur
                    active_days.add(start_dt.strftime("%Y-%m-%d"))

            check = now.date()
            while check.strftime("%Y-%m-%d") in active_days:
                streak_days += 1
                check -= timedelta(days=1)

            for weekday, (_, bar, val_lbl) in enumerate(self._week_bars):
                mins = int(day_minutes.get(weekday, 0))
                bar.setValue(min(mins, 120))
                val_lbl.setText(f"{mins}m" if mins < 120 else "2h+")
                
                tooltip = f"{mins} minutes focused"
                bar.setToolTip(tooltip)
                val_lbl.setToolTip(tooltip)
                bar.setCursor(Qt.CursorShape.PointingHandCursor)

        except Exception as exc:
            logger.debug(f"AccountPage: session stats failed: {exc}")

        avg_session = (
            (total_focus_minutes / len(session_durations)) if session_durations else 0.0
        )

        raw_score: float = 0.0
        if created > 0:
            completion_ratio = done_count / created
            streak_bonus = min(streak_days * 3, 30)
            focus_bonus = min(total_focus_minutes / 10.0, 40)
            raw_score = min(100.0, completion_ratio * 30 + streak_bonus + focus_bonus)

        score_text = f"{int(raw_score)}/100" if created > 0 else "—"

        kpi_data = [
            str(done_count),
            _format_duration(total_focus_minutes),
            f"{streak_days} day{'s' if streak_days != 1 else ''}",
            str(created),
            _format_duration(avg_session),
            score_text,
        ]
        for tile, value in zip(self._kpi_tiles, kpi_data):
            val_lbl = tile.findChild(QLabel, "KpiValue")
            if val_lbl is not None:
                val_lbl.setText(value)

        try:
            dates = []
            for t in all_tasks:
                created_at = getattr(t, "created_at", None)
                if created_at:
                    if isinstance(created_at, (int, float)):
                        created_at = datetime.fromtimestamp(created_at)
                    dates.append(created_at)
            if dates:
                earliest = min(dates)
                self._joined_label.setText(f"Tracking since {earliest.strftime('%b %d, %Y')}")
        except Exception:
            pass

    def _refresh_activity(self) -> None:
        # Clear previous rows
        while self._activity_body.count():
            item = self._activity_body.takeAt(0)
            if item is None:
                continue
            widget = item.widget()          
            if widget is not None:
                widget.deleteLater()

        try:
            all_tasks = self._task_service.list_tasks()
            completed = sorted(
                [t for t in all_tasks if getattr(t, "status", "") == "done"],
                key=lambda t: getattr(t, "updated_at", 0) or getattr(t, "created_at", 0),
                reverse=True,
            )[:10]

            if not completed:
                empty = make_label("No completed tasks yet. Finish a task to see it here.", "muted", word_wrap=True)
                self._activity_body.addWidget(empty)
                return

            for task in completed:
                title = getattr(task, "title", "Untitled task")
                updated = getattr(task, "updated_at", None) or getattr(task, "created_at", None)
                if updated:
                    if isinstance(updated, (int, float)):
                        updated = datetime.fromtimestamp(updated)
                    meta = updated.strftime("%b %d, %H:%M")
                else:
                    meta = "—"
                row = _make_activity_row_wrapper(title, meta, dot_color="#3ECF8E")
                self._activity_body.addWidget(row)

        except Exception as exc:
            logger.debug(f"AccountPage: activity refresh failed: {exc}")
            err = make_label("Could not load activity.", "muted")
            self._activity_body.addWidget(err)

    def _refresh_app_info(self) -> None:
        if self._settings:
            try:
                data_dir = str(self._settings.app_data_dir())
                self._data_dir_label.setText(f"Data directory: {data_dir}")
            except Exception:
                pass

    def _on_export_data(self) -> None:
        import json
        import os
        from pathlib import Path

        from PySide6.QtWidgets import QFileDialog, QMessageBox

        default_path = str(Path.home() / "abte_export.json")
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export data",
            default_path,
            "JSON files (*.json)",
        )
        if not path:
            return

        try:
            all_tasks = self._task_service.list_tasks()

            def _serialize(obj):
                if isinstance(obj, datetime):
                    return obj.isoformat()
                if hasattr(obj, "__dict__"):
                    return {k: v for k, v in obj.__dict__.items() if not k.startswith("_")}
                return str(obj)

            payload = {
                "exported_at": datetime.now().isoformat(),
                "tasks": [_serialize(t) for t in all_tasks],
            }
            Path(path).write_text(json.dumps(payload, indent=2, default=_serialize), encoding="utf-8")
            QMessageBox.information(self, "Export complete", f"Data exported to:\n{path}")
        except Exception as exc:
            QMessageBox.warning(self, "Export failed", str(exc))

    def _on_clear_sessions(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        reply = QMessageBox.question(
            self,
            "Clear session history",
            "This removes all recorded focus sessions. Task data is not affected.\n\nContinue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            
            clear_fn = getattr(self._focus_session_service, "clear_all_sessions", None)
            if clear_fn is not None and callable(clear_fn):
                clear_fn()
            else:
                logger.info(
                    "AccountPage: FocusSessionService.clear_all_sessions not implemented — skipped."
                )
            self.refresh_data()
        except Exception as exc:
            logger.warning(f"clear_sessions failed: {exc}")