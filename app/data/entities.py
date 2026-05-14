from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

TaskStatus = Literal["todo", "in_progress", "done", "blocked", "missed", "cancelled"]


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

