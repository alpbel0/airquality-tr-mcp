from __future__ import annotations

from .models import Station
from .pollutants import POLLUTANT_FIELDS

_CANONICAL_ORDER = ("PM10", "PM25", "SO2", "NO2", "CO", "O3")


def common_measured_pollutants(
    stations1: list[Station], stations2: list[Station]
) -> list[str]:
    """Return pollutants measured on both sides in canonical order."""
    measured1 = {p for station in stations1 for p in station.parameters}
    measured2 = {p for station in stations2 for p in station.parameters}
    return [
        pollutant
        for pollutant in _CANONICAL_ORDER
        if pollutant in measured1 and pollutant in measured2
    ]


def province_worst_pollutant_value(
    pollutant: str, stations: list[Station]
) -> float | None:
    """Return the highest non-null pollutant reading."""
    field = POLLUTANT_FIELDS[pollutant]
    values = [
        value
        for station in stations
        if (value := getattr(station.current, field)) is not None
    ]
    return max(values) if values else None
