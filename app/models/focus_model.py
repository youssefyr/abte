from __future__ import annotations

from pathlib import Path
from typing import Any
import logging

logger = logging.getLogger(__name__)


class FocusLightGBMModel:
    def __init__(self) -> None:
        self._booster: Any | None = None
        self._feature_order: list[str] = []

    @property
    def is_loaded(self) -> bool:
        return self._booster is not None

    @property
    def feature_order(self) -> list[str]:
        return list(self._feature_order)

    def load_model(self, path: Path) -> None:
        
        import lightgbm as lgb
        

        if not path.exists():
            logger.warning("Focus model file not found at %s; model disabled.", path)
            self._booster = None
            self._feature_order = []
            return

        self._booster = lgb.Booster(model_file=str(path))
        self._feature_order = list(self._booster.feature_name())
        logger.info("Focus model loaded with %d features.", len(self._feature_order))

    def predict_proba(self, features: dict[str, Any]) -> float:
        if self._booster is None:
            return self._heuristic_fallback(features)

        try:
            row = [float(features.get(name, 0.0) or 0.0) for name in self._feature_order]
            pred = self._booster.predict([row])
            value = float(pred[0]) if pred is not None else 0.5
            return max(0.0, min(1.0, value))
        except Exception as exc:
            logger.warning("Focus model prediction failed; using fallback: %s", exc)
            return self._heuristic_fallback(features)

    def _heuristic_fallback(self, features: dict[str, Any]) -> float:
        score = 0.35
        score += 0.20 * float(features.get("is_browser", 0.0))
        score += 0.25 * float(features.get("is_distracting_window", 0.0))
        score += 0.20 * min(1.0, float(features.get("app_switch_frequency_5m", 0.0)) / 20.0)
        score += 0.15 * min(1.0, float(features.get("tab_switch_frequency_5m", 0.0)) / 30.0)
        score += 0.20 * (1.0 - float(features.get("gaze_present_ratio_5m", 1.0)))
        score -= 0.25 * float(features.get("productive_keyword_hit", 0.0))
        score -= 0.20 * float(features.get("gaze_present", 0.0))
        return max(0.0, min(1.0, score))