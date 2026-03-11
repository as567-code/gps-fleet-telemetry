"""
Prometheus-compatible metrics formatter.

Formats internal processor and fleet metrics into the Prometheus
exposition text format for scraping by a Prometheus server.
"""

from datetime import datetime, timezone


def format_prometheus_metrics(
    processor_metrics: dict,
    fleet_device_count: int,
    anomaly_sample_count: int,
) -> str:
    """Format internal metrics as Prometheus exposition text."""
    ts = int(datetime.now(timezone.utc).timestamp() * 1000)

    lines = [
        "# HELP telemetry_events_accepted_total Total telemetry events accepted",
        "# TYPE telemetry_events_accepted_total counter",
        f"telemetry_events_accepted_total {processor_metrics.get('events_accepted', 0)} {ts}",

        "# HELP telemetry_events_duplicated_total Total duplicate events dropped",
        "# TYPE telemetry_events_duplicated_total counter",
        f"telemetry_events_duplicated_total {processor_metrics.get('events_duplicated', 0)} {ts}",

        "# HELP telemetry_events_rejected_total Total events rejected",
        "# TYPE telemetry_events_rejected_total counter",
        f"telemetry_events_rejected_total {processor_metrics.get('events_rejected', 0)} {ts}",

        "# HELP fleet_geofence_warnings_total Total geofence boundary warnings",
        "# TYPE fleet_geofence_warnings_total counter",
        f"fleet_geofence_warnings_total {processor_metrics.get('geofence_warnings', 0)} {ts}",

        "# HELP fleet_dedup_cache_size Current size of the deduplication cache",
        "# TYPE fleet_dedup_cache_size gauge",
        f"fleet_dedup_cache_size {processor_metrics.get('dedup_cache_size', 0)} {ts}",

        "# HELP fleet_stored_events_total Total events persisted in event store",
        "# TYPE fleet_stored_events_total gauge",
        f"fleet_stored_events_total {processor_metrics.get('total_stored_events', 0)} {ts}",

        "# HELP fleet_devices_tracked Total edge devices currently tracked",
        "# TYPE fleet_devices_tracked gauge",
        f"fleet_devices_tracked {fleet_device_count} {ts}",

        "# HELP fleet_anomaly_samples_total Total samples fed into the anomaly detector",
        "# TYPE fleet_anomaly_samples_total counter",
        f"fleet_anomaly_samples_total {anomaly_sample_count} {ts}",
    ]

    return "\n".join(lines) + "\n"
