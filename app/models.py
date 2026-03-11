"""
Pydantic schemas for GPS telemetry events and fleet health.

Enforces strict validation at the API boundary — malformed payloads
are rejected before entering the processing pipeline.
"""

from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field, field_validator


class DeviceType(str, Enum):
    BUS_CAMERA = "bus_camera"
    LIDAR_UNIT = "lidar_unit"
    GPS_TRACKER = "gps_tracker"
    EDGE_COMPUTE = "edge_compute"


class GPSCoordinate(BaseModel):
    """Validated GPS coordinate with geofence bounds."""
    latitude: float = Field(..., ge=-90.0, le=90.0, description="WGS84 latitude")
    longitude: float = Field(..., ge=-180.0, le=180.0, description="WGS84 longitude")
    altitude_m: Optional[float] = Field(None, ge=-500, le=20000, description="Altitude in meters")
    hdop: Optional[float] = Field(None, ge=0, le=50, description="Horizontal dilution of precision")
    speed_kmh: Optional[float] = Field(None, ge=0, le=200, description="Ground speed in km/h")
    heading_deg: Optional[float] = Field(None, ge=0, lt=360, description="Heading in degrees")


class SensorReadings(BaseModel):
    """Edge device sensor diagnostics for fleet health monitoring."""
    battery_pct: float = Field(..., ge=0, le=100, description="Battery level percentage")
    temperature_c: float = Field(..., ge=-40, le=85, description="Device temperature in Celsius")
    signal_strength_dbm: float = Field(..., ge=-120, le=0, description="Cellular signal in dBm")
    storage_used_pct: Optional[float] = Field(None, ge=0, le=100)
    cpu_usage_pct: Optional[float] = Field(None, ge=0, le=100)
    uptime_seconds: Optional[int] = Field(None, ge=0)


class TelemetryEvent(BaseModel):
    """
    Core telemetry event schema — the unit of data flowing through
    the event processing pipeline from edge device to cloud.
    """
    device_id: str = Field(
        ..., min_length=3, max_length=64,
        pattern=r"^[a-zA-Z0-9\-_]+$",
        description="Unique device identifier"
    )
    device_type: DeviceType
    timestamp: datetime = Field(..., description="Event timestamp (ISO 8601)")
    gps: GPSCoordinate
    sensors: SensorReadings
    route_id: Optional[str] = Field(None, max_length=64, description="Assigned transit route")
    event_seq: Optional[int] = Field(None, ge=0, description="Monotonic sequence number")

    @field_validator("timestamp")
    @classmethod
    def timestamp_not_future(cls, v: datetime) -> datetime:
        """Reject events with timestamps more than 5 minutes in the future."""
        from datetime import timezone, timedelta
        now = datetime.now(timezone.utc)
        if v.tzinfo is None:
            from datetime import timezone
            v = v.replace(tzinfo=timezone.utc)
        if v > now + timedelta(minutes=5):
            raise ValueError("Event timestamp cannot be more than 5 minutes in the future")
        return v


class EventResponse(BaseModel):
    """Response returned after event ingestion."""
    event_id: str
    status: str  # "accepted", "duplicate", "rejected"
    device_id: str
    timestamp: datetime


class BatchEventRequest(BaseModel):
    """Batch ingestion — up to 100 events per request."""
    events: list[TelemetryEvent] = Field(..., min_length=1, max_length=100)


class BatchEventResponse(BaseModel):
    accepted: int
    duplicates: int
    rejected: int
    results: list[EventResponse]


class DeviceHealth(BaseModel):
    """Health snapshot for a single edge device."""
    device_id: str
    device_type: DeviceType
    status: str  # "healthy", "degraded", "offline", "anomaly"
    last_seen: datetime
    last_gps: GPSCoordinate
    last_sensors: SensorReadings
    anomaly_score: Optional[float] = None
    events_last_hour: int = 0
    uptime_pct: Optional[float] = None


class FleetHealthSummary(BaseModel):
    """Fleet-wide health aggregation."""
    total_devices: int
    healthy: int
    degraded: int
    offline: int
    anomalies: int
    fleet_health_score: float = Field(..., ge=0, le=100, description="0-100 fleet health score")
    events_processed_total: int
    events_per_minute: float
    avg_battery_pct: float
    avg_signal_dbm: float
    timestamp: datetime
