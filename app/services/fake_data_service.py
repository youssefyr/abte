from __future__ import annotations

import random
import uuid
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from app.data.entities import (
    CalendarEventItem,
    FocusTickItem,
    SessionLogItem,
    TaskItem,
)

if TYPE_CHECKING:
    from app.data.repository import SqliteRepository


class FakeDataService:
    """
    An expansive fake data generator for development and testing.
    Generates realistic distributions of tasks, focus sessions, and related entities.
    """

    def __init__(self, repository: SqliteRepository) -> None:
        self.repository = repository
        self._tags = ["work", "study", "os", "deep-work", "planning", "review", "admin"]
        self._outcomes = ["completed", "running", "cancelled"]

    def _utcnow(self) -> datetime:
        from datetime import timezone
        return datetime.now(timezone.utc)

    def generate_all(self, tasks_count: int = 50, sessions_count: int = 20, days_back: int = 30) -> None:
        self.generate_tasks(tasks_count, days_back)
        self.generate_sessions(sessions_count, days_back)
        self.generate_calendar_events(min(tasks_count // 2, 10), days_back)
        self.generate_coach_reports(weeks_back=max(1, days_back // 7))

        self.repository.tasks_changed.emit()
        self.repository.sessions_changed.emit()
        self.repository.calendar_changed.emit()

    def generate_tasks(self, count: int, days_back: int) -> None:
        now = self._utcnow()
        for i in range(count):
            day_offset = random.randint(0, days_back)
            created = now - timedelta(days=day_offset, hours=random.randint(0, 23))
            
            is_done = random.random() > 0.4
            status = "done" if is_done else "todo"
            completed_at = created + timedelta(hours=random.randint(1, 48)) if is_done else None
            
            due_at = created + timedelta(days=random.randint(1, 7)) if random.random() > 0.5 else None
            
            task = TaskItem(
                id=f"task-{uuid.uuid4().hex[:12]}",
                title=f"Fake Task {i+1}: {random.choice(['Write', 'Review', 'Plan', 'Refactor'])} {random.choice(['Module', 'Chapter', 'Report', 'Code'])}",
                description="This is an automatically generated fake task for testing.",
                estimated_minutes=random.choice([15, 30, 45, 60, 90, 120]),
                due_at=due_at,
                priority=random.randint(1, 4),
                tags=random.sample(self._tags, k=random.randint(1, 3)),
                status=status,
                created_at=created,
                updated_at=completed_at or created,
                completed_at=completed_at,
                source="fake_data",
                energy_cost=random.randint(1, 5),
            )
            self.repository.add_task(task)

    def generate_sessions(self, count: int, days_back: int) -> None:
        now = self._utcnow()
        for _ in range(count):
            day_offset = random.randint(0, days_back)
            started = now - timedelta(days=day_offset, hours=random.randint(0, 23), minutes=random.randint(0, 59))
            
            duration_minutes = random.choice([15, 25, 45, 50, 90])
            ended = started + timedelta(minutes=duration_minutes)
            
            outcome = random.choices(["completed", "cancelled", "running"], weights=[0.7, 0.2, 0.1])[0]
            
            score = random.uniform(0.4, 0.95) if outcome == "completed" else random.uniform(0.1, 0.5)
            distractions = random.randint(0, 5) if outcome == "completed" else random.randint(3, 10)
            
            session = SessionLogItem(
                id=f"session-{uuid.uuid4().hex[:12]}",
                started_at=started,
                ended_at=ended if outcome != "running" else None,
                mode="focus",
                outcome=outcome,
                focus_score_avg=score,
                distraction_events=distractions,
                absent_seconds=random.randint(0, duration_minutes * 10),
                meta={"seeded": True, "fake_data": True},
            )
            self.repository.add_session(session)
            
            # Generate focus ticks for this session
            if outcome != "running":
                self._generate_focus_ticks(session.id, started, ended)

    def _generate_focus_ticks(self, session_id: str, started: datetime, ended: datetime) -> None:
        current = started
        while current < ended:
            tick_end = current + timedelta(seconds=10)
            if tick_end > ended:
                break
                
            tick = FocusTickItem(
                id=f"tick-{uuid.uuid4().hex[:12]}",
                started_at=current,
                ended_at=tick_end,
                p_drift_mean=random.uniform(0.01, 0.2),
                sample_count=10,
                session_id=session_id,
            )
            self.repository.add_focus_tick(tick)
            current = tick_end

    def generate_calendar_events(self, count: int, days_back: int) -> None:
        now = self._utcnow()
        for i in range(count):
            day_offset = random.randint(0, days_back)
            starts = now - timedelta(days=day_offset, hours=random.randint(8, 16))
            ends = starts + timedelta(hours=random.randint(1, 3))
            
            event = CalendarEventItem(
                id=f"event-{uuid.uuid4().hex[:12]}",
                title=f"Fake Event {i+1}: Sync",
                starts_at=starts,
                ends_at=ends,
                all_day=random.random() > 0.8,
                location=random.choice(["Zoom", "Office", ""]),
                meta={"seeded": True},
            )
            self.repository.add_calendar_event(event)

    def generate_coach_reports(self, weeks_back: int) -> None:
        now = self._utcnow()
        for i in range(weeks_back):
            week_start = now - timedelta(days=(i * 7) + 7)
            week_end = week_start + timedelta(days=7)
            
            summary = f"Fake coach report for week {i+1}. You focused well, completing several deep work sessions. Distractions were kept to a minimum."
            self.repository.add_coach_report(week_start, week_end, summary, {"seeded": True})
