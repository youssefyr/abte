from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
import math


@dataclass(slots=True)
class FocusObservation:
    timestamp: datetime
    process: str
    title: str
    is_browser: bool
    productive_keyword_hit: bool
    tab_switch_frequency_5m: float
    app_switch_frequency_5m: float
    idle_seconds: float
    gaze_present: bool
    face_present: bool
    absent_seconds_estimate: float
    focus_score_window_5m: float
    process_tags: str = ""


class FocusFeatureExtractor:
    def __init__(self, history_minutes: int = 60) -> None:
        self._history = deque(maxlen=history_minutes * 120)

    def record(self, observation: FocusObservation) -> None:
        self._history.append(observation)

    def build_features(self, observation: FocusObservation) -> dict[str, float]:
        self._prune(observation.timestamp)
        recent_5m = [x for x in self._history if x.timestamp >= observation.timestamp - timedelta(minutes=5)]
        recent_60m = [x for x in self._history if x.timestamp >= observation.timestamp - timedelta(minutes=60)]

        gaze_ratio_5m = self._mean_bool(recent_5m, "gaze_present")
        face_ratio_5m = self._mean_bool(recent_5m, "face_present")
        productive_ratio_60m = self._mean_bool(recent_60m, "productive_keyword_hit")
        browser_ratio_60m = self._mean_bool(recent_60m, "is_browser")
        distractor_ratio_60m = self._distractor_ratio(recent_60m)
        switches_60m = self._sum_attr(recent_60m, "app_switch_frequency_5m") / max(1.0, len(recent_60m))
        idle_mean_5m = self._mean_attr(recent_5m, "idle_seconds")

        process_l = (observation.process or "").lower()
        title_l = (observation.title or "").lower()
        tags_l = (observation.process_tags or "").lower()

        is_coding = 1.0 if any(k in process_l for k in ("code", "pycharm", "idea", "vim", "nvim")) else 0.0
        is_terminal = 1.0 if any(k in process_l for k in ("terminal", "wezterm", "kitty", "alacritty", "konsole")) else 0.0
        is_docs = 1.0 if any(k in title_l for k in ("docs", "spec", "design", "ticket", "jira", "notion")) else 0.0
        is_distracting_window = 1.0 if self._looks_distracting(process_l, title_l, tags_l) else 0.0

        return {
            "is_browser": 1.0 if observation.is_browser else 0.0,
            "productive_keyword_hit": 1.0 if observation.productive_keyword_hit else 0.0,
            "tab_switch_frequency_5m": float(observation.tab_switch_frequency_5m),
            "app_switch_frequency_5m": float(observation.app_switch_frequency_5m),
            "idle_seconds": float(observation.idle_seconds),
            "gaze_present": 1.0 if observation.gaze_present else 0.0,
            "face_present": 1.0 if observation.face_present else 0.0,
            "absent_seconds_estimate": float(observation.absent_seconds_estimate),
            "focus_score_window_5m": float(observation.focus_score_window_5m),
            "gaze_present_ratio_5m": gaze_ratio_5m,
            "face_present_ratio_5m": face_ratio_5m,
            "productive_ratio_60m": productive_ratio_60m,
            "browser_ratio_60m": browser_ratio_60m,
            "distractor_ratio_60m": distractor_ratio_60m,
            "switches_60m_norm": min(1.0, switches_60m / 20.0),
            "idle_mean_5m_norm": min(1.0, idle_mean_5m / 120.0),
            "is_coding_window": is_coding,
            "is_terminal_window": is_terminal,
            "is_docs_window": is_docs,
            "is_distracting_window": is_distracting_window,
            "hour_sin": math.sin((observation.timestamp.hour / 24.0) * 2.0 * math.pi),
            "hour_cos": math.cos((observation.timestamp.hour / 24.0) * 2.0 * math.pi),
        }

    def _prune(self, now: datetime) -> None:
        cutoff = now - timedelta(minutes=60)
        while self._history and self._history[0].timestamp < cutoff:
            self._history.popleft()

    @staticmethod
    def _mean_bool(items: list[FocusObservation], attr: str) -> float:
        if not items:
            return 0.0
        return sum(1.0 for item in items if bool(getattr(item, attr, False))) / float(len(items))

    @staticmethod
    def _mean_attr(items: list[FocusObservation], attr: str) -> float:
        if not items:
            return 0.0
        return sum(float(getattr(item, attr, 0.0) or 0.0) for item in items) / float(len(items))

    @staticmethod
    def _sum_attr(items: list[FocusObservation], attr: str) -> float:
        return sum(float(getattr(item, attr, 0.0) or 0.0) for item in items)

    @staticmethod
    def _looks_distracting(process: str, title: str, tags: str) -> bool:
        text = " ".join((process, title, tags)).lower()
        keywords = (
            "youtube", "netflix", "tiktok", "instagram", "x.com", "twitter",
            "reddit", "discord", "twitch", "steam", "game", "spotify"
        )
        return any(k in text for k in keywords)

    def _distractor_ratio(self, items: list[FocusObservation]) -> float:
        if not items:
            return 0.0
        hits = sum(1.0 for item in items if self._looks_distracting(item.process, item.title, item.process_tags))
        return hits / float(len(items))