from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from .models import Station, StationReading
from .provider import AirQualityProvider, UpstreamError

DEFAULT_TTL_SECONDS = 600.0
DEFAULT_STALE_SECONDS = 3600.0


@dataclass
class _CacheEntry:
    stations: list[Station]
    fetched_at: float


class CachedProvider:
    """Add single-flight TTL caching and stale-if-error fallback."""

    def __init__(
        self,
        inner: AirQualityProvider,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        stale_seconds: float = DEFAULT_STALE_SECONDS,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._inner = inner
        self._ttl_seconds = ttl_seconds
        self._stale_seconds = stale_seconds
        self._clock = clock
        self._entry: _CacheEntry | None = None
        self._last_attempt_at: float | None = None
        self._lock = asyncio.Lock()
        self._staleness_warning: str | None = None

    def _fresh_stations(self, now: float) -> list[Station] | None:
        if self._entry is None:
            return None
        if now - self._entry.fetched_at >= self._ttl_seconds:
            return None
        return self._entry.stations

    def _stale_stations(self, now: float) -> list[Station] | None:
        if self._entry is None:
            return None
        if now - self._entry.fetched_at >= self._stale_seconds:
            return None
        age_minutes = int((now - self._entry.fetched_at) // 60)
        self._staleness_warning = (
            "Güncel veri alınamadı, "
            f"{age_minutes} dakika önceki veri gösteriliyor."
        )
        return self._entry.stations

    async def fetch_all_stations(self) -> list[Station]:
        now = self._clock()
        fresh = self._fresh_stations(now)
        if fresh is not None:
            return fresh

        async with self._lock:
            now = self._clock()
            fresh = self._fresh_stations(now)
            if fresh is not None:
                return fresh

            if (
                self._last_attempt_at is not None
                and now - self._last_attempt_at < self._ttl_seconds
            ):
                stale = self._stale_stations(now)
                if stale is not None:
                    return stale

            self._last_attempt_at = now
            try:
                stations = await self._inner.fetch_all_stations()
            except UpstreamError:
                stale = self._stale_stations(now)
                if stale is not None:
                    return stale
                raise

            self._entry = _CacheEntry(stations=stations, fetched_at=now)
            self._staleness_warning = None
            return stations

    async def fetch_station_history(
        self, station_id: str, end_date: datetime | None = None
    ) -> list[StationReading]:
        return await self._inner.fetch_station_history(station_id, end_date)

    def pop_staleness_warning(self) -> str | None:
        warning = self._staleness_warning
        self._staleness_warning = None
        return warning

    async def aclose(self) -> None:
        close = getattr(self._inner, "aclose", None)
        if close is not None:
            await close()
