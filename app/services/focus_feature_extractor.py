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
    productive_keyword_hit: bool = False
    tab_switch_frequency_5m: float = 0.0
    app_switch_frequency_5m: float = 0.0
    idle_seconds: float = 0.0
    gaze_present: bool = True
    face_present: bool = True
    absent_seconds_estimate: float = 0.0
    focus_score_window_5m: float = 0.5
    process_tags: str = ""
    # New features
    yaw_deg: float = 0.0
    pitch_deg: float = 0.0
    blink_rate_per_min: float = 18.0
    eye_open_avg: float = 0.8
    gaze_zone: str = "ABSENT"
    tab_fuzzy_match_score: float = 100.0
    slm_distraction_class: str = "NEUTRAL"


@dataclass
class GazeObservation(FocusObservation):
    is_coding_window: bool = False
    is_terminal_window: bool = False
    is_docs_window: bool = False
    is_distracting_window: bool = False
    tab_title: str | None = None
    tab_url: str | None = None


# Expanded distracting keyword list — covers social, entertainment, gaming, messaging.
# Extend user_extra_distracting_keywords at runtime (e.g., from user settings) without
# touching source code.
_DISTRACTING_KEYWORDS: frozenset[str] = frozenset({
    # Video / streaming
    "youtube", "netflix", "twitch", "tiktok", "hulu", "disneyplus", "hbomax",
    "primevideo", "crunchyroll", "vimeo", "dailymotion",
    # Social media
    "instagram", "x.com", "twitter", "facebook", "fb.com", "snapchat", "pinterest",
    "reddit", "tumblr", "linkedin.com/feed", "threads.net",
    # Messaging / chat
    "discord", "whatsapp", "telegram", "messenger", "slack.com/client", "teams.microsoft.com",
    # Gaming
    "steam", "epicgames", "gog.com", "itch.io", "roblox", "minecraft", "fortnite",
    "leagueoflegends", "valorant", "game",
    # Music when not productivity-tagged
    "spotify", "soundcloud", "deezer",
    # Misc
    "buzzfeed", "9gag", "boredpanda",
})
# Runtime-configurable extension (populated by settings, not source code)
user_extra_distracting_keywords: set[str] = set()

class FocusFeatureExtractor:
    def __init__(self, history_minutes: int = 60) -> None:
        self._history = deque(maxlen=history_minutes * 120)
        self.user_extra_distracting_keywords: set[str] = set()

    def record(self, observation: FocusObservation) -> None:
        self._history.append(observation)

    def build_features(self, observation: FocusObservation) -> dict[str, Any]:
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

        # Physical restlessness (head movement variance)
        yaws = [x.yaw_deg for x in recent_5m if x.yaw_deg is not None]
        pitches = [x.pitch_deg for x in recent_5m if x.pitch_deg is not None]
        if len(yaws) >= 2 and len(pitches) >= 2:
            import numpy as np
            yaw_pitch_variance_5m = float(np.var(yaws) + np.var(pitches))
        else:
            yaw_pitch_variance_5m = 0.0

        blink_rate_delta = float(observation.blink_rate_per_min - 18.0)

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
            # New features
            "yaw_deg": float(observation.yaw_deg),
            "pitch_deg": float(observation.pitch_deg),
            "blink_rate_per_min": float(observation.blink_rate_per_min),
            "eye_open_avg": float(observation.eye_open_avg),
            "gaze_zone": str(observation.gaze_zone),
            "tab_fuzzy_match_score": float(observation.tab_fuzzy_match_score),
            "slm_distraction_class": str(observation.slm_distraction_class),
            "yaw_pitch_variance_5m": float(yaw_pitch_variance_5m),
            "blink_rate_delta": float(blink_rate_delta),
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

    def _looks_distracting(self, process: str, title: str, tags: str) -> bool:
        text = " ".join((process, title, tags)).lower()
        extra = set(self.user_extra_distracting_keywords) if hasattr(self, "user_extra_distracting_keywords") else set()
        all_keywords = _DISTRACTING_KEYWORDS | extra | user_extra_distracting_keywords
        return any(k in text for k in all_keywords)

    def _distractor_ratio(self, items: list[FocusObservation]) -> float:
        if not items:
            return 0.0
        hits = sum(1.0 for item in items if self._looks_distracting(item.process, item.title, item.process_tags))
        return hits / float(len(items))