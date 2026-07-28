from __future__ import annotations

from dataclasses import dataclass
from statistics import fmean, median

from .models import Station


@dataclass(frozen=True)
class AqiSummary:
    highest: float
    average: float
    median: float
    lowest: float
    valid_station_count: int
    worst_station: Station
    best_station: Station


def summarize_aqi(stations: list[Station]) -> AqiSummary | None:
    rated = [
        station
        for station in stations
        if station.current.aqi_index is not None
    ]
    if not rated:
        return None

    values = [station.current.aqi_index for station in rated]
    worst = max(rated, key=lambda station: station.current.aqi_index)
    best = min(rated, key=lambda station: station.current.aqi_index)
    return AqiSummary(
        highest=worst.current.aqi_index,
        average=round(fmean(values), 1),
        median=round(median(values), 1),
        lowest=best.current.aqi_index,
        valid_station_count=len(rated),
        worst_station=worst,
        best_station=best,
    )
