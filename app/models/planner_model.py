from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Iterable

from ortools.sat.python import cp_model

try:
    from sklearn.cluster import KMeans  
except Exception:
    KMeans = None

from app.data.entities import CalendarEventItem, SessionLogItem, TaskItem


@dataclass(slots=True)
class TimeSlot:
    start: datetime
    end: datetime
    energy_score: float
    source: str = 'free_block'

    @property
    def duration_minutes(self) -> int:
        return max(0, int((self.end - self.start).total_seconds() // 60))


@dataclass(slots=True)
class PlannerSuggestion:
    task_id: str
    title: str
    scheduled_start: datetime
    scheduled_end: datetime
    energy_score: float
    score: float
    reason: str


@dataclass(slots=True)
class PlannerResult:
    generated_at: datetime
    suggestions: list[PlannerSuggestion]
    unscheduled_task_ids: list[str]
    diagnostics: dict[str, Any]


class EnergyPatternModel:
    def __init__(self, cluster_count: int = 3, random_state: int = 7) -> None:
        self.cluster_count = max(1, cluster_count)
        self.random_state = random_state

    def build_hour_scores(self, sessions: Iterable[SessionLogItem]) -> dict[int, float]:
        rows: list[list[float]] = []
        for session in sessions:
            if session.started_at is None:
                continue
            hour = float(session.started_at.hour)
            focus = float(session.focus_score_avg if session.focus_score_avg is not None else 50.0)
            duration_minutes = self._session_duration_minutes(session)
            rows.append([hour, focus, duration_minutes])
        if len(rows) < self.cluster_count or KMeans is None:
            return self._fallback_hour_scores(rows)
        model = KMeans(n_clusters=min(self.cluster_count, len(rows)), n_init='auto', random_state=self.random_state)
        labels = model.fit_predict(rows)
        cluster_quality: dict[int, float] = {}
        for idx, label in enumerate(labels):
            cluster_quality.setdefault(int(label), 0.0)
            cluster_quality[int(label)] += rows[idx][1]
        cluster_sizes: dict[int, int] = {}
        for label in labels:
            cluster_sizes[int(label)] = cluster_sizes.get(int(label), 0) + 1
        for label, total in list(cluster_quality.items()):
            cluster_quality[label] = total / max(1, cluster_sizes.get(label, 1))
        centers = model.cluster_centers_
        hour_scores: dict[int, float] = {hour: 0.45 for hour in range(24)}
        qualities = list(cluster_quality.values()) or [50.0]
        q_min = min(qualities)
        q_max = max(qualities)
        spread = max(1e-6, q_max - q_min)
        for label, center in enumerate(centers):
            center_hour = int(round(float(center[0]))) % 24
            normalized_quality = (cluster_quality.get(label, q_min) - q_min) / spread if q_max != q_min else 0.7
            for hour in range(24):
                distance = min(abs(hour - center_hour), 24 - abs(hour - center_hour))
                influence = max(0.0, 1.0 - (distance / 6.0))
                hour_scores[hour] = max(hour_scores[hour], 0.35 + normalized_quality * 0.65 * influence)
        return {hour: round(score, 4) for hour, score in hour_scores.items()}

    def _fallback_hour_scores(self, rows: list[list[float]]) -> dict[int, float]:
        if not rows:
            return {hour: 0.5 for hour in range(24)}
        sums = {hour: 0.0 for hour in range(24)}
        counts = {hour: 0 for hour in range(24)}
        for hour, focus, _duration in rows:
            h = int(hour) % 24
            sums[h] += float(focus)
            counts[h] += 1
        values = [sums[h] / counts[h] for h in range(24) if counts[h] > 0] or [50.0]
        lo, hi = min(values), max(values)
        span = max(1e-6, hi - lo)
        result: dict[int, float] = {}
        for hour in range(24):
            if counts[hour] == 0:
                result[hour] = 0.5
                continue
            avg = sums[hour] / counts[hour]
            result[hour] = round(0.3 + ((avg - lo) / span if hi != lo else 0.7) * 0.7, 4)
        return result

    def _session_duration_minutes(self, session: SessionLogItem) -> float:
        if session.started_at is None or session.ended_at is None:
            return 25.0
        return max(5.0, (session.ended_at - session.started_at).total_seconds() / 60.0)


class ConstraintSchedulingSolver:
    def __init__(self, step_minutes: int = 15) -> None:
        self.step_minutes = max(5, step_minutes)

    def solve(
        self,
        *,
        tasks: list[TaskItem],
        free_slots: list[TimeSlot],
        hour_scores: dict[int, float],
        now: datetime,
    ) -> PlannerResult:
        candidate_map = self._build_candidates(tasks, free_slots, hour_scores, now)
        if cp_model is None:
            return self._solve_greedy(tasks, candidate_map, now)
        model = cp_model.CpModel()
        variables: dict[tuple[str, int], Any] = {}
        objective_terms: list[Any] = []
        slot_usage: dict[int, list[Any]] = {}
        for task in tasks:
            candidates = candidate_map.get(task.id, [])
            if not candidates:
                continue
            task_vars = []
            for idx, candidate in enumerate(candidates):
                var = model.new_bool_var(f"task_{task.id}_{idx}")
                variables[(task.id, idx)] = var
                task_vars.append(var)
                slot_usage.setdefault(candidate['slot_index'], []).append(var)
                objective_terms.append(int(candidate['score'] * 1000) * var)
            model.add(sum(task_vars) <= 1)
        for vars_for_slot in slot_usage.values():
            model.add(sum(vars_for_slot) <= 1)
        model.maximize(sum(objective_terms) if objective_terms else 0)
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = 1.5
        solver.parameters.num_search_workers = 8
        status = solver.solve(model)
        suggestions: list[PlannerSuggestion] = []
        chosen_ids: set[str] = set()
        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            for task in tasks:
                candidates = candidate_map.get(task.id, [])
                for idx, candidate in enumerate(candidates):
                    var = variables.get((task.id, idx))
                    if var is not None and solver.value(var):
                        chosen_ids.add(task.id)
                        suggestions.append(self._candidate_to_suggestion(task, candidate))
                        break
        suggestions.sort(key=lambda item: item.scheduled_start)
        return PlannerResult(
            generated_at=now,
            suggestions=suggestions,
            unscheduled_task_ids=[task.id for task in tasks if task.id not in chosen_ids],
            diagnostics={
                'solver': 'ortools_cp_sat',
                'task_count': len(tasks),
                'slot_count': len(free_slots),
                'candidate_count': sum(len(x) for x in candidate_map.values()),
            },
        )

    def _solve_greedy(self, tasks: list[TaskItem], candidate_map: dict[str, list[dict[str, Any]]], now: datetime) -> PlannerResult:
        suggestions: list[PlannerSuggestion] = []
        taken_slots: set[int] = set()
        scheduled: set[str] = set()
        ordered = sorted(tasks, key=lambda task: (-int(task.priority), task.due_at or datetime.max))
        for task in ordered:
            for candidate in candidate_map.get(task.id, []):
                slot_index = int(candidate['slot_index'])
                if slot_index in taken_slots:
                    continue
                taken_slots.add(slot_index)
                scheduled.add(task.id)
                suggestions.append(self._candidate_to_suggestion(task, candidate))
                break
        suggestions.sort(key=lambda item: item.scheduled_start)
        return PlannerResult(
            generated_at=now,
            suggestions=suggestions,
            unscheduled_task_ids=[task.id for task in tasks if task.id not in scheduled],
            diagnostics={
                'solver': 'greedy_fallback',
                'task_count': len(tasks),
                'candidate_count': sum(len(x) for x in candidate_map.values()),
            },
        )

    def _build_candidates(
        self,
        tasks: list[TaskItem],
        free_slots: list[TimeSlot],
        hour_scores: dict[int, float],
        now: datetime,
    ) -> dict[str, list[dict[str, Any]]]:
        result: dict[str, list[dict[str, Any]]] = {}
        ordered_tasks = sorted(tasks, key=lambda task: (-int(task.priority), task.due_at or datetime.max, task.created_at or datetime.min))
        for task in ordered_tasks:
            candidates: list[dict[str, Any]] = []
            duration = max(15, int(task.estimated_minutes or 30))
            due_at_naive = _naive_utc(task.due_at) if task.due_at is not None else None
            for slot_index, slot in enumerate(free_slots):
                if slot.duration_minutes < duration:
                    continue
                starts = self._candidate_starts(slot.start, slot.end, duration)
                for start in starts:
                    end = start + timedelta(minutes=duration)
                    if due_at_naive is not None and end > due_at_naive:
                        continue
                    base_energy = hour_scores.get(start.hour, slot.energy_score)
                    due_urgency = self._due_urgency(task, end, now, due_at_naive)
                    energy_fit = self._energy_fit(task, base_energy)
                    score = (due_urgency * 0.45) + (energy_fit * 0.4) + (float(task.priority) / 5.0 * 0.15)
                    candidates.append({
                        'slot_index': slot_index,
                        'start': start,
                        'end': end,
                        'energy_score': base_energy,
                        'score': round(score, 5),
                        'reason': self._reason_text(task, base_energy, due_urgency),
                    })
            candidates.sort(key=lambda item: item['score'], reverse=True)
            result[task.id] = candidates[:12]
        return result

    def _candidate_starts(self, start: datetime, end: datetime, duration: int) -> list[datetime]:
        starts: list[datetime] = []
        start_naive = _naive_utc(start)
        end_naive = _naive_utc(end)
        assert start_naive is not None
        assert end_naive is not None
        cursor = start_naive
        latest = end_naive - timedelta(minutes=duration)
        while cursor <= latest:
            starts.append(cursor)
            cursor += timedelta(minutes=self.step_minutes)
        return starts

    def _due_urgency(self, task: TaskItem, proposed_end: datetime, now: datetime, due_at_naive: datetime | None = None) -> float:
        if due_at_naive is None and task.due_at is None:
            return 0.65
        due_at = due_at_naive if due_at_naive is not None else _naive_utc(task.due_at)
        assert due_at is not None
        now_naive = _naive_utc(now)
        proposed_end_naive = _naive_utc(proposed_end)
        assert now_naive is not None
        assert proposed_end_naive is not None
        seconds_left = max(60.0, (due_at - now_naive).total_seconds())
        seconds_margin = max(0.0, (due_at - proposed_end_naive).total_seconds())
        normalized = max(0.0, min(1.0, 1.0 - (seconds_margin / seconds_left)))
        return 0.45 + (normalized * 0.55)

    def _energy_fit(self, task: TaskItem, base_energy: float) -> float:
        demand = max(1, min(5, int(task.energy_cost or 3)))
        demand_norm = demand / 5.0
        return max(0.0, 1.0 - abs(base_energy - demand_norm))

    def _reason_text(self, task: TaskItem, energy_score: float, due_urgency: float) -> str:
        if task.due_at is not None and due_urgency > 0.8:
            return 'High urgency fit before deadline'
        if energy_score >= 0.7:
            return 'Matched to a historically strong focus window'
        return 'Placed in the nearest feasible gap without overlap'

    def _candidate_to_suggestion(self, task: TaskItem, candidate: dict[str, Any]) -> PlannerSuggestion:
        return PlannerSuggestion(
            task_id=task.id,
            title=task.title,
            scheduled_start=candidate['start'],
            scheduled_end=candidate['end'],
            energy_score=float(candidate['energy_score']),
            score=float(candidate['score']),
            reason=str(candidate['reason']),
        )

    
def build_free_slots(
    *,
    anchor_day: date,
    day_count: int,
    events: Iterable[CalendarEventItem],
    tasks: Iterable[TaskItem],
    workday_start: time = time(8, 0),
    workday_end: time = time(22, 0),
) -> list[TimeSlot]:
    busy_by_day: dict[date, list[tuple[datetime, datetime]]] = {}
    for item in events:
        busy_by_day.setdefault(item.starts_at.date(), []).append((item.starts_at, item.ends_at))
    for task in tasks:
        if task.scheduled_start and task.scheduled_end:
            busy_by_day.setdefault(task.scheduled_start.date(), []).append((task.scheduled_start, task.scheduled_end))
    slots: list[TimeSlot] = []
    for offset in range(max(1, day_count)):
        day = anchor_day + timedelta(days=offset)
        day_start = datetime.combine(day, workday_start)
        day_end = datetime.combine(day, workday_end)
        busy = sorted(busy_by_day.get(day, []), key=lambda pair: pair[0])
        cursor = _naive_utc(day_start)
        day_end_naive = _naive_utc(day_end)
        assert cursor is not None
        assert day_end_naive is not None
        for start, end in busy:
            start_naive = _naive_utc(start)
            end_naive = _naive_utc(end)
            assert start_naive is not None
            assert end_naive is not None
            if end_naive <= cursor:
                continue
            if start_naive > cursor:
                slots.append(TimeSlot(start=cursor, end=min(start_naive, day_end_naive), energy_score=0.5))
            cursor = max(cursor, end_naive)
            if cursor >= day_end_naive:
                break
        if cursor < day_end_naive:
            slots.append(TimeSlot(start=cursor, end=day_end_naive, energy_score=0.5))
    return [slot for slot in slots if slot.duration_minutes >= 15]


def _naive_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)