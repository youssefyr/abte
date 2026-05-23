from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
import uuid


@dataclass(slots=True)
class MinuteBucket:
    started_at: datetime
    ended_at: datetime
    p_drift_mean: float
    sample_count: int
    session_id: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class _OpenBucket:
    minute_key: datetime
    started_at: datetime
    sum_p: float = 0.0
    sample_count: int = 0
    session_id: str | None = None

    def add(self, p: float) -> None:
        self.sum_p += float(p)
        self.sample_count += 1

    def close(self, ended_at: datetime) -> MinuteBucket | None:
        if self.sample_count <= 0:
            return None
        return MinuteBucket(
            started_at=self.started_at,
            ended_at=ended_at,
            p_drift_mean=max(0.0, min(1.0, self.sum_p / self.sample_count)),
            sample_count=self.sample_count,
            session_id=self.session_id,
            meta={},
        )

@dataclass(slots=True)
class LiveFocusSnapshot:
    """
    Emitted every model tick. smoothed_score is the only value the UI should display.
    raw_score is kept for logging/debugging.
    """
    raw_score: float          # 0.0–1.0 as returned directly by the model
    smoothed_score: float     # EWMA over the last ~60 min of ticks, 0–100 int-rounded
    state_label: str          # e.g. "focused", "distracted", "absent", "idle"
    window_title: str
    process_name: str
    gaze_present: bool
    tick_index: int           # monotonic counter for debugging
    context: dict[str, Any] = field(default_factory=dict)



class FocusSmoother:
    def __init__(self, max_minutes: int = 60, ema_alpha: float | None = 0.15, bucket_minutes: int | None = None) -> None:
        if bucket_minutes is not None:
            max_minutes = bucket_minutes
        self._max_minutes = max(1, max_minutes)
        self._ema_alpha = ema_alpha
        self._minute_buckets: deque[MinuteBucket] = deque(maxlen=self._max_minutes)
        self._open_bucket: _OpenBucket | None = None
        self._ema_focus: float | None = None

        self._session_bucket_sum = 0.0
        self._session_bucket_count = 0
        self._current_session_id: str | None = None

    def start_session(self, session_id: str | None) -> None:
        self._current_session_id = session_id
        self._session_bucket_sum = 0.0
        self._session_bucket_count = 0
        if self._open_bucket is not None:
            self._open_bucket.session_id = session_id

    def end_session(self, now: datetime | None = None) -> float | None:
        if now is None:
            now = datetime.now(timezone.utc)
        closed = self.flush(now)
        if closed and closed.session_id == self._current_session_id:
            self._session_bucket_sum += (1.0 - closed.p_drift_mean)
            self._session_bucket_count += 1

        value = None
        if self._session_bucket_count > 0:
            value = self._session_bucket_sum / float(self._session_bucket_count)

        self._current_session_id = None
        self._session_bucket_sum = 0.0
        self._session_bucket_count = 0
        return value

    def update(self, raw_p: float | None = None, now: datetime | None = None, p_drift: float | None = None) -> MinuteBucket | None:
        if raw_p is None and p_drift is not None:
            raw_p = p_drift
        if raw_p is None:
            raw_p = 0.5
        if now is None:
            now = datetime.now(timezone.utc)
        minute_key = now.replace(second=0, microsecond=0)

        if self._open_bucket is None:
            self._open_bucket = _OpenBucket(
                minute_key=minute_key,
                started_at=minute_key,
                session_id=self._current_session_id,
            )

        if self._open_bucket.minute_key != minute_key:
            closed = self._open_bucket.close(ended_at=minute_key)
            if closed is not None:
                self._minute_buckets.append(closed)
                if closed.session_id == self._current_session_id:
                    self._session_bucket_sum += (1.0 - closed.p_drift_mean)
                    self._session_bucket_count += 1
            self._open_bucket = _OpenBucket(
                minute_key=minute_key,
                started_at=minute_key,
                session_id=self._current_session_id,
            )
            self._open_bucket.add(raw_p)
            self._update_ema()
            return closed

        self._open_bucket.add(raw_p)
        self._update_ema()
        return None

    def flush(self, now: datetime) -> MinuteBucket | None:
        if self._open_bucket is None:
            return None
        ended_at = now.replace(second=0, microsecond=0)
        if ended_at <= self._open_bucket.started_at:
            ended_at = now
        closed = self._open_bucket.close(ended_at=ended_at)
        self._open_bucket = None
        if closed is not None:
            self._minute_buckets.append(closed)
            self._update_ema()
        return closed

    def current_focus_score(self) -> float:
        if not self._minute_buckets and self._open_bucket is None:
            return 0.5

        values = [1.0 - bucket.p_drift_mean for bucket in self._minute_buckets]
        if self._open_bucket is not None and self._open_bucket.sample_count > 0:
            current_mean = self._open_bucket.sum_p / float(self._open_bucket.sample_count)
            values.append(1.0 - current_mean)

        base = sum(values) / float(len(values)) if values else 0.5
        if self._ema_alpha is None:
            return max(0.0, min(1.0, base))
        if self._ema_focus is None:
            return max(0.0, min(1.0, base))
        return max(0.0, min(1.0, self._ema_focus))

    def current_drift_mean(self) -> float:
        return 1.0 - self.current_focus_score()

    def recent_buckets(self) -> list[MinuteBucket]:
        return list(self._minute_buckets)

    def _update_ema(self) -> None:
        if self._ema_alpha is None:
            return
        raw_focus = self._raw_focus_score()
        if self._ema_focus is None:
            self._ema_focus = raw_focus
            return
        alpha = self._ema_alpha
        self._ema_focus = (alpha * raw_focus) + ((1.0 - alpha) * self._ema_focus)

    def _raw_focus_score(self) -> float:
        values = [1.0 - bucket.p_drift_mean for bucket in self._minute_buckets]
        if self._open_bucket is not None and self._open_bucket.sample_count > 0:
            current_mean = self._open_bucket.sum_p / float(self._open_bucket.sample_count)
            values.append(1.0 - current_mean)
        if not values:
            return 0.5
        return max(0.0, min(1.0, sum(values) / float(len(values))))

    def make_tick_id(self) -> str:
        return f"ftick-{uuid.uuid4().hex[:12]}"