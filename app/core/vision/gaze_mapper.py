from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import PolynomialFeatures

logger = logging.getLogger(__name__)


@dataclass
class GazePoint:
    x_norm: float = 0.5   # 0.0 = left edge, 1.0 = right edge of target screen
    y_norm: float = 0.5   # 0.0 = top edge, 1.0 = bottom edge
    x_px: int = 0
    y_px: int = 0
    screen_id: str = ""
    confidence: float = 0.0
    calibration_noise: float = 0.0


class PolynomialGazeMapper:
    """
    Maps raw iris features (iris_x_avg, iris_y_avg, yaw, pitch) to screen
    normalised gaze coordinates using 2nd-degree polynomial regression.
    A Kalman filter smooths predictions.
    Maintains one regressor pair per screen_id.
    """

    POLY_DEGREE = 2
    RIDGE_ALPHA = 0.001

    def __init__(self) -> None:
        self._x_pipelines: dict[str, Pipeline] = {}
        self._y_pipelines: dict[str, Pipeline] = {}
        self._is_calibrated: dict[str, bool] = {}

        # Kalman filter state: [x, y, vx, vy]
        self._kf_state = np.array([0.5, 0.5, 0.0, 0.0], dtype=np.float64)
        self._kf_cov = np.eye(4) * 0.1
        dt = 1.0 / 10.0  # 10 FPS nominal
        self._kf_F = np.array([
            [1, 0, dt, 0],
            [0, 1, 0, dt],
            [0, 0, 1, 0],
            [0, 0, 0, 1],
        ], dtype=np.float64)
        self._kf_H = np.array([[1, 0, 0, 0], [0, 1, 0, 0]], dtype=np.float64)
        self._kf_Q = np.eye(4) * 0.002
        self._kf_R = np.eye(2) * 0.01

    def fit(
        self,
        screen_id: str,
        iris_xs: list[float],
        iris_ys: list[float],
        yaws: list[float],
        pitches: list[float],
        screen_xs_norm: list[float],
        screen_ys_norm: list[float],
    ) -> dict:
        """
        Fits polynomial regressors from collected calibration pairs.
        Returns dict with RMSE metrics.
        """
        if len(iris_xs) < 4:
            raise ValueError(f"Need at least 4 calibration points, got {len(iris_xs)}")

        X = np.column_stack([iris_xs, iris_ys, yaws, pitches]).astype(np.float64)
        y_x = np.array(screen_xs_norm, dtype=np.float64)
        y_y = np.array(screen_ys_norm, dtype=np.float64)

        pipe_x = Pipeline([
            ("poly", PolynomialFeatures(degree=self.POLY_DEGREE, include_bias=True)),
            ("ridge", Ridge(alpha=self.RIDGE_ALPHA)),
        ])
        pipe_y = Pipeline([
            ("poly", PolynomialFeatures(degree=self.POLY_DEGREE, include_bias=True)),
            ("ridge", Ridge(alpha=self.RIDGE_ALPHA)),
        ])

        pipe_x.fit(X, y_x)
        pipe_y.fit(X, y_y)

        self._x_pipelines[screen_id] = pipe_x
        self._y_pipelines[screen_id] = pipe_y
        self._is_calibrated[screen_id] = True

        # Compute RMSE on training set (calibration validation)
        pred_x = np.clip(pipe_x.predict(X), 0.0, 1.0)
        pred_y = np.clip(pipe_y.predict(X), 0.0, 1.0)
        rmse_x = float(np.sqrt(np.mean((pred_x - y_x) ** 2)))
        rmse_y = float(np.sqrt(np.mean((pred_y - y_y) ** 2)))

        logger.info(f"Gaze mapper fitted for {screen_id}: RMSE_x={rmse_x:.4f}, RMSE_y={rmse_y:.4f}")
        return {"rmse_x": rmse_x, "rmse_y": rmse_y, "n_points": len(iris_xs)}

    def predict(
        self,
        screen_id: str,
        iris_x: float,
        iris_y: float,
        yaw: float,
        pitch: float,
        screen_w: int,
        screen_h: int,
        iris_confidence: float = 1.0,
    ) -> GazePoint:
        if not self._is_calibrated.get(screen_id, False):
            return GazePoint(x_norm=0.5, y_norm=0.5, x_px=screen_w // 2, y_px=screen_h // 2, screen_id=screen_id)

        X = np.array([[iris_x, iris_y, yaw, pitch]], dtype=np.float64)
        raw_x = float(np.clip(self._x_pipelines[screen_id].predict(X)[0], 0.0, 1.0))
        raw_y = float(np.clip(self._y_pipelines[screen_id].predict(X)[0], 0.0, 1.0))

        # Kalman filter
        self._kf_state, self._kf_cov = self._kf_predict(self._kf_state, self._kf_cov)
        # Scale noise by inverse confidence: low confidence → trust prediction more than measurement
        r_scale = 1.0 / (iris_confidence + 0.1)
        R_dynamic = self._kf_R * r_scale
        z = np.array([raw_x, raw_y], dtype=np.float64)
        self._kf_state, self._kf_cov = self._kf_update(self._kf_state, self._kf_cov, z, R_dynamic)

        sx = float(np.clip(self._kf_state[0], 0.0, 1.0))
        sy = float(np.clip(self._kf_state[1], 0.0, 1.0))

        # Calculate distance between raw and smoothed prediction
        noise = float(np.sqrt((raw_x - sx)**2 + (raw_y - sy)**2))

        return GazePoint(
            x_norm=sx,
            y_norm=sy,
            x_px=int(sx * screen_w),
            y_px=int(sy * screen_h),
            screen_id=screen_id,
            confidence=iris_confidence,
            calibration_noise=noise,
        )

    def is_calibrated(self, screen_id: str) -> bool:
        return bool(self._is_calibrated.get(screen_id, False))

    def reset_kalman(self) -> None:
        self._kf_state = np.array([0.5, 0.5, 0.0, 0.0], dtype=np.float64)
        self._kf_cov = np.eye(4) * 0.1

    def _kf_predict(self, state: np.ndarray, cov: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        state_p = self._kf_F @ state
        cov_p = self._kf_F @ cov @ self._kf_F.T + self._kf_Q
        return state_p, cov_p

    def _kf_update(
        self,
        state: np.ndarray,
        cov: np.ndarray,
        z: np.ndarray,
        R: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        S = self._kf_H @ cov @ self._kf_H.T + R
        K = cov @ self._kf_H.T @ np.linalg.inv(S)
        state_u = state + K @ (z - self._kf_H @ state)
        cov_u = (np.eye(4) - K @ self._kf_H) @ cov
        return state_u, cov_u

    def export_coefficients(self, screen_id: str) -> dict:
        if not self._is_calibrated.get(screen_id, False):
            return {}
        import joblib, io
        buf_x, buf_y = io.BytesIO(), io.BytesIO()
        joblib.dump(self._x_pipelines[screen_id], buf_x)
        joblib.dump(self._y_pipelines[screen_id], buf_y)
        import base64
        return {
            "pipe_x_b64": base64.b64encode(buf_x.getvalue()).decode(),
            "pipe_y_b64": base64.b64encode(buf_y.getvalue()).decode(),
        }

    def import_coefficients(self, screen_id: str, data: dict) -> None:
        import joblib, io, base64
        self._x_pipelines[screen_id] = joblib.load(io.BytesIO(base64.b64decode(data["pipe_x_b64"])))
        self._y_pipelines[screen_id] = joblib.load(io.BytesIO(base64.b64decode(data["pipe_y_b64"])))
        self._is_calibrated[screen_id] = True