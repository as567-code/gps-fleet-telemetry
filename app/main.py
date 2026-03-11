"""
GPS Fleet Telemetry Processing Service

FastAPI application exposing REST APIs for:
  - Event ingestion (single + batch)
  - Fleet health monitoring
  - Anomaly alerts
  - Prometheus metrics
  - Health checks
"""

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from app.models import (
    TelemetryEvent, EventResponse, BatchEventRequest, BatchEventResponse,
    DeviceHealth, FleetHealthSummary,
)
from app.event_processor import EventProcessor
from app.fleet_monitor import FleetMonitor
from app.anomaly_detector import AnomalyDetector
from app.metrics import format_prometheus_metrics

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)

# Shared service instances (initialized at startup)
processor: EventProcessor
fleet_monitor: FleetMonitor
anomaly_detector: AnomalyDetector


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize services on startup, cleanup on shutdown."""
    global processor, fleet_monitor, anomaly_detector

    logger.info("Initializing GPS Fleet Telemetry Pipeline...")
    processor = EventProcessor()
    fleet_monitor = FleetMonitor()
    anomaly_detector = AnomalyDetector(min_samples=50)
    logger.info("Pipeline ready. Listening for telemetry events.")

    yield

    logger.info("Shutting down pipeline. Final metrics:")
    logger.info(f"  Events processed: {processor.events_accepted}")
    logger.info(f"  Devices tracked: {fleet_monitor.device_count}")


app = FastAPI(
    title="GPS Fleet Telemetry Pipeline",
    description="Edge-to-cloud event processing for fleet GPS and sensor data",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse(url="/docs")


# ── Event Ingestion ──────────────────────────────────────────────


@app.post("/api/v1/events", response_model=EventResponse)
async def ingest_event(event: TelemetryEvent):
    """
    Ingest a single GPS/sensor telemetry event from an edge device.
    
    Pipeline: validate → dedup → geofence → store → fleet update → anomaly check
    """
    # Step 1: Process through event pipeline
    result = processor.process(event)

    if result.status == "accepted":
        # Step 2: Update fleet health monitor
        fleet_monitor.record_event(event)

        # Step 3: Run anomaly detection
        anomaly_score = anomaly_detector.ingest(event)
        if anomaly_score is not None:
            fleet_monitor.set_anomaly_score(event.device_id, anomaly_score)

    return result


@app.post("/api/v1/events/batch", response_model=BatchEventResponse)
async def ingest_batch(batch: BatchEventRequest):
    """
    Batch ingest up to 100 telemetry events.
    
    Each event is processed independently — partial failures don't
    block the rest of the batch.
    """
    results = []
    accepted = 0
    duplicates = 0
    rejected = 0

    for event in batch.events:
        try:
            result = processor.process(event)
            if result.status == "accepted":
                fleet_monitor.record_event(event)
                anomaly_score = anomaly_detector.ingest(event)
                if anomaly_score is not None:
                    fleet_monitor.set_anomaly_score(event.device_id, anomaly_score)
                accepted += 1
            elif result.status == "duplicate":
                duplicates += 1
            results.append(result)
        except Exception as e:
            rejected += 1
            results.append(EventResponse(
                event_id="error",
                status="rejected",
                device_id=event.device_id,
                timestamp=event.timestamp,
            ))
            logger.error(f"Failed to process event from {event.device_id}: {e}")

    return BatchEventResponse(
        accepted=accepted,
        duplicates=duplicates,
        rejected=rejected,
        results=results,
    )


# ── Fleet Health ─────────────────────────────────────────────────


@app.get("/api/v1/fleet/health", response_model=FleetHealthSummary)
async def get_fleet_health():
    """Get fleet-wide health summary with aggregate metrics."""
    return fleet_monitor.get_fleet_summary()


@app.get("/api/v1/fleet/devices/{device_id}", response_model=DeviceHealth)
async def get_device_health(device_id: str):
    """Get health status for a specific edge device."""
    health = fleet_monitor.get_device_health(device_id)
    if health is None:
        raise HTTPException(status_code=404, detail=f"Device {device_id} not found")
    return health


@app.get("/api/v1/fleet/anomalies", response_model=list[DeviceHealth])
async def get_anomalies():
    """Get all devices currently flagged as anomalous or degraded."""
    return fleet_monitor.get_anomalous_devices()


# ── Observability ────────────────────────────────────────────────


@app.get("/api/v1/metrics")
async def get_metrics():
    """Prometheus-compatible metrics endpoint."""
    body = format_prometheus_metrics(
        processor_metrics=processor.metrics,
        fleet_device_count=fleet_monitor.device_count,
        anomaly_sample_count=anomaly_detector.sample_count,
    )
    return Response(content=body, media_type="text/plain; charset=utf-8")


@app.get("/health")
async def health_check():
    """Service health check for load balancer / Kubernetes probes."""
    return {
        "status": "healthy",
        "service": "gps-fleet-pipeline",
        "version": "1.0.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "uptime_events": processor.events_accepted,
        "fleet_devices": fleet_monitor.device_count,
    }
