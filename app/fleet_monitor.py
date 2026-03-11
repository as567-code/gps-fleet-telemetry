"""
Fleet health monitoring service.

Tracks per-device health state, computes fleet-wide health scores,
and detects offline/degraded devices based on heartbeat staleness.

Device states:
  - healthy:  recent heartbeat + sensors within normal range
  - degraded: recent heartbeat but sensor readings indicate issues
  - offline:  no heartbeat within staleness threshold
  - anomaly:  anomaly detector flagged this device
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from app.models import (
    TelemetryEvent, DeviceHealth, FleetHealthSummary,
    GPSCoordinate, SensorReadings, DeviceType,
)

logger = logging.getLogger(__name__)

# Thresholds for fleet health classification
OFFLINE_THRESHOLD_SECONDS = 300      # 5 min without heartbeat = offline
DEGRADED_BATTERY_PCT = 20.0          # below 20% = degraded
DEGRADED_TEMP_C = 70.0               # above 70°C = degraded
DEGRADED_SIGNAL_DBM = -100.0         # below -100 dBm = degraded


class DeviceState:
    """Tracks the running state of a single edge device."""

    def __init__(self, device_id: str, device_type: DeviceType):
        self.device_id = device_id
        self.device_type = device_type
        self.last_seen: Optional[datetime] = None
        self.last_gps: Optional[GPSCoordinate] = None
        self.last_sensors: Optional[SensorReadings] = None
        self.event_count: int = 0
        self.event_timestamps: list[datetime] = []  # last 1 hour
        self.anomaly_score: Optional[float] = None

    def update(self, event: TelemetryEvent) -> None:
        """Update device state with latest telemetry event."""
        self.last_seen = datetime.now(timezone.utc)
        self.last_gps = event.gps
        self.last_sensors = event.sensors
        self.event_count += 1

        # Keep only last hour of timestamps for rate calculation
        cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
        self.event_timestamps.append(self.last_seen)
        self.event_timestamps = [
            t for t in self.event_timestamps if t > cutoff
        ]

    @property
    def events_last_hour(self) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
        return len([t for t in self.event_timestamps if t > cutoff])

    @property
    def is_offline(self) -> bool:
        if self.last_seen is None:
            return True
        elapsed = (datetime.now(timezone.utc) - self.last_seen).total_seconds()
        return elapsed > OFFLINE_THRESHOLD_SECONDS

    @property
    def is_degraded(self) -> bool:
        if self.last_sensors is None:
            return False
        return (
            self.last_sensors.battery_pct < DEGRADED_BATTERY_PCT
            or self.last_sensors.temperature_c > DEGRADED_TEMP_C
            or self.last_sensors.signal_strength_dbm < DEGRADED_SIGNAL_DBM
        )

    def get_status(self) -> str:
        if self.is_offline:
            return "offline"
        if self.anomaly_score is not None and self.anomaly_score > 0.7:
            return "anomaly"
        if self.is_degraded:
            return "degraded"
        return "healthy"

    def to_health(self) -> DeviceHealth:
        return DeviceHealth(
            device_id=self.device_id,
            device_type=self.device_type,
            status=self.get_status(),
            last_seen=self.last_seen or datetime.now(timezone.utc),
            last_gps=self.last_gps or GPSCoordinate(latitude=0, longitude=0),
            last_sensors=self.last_sensors or SensorReadings(
                battery_pct=0, temperature_c=0, signal_strength_dbm=-120
            ),
            anomaly_score=self.anomaly_score,
            events_last_hour=self.events_last_hour,
        )


class FleetMonitor:
    """
    Fleet-wide health monitor.
    
    Maintains per-device state and computes aggregate health metrics.
    In production, this runs as a background worker with periodic
    health checks pushed to Prometheus/Grafana.
    """

    def __init__(self):
        self._devices: dict[str, DeviceState] = {}
        self._total_events: int = 0
        self._start_time: datetime = datetime.now(timezone.utc)

    def record_event(self, event: TelemetryEvent) -> DeviceHealth:
        """Update fleet state with a new telemetry event."""
        if event.device_id not in self._devices:
            self._devices[event.device_id] = DeviceState(
                event.device_id, event.device_type
            )
            logger.info(f"New device registered: {event.device_id} ({event.device_type.value})")

        device = self._devices[event.device_id]
        device.update(event)
        self._total_events += 1

        return device.to_health()

    def get_device_health(self, device_id: str) -> Optional[DeviceHealth]:
        device = self._devices.get(device_id)
        if device is None:
            return None
        return device.to_health()

    def set_anomaly_score(self, device_id: str, score: float) -> None:
        """Called by anomaly detector to flag devices."""
        if device_id in self._devices:
            self._devices[device_id].anomaly_score = score

    def get_fleet_summary(self) -> FleetHealthSummary:
        """Compute fleet-wide health aggregation."""
        now = datetime.now(timezone.utc)
        statuses = {"healthy": 0, "degraded": 0, "offline": 0, "anomaly": 0}
        battery_values = []
        signal_values = []

        for device in self._devices.values():
            status = device.get_status()
            statuses[status] = statuses.get(status, 0) + 1
            if device.last_sensors:
                battery_values.append(device.last_sensors.battery_pct)
                signal_values.append(device.last_sensors.signal_strength_dbm)

        total = len(self._devices)
        uptime_seconds = max((now - self._start_time).total_seconds(), 1)
        events_per_minute = (self._total_events / uptime_seconds) * 60

        # Fleet health score: weighted combination of device statuses
        if total > 0:
            health_score = (
                (statuses["healthy"] * 100
                 + statuses["degraded"] * 50
                 + statuses["anomaly"] * 25
                 + statuses["offline"] * 0)
                / total
            )
        else:
            health_score = 100.0

        return FleetHealthSummary(
            total_devices=total,
            healthy=statuses["healthy"],
            degraded=statuses["degraded"],
            offline=statuses["offline"],
            anomalies=statuses["anomaly"],
            fleet_health_score=round(health_score, 1),
            events_processed_total=self._total_events,
            events_per_minute=round(events_per_minute, 2),
            avg_battery_pct=round(
                sum(battery_values) / len(battery_values), 1
            ) if battery_values else 0.0,
            avg_signal_dbm=round(
                sum(signal_values) / len(signal_values), 1
            ) if signal_values else 0.0,
            timestamp=now,
        )

    def get_anomalous_devices(self) -> list[DeviceHealth]:
        """Return all devices currently flagged as anomalous or degraded."""
        return [
            device.to_health()
            for device in self._devices.values()
            if device.get_status() in ("anomaly", "degraded")
        ]

    @property
    def device_count(self) -> int:
        return len(self._devices)
