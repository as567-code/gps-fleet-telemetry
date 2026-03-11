"""
Fleet simulation script — generates realistic GPS telemetry
from a simulated fleet of edge devices.

Usage:
    python simulate_fleet.py --devices 50 --duration 60 --url http://localhost:8000
"""

import argparse
import json
import random
import time
from datetime import datetime, timezone

import httpx

# San Francisco bounding box for realistic GPS data
SF_BOUNDS = {
    "min_lat": 37.70, "max_lat": 37.82,
    "min_lon": -122.52, "max_lon": -122.38,
}

DEVICE_TYPES = ["bus_camera", "lidar_unit", "gps_tracker", "edge_compute"]
ROUTE_IDS = ["SF-MUNI-38", "SF-MUNI-14", "SF-MUNI-22", "SF-MUNI-49", "SF-MUNI-7"]


def generate_device_fleet(n: int) -> list[dict]:
    """Generate a fleet of simulated edge devices."""
    devices = []
    for i in range(n):
        devices.append({
            "device_id": f"fleet-{DEVICE_TYPES[i % len(DEVICE_TYPES)]}-{i:04d}",
            "device_type": DEVICE_TYPES[i % len(DEVICE_TYPES)],
            "lat": random.uniform(SF_BOUNDS["min_lat"], SF_BOUNDS["max_lat"]),
            "lon": random.uniform(SF_BOUNDS["min_lon"], SF_BOUNDS["max_lon"]),
            "battery": random.uniform(30, 100),
            "route": random.choice(ROUTE_IDS),
            "heading": random.uniform(0, 360),
        })
    return devices


def simulate_movement(device: dict) -> dict:
    """Simulate GPS movement for one tick."""
    device["lat"] += random.gauss(0, 0.0005)
    device["lon"] += random.gauss(0, 0.0005)
    device["lat"] = max(SF_BOUNDS["min_lat"], min(SF_BOUNDS["max_lat"], device["lat"]))
    device["lon"] = max(SF_BOUNDS["min_lon"], min(SF_BOUNDS["max_lon"], device["lon"]))
    device["battery"] = max(0, device["battery"] - random.uniform(0, 0.1))
    device["heading"] = (device["heading"] + random.gauss(0, 10)) % 360
    return device


def build_event(device: dict) -> dict:
    """Build a telemetry event payload from device state."""
    # Occasionally simulate anomalous readings (5% chance)
    is_anomaly = random.random() < 0.05
    temp = random.uniform(70, 84) if is_anomaly else random.uniform(25, 55)
    signal = random.uniform(-118, -105) if is_anomaly else random.uniform(-80, -40)

    return {
        "device_id": device["device_id"],
        "device_type": device["device_type"],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "gps": {
            "latitude": round(device["lat"], 6),
            "longitude": round(device["lon"], 6),
            "altitude_m": round(random.uniform(0, 50), 1),
            "hdop": round(random.uniform(0.5, 3.0), 2),
            "speed_kmh": round(random.uniform(0, 60), 1),
            "heading_deg": round(device["heading"], 1),
        },
        "sensors": {
            "battery_pct": round(device["battery"], 1),
            "temperature_c": round(temp, 1),
            "signal_strength_dbm": round(signal, 1),
            "storage_used_pct": round(random.uniform(10, 80), 1),
            "cpu_usage_pct": round(random.uniform(5, 60), 1),
            "uptime_seconds": random.randint(3600, 864000),
        },
        "route_id": device["route"],
    }


def main():
    parser = argparse.ArgumentParser(description="Simulate GPS fleet telemetry")
    parser.add_argument("--devices", type=int, default=20, help="Number of devices")
    parser.add_argument("--duration", type=int, default=30, help="Duration in seconds")
    parser.add_argument("--interval", type=float, default=1.0, help="Seconds between ticks")
    parser.add_argument("--url", default="http://localhost:8000", help="Pipeline URL")
    args = parser.parse_args()

    fleet = generate_device_fleet(args.devices)
    client = httpx.Client(timeout=10.0)

    print(f"Simulating {args.devices} devices for {args.duration}s → {args.url}")
    print(f"Sending events every {args.interval}s...\n")

    start = time.time()
    total_sent = 0
    total_accepted = 0
    total_dupes = 0

    try:
        while time.time() - start < args.duration:
            # Build batch of events from all devices
            events = []
            for device in fleet:
                simulate_movement(device)
                events.append(build_event(device))

            # Send as batch
            try:
                resp = client.post(
                    f"{args.url}/api/v1/events/batch",
                    json={"events": events},
                    timeout=10.0,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    total_sent += len(events)
                    total_accepted += data["accepted"]
                    total_dupes += data["duplicates"]
                    elapsed = time.time() - start
                    print(
                        f"[{elapsed:6.1f}s] Sent {len(events)} events | "
                        f"Accepted: {data['accepted']} | "
                        f"Dupes: {data['duplicates']} | "
                        f"Total: {total_sent}"
                    )
                else:
                    print(f"Error: HTTP {resp.status_code}")
            except Exception as e:
                print(f"Connection error: {e}")

            time.sleep(args.interval)

    except KeyboardInterrupt:
        print("\nStopped by user.")

    # Final fleet health check
    try:
        health = client.get(f"{args.url}/api/v1/fleet/health").json()
        print(f"\n{'='*50}")
        print(f"Fleet Health Summary")
        print(f"{'='*50}")
        print(f"  Devices:      {health['total_devices']}")
        print(f"  Healthy:      {health['healthy']}")
        print(f"  Degraded:     {health['degraded']}")
        print(f"  Offline:      {health['offline']}")
        print(f"  Anomalies:    {health['anomalies']}")
        print(f"  Health Score: {health['fleet_health_score']}%")
        print(f"  Events Total: {health['events_processed_total']}")
        print(f"  Events/min:   {health['events_per_minute']}")
        print(f"  Avg Battery:  {health['avg_battery_pct']}%")
        print(f"  Avg Signal:   {health['avg_signal_dbm']} dBm")
    except Exception:
        pass

    print(f"\nDone. Sent {total_sent} events, {total_accepted} accepted, {total_dupes} duplicates.")


if __name__ == "__main__":
    main()
