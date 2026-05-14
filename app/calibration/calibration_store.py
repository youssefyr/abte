from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class CalibrationStore:
    """
    Persists calibration data per screen fingerprint.
    Stores natural gaze baseline and polynomial mapper coefficients.
    """

    def __init__(self, data_dir: Path) -> None:
        self._path = data_dir / "gaze_calibration.json"
        self._data: dict = self._load()

    def _load(self) -> dict:
        if not self._path.exists():
            return {}
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning(f"Failed to load calibration store: {exc}")
            return {}

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.error(f"Failed to save calibration store: {exc}")

    def save_natural_gaze(
        self,
        screen_id: str,
        neutral_iris_x: float,
        neutral_iris_y: float,
        neutral_yaw: float,
        neutral_pitch: float,
    ) -> None:
        if screen_id not in self._data:
            self._data[screen_id] = {}
        self._data[screen_id]["natural_gaze"] = {
            "neutral_iris_x": neutral_iris_x,
            "neutral_iris_y": neutral_iris_y,
            "neutral_yaw": neutral_yaw,
            "neutral_pitch": neutral_pitch,
        }
        self._save()

    def load_natural_gaze(self, screen_id: str) -> Optional[dict]:
        return self._data.get(screen_id, {}).get("natural_gaze", None)

    def save_mapper_coefficients(self, screen_id: str, coefficients: dict) -> None:
        if screen_id not in self._data:
            self._data[screen_id] = {}
        self._data[screen_id]["mapper_coefficients"] = coefficients
        self._save()

    def load_mapper_coefficients(self, screen_id: str) -> Optional[dict]:
        return self._data.get(screen_id, {}).get("mapper_coefficients", None)

    def is_calibrated(self, screen_id: str) -> bool:
        return bool(
            self._data.get(screen_id, {}).get("natural_gaze")
            and self._data.get(screen_id, {}).get("mapper_coefficients")
        )

    def all_screen_ids(self) -> list[str]:
        return list(self._data.keys())