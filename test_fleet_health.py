"""
Tests for fleet health monitoring service.
"""

import pytest
from datetime import datetime, timezone, timedelta

from app.fleet_monitor import FleetMonitor, DeviceState, OFFLINE_THRESHOLD_SECONDS
from app.models import (
    TelemetryEvent, DeviceType, GPSCoordinate, SensorReadings,
)


def _make_event(
    device_id: str = "bus-001",
    battery: float = 85.0,
    temp: float = 42.0,
    signal: float = -65.0,
) -> TelemetryEvent:
    return TelemetryEvent(
        device_id=device_id,
        device_type=DeviceType.BUS_CAMERA,
        timestamp=datetime.now(timezone.utc),
        gps=GPSCoordinate(latitude=37.77, longitude=-122.41),
        sensors=SensorReadings(
            battery_pct=battery, temperature_c=temp,
            signal_strength_dbm=signal,
        ),
    )


class TestDeviceState:
    def test_new_device_is_offline(self):
        ds = DeviceState("dev-001", DeviceType.GPS_TRACKER)
        assert ds.is_offline is True

    def test_updated_device_is_online(self):
        ds = DeviceState("dev-001", DeviceType.GPS_TRACKER)
        ds.update(_make_event("dev-001"))
        assert ds.is_offline is False

    def test_low_battery_is_degraded(self):
        ds = DeviceState("dev-001", DeviceType.GPS_TRACKER)
        ds.update(_make_event("dev-001", battery=10.0))
        assert ds.is_degraded is True
        assert ds.get_status() == "degraded"

    def test_high_temp_is_degraded(self):
        ds = DeviceState("dev-001", DeviceType.GPS_TRACKER)
        ds.update(_make_event("dev-001", temp=75.0))
        assert ds.is_degraded is True

    def test_weak_signal_is_degraded(self):
        ds = DeviceState("dev-001", DeviceType.GPS_TRACKER)
        ds.update(_make_event("dev-001", signal=-110.0))
        assert ds.is_degraded is True

    def test_healthy_device(self):
        ds = DeviceState("dev-001", DeviceType.GPS_TRACKER)
        ds.update(_make_event("dev-001"))
        assert ds.get_status() == "healthy"

    def test_anomaly_flag_overrides_healthy(self):
        ds = DeviceState("dev-001", DeviceType.GPS_TRACKER)
        ds.update(_make_event("dev-001"))
        ds.anomaly_score = 0.85
        assert ds.get_status() == "anomaly"

    def test_event_count_tracks(self):
        ds = DeviceState("dev-001", DeviceType.GPS_TRACKER)
        for _ in range(5):
            ds.update(_make_event("dev-001"))
        assert ds.event_count == 5
        assert ds.events_last_hour == 5


class TestFleetMonitor:
    def test_new_device_registered(self):
        fm = FleetMonitor()
        fm.record_event(_make_event("bus-001"))
        assert fm.device_count == 1

    def test_multiple_devices(self):
        fm = FleetMonitor()
        fm.record_event(_make_event("bus-001"))
        fm.record_event(_make_event("bus-002"))
        fm.record_event(_make_event("bus-003"))
        assert fm.device_count == 3

    def test_fleet_summary_counts(self):
        fm = FleetMonitor()
        fm.record_event(_make_event("healthy-001"))
        fm.record_event(_make_event("degraded-001", battery=5.0))
        summary = fm.get_fleet_summary()
        assert summary.total_devices == 2
        assert summary.healthy == 1
        assert summary.degraded == 1

    def test_fleet_health_score(self):
        fm = FleetMonitor()
        # All healthy devices = 100% score
        for i in range(10):
            fm.record_event(_make_event(f"healthy-{i}"))
        summary = fm.get_fleet_summary()
        assert summary.fleet_health_score == 100.0

    def test_degraded_devices_lower_score(self):
        fm = FleetMonitor()
        fm.record_event(_make_event("healthy-001"))
        fm.record_event(_make_event("degraded-001", battery=5.0))
        summary = fm.get_fleet_summary()
        # (100 + 50) / 2 = 75.0
        assert summary.fleet_health_score == 75.0

    def test_get_anomalous_devices(self):
        fm = FleetMonitor()
        fm.record_event(_make_event("normal-001"))
        fm.record_event(_make_event("degraded-001", battery=5.0))
        anomalies = fm.get_anomalous_devices()
        assert len(anomalies) == 1
        assert anomalies[0].device_id == "degraded-001"

    def test_get_device_health_not_found(self):
        fm = FleetMonitor()
        assert fm.get_device_health("nonexistent") is None

    def test_set_anomaly_score(self):
        fm = FleetMonitor()
        fm.record_event(_make_event("bus-001"))
        fm.set_anomaly_score("bus-001", 0.9)
        health = fm.get_device_health("bus-001")
        assert health.anomaly_score == 0.9
        assert health.status == "anomaly"


class TestFleetAPIIntegration:
    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from app.main import app
        with TestClient(app) as c:
            yield c

    def test_fleet_health_empty(self, client):
        resp = client.get("/api/v1/fleet/health")
        assert resp.status_code == 200
        assert resp.json()["total_devices"] == 0

    def test_fleet_health_after_events(self, client):
        for i in range(5):
            client.post("/api/v1/events", json={
                "device_id": f"test-dev-{i}",
                "device_type": "gps_tracker",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "gps": {"latitude": 37.77, "longitude": -122.41},
                "sensors": {"battery_pct": 80, "temperature_c": 35, "signal_strength_dbm": -60},
            })
        resp = client.get("/api/v1/fleet/health")
        assert resp.json()["total_devices"] == 5

    def test_device_health_endpoint(self, client):
        client.post("/api/v1/events", json={
            "device_id": "specific-device",
            "device_type": "bus_camera",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "gps": {"latitude": 37.77, "longitude": -122.41},
            "sensors": {"battery_pct": 95, "temperature_c": 30, "signal_strength_dbm": -50},
        })
        resp = client.get("/api/v1/fleet/devices/specific-device")
        assert resp.status_code == 200
        assert resp.json()["device_id"] == "specific-device"

    def test_device_not_found(self, client):
        resp = client.get("/api/v1/fleet/devices/ghost-device")
        assert resp.status_code == 404
