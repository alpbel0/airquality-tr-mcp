from __future__ import annotations

import asyncio
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Awaitable, Callable

from .models import ISTANBUL_TZ, StationReading
from .provider import AirQualityProvider

MIN_DAYS = 1
MAX_DAYS = 90
TREND_WINDOWS = (3, 6)
TREND_THRESHOLD_HKI = 5.0


class InvalidDaysError(Exception):
    def __init__(
        self,
        days: int,
        *,
        minimum: int,
        maximum: int,
        allowed_values: tuple[int, ...] | None = None,
    ) -> None:
        self.days = days
        self.minimum = minimum
        self.maximum = maximum
        self.allowed_values = allowed_values
        super().__init__(
            f"invalid days={days}, expected {minimum}..{maximum}"
        )


def validate_history_days(days: int) -> None:
    if days < MIN_DAYS or days > MAX_DAYS:
        raise InvalidDaysError(
            days, minimum=MIN_DAYS, maximum=MAX_DAYS
        )


def validate_trend_days(days: int) -> None:
    if days not in TREND_WINDOWS:
        raise InvalidDaysError(
            days,
            minimum=min(TREND_WINDOWS),
            maximum=max(TREND_WINDOWS),
            allowed_values=TREND_WINDOWS,
        )


class SequentialThrottle:
    """Delay every upstream call after the first call."""

    def __init__(
        self,
        delay_seconds: float = 1.0,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._delay_seconds = delay_seconds
        self._sleep = sleep
        self._called = False

    async def wait(self) -> None:
        if self._called:
            await self._sleep(self._delay_seconds)
        self._called = True


async def fetch_station_window(
    provider: AirQualityProvider,
    station_id: str,
    days: int,
    *,
    throttle: SequentialThrottle,
    now: datetime | None = None,
) -> list[StationReading]:
    """Fetch, deduplicate, and time-filter one station's hourly history."""
    now = now or datetime.now(ISTANBUL_TZ)
    cutoff = now - timedelta(days=days)

    await throttle.wait()
    readings = list(await provider.fetch_station_history(station_id))

    if days > 6:
        oldest = min(
            (reading.measured_at for reading in readings), default=None
        )
        while oldest is not None and oldest > cutoff:
            await throttle.wait()
            step_readings = await provider.fetch_station_history(
                station_id, end_date=oldest
            )
            if not step_readings:
                break
            readings.extend(step_readings)
            new_oldest = min(
                reading.measured_at for reading in step_readings
            )
            if new_oldest >= oldest:
                break
            oldest = new_oldest

    deduped = {
        (reading.station_id, reading.measured_at): reading
        for reading in readings
    }
    return sorted(
        (
            reading
            for reading in deduped.values()
            if cutoff <= reading.measured_at <= now
        ),
        key=lambda reading: reading.measured_at,
    )


@dataclass
class DailySummary:
    date: str
    hki_min: float | None
    hki_max: float | None
    hki_ortalama: float | None
    baskin_kirletici: str | None


def daily_summaries(
    readings: list[StationReading],
) -> list[DailySummary]:
    by_day: dict[str, list[StationReading]] = defaultdict(list)
    for reading in readings:
        local_date = (
            reading.measured_at.astimezone(ISTANBUL_TZ).date().isoformat()
        )
        by_day[local_date].append(reading)

    summaries: list[DailySummary] = []
    for date in sorted(by_day):
        day_readings = by_day[date]
        rated = [
            reading
            for reading in day_readings
            if reading.aqi_index is not None
        ]
        if not rated:
            summaries.append(DailySummary(date, None, None, None, None))
            continue

        hki_values = [
            reading.aqi_index
            for reading in rated
            if reading.aqi_index is not None
        ]
        dominant_counts = Counter(
            reading.dominant_pollutant
            for reading in day_readings
            if reading.dominant_pollutant
        )
        dominant = (
            dominant_counts.most_common(1)[0][0]
            if dominant_counts
            else None
        )
        summaries.append(
            DailySummary(
                date=date,
                hki_min=min(hki_values),
                hki_max=max(hki_values),
                hki_ortalama=sum(hki_values) / len(hki_values),
                baskin_kirletici=dominant,
            )
        )
    return summaries


@dataclass
class TrendResult:
    direction: str
    window_days: int
    first_half_avg: float | None
    second_half_avg: float | None
    difference: float | None


def compute_trend(
    readings: list[StationReading], window_days: int
) -> TrendResult:
    sorted_readings = sorted(
        readings, key=lambda reading: reading.measured_at
    )
    rated = [
        reading
        for reading in sorted_readings
        if reading.aqi_index is not None
    ]
    if len(rated) < 2:
        return TrendResult("stabil", window_days, None, None, None)

    midpoint = len(rated) // 2
    first_half = rated[:midpoint]
    second_half = rated[midpoint:]
    first_avg = sum(
        reading.aqi_index
        for reading in first_half
        if reading.aqi_index is not None
    ) / len(first_half)
    second_avg = sum(
        reading.aqi_index
        for reading in second_half
        if reading.aqi_index is not None
    ) / len(second_half)
    difference = second_avg - first_avg

    if difference <= -TREND_THRESHOLD_HKI:
        direction = "iyilesiyor"
    elif difference >= TREND_THRESHOLD_HKI:
        direction = "kotulesiyor"
    else:
        direction = "stabil"

    return TrendResult(
        direction, window_days, first_avg, second_avg, difference
    )
