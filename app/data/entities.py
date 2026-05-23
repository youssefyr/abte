from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

TaskStatus = Literal["todo", "in_progress", "done", "blocked", "missed", "cancelled"]
SessionOutcome = Literal["running", "paused", "completed", "stopped", "missed", "cancelled"]


@dataclass(slots=True)
class TaskItem:
    id: str
    title: str
    description: str = ""
    estimated_minutes: int = 30
    due_at: datetime | None = None
    priority: int = 3
    tags: list[str] = field(default_factory=list)
    status: TaskStatus = "todo"
    scheduled_start: datetime | None = None
    scheduled_end: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    completed_at: datetime | None = None
    source: str = "manual"
    energy_cost: int = 3
    focus_score_hint: float | None = None
    recurrence_rule: str | None = None
    parent_task_id: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class NotificationItem:
    id: str
    title: str
    message: str
    level: str = "info"
    created_at: datetime | None = None
    read_at: datetime | None = None
    action_key: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PluginItem:
    id: str
    name: str
    version: str
    description: str
    enabled: bool = False
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CalendarEventItem:
    id: str
    title: str
    starts_at: datetime
    ends_at: datetime
    source: str = "local"
    task_id: str | None = None
    all_day: bool = False
    location: str | None = None
    notes: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SessionLogItem:
    id: str
    started_at: datetime
    ended_at: datetime | None = None
    mode: str = "focus"
    planned_task_id: str | None = None
    outcome: str = "running"
    focus_score_avg: float | None = None
    distraction_events: int = 0
    absent_seconds: int = 0
    # Pomodoro / target duration support (#18)
    target_minutes: int | None = None  # None = open-ended; set for Pomodoro/timed modes
    meta: dict[str, Any] = field(default_factory=dict)

@dataclass(slots=True)
class FocusTickItem:
    id: str
    started_at: datetime
    ended_at: datetime
    p_drift_mean: float
    sample_count: int
    session_id: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class UserProfileItem:
    id: str
    display_name: str
    avatar_path: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def __getattr__(self, name: str) -> Any:
        try:
            meta = object.__getattribute__(self, "meta")
            if name in meta:
                return meta[name]
        except AttributeError:
            pass
        raise AttributeError(f"'UserProfileItem' object has no attribute '{name}'")

    def __setattr__(self, name: str, value: Any) -> None:
        if name in {"id", "display_name", "avatar_path", "created_at", "updated_at", "meta"}:
            object.__setattr__(self, name, value)
        else:
            try:
                meta = object.__getattribute__(self, "meta")
            except AttributeError:
                meta = {}
                object.__setattr__(self, "meta", meta)
            meta[name] = value



@dataclass(slots=True)
class FocusObservationRecord:
    """Lightweight record written by FocusTickEngine for ML training data collection (#2).

    Written as newline-delimited JSON to the data directory. Consuming scripts
    (e.g. tools/foc.py) can read these files to retrain the drift model without
    needing the live application state.
    """
    timestamp: str          # ISO-8601 UTC
    session_id: str | None
    drift_label: int        # 0 = focused, 1 = drifting (ground-truth from outcome)
    raw_drift_risk: float   # model output at this tick
    focus_score: float      # smoother EMA output
    features: dict[str, Any] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)
