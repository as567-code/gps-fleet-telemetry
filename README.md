# GPS Fleet Telemetry Processing Pipeline

A production-style event processing service that ingests GPS and sensor telemetry from edge devices, validates and deduplicates events, monitors fleet health, and exposes REST APIs for downstream consumers.

Built to demonstrate backend/platform engineering patterns: event-driven architecture, schema validation, idempotent processing, anomaly detection, and observability.

## Architecture

```
Edge Devices (GPS + Sensors)
        │
        ▼
  ┌─────────────┐
  │  FastAPI     │  ← Event Ingestion (REST API)
  │  Ingestion   │
  └──────┬──────┘
         │
    Schema Validation + Deduplication
         │
         ▼
  ┌─────────────┐
  │  Event Store │  ← PostgreSQL / In-Memory Store
  │  (Time-Series)│
  └──────┬──────┘
         │
    ┌────┴────┐
    ▼         ▼
┌────────┐ ┌──────────┐
│ Fleet  │ │ Anomaly  │
│ Health │ │ Detection│
│ Monitor│ │ Service  │
└────────┘ └──────────┘
    │           │
    ▼           ▼
  Prometheus   Alerts
  /Grafana     /Webhooks
```

## Features

- **Event Ingestion API**: Accepts GPS + sensor telemetry via REST, validates schema, rejects malformed payloads
- **Idempotent Processing**: SHA-256 based deduplication prevents duplicate event processing
- **Fleet Health Monitoring**: Tracks device heartbeats, detects stale/offline devices, computes fleet-wide health scores
- **Anomaly Detection**: Isolation Forest scoring on sensor metrics (battery, temperature, signal strength) to flag failing devices
- **GPS Data Analysis**: Geofence validation, speed anomaly detection, route deviation alerts
- **Observability**: Structured logging, Prometheus-compatible metrics endpoint, health checks
- **End-to-End Tests**: Full integration test suite covering ingestion → processing → health monitoring

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Run the service
uvicorn app.main:app --host 0.0.0.0 --port 8000

# Run tests
pytest tests/ -v

# Simulate edge device fleet
python simulate_fleet.py --devices 50 --duration 60
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/events` | Ingest GPS/sensor telemetry event |
| POST | `/api/v1/events/batch` | Batch ingest (up to 100 events) |
| GET | `/api/v1/fleet/health` | Fleet-wide health summary |
| GET | `/api/v1/fleet/devices/{device_id}` | Individual device status |
| GET | `/api/v1/fleet/anomalies` | Active anomaly alerts |
| GET | `/api/v1/metrics` | Prometheus-compatible metrics |
| GET | `/health` | Service health check |

## Tech Stack

- **Python 3.11+** / **FastAPI** — async REST API
- **Pydantic v2** — schema validation
- **scikit-learn** — anomaly detection (Isolation Forest)
- **PostgreSQL** (production) / in-memory store (dev)
- **Docker** + **docker-compose** — containerized deployment
- **pytest** — test suite

## Project Structure

```
gps-fleet-pipeline/
├── app/
│   ├── main.py              # FastAPI application + routes
│   ├── models.py            # Pydantic schemas for events
│   ├── event_processor.py   # Ingestion, validation, dedup
│   ├── fleet_monitor.py     # Fleet health tracking
│   ├── anomaly_detector.py  # Isolation Forest anomaly scoring
│   └── metrics.py           # Prometheus metrics
├── tests/
│   ├── test_ingestion.py    # Event ingestion tests
│   ├── test_fleet_health.py # Fleet monitoring tests
│   └── test_anomaly.py      # Anomaly detection tests
├── simulate_fleet.py        # Fleet simulation script
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── README.md
```

## License

MIT
