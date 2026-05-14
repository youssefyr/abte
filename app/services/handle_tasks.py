from __future__ import annotations

import re
import uuid
from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.data.entities import TaskItem
from app.services.slm import SlmService

TASK_STATUSES = {"todo", "in_progress", "done", "blocked", "missed", "cancelled"}
PRIORITY_TOKENS = {"!!!": 5, "!!": 4, "!": 3}
TAG_PATTERN = re.compile(r"#([A-Za-z0-9_\-]+)")
MINUTE_PATTERN = re.compile(r"\b(\d+)\s*(m|min|mins|minute|minutes)\b", re.IGNORECASE)
HOUR_PATTERN = re.compile(r"\b(\d+(?:\.\d+)?)\s*(h|hr|hrs|hour|hours)\b", re.IGNORECASE)
AT_TIME_PATTERN = re.compile(r"\b(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", re.IGNORECASE)


class TaskService:
    def __init__(self, repository: Any, slm_service: SlmService | None = None) -> None:
        self.repository = repository
        self._slm_service = slm_service

    def list_tasks(self) -> list[TaskItem]:
        if self.repository is None or not hasattr(self.repository, "all_tasks"):
            return []
        tasks = list(self.repository.all_tasks())
        tasks.sort(key=self._sort_key)
        return tasks

    def get_task(self, task_id: str) -> TaskItem | None:
        if not task_id:
            return None
        if hasattr(self.repository, "task_by_id"):
            return self.repository.task_by_id(task_id)
        for task in self.list_tasks():
            if task.id == task_id:
                return task
        return None

    def create_task(
        self,
        *,
        title: str,
        description: str = "",
        estimated_minutes: int = 30,
        due_at: datetime | None = None,
        priority: int = 3,
        tags: list[str] | None = None,
        status: str = "todo",
        scheduled_start: datetime | None = None,
        scheduled_end: datetime | None = None,
        source: str = "manual",
        energy_cost: int = 3,
        focus_score_hint: float | None = None,
        recurrence_rule: str | None = None,
        parent_task_id: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> TaskItem:
        normalized = self._normalize_payload(
            {
                "title": title,
                "description": description,
                "estimated_minutes": estimated_minutes,
                "due_at": due_at,
                "priority": priority,
                "tags": tags or [],
                "status": status,
                "scheduled_start": scheduled_start,
                "scheduled_end": scheduled_end,
                "source": source,
                "energy_cost": energy_cost,
                "focus_score_hint": focus_score_hint,
                "recurrence_rule": recurrence_rule,
                "parent_task_id": parent_task_id,
                "meta": meta or {},
            }
        )
        now = datetime.utcnow()
        task = TaskItem(
            id=f"task-{uuid.uuid4().hex[:12]}",
            title=normalized["title"],
            description=normalized["description"],
            estimated_minutes=normalized["estimated_minutes"],
            due_at=normalized["due_at"],
            priority=normalized["priority"],
            tags=normalized["tags"],
            status=normalized["status"],
            scheduled_start=normalized["scheduled_start"],
            scheduled_end=normalized["scheduled_end"],
            created_at=now,
            updated_at=now,
            completed_at=now if normalized["status"] == "done" else None,
            source=normalized["source"],
            energy_cost=normalized["energy_cost"],
            focus_score_hint=normalized["focus_score_hint"],
            recurrence_rule=normalized["recurrence_rule"],
            parent_task_id=normalized["parent_task_id"],
            meta=normalized["meta"],
        )
        if hasattr(self.repository, "add_task"):
            stored = self.repository.add_task(task)
            final_task = stored if isinstance(stored, TaskItem) else task
            if bool(final_task.meta.get("auto_decompose", False)):
                self.maybe_auto_decompose_task(final_task)
            return final_task
        raise RuntimeError("Repository does not support add_task")

    def update_task(self, task: TaskItem) -> TaskItem:
        existing = self.get_task(task.id)
        if existing is None:
            raise KeyError(f"Task not found: {task.id}")
        normalized = self._normalize_payload(
            {
                "title": task.title,
                "description": task.description,
                "estimated_minutes": task.estimated_minutes,
                "due_at": task.due_at,
                "priority": task.priority,
                "tags": list(task.tags),
                "status": task.status,
                "scheduled_start": task.scheduled_start,
                "scheduled_end": task.scheduled_end,
                "source": task.source,
                "energy_cost": task.energy_cost,
                "focus_score_hint": task.focus_score_hint,
                "recurrence_rule": task.recurrence_rule,
                "parent_task_id": task.parent_task_id,
                "meta": deepcopy(task.meta),
            }
        )
        updated = replace(
            existing,
            title=normalized["title"],
            description=normalized["description"],
            estimated_minutes=normalized["estimated_minutes"],
            due_at=normalized["due_at"],
            priority=normalized["priority"],
            tags=normalized["tags"],
            status=normalized["status"],
            scheduled_start=normalized["scheduled_start"],
            scheduled_end=normalized["scheduled_end"],
            updated_at=datetime.utcnow(),
            completed_at=datetime.utcnow() if normalized["status"] == "done" and existing.completed_at is None else (None if normalized["status"] != "done" else existing.completed_at),
            source=normalized["source"],
            energy_cost=normalized["energy_cost"],
            focus_score_hint=normalized["focus_score_hint"],
            recurrence_rule=normalized["recurrence_rule"],
            parent_task_id=normalized["parent_task_id"],
            meta=normalized["meta"],
        )
        if hasattr(self.repository, "update_task"):
            stored = self.repository.update_task(updated)
            return stored if isinstance(stored, TaskItem) else updated
        raise RuntimeError("Repository does not support update_task")


    def list_subtasks(self, parent_task_id: str) -> list[TaskItem]:
        if not parent_task_id:
            return []
        return [task for task in self.list_tasks() if task.parent_task_id == parent_task_id]

    def can_decompose_task(self, task: TaskItem | None) -> bool:
        if task is None:
            return False
        if task.status in {"done", "cancelled"}:
            return False
        if task.parent_task_id:
            return False
        return True

    def decompose_task(
        self,
        task_id: str,
        *,
        persist: bool = True,
        max_subtasks: int = 6,
        include_existing_description: bool = True,
    ) -> list[TaskItem]:
        task = self.get_task(task_id)
        if task is None:
            raise KeyError(f"Task not found: {task_id}")
        if not self.can_decompose_task(task):
            return []
        if self._slm_service is None:
            raise RuntimeError("SLM service is not configured")

        draft_items = self._slm_service.decompose_task(
            title=task.title,
            description=task.description if include_existing_description else "",
            estimated_minutes=task.estimated_minutes,
            max_subtasks=max_subtasks,
            tags=list(task.tags),
            priority=task.priority,
            energy_cost=task.energy_cost,
        )

        return self.create_subtasks_from_drafts(task.id, draft_items, persist=persist)

    def persist_decomposition(self, task_id: str, created: list[TaskItem]) -> list[TaskItem]:
        task = self.get_task(task_id)
        if task is None:
            raise KeyError(f"Task not found: {task_id}")
        child_ids = [task.id for task in created]
        return self._persist_decomposition(task, created, child_ids)

    def create_subtasks_from_drafts(
        self,
        task_id: str,
        drafts: list[dict[str, Any]],
        *,
        persist: bool = True,
    ) -> list[TaskItem]:
        task = self.get_task(task_id)
        if task is None:
            raise KeyError(f"Task not found: {task_id}")
        if not self.can_decompose_task(task):
            return []

        created = self._build_subtasks(task, drafts)
        if not created:
            return []

        if persist and hasattr(self.repository, "add_task"):
            stored_tasks: list[TaskItem] = []
            for item in created:
                stored = self.repository.add_task(item)
                stored_tasks.append(stored if isinstance(stored, TaskItem) else item)
            created = stored_tasks

        if persist:
            child_ids = [item.id for item in created]
            self._persist_decomposition(task, created, child_ids)

        return created

    def _build_subtasks(self, task: TaskItem, drafts: list[dict[str, Any]]) -> list[TaskItem]:
        created: list[TaskItem] = []
        for item in drafts:
            title = str(item.get("title", "")).strip()
            if not title:
                continue
            created.append(
                TaskItem(
                    id=f"task-{uuid.uuid4().hex[:12]}",
                    title=title,
                    description=str(item.get("description", "") or "").strip(),
                    estimated_minutes=max(5, int(item.get("estimated_minutes", 25) or 25)),
                    due_at=self._normalize_dt(item.get("due_at")) if isinstance(item.get("due_at"), datetime) else None,
                    priority=min(5, max(1, int(item.get("priority", task.priority) or task.priority))),
                    tags=self._dedupe_tags(item.get("tags", list(task.tags)) or list(task.tags)),
                    status="todo",
                    scheduled_start=self._normalize_dt(item.get("scheduled_start")) if isinstance(item.get("scheduled_start"), datetime) else None,
                    scheduled_end=self._normalize_dt(item.get("scheduled_end")) if isinstance(item.get("scheduled_end"), datetime) else None,
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                    completed_at=None,
                    source="slm",
                    energy_cost=min(5, max(1, int(item.get("energy_cost", task.energy_cost) or task.energy_cost))),
                    focus_score_hint=item.get("focus_score_hint"),
                    recurrence_rule=None,
                    parent_task_id=task.id,
                    meta={
                        "generated_by": "task_decomposition",
                        "root_task_id": task.id,
                        "root_task_title": task.title,
                    },
                )
            )

        return created

    def _persist_decomposition(self, task: TaskItem, created: list[TaskItem], child_ids: list[str]) -> list[TaskItem]:
        meta = deepcopy(task.meta or {})
        meta["decomposition_status"] = "completed" if created else "empty"
        meta["decomposition_source"] = "slm"
        meta["child_task_ids"] = child_ids
        meta["last_decomposed_at"] = datetime.utcnow().isoformat()
        self.patch_task(task.id, {"meta": meta})
        return created

    def create_tasks_from_natural_language(
        self,
        text: str,
        *,
        persist: bool = False,
        default_tags: list[str] | None = None,
        parent_task_id: str | None = None,
    ) -> list[TaskItem]:
        raw = (text or "").strip()
        if not raw:
            raise ValueError("Natural language task input is required")
        if self._slm_service is None:
            raise RuntimeError("SLM service is not configured")

        drafts = self._slm_service.extract_tasks_from_text(
            raw,
            default_tags=default_tags,
        )

        created: list[TaskItem] = []
        now = datetime.utcnow()

        for draft in drafts:
            title = str(draft.get("title", "")).strip()
            if not title:
                continue

            payload = self._normalize_payload(
                {
                    "title": title,
                    "description": str(draft.get("description", "") or "").strip(),
                    "estimated_minutes": draft.get("estimated_minutes", 30),
                    "due_at": draft.get("due_at"),
                    "priority": draft.get("priority", 3),
                    "tags": self._dedupe_tags((draft.get("tags") or []) + (default_tags or [])),
                    "status": "todo",
                    "scheduled_start": draft.get("scheduled_start"),
                    "scheduled_end": draft.get("scheduled_end"),
                    "source": "coach_nl",
                    "energy_cost": draft.get("energy_cost", 3),
                    "focus_score_hint": draft.get("focus_score_hint"),
                    "recurrence_rule": draft.get("recurrence_rule"),
                    "parent_task_id": parent_task_id,
                    "meta": {
                        "captured_from": "coach_chat",
                        "raw_text": raw,
                    },
                }
            )

            task = TaskItem(
                id=f"task-{uuid.uuid4().hex[:12]}",
                title=payload["title"],
                description=payload["description"],
                estimated_minutes=payload["estimated_minutes"],
                due_at=payload["due_at"],
                priority=payload["priority"],
                tags=payload["tags"],
                status=payload["status"],
                scheduled_start=payload["scheduled_start"],
                scheduled_end=payload["scheduled_end"],
                created_at=now,
                updated_at=now,
                completed_at=None,
                source=payload["source"],
                energy_cost=payload["energy_cost"],
                focus_score_hint=payload["focus_score_hint"],
                recurrence_rule=payload["recurrence_rule"],
                parent_task_id=payload["parent_task_id"],
                meta=payload["meta"],
            )

            if persist and hasattr(self.repository, "add_task"):
                stored = self.repository.add_task(task)
                created.append(stored if isinstance(stored, TaskItem) else task)
            else:
                created.append(task)

        return created

    def weekly_coach_snapshot(self) -> dict[str, Any]:
        tasks = self.list_tasks()
        active = [t for t in tasks if t.status not in {"done", "cancelled"}]
        completed = [t for t in tasks if t.status == "done"]
        blocked = [t for t in tasks if t.status in {"blocked", "missed"}]
        unscheduled = [t for t in active if t.scheduled_start is None]
        high_priority = [t for t in active if int(t.priority) >= 4]

        return {
            "active_count": len(active),
            "completed_count": len(completed),
            "blocked_count": len(blocked),
            "unscheduled_count": len(unscheduled),
            "high_priority_count": len(high_priority),
            "high_priority_tasks": high_priority[:8],
            "blocked_tasks": blocked[:8],
            "unscheduled_tasks": unscheduled[:8],
        }

    def maybe_auto_decompose_task(self, task: TaskItem) -> None:
        if self._slm_service is None:
            return
        if task.parent_task_id:
            return
        if task.source == "slm":
            return
        meta = task.meta or {}
        if bool(meta.get("slm_decomposed", False)):
            return
        if not self._looks_like_high_level_goal(task):
            return

        created = self.decompose_task(task.id, persist=True)
        if not created:
            return

        meta = deepcopy(task.meta or {})
        meta["slm_decomposed"] = True
        meta["slm_decomposition_count"] = len(created)
        self.patch_task(task.id, {"meta": meta})

    def _looks_like_high_level_goal(self, task: TaskItem) -> bool:
        title = (task.title or "").strip().lower()
        if not title:
            return False
        if len(title.split()) >= 3:
            return True
        keywords = (
            "finish",
            "build",
            "prepare",
            "complete",
            "write",
            "study",
            "plan",
            "organize",
        )
        return any(keyword in title for keyword in keywords)


    def patch_task(self, task_id: str, changes: dict[str, Any]) -> TaskItem:
        existing = self.get_task(task_id)
        if existing is None:
            raise KeyError(f"Task not found: {task_id}")
        payload = {
            "title": existing.title,
            "description": existing.description,
            "estimated_minutes": existing.estimated_minutes,
            "due_at": existing.due_at,
            "priority": existing.priority,
            "tags": list(existing.tags),
            "status": existing.status,
            "scheduled_start": existing.scheduled_start,
            "scheduled_end": existing.scheduled_end,
            "source": existing.source,
            "energy_cost": existing.energy_cost,
            "focus_score_hint": existing.focus_score_hint,
            "recurrence_rule": existing.recurrence_rule,
            "parent_task_id": existing.parent_task_id,
            "meta": deepcopy(existing.meta),
        }
        payload.update(changes)
        normalized = self._normalize_payload(payload)
        updated = replace(
            existing,
            title=normalized["title"],
            description=normalized["description"],
            estimated_minutes=normalized["estimated_minutes"],
            due_at=normalized["due_at"],
            priority=normalized["priority"],
            tags=normalized["tags"],
            status=normalized["status"],
            scheduled_start=normalized["scheduled_start"],
            scheduled_end=normalized["scheduled_end"],
            updated_at=datetime.utcnow(),
            completed_at=self._next_completed_at(existing, normalized["status"]),
            source=normalized["source"],
            energy_cost=normalized["energy_cost"],
            focus_score_hint=normalized["focus_score_hint"],
            recurrence_rule=normalized["recurrence_rule"],
            parent_task_id=normalized["parent_task_id"],
            meta=normalized["meta"],
        )
        if hasattr(self.repository, "update_task"):
            stored = self.repository.update_task(updated)
            return stored if isinstance(stored, TaskItem) else updated
        raise RuntimeError("Repository does not support update_task")

    def set_status(self, task_id: str, status: str) -> TaskItem:
        return self.patch_task(task_id, {"status": status})

    def schedule_task(
        self,
        task_id: str,
        scheduled_start: datetime | None,
        *,
        duration_minutes: int | None = None,
    ) -> TaskItem:
        task = self.get_task(task_id)
        if task is None:
            raise KeyError(f"Task not found: {task_id}")
        duration = max(5, int(duration_minutes or task.estimated_minutes or 30))
        scheduled_end = scheduled_start + timedelta(minutes=duration) if scheduled_start else None
        return self.patch_task(
            task_id,
            {
                "scheduled_start": scheduled_start,
                "scheduled_end": scheduled_end,
            },
        )

    def reschedule_to_day(self, task_id: str, day: date) -> TaskItem:
        target_datetime = datetime.combine(day, datetime.min.time())
        return self.patch_task(
            task_id,
            {
                "scheduled_start": target_datetime,
                "scheduled_end": target_datetime + timedelta(minutes=30),
            },
        )

    def unschedule_task(self, task_id: str) -> TaskItem:
        return self.patch_task(task_id, {"scheduled_start": None, "scheduled_end": None})

    def delete_task(self, task_id: str) -> bool:
        if hasattr(self.repository, "delete_task"):
            return bool(self.repository.delete_task(task_id))
        existing = self.get_task(task_id)
        if existing is None:
            return False
        if hasattr(self.repository, "set_tasks"):
            tasks = [task for task in self.list_tasks() if task.id != task_id]
            self.repository.set_tasks(tasks)
            return True
        raise RuntimeError("Repository does not support delete_task or set_tasks")

    def tasks_for_day(self, day: datetime | date) -> list[TaskItem]:
        if hasattr(self.repository, "tasks_for_day"):
            tasks = list(self.repository.tasks_for_day(day))
            tasks.sort(key=self._sort_key)
            return tasks
        target = day.date() if isinstance(day, datetime) else day
        return [task for task in self.list_tasks() if (dt := task.scheduled_start or task.due_at) is not None and dt.date() == target]

    def upcoming_tasks(self, limit: int = 10) -> list[TaskItem]:
        if hasattr(self.repository, "upcoming_tasks"):
            return list(self.repository.upcoming_tasks(limit=limit))
        active = [task for task in self.list_tasks() if task.status not in {"done", "cancelled"}]
        return active[:limit]

    def parse_quick_add(self, text: str, notes: str = "") -> dict[str, Any]:
        raw = (text or "").strip()
        if not raw:
            raise ValueError("Task text is required")

        working = raw
        tags = [match.group(1).lower() for match in TAG_PATTERN.finditer(working)]
        working = TAG_PATTERN.sub("", working)

        priority = 2
        for token, value in PRIORITY_TOKENS.items():
            if token in working:
                priority = value
                working = working.replace(token, " ")
                break

        estimated_minutes = 30
        minute_match = MINUTE_PATTERN.search(working)
        if minute_match:
            estimated_minutes = max(5, int(minute_match.group(1)))
            working = working.replace(minute_match.group(0), " ")
        else:
            hour_match = HOUR_PATTERN.search(working)
            if hour_match:
                estimated_minutes = max(5, int(float(hour_match.group(1)) * 60))
                working = working.replace(hour_match.group(0), " ")

        due_at = None
        scheduled_start = None
        lower = working.lower()
        base_date = datetime.now().replace(second=0, microsecond=0)

        if "tomorrow" in lower:
            due_at = base_date + timedelta(days=1)
            working = re.sub(r"\btomorrow\b", " ", working, flags=re.IGNORECASE)
        elif "today" in lower:
            due_at = base_date
            working = re.sub(r"\btoday\b", " ", working, flags=re.IGNORECASE)

        time_match = AT_TIME_PATTERN.search(working)
        if time_match:
            hour = int(time_match.group(1))
            minute = int(time_match.group(2) or 0)
            ampm = (time_match.group(3) or "").lower()
            if ampm == "pm" and hour < 12:
                hour += 12
            if ampm == "am" and hour == 12:
                hour = 0
            if hour > 23 or minute > 59:
                raise ValueError("Invalid time in quick add")
            anchor = due_at or base_date
            due_at = anchor.replace(hour=hour, minute=minute, second=0, microsecond=0)
            scheduled_start = due_at
            working = working.replace(time_match.group(0), " ")

        title = " ".join(working.split()).strip(" -–—")
        if not title:
            raise ValueError("Task title could not be parsed")

        description = notes.strip()
        scheduled_end = scheduled_start + timedelta(minutes=estimated_minutes) if scheduled_start else None
        return {
            "title": title,
            "description": description,
            "estimated_minutes": estimated_minutes,
            "due_at": due_at,
            "priority": priority,
            "tags": self._dedupe_tags(tags),
            "status": "todo",
            "scheduled_start": scheduled_start,
            "scheduled_end": scheduled_end,
            "source": "quick_add",
            "energy_cost": 3,
            "focus_score_hint": None,
            "recurrence_rule": None,
            "parent_task_id": None,
            "meta": {"captured_from": "quick_add", "raw_text": raw},
        }

    def create_from_quick_add(self, text: str, notes: str = "") -> TaskItem:
        parsed = self.parse_quick_add(text, notes)
        return self.create_task(**parsed)

    def _normalize_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        title = str(payload.get("title", "")).strip()
        if not title:
            raise ValueError("Task title is required")

        status = str(payload.get("status", "todo")).strip()
        if status not in TASK_STATUSES:
            raise ValueError(f"Unsupported task status: {status}")

        estimated_minutes = max(5, int(payload.get("estimated_minutes", 30) or 30))
        priority = min(5, max(1, int(payload.get("priority", 3) or 3)))
        energy_cost = min(5, max(1, int(payload.get("energy_cost", 3) or 3)))

        due_at = self._normalize_dt(payload.get("due_at"))
        scheduled_start = self._normalize_dt(payload.get("scheduled_start"))
        scheduled_end = self._normalize_dt(payload.get("scheduled_end"))
        if scheduled_start and scheduled_end and scheduled_end <= scheduled_start:
            scheduled_end = scheduled_start + timedelta(minutes=estimated_minutes)
        if scheduled_start and scheduled_end is None:
            scheduled_end = scheduled_start + timedelta(minutes=estimated_minutes)

        tags_value = payload.get("tags", [])
        tags = self._dedupe_tags(tags_value if isinstance(tags_value, list) else list(tags_value))

        meta_value = payload.get("meta") or {}
        meta = deepcopy(meta_value if isinstance(meta_value, dict) else {"value": meta_value})

        return {
            "title": title,
            "description": str(payload.get("description", "") or "").strip(),
            "estimated_minutes": estimated_minutes,
            "due_at": due_at,
            "priority": priority,
            "tags": tags,
            "status": status,
            "scheduled_start": scheduled_start,
            "scheduled_end": scheduled_end,
            "source": str(payload.get("source", "manual") or "manual"),
            "energy_cost": energy_cost,
            "focus_score_hint": payload.get("focus_score_hint"),
            "recurrence_rule": payload.get("recurrence_rule"),
            "parent_task_id": payload.get("parent_task_id"),
            "meta": meta,
        }


    def _normalize_dt(self, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def _sort_anchor(self, task: TaskItem) -> datetime:
        anchor = self._normalize_dt(task.scheduled_start) or self._normalize_dt(task.due_at)
        return anchor or datetime.max

    def _created_anchor(self, task: TaskItem) -> datetime:
        created = self._normalize_dt(task.created_at)
        return created or datetime.min

    def _dedupe_tags(self, tags: list[Any]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for raw in tags:
            tag = str(raw).strip().lstrip("#").lower()
            if not tag or tag in seen:
                continue
            seen.add(tag)
            result.append(tag)
        return result

    def as_datetime(self, value: Any) -> datetime | None:
        if value is None or value == "":
            return None
        if isinstance(value, datetime):
            return self._normalize_dt(value)
        if hasattr(value, "toPython"):
            converted = value.toPython()
            return self._normalize_dt(converted) if isinstance(converted, datetime) else None
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            try:
                return self._normalize_dt(datetime.fromisoformat(text))
            except ValueError:
                pass
            for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
                try:
                    return self._normalize_dt(datetime.strptime(text, fmt))
                except ValueError:
                    continue
        raise ValueError(f"Unsupported datetime value: {value!r}")

    def _next_completed_at(self, existing: TaskItem, next_status: str) -> datetime | None:
        if next_status == "done":
            return existing.completed_at or datetime.utcnow()
        return None

    def _sort_key(self, task: TaskItem) -> tuple[int, datetime, int, datetime]:
        status_rank = 1 if task.status in {"done", "cancelled"} else 0
        return (
            status_rank,
            self._sort_anchor(task),
            -int(task.priority),
            self._created_anchor(task),
        )


def _maybe_decompose_new_goal(self, task: TaskItem) -> None:
    if self._slm_service is None:
        return
    if task.parent_task_id:
        return
    if task.source == "slm":
        return
    meta = task.meta or {}
    if bool(meta.get("slm_decomposed", False)):
        return
    if not self._looks_like_high_level_goal(task):
        return
    created = self._slm_service.decompose_and_persist_goal(task, self.create_task)
    if not created:
        return
    meta["slm_decomposed"] = True
    meta["slm_decomposition_count"] = len(created)
    self.patch_task(task.id, {"meta": meta})

def _looks_like_high_level_goal(self, task: TaskItem) -> bool:
    title = (task.title or "").strip().lower()
    if not title:
        return False
    if len(title.split()) >= 3:
        return True
    keywords = ("finish", "build", "prepare", "complete", "write", "study", "plan", "organize")
    return any(keyword in title for keyword in keywords)
