import pytest
from datetime import datetime, timedelta as _timedelta

from airquality_tr_mcp.historical import (
    MAX_DAYS,
    MIN_DAYS,
    TREND_WINDOWS,
    InvalidDaysError,
    SequentialThrottle,
    validate_history_days,
    validate_trend_days,
)
from airquality_tr_mcp.models import ISTANBUL_TZ, StationReading


class SequentialThrottleNoSleep(SequentialThrottle):
    def __init__(self):
        super().__init__(sleep=self._noop)

    @staticmethod
    async def _noop(seconds):
        return None


def test_validate_history_days_accepts_boundaries():
    validate_history_days(MIN_DAYS)
    validate_history_days(MAX_DAYS)


def test_validate_history_days_rejects_below_minimum():
    with pytest.raises(InvalidDaysError):
        validate_history_days(0)


def test_validate_history_days_rejects_above_maximum():
    with pytest.raises(InvalidDaysError):
        validate_history_days(91)


def test_validate_trend_days_accepts_three_and_six():
    for days in TREND_WINDOWS:
        validate_trend_days(days)


def test_validate_trend_days_rejects_four():
    with pytest.raises(InvalidDaysError) as exc_info:
        validate_trend_days(4)
    assert exc_info.value.allowed_values == TREND_WINDOWS


async def test_sequential_throttle_does_not_sleep_before_first_call():
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    throttle = SequentialThrottle(sleep=fake_sleep)
    await throttle.wait()
    assert sleeps == []


async def test_sequential_throttle_sleeps_before_every_subsequent_call():
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    throttle = SequentialThrottle(sleep=fake_sleep)
    await throttle.wait()
    await throttle.wait()
    await throttle.wait()
    assert sleeps == [1.0, 1.0]


def _reading(
    hours_ago: int, now: datetime, *, aqi=10.0
) -> StationReading:
    return StationReading.model_validate(
        {
            "StationId": "abc",
            "Date": (now - _timedelta(hours=hours_ago))
            .replace(tzinfo=None)
            .isoformat(),
            "NO2": None,
            "SO2": None,
            "CO": None,
            "O3": None,
            "PM10": None,
            "PM25": None,
            "CO_1": None,
            "O3_1": None,
            "PM10_1": None,
            "AQIIndex": aqi,
            "AQIStatus": 0,
            "ContaminantParameter": "NO2",
            "AQIType": 0,
        }
    )


class _StubProvider:
    def __init__(self, responses: dict) -> None:
        self._responses = responses
        self.calls: list[datetime | None] = []

    async def fetch_all_stations(self):
        return []

    async def fetch_station_history(self, station_id, end_date=None):
        self.calls.append(end_date)
        key = None if end_date is None else end_date.isoformat()
        return self._responses.get(key, [])


async def test_fetch_station_window_single_call_for_days_within_six():
    from airquality_tr_mcp.historical import fetch_station_window

    now = datetime(2026, 7, 27, 12, 0, 0, tzinfo=ISTANBUL_TZ)
    readings = [_reading(hours, now) for hours in range(0, 145)]
    provider = _StubProvider({None: readings})

    result = await fetch_station_window(
        provider,
        "abc",
        3,
        throttle=SequentialThrottleNoSleep(),
        now=now,
    )

    assert len(provider.calls) == 1
    assert all(
        reading.measured_at >= now - _timedelta(days=3)
        for reading in result
    )
    assert result == sorted(result, key=lambda reading: reading.measured_at)


async def test_fetch_station_window_steps_end_date_for_days_beyond_six():
    from airquality_tr_mcp.historical import fetch_station_window

    now = datetime(2026, 7, 27, 12, 0, 0, tzinfo=ISTANBUL_TZ)
    first_batch = [_reading(hours, now) for hours in range(0, 145)]
    oldest_first = min(reading.measured_at for reading in first_batch)
    second_batch = [
        StationReading.model_validate(
            {
                "StationId": "abc",
                "Date": (
                    oldest_first - _timedelta(hours=hours)
                ).replace(tzinfo=None).isoformat(),
                "NO2": None,
                "SO2": None,
                "CO": None,
                "O3": None,
                "PM10": None,
                "PM25": None,
                "CO_1": None,
                "O3_1": None,
                "PM10_1": None,
                "AQIIndex": 10.0,
                "AQIStatus": 0,
                "ContaminantParameter": "NO2",
                "AQIType": 0,
            }
        )
        for hours in range(1, 74)
    ]
    provider = _StubProvider(
        {None: first_batch, oldest_first.isoformat(): second_batch}
    )

    result = await fetch_station_window(
        provider,
        "abc",
        8,
        throttle=SequentialThrottleNoSleep(),
        now=now,
    )

    assert len(provider.calls) == 2
    assert provider.calls[1] == oldest_first
    assert min(reading.measured_at for reading in result) <= (
        now - _timedelta(days=8)
    )


