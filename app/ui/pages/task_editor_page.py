from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from PySide6.QtCore import Qt, QDateTime, QDate, QTime, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDateTimeEdit,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QSpinBox,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QLineEdit,
    QScrollArea,
    QFrame,
)

from app.data.entities import TaskItem
from app.ui.pages.base_page import BasePage
from app.services.slm import SlmService
from app.ui.ui_helpers import make_button, make_card, make_label, make_pill, make_section_header


class TaskEditorPage(BasePage):
    def __init__(
        self,
        metrics,
        task_service: Any,
        notification_service: Any,
        slm_service: SlmService | None = None,
        parent=None,
    ):
        super().__init__(metrics, parent)
        self.task_service = task_service
        self.notification_service = notification_service
        self._slm_service = slm_service
        self._selected_task_id: str | None = None
        self._task_ids: list[str] = []
        self.labels_to_search: list[QLabel] = []
        self._filter_status = "all"
        self._status_group = QButtonGroup(self)
        self._status_group.setExclusive(True)
        self._status_buttons: dict[str, QToolButton] = {}
        self._filter_buttons: dict[str, QToolButton] = {}
        self._autosave_dirty = False
        self._decompose_thread: QThread | None = None
        self._spinner_timer = QTimer(self)
        self._spinner_frames = ["|", "/", "-", "\\"]
        self._spinner_index = 0

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.main_layout.addWidget(splitter, 1)

        list_host = QWidget()
        list_host.setObjectName("Card")
        list_layout = QVBoxLayout(list_host)
        list_layout.setContentsMargins(self.metrics.card_padding, self.metrics.card_padding, self.metrics.card_padding, self.metrics.card_padding)
        list_layout.setSpacing(12)

        header_host, _ = make_section_header("Tasks", "Create, edit, schedule, and clean up task state.")
        list_layout.addWidget(header_host)

        stats_row = QHBoxLayout()
        stats_row.setContentsMargins(0, 0, 0, 0)
        stats_row.setSpacing(8)
        self.total_pill = make_pill("0 total", "default")
        self.active_pill = make_pill("0 active", "default")
        self.done_pill = make_pill("0 done", "default")
        stats_row.addWidget(self.total_pill)
        stats_row.addWidget(self.active_pill)
        stats_row.addWidget(self.done_pill)
        stats_row.addStretch()
        list_layout.addLayout(stats_row)

        filter_row = QHBoxLayout()
        filter_row.setContentsMargins(0, 0, 0, 0)
        filter_row.setSpacing(6)
        for key, label in (
            ("all", "All"),
            ("active", "Active"),
            ("unscheduled", "Unscheduled"),
            ("blocked", "Blocked"),
            ("done", "Done"),
        ):
            chip = QToolButton()
            chip.setObjectName("FilterChip")
            chip.setCheckable(True)
            chip.setText(label)
            chip.clicked.connect(lambda checked=False, k=key: self._set_filter(k))
            self._filter_buttons[key] = chip
            filter_row.addWidget(chip)
        filter_row.addStretch(1)
        list_layout.addLayout(filter_row)

        self.search_input = QLineEdit()
        self.search_input.setObjectName("TaskSearchInput")
        self.search_input.setPlaceholderText("Filter tasks by title, description, or tags")
        list_layout.addWidget(self.search_input)

        self.task_list = QListWidget()
        self.task_list.setObjectName("TaskEditorList")
        list_layout.addWidget(self.task_list, 1)

        editor_host = QWidget()
        editor_host.setObjectName("Card")
        editor_layout = QVBoxLayout(editor_host)
        editor_layout.setContentsMargins(self.metrics.card_padding, self.metrics.card_padding, self.metrics.card_padding, self.metrics.card_padding)
        editor_layout.setSpacing(12)

        editor_header, _ = make_section_header("Task editor", "Structured editing for one task at a time.")
        editor_layout.addWidget(editor_header)

        autosave_row = QHBoxLayout()
        autosave_row.setContentsMargins(0, 0, 0, 0)
        autosave_row.setSpacing(8)
        self.autosave_pill = make_pill("Not saved", "default")
        self.model_status_pill = make_pill("Model: unknown", "default")
        self.model_spinner = make_label("", "muted")
        self.model_spinner.setFixedWidth(16)
        self.decompose_btn = make_button("Decompose task", "secondary")
        autosave_row.addWidget(self.autosave_pill)
        autosave_row.addWidget(self.model_status_pill)
        autosave_row.addWidget(self.model_spinner)
        autosave_row.addStretch(1)
        autosave_row.addWidget(self.decompose_btn)
        editor_layout.addLayout(autosave_row)

        self.title_edit = QLineEdit()
        self.description_edit = QPlainTextEdit()
        self.description_edit.setMinimumHeight(120)

        self.status_combo = QComboBox()
        self.status_combo.addItems(["todo", "in_progress", "done", "blocked", "missed", "cancelled"])

        status_row = QHBoxLayout()
        status_row.setContentsMargins(0, 0, 0, 0)
        status_row.setSpacing(6)
        for key, label in (
            ("todo", "Todo"),
            ("in_progress", "Doing"),
            ("blocked", "Blocked"),
            ("done", "Done"),
        ):
            button = QToolButton()
            button.setObjectName("SegmentButton")
            button.setCheckable(True)
            button.setText(label)
            button.clicked.connect(lambda checked=False, k=key: self._set_status(k))
            self._status_group.addButton(button)
            self._status_buttons[key] = button
            status_row.addWidget(button)
        status_row.addStretch(1)

        self.priority_spin = QSpinBox()
        self.priority_spin.setRange(1, 5)
        self.priority_spin.setValue(3)

        self.estimate_spin = QSpinBox()
        self.estimate_spin.setRange(5, 600)
        self.estimate_spin.setSingleStep(5)
        self.estimate_spin.setValue(30)

        self.energy_spin = QSpinBox()
        self.energy_spin.setRange(1, 5)
        self.energy_spin.setValue(3)

        self.tags_edit = QLineEdit()
        self.tags_edit.setPlaceholderText("comma, separated, tags")

        self.due_edit = QDateTimeEdit()
        self.due_edit.setCalendarPopup(True)
        self.due_edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.due_edit.setSpecialValueText("No due date")
        self.due_edit.setDateTime(self._to_qdatetime(None))
        self.due_edit.setMinimumDateTime(self._to_qdatetime(None))

        self.scheduled_start_edit = QDateTimeEdit()
        self.scheduled_start_edit.setCalendarPopup(True)
        self.scheduled_start_edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.scheduled_start_edit.setSpecialValueText("Unscheduled")
        self.scheduled_start_edit.setDateTime(self._to_qdatetime(None))
        self.scheduled_start_edit.setMinimumDateTime(self._to_qdatetime(None))

        details_card, details_layout = make_card("Details", "Title, description, and status.", elevated=False)
        details_form = QFormLayout()
        details_form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        details_form.setFormAlignment(Qt.AlignmentFlag.AlignTop)
        details_form.setSpacing(14)
        details_form.setVerticalSpacing(30)
        details_form.addRow("Title", self.title_edit)
        details_form.addRow("Description", self.description_edit)
        details_form.addRow("Status", status_row)
        details_form.addRow("Status detail", self.status_combo)
        details_layout.addLayout(details_form)
        editor_layout.addWidget(details_card)

        schedule_card, schedule_layout = make_card("Schedule", "Due dates and planned blocks.", elevated=False)
        schedule_form = QFormLayout()
        schedule_form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        schedule_form.setFormAlignment(Qt.AlignmentFlag.AlignTop)
        schedule_form.setSpacing(14)
        schedule_form.setVerticalSpacing(14)
        schedule_form.addRow("Due at", self.due_edit)
        schedule_form.addRow("Scheduled start", self.scheduled_start_edit)
        schedule_layout.addLayout(schedule_form)
        editor_layout.addWidget(schedule_card)

        effort_card, effort_layout = make_card("Effort", "Priority, estimate, and energy.", elevated=False)
        effort_form = QFormLayout()
        effort_form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        effort_form.setFormAlignment(Qt.AlignmentFlag.AlignTop)
        effort_form.setSpacing(14)
        effort_form.setVerticalSpacing(14)
        effort_form.addRow("Priority", self.priority_spin)
        effort_form.addRow("Estimate (min)", self.estimate_spin)
        effort_form.addRow("Energy cost", self.energy_spin)
        effort_layout.addLayout(effort_form)
        editor_layout.addWidget(effort_card)

        tag_card, tag_layout = make_card("Tags", "Optional tags and labels.", elevated=False)
        tag_form = QFormLayout()
        tag_form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        tag_form.setFormAlignment(Qt.AlignmentFlag.AlignTop)
        tag_form.setSpacing(14)
        tag_form.setVerticalSpacing(14)
        tag_form.addRow("Tags", self.tags_edit)
        tag_layout.addLayout(tag_form)
        editor_layout.addWidget(tag_card)

        self.meta_label = make_label("No task selected.", "muted", True)
        editor_layout.addWidget(self.meta_label)
        self.labels_to_search.append(self.meta_label)

        action_row = QHBoxLayout()
        self.new_btn = QPushButton("New task")
        self.save_btn = QPushButton("Save changes")
        self.complete_btn = QPushButton("Mark done")
        self.delete_btn = QPushButton("Delete task")
        self.subtasks_list = QListWidget()
        self.decompose_btn.clicked.connect(self._decompose_current_task)
        action_row.addWidget(self.new_btn)
        action_row.addWidget(self.save_btn)
        action_row.addWidget(self.complete_btn)
        action_row.addWidget(self.delete_btn)
        action_row.addStretch()
        editor_layout.addLayout(action_row)

        subtasks_card, subtasks_layout = make_card("Subtasks", "Items generated from task decomposition.", elevated=False)
        subtasks_layout.addWidget(self.subtasks_list)
        editor_layout.addWidget(subtasks_card)

        self.status_label = make_label("", "muted", True)
        editor_layout.addWidget(self.status_label)
        editor_layout.addStretch(1)
        
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(editor_host)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setObjectName("EditorScrollArea")
        
        splitter.addWidget(list_host)
        splitter.addWidget(scroll)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)

        self.search_input.textChanged.connect(self.filter_content)
        self.task_list.currentRowChanged.connect(self._on_task_selected)
        self.new_btn.clicked.connect(self._new_task)
        self.save_btn.clicked.connect(self._save_task)
        self.complete_btn.clicked.connect(self._mark_done)
        self.delete_btn.clicked.connect(self._delete_task)
        self.title_edit.textChanged.connect(self._mark_dirty)
        self.description_edit.textChanged.connect(self._mark_dirty)
        self.status_combo.currentTextChanged.connect(self._on_status_combo_changed)
        self.priority_spin.valueChanged.connect(self._mark_dirty)
        self.estimate_spin.valueChanged.connect(self._mark_dirty)
        self.energy_spin.valueChanged.connect(self._mark_dirty)
        self.tags_edit.textChanged.connect(self._mark_dirty)
        self.due_edit.dateTimeChanged.connect(self._mark_dirty)
        self.scheduled_start_edit.dateTimeChanged.connect(self._mark_dirty)

        repository = getattr(self.task_service, "repository", None)
        if repository is not None and hasattr(repository, "tasks_changed"):
            repository.tasks_changed.connect(self.refresh_data)

        self._set_filter("all")
        self._refresh_model_status()
        self._spinner_timer.timeout.connect(self._advance_spinner)

        self.refresh_data()

    def refresh_data(self) -> None:
        self._refresh_model_status()
        tasks = list(self.task_service.list_tasks())
        current_id = self._selected_task_id
        query = self.search_input.text().strip().lower()
        self.task_list.clear()
        self._task_ids.clear()

        total = len(tasks)
        active = sum(1 for task in tasks if task.status not in {"done", "cancelled"})
        done = sum(1 for task in tasks if task.status == "done")
        self.total_pill.setText(f"{total} total")
        self.active_pill.setText(f"{active} active")
        self.done_pill.setText(f"{done} done")

        visible_tasks = []
        for task in tasks:
            hay = " ".join([task.title, task.description, ",".join(task.tags)]).lower()
            if query and query not in hay:
                continue
            if not self._matches_filter(task):
                continue
            visible_tasks.append(task)

        for task in visible_tasks:
            subtitle = self._subtitle(task)
            item = QListWidgetItem(f"{task.title}\n{subtitle}")
            item.setToolTip(task.description or subtitle)
            self.task_list.addItem(item)
            self._task_ids.append(task.id)

        if not self._task_ids:
            self._selected_task_id = None
            self._clear_form()
            return

        if current_id in self._task_ids:
            index = self._task_ids.index(current_id)
        else:
            index = 0
        self.task_list.setCurrentRow(index)

    def _to_qdatetime(self, dt: datetime | None) -> QDateTime:
        if not dt:
            return QDateTime(QDate(2000, 1, 1), QTime(0, 0))
        return QDateTime(QDate(dt.year, dt.month, dt.day), QTime(dt.hour, dt.minute, dt.second))

    def filter_content(self, text: str) -> None:
        _ = text
        self.refresh_data()

    def _subtitle(self, task: TaskItem) -> str:
        anchor = task.scheduled_start or task.due_at
        anchor_text = anchor.strftime("%Y-%m-%d %H:%M") if anchor else "No time"
        return f"{task.status} · p{task.priority} · {task.estimated_minutes}m · {anchor_text}"

    def _clear_form(self) -> None:
        self.title_edit.clear()
        self.description_edit.clear()
        self._set_status("todo", sync=False)
        self.priority_spin.setValue(3)
        self.estimate_spin.setValue(30)
        self.energy_spin.setValue(3)
        self.tags_edit.clear()
        self.due_edit.setDateTime(self._to_qdatetime(None))
        self.scheduled_start_edit.setDateTime(self._to_qdatetime(None))
        self.meta_label.setText("No task selected.")
        self.status_label.setText("")
        self._refresh_subtasks(None)
        self._set_autosave(False)

    def _on_task_selected(self, row: int) -> None:
        if row < 0 or row >= len(self._task_ids):
            self._selected_task_id = None
            self._clear_form()
            return
        task_id = self._task_ids[row]
        self._selected_task_id = task_id
        task = self.task_service.get_task(task_id)
        if task is None:
            self._clear_form()
            return
        self._populate_form(task)

    def _populate_form(self, task: TaskItem) -> None:
        self.title_edit.setText(task.title)
        self.description_edit.setPlainText(task.description)
        self._set_status(task.status, sync=False)
        self.priority_spin.setValue(task.priority)
        self.estimate_spin.setValue(task.estimated_minutes)
        self.energy_spin.setValue(task.energy_cost)
        self.tags_edit.setText(", ".join(task.tags))
        self.due_edit.setDateTime(self._to_qdatetime(task.due_at))
        self.scheduled_start_edit.setDateTime(self._to_qdatetime(task.scheduled_start))
        self.meta_label.setText(
            f"Task id: {task.id} · Created: {(task.created_at.strftime('%Y-%m-%d %H:%M') if task.created_at else 'unknown')}"
        )
        self._refresh_subtasks(task.id)
        self._set_autosave(False)

    def _collect_payload(self) -> dict[str, Any]:
        sentinel = datetime(2000, 1, 1, 0, 0)
        due_value = cast(datetime, self.due_edit.dateTime().toPython())
        scheduled_value = cast(datetime, self.scheduled_start_edit.dateTime().toPython())
        due_at = None if due_value <= sentinel else due_value
        scheduled_start = None if scheduled_value <= sentinel else scheduled_value
        return {
            "title": self.title_edit.text().strip(),
            "description": self.description_edit.toPlainText().strip(),
            "status": self.status_combo.currentText(),
            "priority": int(self.priority_spin.value()),
            "estimated_minutes": int(self.estimate_spin.value()),
            "energy_cost": int(self.energy_spin.value()),
            "tags": [tag.strip() for tag in self.tags_edit.text().split(",") if tag.strip()],
            "due_at": due_at,
            "scheduled_start": scheduled_start,
        }

    def _new_task(self) -> None:
        self._selected_task_id = None
        self._clear_form()
        self.title_edit.setFocus()

    def _save_task(self) -> None:
        payload = self._collect_payload()
        try:
            if self._selected_task_id:
                task = self.task_service.patch_task(self._selected_task_id, payload)
                self.notification_service.publish(
                    "Task updated",
                    f"Saved changes to {task.title}.",
                    level="info",
                    action_key="task_updated",
                    meta={"task_id": task.id},
                )
            else:
                task = self.task_service.create_task(**payload)
                self.notification_service.publish(
                    "Task created",
                    f"Added {task.title} to your task list.",
                    level="info",
                    action_key="task_created",
                    meta={"task_id": task.id},
                )
                self._selected_task_id = task.id
        except Exception as exc:
            QMessageBox.warning(self, "Could not save task", str(exc))
            return
            self._set_autosave(False)
        self.refresh_data()

    def _mark_done(self) -> None:
        if not self._selected_task_id:
            return
        try:
            task = self.task_service.set_status(self._selected_task_id, "done")
            self.notification_service.publish(
                "Task completed",
                f"Marked {task.title} as done.",
                level="info",
                action_key="task_done",
                meta={"task_id": task.id},
            )
        except Exception as exc:
            QMessageBox.warning(self, "Could not complete task", str(exc))
            return
        self.refresh_data()

    def _delete_task(self) -> None:
        if not self._selected_task_id:
            return
        task = self.task_service.get_task(self._selected_task_id)
        if task is None:
            return
        answer = QMessageBox.question(self, "Delete task", f"Delete '{task.title}'?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        deleted = self.task_service.delete_task(task.id)
        if deleted:
            self.notification_service.publish(
                "Task deleted",
                f"Removed {task.title} from the list.",
                level="warning",
                action_key="task_deleted",
                meta={"task_id": task.id},
            )
        self._selected_task_id = None
        self.refresh_data()

    def current_task(self) -> TaskItem | None:
        if self._selected_task_id is None:
            return None
        return self.task_service.get_task(self._selected_task_id)

    def _decompose_current_task(self) -> None:
        task = self.current_task()
        if task is None:
            return
        if self._slm_service is None:
            self._on_decompose_failed("Model not configured")
            return
        self._set_model_status("Processing", tone="accent")
        self._start_spinner()
        self.decompose_btn.setEnabled(False)
        self._run_decompose(task)

    
    def _refresh_subtasks(self, task_id: str | None) -> None:
        self.subtasks_list.clear()
        if not task_id:
            return
        for task in self.task_service.list_subtasks(task_id):
            self.subtasks_list.addItem(f"{task.title} · {task.estimated_minutes}m")

    def _set_filter(self, key: str) -> None:
        self._filter_status = key
        for name, button in self._filter_buttons.items():
            button.setChecked(name == key)
        self.refresh_data()

    def _matches_filter(self, task: TaskItem) -> bool:
        status = str(getattr(task, "status", "todo") or "todo").lower()
        if self._filter_status == "all":
            return True
        if self._filter_status == "active":
            return status not in {"done", "cancelled"}
        if self._filter_status == "done":
            return status == "done"
        if self._filter_status == "blocked":
            return status in {"blocked", "missed"}
        if self._filter_status == "unscheduled":
            return status not in {"done", "cancelled"} and getattr(task, "scheduled_start", None) is None
        return True

    def _set_status(self, status: str, sync: bool = True) -> None:
        status_value = status if status in {"todo", "in_progress", "done", "blocked", "missed", "cancelled"} else "todo"
        if sync:
            self.status_combo.setCurrentText(status_value)
        button = self._status_buttons.get(status_value)
        if button is not None:
            button.setChecked(True)
        self._mark_dirty()

    def _on_status_combo_changed(self, value: str) -> None:
        self._set_status(value, sync=False)

    def _mark_dirty(self) -> None:
        self._set_autosave(True)

    def _set_autosave(self, dirty: bool) -> None:
        self._autosave_dirty = dirty
        if dirty:
            self.autosave_pill.setText("Not saved")
        else:
            self.autosave_pill.setText("Saved")

    def _refresh_model_status(self) -> None:
        if self._slm_service is None:
            self._set_model_status("Model: unavailable", tone="default")
            return
        if not hasattr(self._slm_service, "current_config"):
            self._set_model_status("Model: unavailable", tone="default")
            return
        cfg = self._slm_service.current_config()
        if cfg is None:
            self._set_model_status("Model: not configured", tone="default")
            return
        if hasattr(self._slm_service, "is_model_ready") and self._slm_service.is_model_ready():
            self._set_model_status("Model: ready", tone="good")
            return
        self._set_model_status("Model: missing", tone="danger")

    def _set_model_status(self, text: str, *, tone: str = "default") -> None:
        self.model_status_pill.setText(text)
        object_name = {
            "default": "Pill",
            "accent": "PillAccent",
            "danger": "PillDanger",
            "good": "PillGood",
        }.get(tone, "Pill")
        self.model_status_pill.setObjectName(object_name)
        self.model_status_pill.style().unpolish(self.model_status_pill)
        self.model_status_pill.style().polish(self.model_status_pill)

    def _run_decompose(self, task: TaskItem) -> None:
        if self._decompose_thread is not None:
            return
        if self._slm_service is None:
            self._on_decompose_failed("Model not configured")
            return

        class _DecomposeWorker(QThread):
            done = Signal(list)
            failed = Signal(str)

            def __init__(self, slm_service: Any, payload: dict[str, Any]) -> None:
                super().__init__()
                self._slm_service = slm_service
                self._payload = payload

            def run(self) -> None:
                try:
                    created = self._slm_service.decompose_task(
                        title=self._payload["title"],
                        description=self._payload["description"],
                        estimated_minutes=self._payload["estimated_minutes"],
                        max_subtasks=self._payload["max_subtasks"],
                        tags=self._payload["tags"],
                        priority=self._payload["priority"],
                        energy_cost=self._payload["energy_cost"],
                    )
                except Exception as exc:
                    self.failed.emit(str(exc))
                    return
                self.done.emit(created)

        payload = {
            "title": task.title,
            "description": task.description,
            "estimated_minutes": task.estimated_minutes,
            "max_subtasks": 6,
            "tags": list(task.tags),
            "priority": task.priority,
            "energy_cost": task.energy_cost,
        }
        worker = _DecomposeWorker(self._slm_service, payload)
        worker.done.connect(self._on_decompose_done)
        worker.failed.connect(self._on_decompose_failed)
        worker.finished.connect(self._on_decompose_finished)
        self._decompose_thread = worker
        worker.start()

    def _on_decompose_done(self, created: list) -> None:
        try:
            if self._selected_task_id:
                created = self.task_service.create_subtasks_from_drafts(self._selected_task_id, created, persist=True)
        except Exception as exc:
            self.status_label.setText(f"Decomposition failed: {exc}")
            return
        self.status_label.setText(f"Created {len(created)} subtasks.")
        self.refresh_data()

    def _on_decompose_failed(self, message: str) -> None:
        self.status_label.setText(f"Decomposition failed: {message}")

    def _on_decompose_finished(self) -> None:
        self._decompose_thread = None
        self.decompose_btn.setEnabled(True)
        self._stop_spinner()
        self._refresh_model_status()

    def _advance_spinner(self) -> None:
        self.model_spinner.setText(self._spinner_frames[self._spinner_index])
        self._spinner_index = (self._spinner_index + 1) % len(self._spinner_frames)

    def _start_spinner(self) -> None:
        self._spinner_index = 0
        self._spinner_timer.start(120)

    def _stop_spinner(self) -> None:
        self._spinner_timer.stop()
        self.model_spinner.setText("")