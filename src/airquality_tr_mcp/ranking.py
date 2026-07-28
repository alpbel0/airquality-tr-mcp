from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from .models import Station

VALID_MODES = ("best", "worst")


class InvalidModeError(Exception):
    def __init__(self, mode: str) -> None:
        self.mode = mode
        super().__init__(f"invalid mode={mode!r}, expected 'best' or 'worst'")


class InvalidLimitError(Exception):
    def __init__(self, limit: int) -> None:
        self.limit = limit
        super().__init__(f"invalid limit={limit}, expected a positive integer")


def validate_ranking_args(mode: str, limit: int) -> None:
    if mode not in VALID_MODES:
        raise InvalidModeError(mode)
    if limit < 1:
        raise InvalidLimitError(limit)


@dataclass
class ProvinceRank:
    province: str
    representative_station: Station


def rank_provinces(
    stations: list[Station], mode: str, limit: int
) -> list[ProvinceRank]:
    """Rank provinces by their representative worst-station HKİ."""
    by_province: dict[str, list[Station]] = {}
    for station in stations:
        if station.current.aqi_index is None:
            continue
        by_province.setdefault(station.city, []).append(station)

    representatives = [
        ProvinceRank(
            province=province,
            representative_station=max(
                province_stations,
                key=lambda station: station.current.aqi_index,
            ),
        )
        for province, province_stations in by_province.items()
    ]
    representatives.sort(
        key=lambda rank: rank.representative_station.current.aqi_index,
        reverse=(mode == "worst"),
    )
    return representatives[:limit]


def rank_stations(
    stations: list[Station], mode: str, limit: int
) -> list[Station]:
    """Rank individual stations nationwide by HKİ."""
    rated = [
        station
        for station in stations
        if station.current.aqi_index is not None
    ]
    rated.sort(
        key=lambda station: station.current.aqi_index,
        reverse=(mode == "worst"),
    )
    return rated[:limit]


class RankingCache:
    """Cache computed detailed-ranking results."""

    def __init__(
        self,
        ttl_seconds: float = 3600.0,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._store: dict[tuple, tuple[float, list[Station]]] = {}

    def get(self, key: tuple) -> list[Station] | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        fetched_at, value = entry
        if self._clock() - fetched_at >= self._ttl_seconds:
            return None
        return value

    def set(self, key: tuple, value: list[Station]) -> None:
        self._store[key] = (self._clock(), value)
