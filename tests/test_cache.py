import asyncio
from datetime import datetime

import pytest

from airquality_tr_mcp.cache import CachedProvider
from airquality_tr_mcp.models import StationReading
from airquality_tr_mcp.provider import UpstreamError


class _FakeProvider:
    def __init__(self, batches, *, delay: float = 0.0):
        self._batches = list(batches)
        self._delay = delay
        self.call_count = 0

    async def fetch_all_stations(self):
        self.call_count += 1
        if self._delay:
            await asyncio.sleep(self._delay)
        index = min(self.call_count - 1, len(self._batches) - 1)
        result = self._batches[index]
        if result is UpstreamError:
            raise UpstreamError("simulated failure")
        return result


class _FakeClock:
    def __init__(self, start: float = 0.0):
        self.now = start

    def __call__(self) -> float:
        return self.now


async def test_returns_cached_result_within_ttl():
    inner = _FakeProvider([["a"], ["b"]])
    clock = _FakeClock()
    cache = CachedProvider(inner, ttl_seconds=600.0, clock=clock)

    first = await cache.fetch_all_stations()
    clock.now += 100
    second = await cache.fetch_all_stations()

    assert first == ["a"]
    assert second == ["a"]
    assert inner.call_count == 1


async def test_refetches_after_ttl_expires():
    inner = _FakeProvider([["a"], ["b"]])
    clock = _FakeClock()
    cache = CachedProvider(inner, ttl_seconds=600.0, clock=clock)

    await cache.fetch_all_stations()
    clock.now += 601
    second = await cache.fetch_all_stations()

    assert second == ["b"]
    assert inner.call_count == 2


async def test_single_flight_deduplicates_concurrent_refreshes():
    inner = _FakeProvider([["a"]], delay=0.05)
    clock = _FakeClock()
    cache = CachedProvider(inner, ttl_seconds=600.0, clock=clock)

    results = await asyncio.gather(
        cache.fetch_all_stations(),
        cache.fetch_all_stations(),
        cache.fetch_all_stations(),
    )

    assert results == [["a"], ["a"], ["a"]]
    assert inner.call_count == 1


async def test_serves_stale_cache_on_upstream_failure_within_stale_window():
    inner = _FakeProvider([["a"], UpstreamError])
    clock = _FakeClock()
    cache = CachedProvider(
        inner, ttl_seconds=600.0, stale_seconds=3600.0, clock=clock
    )

    await cache.fetch_all_stations()
    clock.now += 601
    result = await cache.fetch_all_stations()

    assert result == ["a"]
    warning = cache.pop_staleness_warning()
    assert warning is not None
    assert "dakika" in warning


async def test_raises_when_stale_window_also_exceeded():
    inner = _FakeProvider([["a"], UpstreamError])
    clock = _FakeClock()
    cache = CachedProvider(
        inner, ttl_seconds=600.0, stale_seconds=3600.0, clock=clock
    )

    await cache.fetch_all_stations()
    clock.now += 3601

    with pytest.raises(UpstreamError):
        await cache.fetch_all_stations()


async def test_pop_staleness_warning_clears_after_read():
    inner = _FakeProvider([["a"], UpstreamError])
    clock = _FakeClock()
    cache = CachedProvider(
        inner, ttl_seconds=600.0, stale_seconds=3600.0, clock=clock
    )
    await cache.fetch_all_stations()
    clock.now += 601
    await cache.fetch_all_stations()

    assert cache.pop_staleness_warning() is not None
    assert cache.pop_staleness_warning() is None


async def test_successful_refresh_clears_previous_staleness_warning():
    inner = _FakeProvider([["a"], UpstreamError, ["c"]])
    clock = _FakeClock()
    cache = CachedProvider(
        inner, ttl_seconds=600.0, stale_seconds=3600.0, clock=clock
    )
    await cache.fetch_all_stations()
    clock.now += 601
    await cache.fetch_all_stations()
    assert cache.pop_staleness_warning() is not None

    clock.now += 601
    result = await cache.fetch_all_stations()

    assert result == ["c"]
    assert cache.pop_staleness_warning() is None


async def test_aclose_delegates_to_inner_provider():
    class _ClosableInner(_FakeProvider):
        def __init__(self):
            super().__init__([["a"]])
            self.closed = False

        async def aclose(self):
            self.closed = True

    inner = _ClosableInner()
    cache = CachedProvider(inner)

    await cache.aclose()

    assert inner.closed


async def test_aclose_is_a_no_op_when_inner_has_no_aclose():
    class _NoCloseInner(_FakeProvider):
        pass

    cache = CachedProvider(_NoCloseInner([["a"]]))

    await cache.aclose()


async def test_does_not_retry_upstream_within_ttl_window_after_a_failure():
    inner = _FakeProvider([["a"], UpstreamError, UpstreamError])
    clock = _FakeClock()
    cache = CachedProvider(
        inner, ttl_seconds=600.0, stale_seconds=3600.0, clock=clock
    )

    await cache.fetch_all_stations()
    clock.now += 601
    first_failure_result = await cache.fetch_all_stations()
    clock.now += 100
    second_result = await cache.fetch_all_stations()

    assert first_failure_result == ["a"]
    assert second_result == ["a"]
    assert inner.call_count == 2


class _HistoryStubProvider:
    def __init__(self) -> None:
        self.calls: list[tuple[str, datetime | None]] = []

    async def fetch_all_stations(self):
        return []

    async def fetch_station_history(self, station_id, end_date=None):
        self.calls.append((station_id, end_date))
        return [
            StationReading.model_validate(
                {
                    "StationId": station_id,
                    "Date": "2026-07-27T10:00:00",
                    "NO2": None,
                    "SO2": None,
                    "CO": None,
                    "O3": None,
                    "PM10": None,
                    "PM25": None,
                    "CO_1": None,
                    "O3_1": None,
                    "PM10_1": None,
                    "AQIIndex": None,
                    "AQIStatus": None,
                    "ContaminantParameter": None,
                    "AQIType": None,
                }
            )
        ]


async def test_cached_provider_passes_through_station_history_uncached():
    inner = _HistoryStubProvider()
    cached = CachedProvider(inner)

    first = await cached.fetch_station_history("abc")
    second = await cached.fetch_station_history("abc")

    assert len(first) == 1
    assert len(second) == 1
    assert len(inner.calls) == 2
