"""
Tests for anomaly detection service.
"""

import pytest
from datetime import datetime, timezone

from app.anomaly_detector import AnomalyDetector
from app.models import (
    TelemetryEvent, DeviceType, GPSCoordinate, SensorReadings,
)


def _make_normal_event(device_id: str = "dev-001") -> TelemetryEvent:
    """Create event with normal sensor readings."""
    return TelemetryEvent(
        device_id=device_id,
        device_type=DeviceType.BUS_CAMERA,
        timestamp=datetime.now(timezone.utc),
        gps=GPSCoordinate(latitude=37.77, longitude=-122.41, speed_kmh=30.0, hdop=1.2),
        sensors=SensorReadings(battery_pct=80.0, temperature_c=40.0, signal_strength_dbm=-60.0),
    )


def _make_anomalous_event(device_id: str = "dev-bad") -> TelemetryEvent:
    """Create event with extreme sensor readings."""
    return TelemetryEvent(
        device_id=device_id,
        device_type=DeviceType.BUS_CAMERA,
        timestamp=datetime.now(timezone.utc),
        gps=GPSCoordinate(latitude=37.77, longitude=-122.41, speed_kmh=180.0, hdop=40.0),
        sensors=SensorReadings(battery_pct=2.0, temperature_c=83.0, signal_strength_dbm=-115.0),
    )


class TestAnomalyDetector:
    def test_returns_none_before_fit(self):
        detector = AnomalyDetector(min_samples=50)
        score = detector.ingest(_make_normal_event())
        assert score is None  # not fitted yet

    def test_fits_after_min_samples(self):
        detector = AnomalyDetector(min_samples=10)
        for i in range(15):
            detector.ingest(_make_normal_event(f"dev-{i}"))
        assert detector.is_fitted is True

    def test_normal_event_low_score(self):
        detector = AnomalyDetector(min_samples=10)
        # Train on normal data
        for i in range(60):
            detector.ingest(_make_normal_event(f"dev-{i}"))
        # Score a normal event
        score = detector.ingest(_make_normal_event("dev-test"))
        assert score is not None
        assert score < 0.5  # should be low

    def test_anomalous_event_high_score(self):
        detector = AnomalyDetector(min_samples=10)
        # Train on normal data
        for i in range(60):
            detector.ingest(_make_normal_event(f"dev-{i}"))
        # Score an anomalous event
        score = detector.ingest(_make_anomalous_event())
        assert score is not None
        assert score > 0.5  # should be elevated

    def test_sample_count_tracks(self):
        detector = AnomalyDetector(min_samples=10)
        for i in range(25):
            detector.ingest(_make_normal_event(f"dev-{i}"))
        assert detector.sample_count == 25
