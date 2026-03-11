"""
Anomaly detection for fleet sensor data using Isolation Forest.

Scores each device's sensor readings against learned fleet-wide
baselines. High anomaly scores trigger alerts and status changes
in the fleet health monitor.

Feature vector: [battery_pct, temperature_c, signal_strength_dbm, speed_kmh, hdop]
"""

import logging
import numpy as np
from typing import Optional

from app.models import TelemetryEvent

logger = logging.getLogger(__name__)

# Threshold above which a device is flagged as anomalous
ANOMALY_THRESHOLD = 0.7


class AnomalyDetector:
    """
    Lightweight anomaly scorer for fleet sensor data.
    
    Uses a simplified Isolation Forest approach:
    - Maintains running statistics (mean, std) per feature
    - Scores new readings based on z-score deviation
    - In production, this would use sklearn.ensemble.IsolationForest
      trained on historical fleet data with periodic retraining.
    """

    def __init__(self, min_samples: int = 50):
        self._min_samples = min_samples
        self._samples: list[list[float]] = []
        self._means: Optional[np.ndarray] = None
        self._stds: Optional[np.ndarray] = None
        self._is_fitted = False

    def _extract_features(self, event: TelemetryEvent) -> list[float]:
        """Extract numeric feature vector from telemetry event."""
        return [
            event.sensors.battery_pct,
            event.sensors.temperature_c,
            event.sensors.signal_strength_dbm,
            event.gps.speed_kmh or 0.0,
            event.gps.hdop or 1.0,
        ]

    def _fit(self) -> None:
        """Recompute baseline statistics from collected samples."""
        if len(self._samples) < self._min_samples:
            return
        arr = np.array(self._samples)
        self._means = np.mean(arr, axis=0)
        self._stds = np.std(arr, axis=0)
        # Prevent division by zero
        self._stds = np.where(self._stds < 1e-6, 1.0, self._stds)
        self._is_fitted = True
        logger.info(
            f"Anomaly detector fitted on {len(self._samples)} samples. "
            f"Means: {self._means.tolist()}, Stds: {self._stds.tolist()}"
        )

    def ingest(self, event: TelemetryEvent) -> Optional[float]:
        """
        Ingest a telemetry event and return an anomaly score (0.0 - 1.0).
        
        Returns None if the model hasn't been fitted yet (insufficient samples).
        Score > 0.7 indicates anomalous behavior.
        """
        features = self._extract_features(event)
        self._samples.append(features)

        # Refit periodically (every 100 new samples)
        if len(self._samples) % 100 == 0:
            self._fit()

        if not self._is_fitted:
            # Not enough data yet — attempt initial fit
            self._fit()
            if not self._is_fitted:
                return None

        # Compute anomaly score as normalized z-score magnitude
        feat_arr = np.array(features)
        z_scores = np.abs((feat_arr - self._means) / self._stds)

        # Anomaly score: max z-score normalized to 0-1 range via sigmoid
        max_z = float(np.max(z_scores))
        score = 1.0 / (1.0 + np.exp(-1.0 * (max_z - 3.0)))  # sigmoid centered at z=3

        if score > ANOMALY_THRESHOLD:
            logger.warning(
                f"Anomaly detected for {event.device_id}: "
                f"score={score:.3f}, z_scores={z_scores.tolist()}"
            )

        return round(float(score), 4)

    @property
    def is_fitted(self) -> bool:
        return self._is_fitted

    @property
    def sample_count(self) -> int:
        return len(self._samples)
