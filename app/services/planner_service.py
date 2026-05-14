from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from app.data.entities import CalendarEventItem, SessionLogItem, TaskItem
from app.models.planner_model import (
    ConstraintSchedulingSolver,
    EnergyPatternModel,
    PlannerResult,
    build_free_slots,
)


@dataclass(slots=True)
class PlannerSnapshot:
    tasks: list[TaskItem]
    events: list[CalendarEventItem]
    sessions: list[SessionLogItem]


class PlannerService:
    def __init__(self, repository: Any) -> None:
        self.repository = repository
        self.energy_model = EnergyPatternModel(cluster_count=3)
        self.solver = ConstraintSchedulingSolver(step_minutes=15)

    def build_plan(self, *, anchor_day: date, day_count: int = 5, task_ids: list[str] | None = None) -> PlannerResult:
        snapshot = self._snapshot(anchor_day=anchor_day, day_count=day_count)
        tasks = self._candidate_tasks(snapshot.tasks, task_ids=task_ids)
        hour_scores = self.energy_model.build_hour_scores(snapshot.sessions)
        free_slots = build_free_slots(
            anchor_day=anchor_day,
            day_count=day_count,
            events=snapshot.events,
            tasks=snapshot.tasks,
        )
        return self.solver.solve(
            tasks=tasks,
            free_slots=free_slots,
            hour_scores=hour_scores,
            now=datetime.utcnow(),
        )

    def apply_plan(self, result: PlannerResult) -> int:
        changed = 0
        for suggestion in result.suggestions:
            task = self._task_by_id(suggestion.task_id)
            if task is None:
                continue
            task.scheduled_start = suggestion.scheduled_start
            task.scheduled_end = suggestion.scheduled_end
            task.meta = dict(task.meta or {})
            task.meta.setdefault('planner', {})
            task.meta['planner'].update({
                'last_score': suggestion.score,
                'last_energy_score': suggestion.energy_score,
                'last_reason': suggestion.reason,
                'generated_at': result.generated_at.isoformat(),
            })
            if hasattr(self.repository, 'update_task'):
                self.repository.update_task(task)
                changed += 1
        return changed

    def energy_profile(self) -> dict[int, float]:
        sessions = self._all_sessions()
        return self.energy_model.build_hour_scores(sessions)

    def _snapshot(self, *, anchor_day: date, day_count: int) -> PlannerSnapshot:
        return PlannerSnapshot(
            tasks=self._all_tasks(),
            events=self._calendar_events(anchor_day=anchor_day, day_count=day_count),
            sessions=self._all_sessions(),
        )

    def _candidate_tasks(self, tasks: list[TaskItem], task_ids: list[str] | None) -> list[TaskItem]:
        allowed = set(task_ids or [])
        result = [
            task for task in tasks
            if task.status not in {'done', 'cancelled'} and (not allowed or task.id in allowed)
        ]
        result.sort(key=lambda task: (-int(task.priority), task.due_at or datetime.max, task.created_at or datetime.min))
        return result[:30]

    def _all_tasks(self) -> list[TaskItem]:
        if hasattr(self.repository, 'all_tasks'):
            return list(self.repository.all_tasks())
        return []

    def _all_sessions(self) -> list[SessionLogItem]:
        if hasattr(self.repository, 'all_sessions'):
            return list(self.repository.all_sessions())
        return []

    def _calendar_events(self, *, anchor_day: date, day_count: int) -> list[CalendarEventItem]:
        if hasattr(self.repository, 'calendar_events_between'):
            start = datetime.combine(anchor_day, datetime.min.time())
            end = start.replace(hour=23, minute=59, second=59, microsecond=999999)
            end = end + __import__('datetime').timedelta(days=max(0, day_count - 1))
            return list(self.repository.calendar_events_between(start, end))
        if hasattr(self.repository, 'all_calendar_events'):
            return list(self.repository.all_calendar_events())
        return []

    def _task_by_id(self, task_id: str) -> TaskItem | None:
        if hasattr(self.repository, 'task_by_id'):
            return self.repository.task_by_id(task_id)
        for task in self._all_tasks():
            if task.id == task_id:
                return task
        return None
