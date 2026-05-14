from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
import json
import re


_JSON_BLOCK_PATTERN = re.compile(r"\{.*\}|\[.*\]", re.DOTALL)
_BULLET_SPLIT_PATTERN = re.compile(r"(?:^|\n)\s*(?:[-*•]|\d+\.)\s+", re.MULTILINE)


def parse_json_list(text: str) -> list[dict[str, Any]]:
    payload = text.strip()
    if not payload:
        return []

    try:
        parsed = json.loads(payload)
        if isinstance(parsed, list):
            return [item for item in parsed if isinstance(item, dict)]
        if isinstance(parsed, dict):
            if isinstance(parsed.get("tasks"), list):
                return [item for item in parsed["tasks"] if isinstance(item, dict)]
            if isinstance(parsed.get("items"), list):
                return [item for item in parsed["items"] if isinstance(item, dict)]
            return [parsed]
    except Exception:
        pass

    match = _JSON_BLOCK_PATTERN.search(payload)
    if match:
        try:
            parsed = json.loads(match.group(0))
            if isinstance(parsed, list):
                return [item for item in parsed if isinstance(item, dict)]
            if isinstance(parsed, dict):
                if isinstance(parsed.get("tasks"), list):
                    return [item for item in parsed["tasks"] if isinstance(item, dict)]
                if isinstance(parsed.get("items"), list):
                    return [item for item in parsed["items"] if isinstance(item, dict)]
                return [parsed]
        except Exception:
            return []

    return []


def normalize_task_draft(item: dict[str, Any]) -> dict[str, Any]:
    title = str(item.get("title", "") or "").strip()
    description = str(item.get("description", "") or "").strip()

    try:
        estimated_minutes = max(5, min(240, int(item.get("estimated_minutes", 30) or 30)))
    except Exception:
        estimated_minutes = 30

    try:
        priority = min(5, max(1, int(item.get("priority", 3) or 3)))
    except Exception:
        priority = 3

    try:
        energy_cost = min(5, max(1, int(item.get("energy_cost", 3) or 3)))
    except Exception:
        energy_cost = 3

    raw_tags = item.get("tags", [])
    tags: list[str] = []
    if isinstance(raw_tags, list):
        for raw in raw_tags:
            tag = str(raw).strip().lstrip("#").lower()
            if tag and tag not in tags:
                tags.append(tag)

    return {
        "title": title,
        "description": description,
        "estimated_minutes": estimated_minutes,
        "priority": priority,
        "tags": tags,
        "energy_cost": energy_cost,
        "due_at": item.get("due_at"),
        "scheduled_start": item.get("scheduled_start"),
        "scheduled_end": item.get("scheduled_end"),
        "focus_score_hint": item.get("focus_score_hint"),
        "recurrence_rule": item.get("recurrence_rule"),
    }


def fallback_decompose_task(
    *,
    title: str,
    description: str,
    estimated_minutes: int,
    max_subtasks: int,
    tags: list[str],
    priority: int,
    energy_cost: int,
) -> list[dict[str, Any]]:
    target_count = max(1, min(max_subtasks, 4))
    base = max(10, estimated_minutes // target_count)
    verbs = [
        f"Clarify scope for {title}",
        f"Prepare materials for {title}",
        f"Execute core work for {title}",
        f"Review and finalize {title}",
    ]
    results: list[dict[str, Any]] = []
    for idx, item_title in enumerate(verbs[:target_count]):
        results.append(
            {
                "title": item_title,
                "description": description if idx == 0 else "",
                "estimated_minutes": base,
                "priority": min(5, max(1, int(priority or 3))),
                "tags": list(tags),
                "energy_cost": min(5, max(1, int(energy_cost or 3))),
                "due_at": None,
                "scheduled_start": None,
                "scheduled_end": None,
                "focus_score_hint": None,
                "recurrence_rule": None,
            }
        )
    return results


def fallback_extract_tasks_from_text(
    text: str,
    default_tags: list[str],
) -> list[dict[str, Any]]:
    chunks = [part.strip(" -–—\t\r\n") for part in _BULLET_SPLIT_PATTERN.split(text) if part.strip()]
    if len(chunks) <= 1:
        chunks = [
            part.strip(" -–—\t\r\n")
            for part in re.split(r"\b(?:and|then|also|,)\b", text)
            if part.strip()
        ]
    if not chunks:
        chunks = [text.strip()]

    drafts: list[dict[str, Any]] = []
    now = datetime.now().replace(second=0, microsecond=0)

    for chunk in chunks[:8]:
        title = clean_title(chunk)
        if not title:
            continue

        due_at = None
        lowered = chunk.lower()

        if "tomorrow" in lowered:
            due_at = now + timedelta(days=1)
        elif "today" in lowered:
            due_at = now

        drafts.append(
            {
                "title": title,
                "description": "",
                "estimated_minutes": estimate_minutes_from_text(chunk),
                "priority": 4 if "urgent" in lowered else 3,
                "tags": list(default_tags),
                "energy_cost": 3,
                "due_at": due_at,
                "scheduled_start": None,
                "scheduled_end": None,
                "focus_score_hint": None,
                "recurrence_rule": None,
            }
        )

    return drafts


def clean_title(text: str) -> str:
    title = re.sub(r"\s+", " ", text).strip(" -–—.,")
    return title[:160]


def estimate_minutes_from_text(text: str) -> int:
    minute_match = re.search(r"\b(\d+)\s*(m|min|mins|minute|minutes)\b", text, re.IGNORECASE)
    if minute_match:
        return max(5, int(minute_match.group(1)))

    hour_match = re.search(r"\b(\d+(?:\.\d+)?)\s*(h|hr|hrs|hour|hours)\b", text, re.IGNORECASE)
    if hour_match:
        return max(5, int(float(hour_match.group(1)) * 60))

    return 30