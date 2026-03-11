"""
Event processing engine — the core of the edge-to-cloud data pipeline.

Handles:
  1. Schema validation (via Pydantic, at API layer)
  2. Dedup: SHA-256 fingerprint rejects duplicate events
  3. GPS geofence validation
  4. Persistence to event store (in-memory for dev, Postgres for prod)
  5. Fan-out to fleet health monitor + anomaly detector
"""

import hashlib
import json
import logging
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Optional

from app.models import TelemetryEvent, EventResponse

logger = logging.getLogger(__name__)


class DeduplicationCache:
    """
    LRU cache of event fingerprints for idempotent processing.
    
    Uses SHA-256 of (device_id, timestamp, gps) as fingerprint.
    In production, this would be backed by Redis with TTL expiry.
    """

    def __init__(self, max_size: int = 100_000):
        self._cache: OrderedDict[str, datetime] = OrderedDict()
        self._max_size = max_size

    def fingerprint(self, event: TelemetryEvent) -> str:
        """Generate deterministic fingerprint for an event."""
        payload = f"{event.device_id}:{event.timestamp.isoformat()}:{event.gps.latitude}:{event.gps.longitude}"
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def is_duplicate(self, fingerprint: str) -> bool:
        return fingerprint in self._cache

    def record(self, fingerprint: str) -> None:
        if fingerprint in self._cache:
            self._cache.move_to_end(fingerprint)
            return
        self._cache[fingerprint] = datetime.now(timezone.utc)
        if len(self._cache) > self._max_size:
            self._cache.popitem(last=False)  # evict oldest

    @property
    def size(self) -> int:
        return len(self._cache)


class GeoFenceValidator:
    """
    Validates GPS coordinates against operational geofences.
    
    In production, geofences are loaded from the route database.
    Default: continental US bounding box as a sanity check.
    """

    def __init__(
        self,
        min_lat: float = 24.0, max_lat: float = 50.0,
        min_lon: float = -125.0, max_lon: float = -66.0,
    ):
        self.min_lat = min_lat
        self.max_lat = max_lat
        self.min_lon = min_lon
        self.max_lon = max_lon

    def is_within_bounds(self, lat: float, lon: float) -> bool:
        return (
            self.min_lat <= lat <= self.max_lat
            and self.min_lon <= lon <= self.max_lon
        )

    def validate(self, event: TelemetryEvent) -> Optional[str]:
        """Returns warning message if GPS is out of bounds, else None."""
        if not self.is_within_bounds(event.gps.latitude, event.gps.longitude):
            return (
                f"Device {event.device_id} GPS out of geofence: "
                f"({event.gps.latitude}, {event.gps.longitude})"
            )
        return None


class EventStore:
    """
    In-memory event store for development. Mirrors the interface
    of a PostgreSQL time-series table in production.
    
    Production schema:
        CREATE TABLE telemetry_events (
            event_id    TEXT PRIMARY KEY,
            device_id   TEXT NOT NULL,
            timestamp   TIMESTAMPTZ NOT NULL,
            payload     JSONB NOT NULL,
            created_at  TIMESTAMPTZ DEFAULT NOW()
        );
        CREATE INDEX idx_events_device_time ON telemetry_events (device_id, timestamp DESC);
    """

    def __init__(self, max_events: int = 500_000):
        self._events: dict[str, dict] = {}  # event_id -> event data
        self._device_index: dict[str, list[str]] = {}  # device_id -> [event_ids]
        self._max_events = max_events

    def store(self, event_id: str, event: TelemetryEvent) -> None:
        self._events[event_id] = {
            "event_id": event_id,
            "device_id": event.device_id,
            "device_type": event.device_type.value,
            "timestamp": event.timestamp.isoformat(),
            "gps": event.gps.model_dump(),
            "sensors": event.sensors.model_dump(),
            "route_id": event.route_id,
            "stored_at": datetime.now(timezone.utc).isoformat(),
        }
        self._device_index.setdefault(event.device_id, []).append(event_id)

    def get_device_events(
        self, device_id: str, limit: int = 100
    ) -> list[dict]:
        event_ids = self._device_index.get(device_id, [])
        return [self._events[eid] for eid in event_ids[-limit:]]

    def get_all_device_ids(self) -> list[str]:
        return list(self._device_index.keys())

    @property
    def total_events(self) -> int:
        return len(self._events)


class EventProcessor:
    """
    Orchestrates the event processing pipeline:
    
    Event In → Validate → Dedup → Geofence Check → Store → Fan-out
    """

    def __init__(
        self,
        dedup_cache: Optional[DeduplicationCache] = None,
        geofence: Optional[GeoFenceValidator] = None,
        event_store: Optional[EventStore] = None,
    ):
        self.dedup = dedup_cache or DeduplicationCache()
        self.geofence = geofence or GeoFenceValidator()
        self.store = event_store or EventStore()

        # Metrics counters
        self.events_accepted = 0
        self.events_duplicated = 0
        self.events_rejected = 0
        self.geofence_warnings = 0

    def process(self, event: TelemetryEvent) -> EventResponse:
        """
        Process a single telemetry event through the pipeline.
        
        Returns EventResponse with status: accepted | duplicate | rejected.
        """
        # Step 1: Deduplication check
        fp = self.dedup.fingerprint(event)
        if self.dedup.is_duplicate(fp):
            self.events_duplicated += 1
            logger.info(f"Duplicate event from {event.device_id}, fingerprint={fp}")
            return EventResponse(
                event_id=fp,
                status="duplicate",
                device_id=event.device_id,
                timestamp=event.timestamp,
            )

        # Step 2: Geofence validation (warn, don't reject)
        geo_warning = self.geofence.validate(event)
        if geo_warning:
            self.geofence_warnings += 1
            logger.warning(geo_warning)

        # Step 3: Store event
        event_id = fp
        self.store.store(event_id, event)
        self.dedup.record(fp)
        self.events_accepted += 1

        logger.info(
            f"Event accepted: device={event.device_id} "
            f"gps=({event.gps.latitude:.4f},{event.gps.longitude:.4f}) "
            f"battery={event.sensors.battery_pct}%"
        )

        return EventResponse(
            event_id=event_id,
            status="accepted",
            device_id=event.device_id,
            timestamp=event.timestamp,
        )

    def process_batch(self, events: list[TelemetryEvent]) -> list[EventResponse]:
        """Process a batch of events, returning individual results."""
        return [self.process(e) for e in events]

    @property
    def metrics(self) -> dict:
        return {
            "events_accepted": self.events_accepted,
            "events_duplicated": self.events_duplicated,
            "events_rejected": self.events_rejected,
            "geofence_warnings": self.geofence_warnings,
            "dedup_cache_size": self.dedup.size,
            "total_stored_events": self.store.total_events,
        }
