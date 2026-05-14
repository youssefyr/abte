from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from PySide6.QtCore import QDateTime, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QSizePolicy,
)

from app.services.handle_tasks import TaskService
from app.ui.ui_helpers import (
    make_button,
    make_card,
    make_label,
    make_pill,
    make_toolbar_card,
)
from app.ui.icon_manager import icon_manager


class TaskCard(QFrame):
    actionRequested = Signal(str, str)

    def __init__(
        self,
        *,
        task_id: str,
        title: str,
        time_text: str,
        status: str | None = None,
        tags: list[str] | None = None,
        planned: bool = False,
        empty: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._task_id = task_id
        self._expanded = False
        self.setObjectName("TaskBlock")

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(8)

        header = QHBoxLayout()
        header.setSpacing(8)

        title_label = make_label(title, "cardTitle", True)
        title_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        header.addWidget(title_label, 1)

        if empty:
            header.addWidget(make_pill("Free", "default"))
        else:
            pill = self._status_pill(status, planned)
            if pill is not None:
                header.addWidget(pill)

        root.addLayout(header)
        root.addWidget(make_label(time_text, "muted"))

        if tags:
            root.addWidget(make_label("#" + " #".join(tags), "meta", True))

        self.actions_widget = QWidget()
        actions_layout = QHBoxLayout(self.actions_widget)
        actions_layout.setContentsMargins(0, 0, 0, 0)
        actions_layout.setSpacing(10)

        if not empty and task_id:
            self._add_action(actions_layout, "Decompose", "decompose")
            self._add_action(actions_layout, "Delay +1 day", "delay")
            self._add_action(actions_layout, "Unschedule", "unschedule")
            self._add_action(actions_layout, "Mark done", "done")

        actions_layout.addStretch()
        self.actions_widget.setMinimumHeight(44)
        self.actions_widget.setVisible(False)
        root.addWidget(self.actions_widget)

    def mousePressEvent(self, event) -> None:
        if self._task_id:
            self._expanded = not self._expanded
            self.actions_widget.setVisible(self._expanded)
        super().mousePressEvent(event)

    def _add_action(self, layout: QHBoxLayout, label: str, key: str) -> None:
        icon_name, tooltip = {
            "decompose": ("mdi6.source-branch", "Decompose: split into subtasks."),
            "delay": ("mdi6.clock-plus-outline", "Delay: push by one day."),
            "unschedule": ("mdi6.calendar-remove-outline", "Unschedule: remove planned time."),
            "done": ("mdi6.check-circle-outline", "Mark done: set status to done."),
        }.get(key, ("mdi6.circle-outline", label))

        button = QToolButton()
        button.setObjectName("TaskActionButton")
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        button.setAutoRaise(True)
        button.setFixedSize(36, 36)
        icon_manager.apply(button, icon_name, size=18)
        button.setToolTip(tooltip)
        button.setAccessibleName(label)
        button.setProperty("task_action", key)
        button.clicked.connect(lambda _checked=False, action_key=key: self.actionRequested.emit(self._task_id, action_key))
        layout.addWidget(button)

    def _status_pill(self, status: str | None, planned: bool) -> QWidget | None:
        if status == "done":
            return make_pill("Done", "good")
        if status == "blocked":
            return make_pill("Blocked", "danger")
        if planned:
            return make_pill("Planned", "accent")
        return None


class FlexibleWeekViewWidget(QWidget):
    dayCountChanged = Signal(int)
    modeChanged = Signal(str)
    taskDropped = Signal(str, QDateTime)
    aiRescheduleRequested = Signal(list)

    def __init__(self, repository: Any | None = None, parent: QWidget | None = None, compact: bool = False) -> None:
        super().__init__(parent)
        self._model = None
        self._mode = "days"
        self._day_count = 5
        self._next_task_count = 8
        self._compact = compact
        self._anchor_date = date.today()
        self.repository = repository
        self.task_service = TaskService(repository) if repository is not None else None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        self.toolbar_card, toolbar_layout = make_toolbar_card()

        row1 = QHBoxLayout()
        row1.setSpacing(8)

        self.prev_btn = make_button("←", "ghost")
        self.today_btn = make_button("Today", "secondary")
        self.next_btn = make_button("→", "ghost")

        self.days_btn = QToolButton()
        self.days_btn.setText("Days")
        self.days_btn.setObjectName("SegmentButton")
        self.days_btn.setCheckable(True)
        self.days_btn.setChecked(True)

        self.next_tasks_btn = QToolButton()
        self.next_tasks_btn.setText("Next X tasks")
        self.next_tasks_btn.setObjectName("SegmentButton")
        self.next_tasks_btn.setCheckable(True)

        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        self.mode_group.addButton(self.days_btn)
        self.mode_group.addButton(self.next_tasks_btn)

        row1.addWidget(self.prev_btn)
        row1.addWidget(self.today_btn)
        row1.addWidget(self.next_btn)
        row1.addSpacing(8)
        row1.addWidget(self.days_btn)
        row1.addWidget(self.next_tasks_btn)
        row1.addStretch()

        if not compact:
            row1.addWidget(make_pill("Planner-aware", "default"))

        row2 = QHBoxLayout()
        row2.setSpacing(8)

        row2.addWidget(make_label("Visible span", "meta"))

        self.day_buttons: list[QToolButton] = []
        self.day_group = QButtonGroup(self)
        self.day_group.setExclusive(True)
        for n in [1, 3, 5, 7]:
            btn = QToolButton()
            btn.setText(str(n))
            btn.setObjectName("SegmentButton")
            btn.setCheckable(True)
            if n == 5:
                btn.setChecked(True)
            btn.clicked.connect(lambda _checked=False, x=n: self.setDayCount(x))
            self.day_group.addButton(btn)
            self.day_buttons.append(btn)
            row2.addWidget(btn)

        row2.addSpacing(8)
        row2.addWidget(make_label("Next X", "meta"))

        self.next_count = QSpinBox()
        self.next_count.setRange(1, 50)
        self.next_count.setValue(self._next_task_count)
        self.next_count.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.UpDownArrows)
        row2.addWidget(self.next_count)

        row2.addStretch()

        self.ai_btn = make_button("Suggest schedule", "primary")
        self.ai_btn.setAccessibleName("Suggest schedule")
        self.ai_btn.setAccessibleDescription("Requests AI-assisted schedule suggestions for the visible tasks.")
        row2.addWidget(self.ai_btn)

        self.range_label = make_label("", "muted")
        toolbar_layout.addLayout(row1)
        if not compact:
            toolbar_layout.addLayout(row2)
        toolbar_layout.addWidget(self.range_label)

        root.addWidget(self.toolbar_card)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)

        self.container = QWidget()
        self.container_layout = QHBoxLayout(self.container)
        self.container_layout.setContentsMargins(0, 0, 0, 0)
        self.container_layout.setSpacing(12)

        self.scroll_area.setWidget(self.container)
        root.addWidget(self.scroll_area, 1)

        self.days_btn.clicked.connect(lambda: self.setMode("days"))
        self.next_tasks_btn.clicked.connect(lambda: self.setMode("next_tasks"))
        self.prev_btn.clicked.connect(self._go_prev)
        self.next_btn.clicked.connect(self._go_next)
        self.today_btn.clicked.connect(self._go_today)
        self.next_count.valueChanged.connect(self.setNextTaskCount)
        self.ai_btn.clicked.connect(self._emit_ai_request)
        self.taskDropped.connect(self._on_task_dropped)

        if compact:
            self.toolbar_card.hide()

        if self.repository is not None and hasattr(self.repository, "tasks_changed"):
            self.repository.tasks_changed.connect(self.refresh_data)

        self._rebuild_view()

    def setModel(self, model) -> None:
        self._model = model
        self._rebuild_view()

    def model(self):
        return self._model

    def refresh_data(self) -> None:
        self._rebuild_view()

    def setMode(self, mode: str) -> None:
        if mode not in ("days", "next_tasks"):
            return
        if self._mode == mode:
            return
        self._mode = mode
        self._rebuild_view()
        self.modeChanged.emit(mode)

    def currentMode(self) -> str:
        return self._mode

    def setDayCount(self, count: int) -> None:
        count = max(1, min(7, count))
        if count == self._day_count:
            return
        self._day_count = count
        if self._mode == "days":
            self._rebuild_view()
        self.dayCountChanged.emit(count)

    def dayCount(self) -> int:
        return self._day_count

    def setNextTaskCount(self, count: int) -> None:
        self._next_task_count = count
        if self._mode == "next_tasks":
            self._rebuild_view()

    def nextTaskCount(self) -> int:
        return self._next_task_count

    def _clear_layout(self) -> None:
        while self.container_layout.count():
            item = self.container_layout.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _rebuild_view(self) -> None:
        self._clear_layout()
        self.range_label.setText(self._range_text())

        if self._mode == "days":
            for i in range(self._day_count):
                d = self._anchor_date + timedelta(days=i)
                self.container_layout.addWidget(self._build_day_column(d), 1)
            self.container_layout.addStretch()
            return

        list_card, list_layout = make_card(
            "Next tasks",
            "Upcoming work grouped into a calmer list.",
            elevated=False,
        )
        for task in self._upcoming_tasks()[: self._next_task_count]:
            list_layout.addWidget(
                self._build_task_card(task)
            )
        list_layout.addStretch()
        self.container_layout.addWidget(list_card)

    def _build_day_column(self, day: date) -> QFrame:
        tasks = self.task_service.tasks_for_day(day) if self.task_service is not None else []
        description = "Open day · no scheduled tasks" if not tasks else f"{len(tasks)} scheduled"
        card, layout = make_card(day.strftime("%A"), description, elevated=False, eyebrow=day.strftime("%d %b"))
        card.setMinimumWidth(210)
        if not tasks:
            layout.addWidget(self._build_empty_block())
            layout.addStretch()
            return card

        limit = 3 if self._compact else 6
        for task in tasks[:limit]:
            layout.addWidget(self._build_task_card(task))
        layout.addStretch()
        return card

    def _build_task_card(self, task: Any) -> QFrame:
        card = TaskCard(
            task_id=task.id,
            title=task.title,
            time_text=self._task_time_text(task),
            status=getattr(task, "status", None),
            tags=list(getattr(task, "tags", []) or []),
            planned=bool(getattr(task, "scheduled_start", None)),
            parent=self,
        )
        card.actionRequested.connect(self._handle_task_action)
        card.setProperty("task_id", task.id)
        return card

    def _build_empty_block(self) -> QFrame:
        return TaskCard(
            task_id="",
            title="Open day",
            time_text="No scheduled tasks yet",
            empty=True,
            parent=self,
        )

    def _task_time_text(self, task: Any) -> str:
        scheduled_start = getattr(task, "scheduled_start", None)
        scheduled_end = getattr(task, "scheduled_end", None)
        estimated_minutes = getattr(task, "estimated_minutes", 30)
        if scheduled_start and scheduled_end:
            mins = int((scheduled_end - scheduled_start).total_seconds() // 60)
            return f"{scheduled_start.strftime('%a %H:%M')} · {mins}m"
        if scheduled_start:
            return f"{scheduled_start.strftime('%a %H:%M')} · {estimated_minutes}m"
        due_at = getattr(task, "due_at", None)
        if due_at:
            return f"Due {due_at.strftime('%a %H:%M')} · {estimated_minutes}m"
        return f"Unscheduled · {estimated_minutes}m"

    def _handle_task_action(self, task_id: str, action: str) -> None:
        if not task_id or self.task_service is None:
            return
        try:
            if action == "decompose":
                self.task_service.decompose_task(task_id, persist=True)
            elif action == "delay":
                self._delay_task(task_id)
            elif action == "unschedule":
                self.task_service.unschedule_task(task_id)
            elif action == "done":
                self.task_service.set_status(task_id, "done")
        except Exception:
            return
        self.refresh_data()

    def _delay_task(self, task_id: str) -> None:
        if self.task_service is None:
            return
        task = self.task_service.get_task(task_id)
        if task is None:
            return
        if task.scheduled_start:
            new_start = task.scheduled_start + timedelta(days=1)
            duration = task.estimated_minutes or 30
            self.task_service.schedule_task(task_id, new_start, duration_minutes=duration)
            return
        if task.due_at:
            self.task_service.patch_task(task_id, {"due_at": task.due_at + timedelta(days=1)})
            return
        tomorrow = date.today() + timedelta(days=1)
        self.task_service.reschedule_to_day(task_id, tomorrow)

    def _upcoming_tasks(self) -> list[Any]:
        if self.task_service is None:
            return []
        return self.task_service.upcoming_tasks(self._next_task_count)

    def _range_text(self) -> str:
        end = self._anchor_date + timedelta(days=max(self._day_count - 1, 0))
        return f"{self._anchor_date.strftime('%d %b')} — {end.strftime('%d %b')}"

    def _go_prev(self) -> None:
        step = self._day_count if self._mode == "days" else 1
        self._anchor_date -= timedelta(days=step)
        self._rebuild_view()

    def _go_next(self) -> None:
        step = self._day_count if self._mode == "days" else 1
        self._anchor_date += timedelta(days=step)
        self._rebuild_view()

    def _go_today(self) -> None:
        self._anchor_date = date.today()
        self._rebuild_view()

    def _emit_ai_request(self) -> None:
        if self._model is not None:
            task_ids: list[str] = []
            row_count = self._model.rowCount()
            for row in range(row_count):
                idx = self._model.index(row, 0)
                task_id = idx.data(Qt.ItemDataRole.UserRole)
                if isinstance(task_id, str):
                    task_ids.append(task_id)
            self.aiRescheduleRequested.emit(task_ids)
            return

        if self.task_service is None:
            self.aiRescheduleRequested.emit([])
            return

        self.aiRescheduleRequested.emit([task.id for task in self.task_service.upcoming_tasks(self._next_task_count)])

    def _on_task_dropped(self, task_id: str, when: QDateTime) -> None:
        if self.task_service is None:
            return
        dt = when.toPython()
        if isinstance(dt, datetime):
            self.task_service.schedule_task(task_id, dt)
        elif isinstance(dt, date):
            self.task_service.reschedule_to_day(task_id, dt)