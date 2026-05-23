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
        import joblib
        import pandas as pd
        import lightgbm as lgb  # ensure lightgbm is imported in context

        if not path.exists():
            logger.warning("Focus model file not found at %s; model disabled.", path)
            self._booster = None
            self._feature_order = []
            return

        try:
            self._booster = joblib.load(str(path))
            if hasattr(self._booster, "feature_names_in_"):
                self._feature_order = list(self._booster.feature_names_in_)
            else:
                self._feature_order = [
                    'is_browser', 'productive_keyword_hit', 'tab_switch_frequency_5m',
                    'app_switch_frequency_5m', 'idle_seconds', 'gaze_present', 'face_present',
                    'absent_seconds_estimate', 'focus_score_window_5m', 'gaze_present_ratio_5m',
                    'face_present_ratio_5m', 'productive_ratio_60m', 'browser_ratio_60m',
                    'distractor_ratio_60m', 'switches_60m_norm', 'idle_mean_5m_norm',
                    'is_coding_window', 'is_terminal_window', 'is_docs_window',
                    'is_distracting_window', 'hour_sin', 'hour_cos', 'yaw_deg', 'pitch_deg',
                    'blink_rate_per_min', 'eye_open_avg', 'gaze_zone', 'tab_fuzzy_match_score',
                    'slm_distraction_class', 'yaw_pitch_variance_5m', 'blink_rate_delta'
                ]
            logger.info("Focus calibrated model loaded with %d features.", len(self._feature_order))
        except Exception as exc:
            logger.error("Failed to load calibrated model from %s: %s", path, exc)
            self._booster = None
            self._feature_order = []

    def predict_proba(self, features: dict[str, Any]) -> float:
        if self._booster is None:
            return self._heuristic_fallback(features)

        try:
            import pandas as pd
            from pandas.api.types import CategoricalDtype
            
            row_dict = {}
            for name in self._feature_order:
                val = features.get(name)
                if name in {'gaze_zone', 'slm_distraction_class'}:
                    row_dict[name] = [str(val) if val is not None else "UNKNOWN"]
                else:
                    row_dict[name] = [float(val) if val is not None else 0.0]
            
            df = pd.DataFrame(row_dict)
            
            gaze_zone_type = CategoricalDtype(
                categories=['ABSENT', 'LOOKING_AWAY', 'ON_SCREEN', 'OFF_SCREEN', 'DEGRADED', 'NOT_CALIBRATED'],
                ordered=False
            )
            slm_type = CategoricalDtype(
                categories=['PRODUCTIVE', 'DISTRACTING', 'NEUTRAL', 'UNKNOWN'],
                ordered=False
            )
            df['gaze_zone'] = df['gaze_zone'].astype(gaze_zone_type)
            df['slm_distraction_class'] = df['slm_distraction_class'].astype(slm_type)
            
            preds = self._booster.predict_proba(df)
            value = float(preds[0, 1]) if preds is not None else 0.5
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

    # Human-readable labels for dashboard tooltip (#7)
    _FEATURE_LABELS: dict[str, str] = {
        "is_browser": "Browser open",
        "productive_keyword_hit": "Productive keyword detected",
        "tab_switch_frequency_5m": "Frequent tab switching",
        "app_switch_frequency_5m": "Frequent app switching",
        "idle_seconds": "Keyboard/mouse idle",
        "gaze_present": "Looking at screen",
        "face_present": "Face detected",
        "absent_seconds_estimate": "Screen absence",
        "gaze_present_ratio_5m": "Gaze presence (5 min)",
        "face_present_ratio_5m": "Face presence (5 min)",
        "productive_ratio_60m": "Productive ratio (1 hr)",
        "browser_ratio_60m": "Browser ratio (1 hr)",
        "distractor_ratio_60m": "Distraction ratio (1 hr)",
        "is_coding_window": "Coding window open",
        "is_terminal_window": "Terminal open",
        "is_docs_window": "Documentation open",
        "is_distracting_window": "Distracting site/app",
        "yaw_deg": "Head yaw angle",
        "pitch_deg": "Head pitch angle",
        "blink_rate_per_min": "Blink rate",
        "tab_fuzzy_match_score": "Tab task relevance",
        "slm_distraction_class": "AI distraction classification",
    }

    def explain_prediction(self, features: dict[str, Any], top_n: int = 3) -> list[tuple[str, float]]:
        """Return the top-N feature contributions as (label, contribution_pct) tuples.

        Uses LightGBM's built-in `pred_contrib` which gives SHAP-like leaf contributions
        without requiring the `shap` library (~10ms overhead per call).

        Returns an empty list if the model is not loaded or pred_contrib fails.
        """
        if self._booster is None:
            # Degraded rule-based fallback explanation
            contribs = []
            
            # Browser open contribution
            is_browser_val = float(features.get("is_browser", 0.0))
            if is_browser_val > 0:
                contribs.append(("is_browser", 0.20 * is_browser_val))
                
            # Distracting window contribution
            is_distracting_val = float(features.get("is_distracting_window", 0.0))
            if is_distracting_val > 0:
                contribs.append(("is_distracting_window", 0.25 * is_distracting_val))
                
            # App switch frequency contribution
            app_switch_val = min(1.0, float(features.get("app_switch_frequency_5m", 0.0)) / 20.0)
            if app_switch_val > 0:
                contribs.append(("app_switch_frequency_5m", 0.20 * app_switch_val))
                
            # Tab switch frequency contribution
            tab_switch_val = min(1.0, float(features.get("tab_switch_frequency_5m", 0.0)) / 30.0)
            if tab_switch_val > 0:
                contribs.append(("tab_switch_frequency_5m", 0.15 * tab_switch_val))
                
            # Gaze absent ratio contribution
            gaze_absent_val = 1.0 - float(features.get("gaze_present_ratio_5m", 1.0))
            if gaze_absent_val > 0:
                contribs.append(("gaze_present_ratio_5m", 0.20 * gaze_absent_val))
                
            # Productive keyword hit contribution
            productive_keyword_val = float(features.get("productive_keyword_hit", 0.0))
            if productive_keyword_val > 0:
                contribs.append(("productive_keyword_hit", -0.25 * productive_keyword_val))
                
            # Gaze present contribution
            gaze_present_val = float(features.get("gaze_present", 0.0))
            if gaze_present_val > 0:
                contribs.append(("gaze_present", -0.20 * gaze_present_val))
                
            if not contribs:
                return [("Baseline Focus", 100.0)]
                
            # Sort by absolute impact descending
            contribs.sort(key=lambda x: abs(x[1]), reverse=True)
            total = sum(abs(v) for _, v in contribs) or 1.0
            
            result = []
            for feat, val in contribs[:top_n]:
                label = self._FEATURE_LABELS.get(feat, feat.replace("_", " ").title())
                pct = round((abs(val) / total) * 100, 1)
                result.append((label, pct))
            return result

        try:
            import pandas as pd
            from pandas.api.types import CategoricalDtype

            # Build the same DataFrame used in predict_proba
            row_dict = {}
            for name in self._feature_order:
                val = features.get(name)
                if name in {"gaze_zone", "slm_distraction_class"}:
                    row_dict[name] = [str(val) if val is not None else "UNKNOWN"]
                else:
                    row_dict[name] = [float(val) if val is not None else 0.0]
            df = pd.DataFrame(row_dict)
            gaze_zone_type = CategoricalDtype(
                categories=["ABSENT", "LOOKING_AWAY", "ON_SCREEN", "OFF_SCREEN", "DEGRADED", "NOT_CALIBRATED"],
                ordered=False,
            )
            slm_type = CategoricalDtype(
                categories=["PRODUCTIVE", "DISTRACTING", "NEUTRAL", "UNKNOWN"],
                ordered=False,
            )
            df["gaze_zone"] = df["gaze_zone"].astype(gaze_zone_type)
            df["slm_distraction_class"] = df["slm_distraction_class"].astype(slm_type)

            # pred_contrib returns shape (n_samples, n_features + 1); last col is bias
            raw_booster = getattr(self._booster, "_Booster", None) or getattr(self._booster, "booster_", None)
            if raw_booster is None:
                # CalibratedClassifierCV wraps a Pipeline — unwrap
                base = getattr(self._booster, "estimator", self._booster)
                raw_booster = getattr(base, "_Booster", None) or getattr(base, "booster_", None)
            if raw_booster is None:
                return []

            import numpy as np
            contrib = raw_booster.predict(df, pred_contrib=True)  # shape (1, n_features+1)
            contribs_row = contrib[0, :-1]  # drop bias term
            # Normalise to percentage contribution of total absolute sum
            total = float(np.abs(contribs_row).sum()) or 1.0
            pairs = list(zip(self._feature_order, contribs_row.tolist()))
            # Sort by absolute value descending; take top N
            pairs.sort(key=lambda x: abs(x[1]), reverse=True)
            result = []
            for feat, val in pairs[:top_n]:
                label = self._FEATURE_LABELS.get(feat, feat.replace("_", " ").title())
                pct = round((abs(val) / total) * 100, 1)
                result.append((label, pct))
            return result
        except Exception as exc:
            logger.debug("explain_prediction failed: %s", exc)
            return []