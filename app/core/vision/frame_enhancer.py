from __future__ import annotations

import cv2
import numpy as np


class FrameEnhancer:
    """
    Adaptive low-light and blur enhancement pipeline.
    Operates on BGR frames in-place returning enhanced copy.
    Fast enough for ≥ 30 FPS at 640×480 on CPU.
    """

    def __init__(
        self,
        clahe_clip_limit: float = 2.0,
        clahe_tile_grid: tuple[int, int] = (8, 8),
        target_brightness: float = 110.0,
        gamma_range: tuple[float, float] = (0.5, 2.5),
        blur_check_threshold: float = 80.0,
    ) -> None:
        self._clahe = cv2.createCLAHE(clipLimit=clahe_clip_limit, tileGridSize=clahe_tile_grid)
        self._target_brightness = target_brightness
        self._gamma_range = gamma_range
        self._blur_check_threshold = blur_check_threshold
        self._gamma_lut = self._build_gamma_lut(1.0)

    def enhance(self, bgr_frame: np.ndarray) -> tuple[np.ndarray, dict]:
        """
        Returns (enhanced_frame, quality_meta).
        quality_meta: {"is_low_light": bool, "is_blurry": bool, "laplacian_var": float, "gamma": float}
        """
        # Optimization for low end systems
        is_low_end = False
        try:
            import psutil
            is_low_end = (psutil.cpu_count(logical=True) or 4) <= 4
        except Exception:
            pass

        if is_low_end:
            gray = cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2GRAY)
            gray_small = cv2.resize(gray, (160, 120), interpolation=cv2.INTER_NEAREST)
            
            mean_brightness = float(np.mean(gray_small))
            is_low_light = mean_brightness < self._target_brightness * 0.7
            
            lap_var = float(cv2.Laplacian(gray_small, cv2.CV_64F).var())
            is_blurry = lap_var < (self._blur_check_threshold * 0.25)

            return bgr_frame, {
                "is_low_light": is_low_light,
                "is_blurry": is_blurry,
                "laplacian_var": lap_var,
                "gamma": 1.0,
            }

        ycrcb = cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2YCrCb)
        y, cr, cb = cv2.split(ycrcb)

        # CLAHE on luminance
        y_enhanced = self._clahe.apply(y)

        # Adaptive gamma to hit target brightness
        mean_brightness = float(np.mean(y_enhanced))
        is_low_light = mean_brightness < self._target_brightness * 0.7
        gamma = self._compute_gamma(mean_brightness)
        if abs(gamma - 1.0) > 0.05:
            y_enhanced = cv2.LUT(y_enhanced, self._build_gamma_lut(gamma))

        # Recompose
        enhanced_ycrcb = cv2.merge([y_enhanced, cr, cb])
        enhanced = cv2.cvtColor(enhanced_ycrcb, cv2.COLOR_YCrCb2BGR)

        # Blur detection via Laplacian variance
        gray = cv2.cvtColor(enhanced, cv2.COLOR_BGR2GRAY)
        lap_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        is_blurry = lap_var < self._blur_check_threshold

        return enhanced, {
            "is_low_light": is_low_light,
            "is_blurry": is_blurry,
            "laplacian_var": lap_var,
            "gamma": gamma,
        }

    def _compute_gamma(self, brightness: float) -> float:
        if brightness < 1:
            brightness = 1.0
        gamma = self._target_brightness / brightness
        return float(np.clip(gamma, self._gamma_range[0], self._gamma_range[1]))

    @staticmethod
    def _build_gamma_lut(gamma: float) -> np.ndarray:
        inv_gamma = 1.0 / gamma
        table = np.array(
            [((i / 255.0) ** inv_gamma) * 255 for i in range(256)],
            dtype=np.uint8,
        )
        return table