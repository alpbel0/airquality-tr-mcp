from __future__ import annotations

POLLUTANT_FIELDS: dict[str, str] = {
    "NO2": "no2",
    "SO2": "so2",
    "CO": "co",
    "O3": "o3",
    "PM10": "pm10",
    "PM25": "pm25",
}

ALERT_POLLUTANTS: tuple[str, ...] = (
    "HKI",
    "PM10",
    "PM2.5",
    "SO2",
    "NO2",
    "CO",
    "O3",
)


def resolve_pollutant(pollutant: str) -> str | None:
    """Canonicalize a user-facing pollutant string for check_alert."""
    normalized = pollutant.strip().upper()
    if not normalized:
        return None
    if normalized == "HKI":
        return "HKI"
    if normalized == "PM2.5":
        normalized = "PM25"
    return normalized if normalized in POLLUTANT_FIELDS else None