async def test_fetch_station_window_dedupes_overlapping_hours():
    from airquality_tr_mcp.historical import fetch_station_window

    now = datetime(2026, 7, 27, 12, 0, 0, tzinfo=ISTANBUL_TZ)
    first_batch = [_reading(hours, now) for hours in range(0, 145)]
    oldest_first = min(reading.measured_at for reading in first_batch)
    overlapping = [_reading(144, now)] + [
        StationReading.model_validate(
            {
                "StationId": "abc",
                "Date": (
                    oldest_first - _timedelta(hours=hours)
                ).replace(tzinfo=None).isoformat(),
                "NO2": None,
                "SO2": None,
                "CO": None,
                "O3": None,
                "PM10": None,
                "PM25": None,
                "CO_1": None,
                "O3_1": None,
                "PM10_1": None,
                "AQIIndex": 10.0,
                "AQIStatus": 0,
                "ContaminantParameter": "NO2",
                "AQIType": 0,
            }
        )
        for hours in range(1, 74)
    ]
    provider = _StubProvider(
        {None: first_batch, oldest_first.isoformat(): overlapping}
    )

    result = await fetch_station_window(
        provider,
        "abc",
        8,
        throttle=SequentialThrottleNoSleep(),
        now=now,
    )

    timestamps = [reading.measured_at for reading in result]
    assert len(timestamps) == len(set(timestamps))


def test_daily_summaries_groups_by_istanbul_local_day():
    from airquality_tr_mcp.historical import daily_summaries

    now = datetime(2026, 7, 27, 1, 0, 0, tzinfo=ISTANBUL_TZ)
    readings = [
        _reading(0, now, aqi=10.0),
        _reading(3, now, aqi=30.0),
        _reading(4, now, aqi=20.0),
    ]

    summaries = daily_summaries(readings)

    assert [summary.date for summary in summaries] == [
        "2026-07-26",
        "2026-07-27",
    ]
    day_26 = summaries[0]
    assert day_26.hki_min == 20.0
    assert day_26.hki_max == 30.0
    assert day_26.hki_ortalama == 25.0
    assert day_26.baskin_kirletici == "NO2"


def test_daily_summaries_returns_none_fields_for_day_with_no_rated_readings():
    from airquality_tr_mcp.historical import daily_summaries

    now = datetime(2026, 7, 27, 1, 0, 0, tzinfo=ISTANBUL_TZ)
    unrated = _reading(0, now, aqi=None)

    summaries = daily_summaries([unrated])

    assert summaries[0].hki_min is None
    assert summaries[0].hki_max is None
    assert summaries[0].hki_ortalama is None
    assert summaries[0].baskin_kirletici is None


def test_compute_trend_detects_worsening():
    from airquality_tr_mcp.historical import compute_trend

    now = datetime(2026, 7, 27, 12, 0, 0, tzinfo=ISTANBUL_TZ)
    readings = [
        _reading(hours, now, aqi=10.0) for hours in range(36, 72)
    ] + [_reading(hours, now, aqi=30.0) for hours in range(0, 36)]

    result = compute_trend(readings, 3)

    assert result.direction == "kotulesiyor"
    assert result.window_days == 3
    assert result.first_half_avg == 10.0
    assert result.second_half_avg == 30.0
    assert result.difference == 20.0


def test_compute_trend_detects_improving():
    from airquality_tr_mcp.historical import compute_trend

    now = datetime(2026, 7, 27, 12, 0, 0, tzinfo=ISTANBUL_TZ)
    readings = [
        _reading(hours, now, aqi=30.0) for hours in range(36, 72)
    ] + [_reading(hours, now, aqi=10.0) for hours in range(0, 36)]

    result = compute_trend(readings, 3)

    assert result.direction == "iyilesiyor"


def test_compute_trend_detects_stable_within_threshold():
    from airquality_tr_mcp.historical import compute_trend

    now = datetime(2026, 7, 27, 12, 0, 0, tzinfo=ISTANBUL_TZ)
    readings = [
        _reading(hours, now, aqi=20.0) for hours in range(36, 72)
    ] + [_reading(hours, now, aqi=23.0) for hours in range(0, 36)]

    result = compute_trend(readings, 3)

    assert result.direction == "stabil"


def test_compute_trend_returns_stable_with_none_averages_when_insufficient_data():
    from airquality_tr_mcp.historical import compute_trend

    now = datetime(2026, 7, 27, 12, 0, 0, tzinfo=ISTANBUL_TZ)
    readings = [_reading(0, now, aqi=None)]

    result = compute_trend(readings, 3)

    assert result.direction == "stabil"
    assert result.first_half_avg is None
    assert result.second_half_avg is None
    assert result.difference is None
