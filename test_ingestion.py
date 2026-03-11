"""
End-to-end tests for the event ingestion pipeline.

Tests cover: schema validation, deduplication, geofence checks,
batch processing, and API integration.
"""

import pytest
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient

from app.main import app
from app.models import TelemetryEvent, DeviceType, GPSCoordinate, SensorReadings
from app.event_processor import EventProcessor, DeduplicationCache, GeoFenceValidator


# ── Fixtures ─────────────────────────────────────────────────────

@pytest.fixture
def client():
    """FastAPI test client."""
    with TestClient(app) as c:
        yield c


@pytest.fixture
def processor():
    return EventProcessor()


@pytest.fixture
def sample_event_payload():
    """Valid telemetry event payload for API tests."""
    return {
        "device_id": "bus-cam-001",
        "device_type": "bus_camera",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "gps": {
            "latitude": 37.7749,
            "longitude": -122.4194,
            "altitude_m": 10.0,
            "hdop": 1.2,
            "speed_kmh": 35.5,
            "heading_deg": 180.0,
        },
        "sensors": {
            "battery_pct": 85.0,
            "temperature_c": 42.0,
            "signal_strength_dbm": -65.0,
            "storage_used_pct": 45.0,
            "cpu_usage_pct": 30.0,
            "uptime_seconds": 86400,
        },
        "route_id": "SF-MUNI-38",
    }


@pytest.fixture
def sample_event():
    """Pydantic TelemetryEvent object for unit tests."""
    return TelemetryEvent(
        device_id="bus-cam-001",
        device_type=DeviceType.BUS_CAMERA,
        timestamp=datetime.now(timezone.utc),
        gps=GPSCoordinate(
            latitude=37.7749, longitude=-122.4194,
            speed_kmh=35.0, hdop=1.2,
        ),
        sensors=SensorReadings(
            battery_pct=85.0, temperature_c=42.0,
            signal_strength_dbm=-65.0,
        ),
        route_id="SF-MUNI-38",
    )


# ── Unit Tests: Deduplication ────────────────────────────────────

class TestDeduplication:
    def test_first_event_is_not_duplicate(self, sample_event, processor):
        result = processor.process(sample_event)
        assert result.status == "accepted"

    def test_same_event_twice_is_duplicate(self, sample_event, processor):
        processor.process(sample_event)
        result = processor.process(sample_event)
        assert result.status == "duplicate"

    def test_different_devices_not_duplicate(self, processor):
        event1 = TelemetryEvent(
            device_id="device-001", device_type=DeviceType.GPS_TRACKER,
            timestamp=datetime.now(timezone.utc),
            gps=GPSCoordinate(latitude=37.77, longitude=-122.41),
            sensors=SensorReadings(battery_pct=90, temperature_c=30, signal_strength_dbm=-50),
        )
        event2 = TelemetryEvent(
            device_id="device-002", device_type=DeviceType.GPS_TRACKER,
            timestamp=datetime.now(timezone.utc),
            gps=GPSCoordinate(latitude=37.77, longitude=-122.41),
            sensors=SensorReadings(battery_pct=90, temperature_c=30, signal_strength_dbm=-50),
        )
        assert processor.process(event1).status == "accepted"
        assert processor.process(event2).status == "accepted"

    def test_cache_lru_eviction(self):
        cache = DeduplicationCache(max_size=3)
        for i in range(5):
            cache.record(f"fp-{i}")
        assert cache.size == 3
        assert not cache.is_duplicate("fp-0")  # evicted
        assert cache.is_duplicate("fp-4")      # still present

    def test_metrics_count_correctly(self, sample_event, processor):
        processor.process(sample_event)
        processor.process(sample_event)  # duplicate
        assert processor.events_accepted == 1
        assert processor.events_duplicated == 1


# ── Unit Tests: Geofence ─────────────────────────────────────────

class TestGeoFence:
    def test_sf_is_within_us_bounds(self):
        geo = GeoFenceValidator()
        assert geo.is_within_bounds(37.77, -122.41) is True

    def test_london_is_outside_us_bounds(self):
        geo = GeoFenceValidator()
        assert geo.is_within_bounds(51.50, -0.12) is False

    def test_geofence_warning_counted(self, processor):
        event = TelemetryEvent(
            device_id="device-uk", device_type=DeviceType.GPS_TRACKER,
            timestamp=datetime.now(timezone.utc),
            gps=GPSCoordinate(latitude=51.5, longitude=-0.12),
            sensors=SensorReadings(battery_pct=90, temperature_c=30, signal_strength_dbm=-50),
        )
        processor.process(event)
        assert processor.geofence_warnings == 1
        # Event should still be accepted (warning, not rejection)
        assert processor.events_accepted == 1


# ── Unit Tests: Schema Validation ────────────────────────────────

class TestSchemaValidation:
    def test_invalid_latitude_rejected(self):
        with pytest.raises(Exception):
            GPSCoordinate(latitude=200.0, longitude=-122.0)

    def test_invalid_battery_rejected(self):
        with pytest.raises(Exception):
            SensorReadings(battery_pct=150, temperature_c=30, signal_strength_dbm=-50)

    def test_invalid_device_id_rejected(self):
        with pytest.raises(Exception):
            TelemetryEvent(
                device_id="bad device!@#",  # invalid characters
                device_type=DeviceType.GPS_TRACKER,
                timestamp=datetime.now(timezone.utc),
                gps=GPSCoordinate(latitude=37.77, longitude=-122.41),
                sensors=SensorReadings(battery_pct=90, temperature_c=30, signal_strength_dbm=-50),
            )

    def test_future_timestamp_rejected(self):
        with pytest.raises(Exception):
            TelemetryEvent(
                device_id="device-001",
                device_type=DeviceType.GPS_TRACKER,
                timestamp=datetime.now(timezone.utc) + timedelta(hours=1),
                gps=GPSCoordinate(latitude=37.77, longitude=-122.41),
                sensors=SensorReadings(battery_pct=90, temperature_c=30, signal_strength_dbm=-50),
            )


# ── API Integration Tests ────────────────────────────────────────

class TestAPIIngestion:
    def test_post_valid_event(self, client, sample_event_payload):
        resp = client.post("/api/v1/events", json=sample_event_payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "accepted"
        assert data["device_id"] == "bus-cam-001"

    def test_post_duplicate_event(self, client, sample_event_payload):
        client.post("/api/v1/events", json=sample_event_payload)
        resp = client.post("/api/v1/events", json=sample_event_payload)
        assert resp.json()["status"] == "duplicate"

    def test_post_invalid_payload(self, client):
        resp = client.post("/api/v1/events", json={"garbage": True})
        assert resp.status_code == 422  # validation error

    def test_batch_ingestion(self, client, sample_event_payload):
        events = []
        for i in range(5):
            e = sample_event_payload.copy()
            e["device_id"] = f"batch-device-{i}"
            events.append(e)

        resp = client.post("/api/v1/events/batch", json={"events": events})
        assert resp.status_code == 200
        data = resp.json()
        assert data["accepted"] == 5
        assert data["duplicates"] == 0

    def test_health_endpoint(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "healthy"

    def test_metrics_endpoint(self, client):
        resp = client.get("/api/v1/metrics")
        assert resp.status_code == 200
        assert "telemetry_events_accepted_total" in resp.text
