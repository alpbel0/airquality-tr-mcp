from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Protocol

import httpx

from .models import Station, StationReading
from .parsing import parse_bulk_stations, parse_historical_readings

BASE_URL = "https://sim.csb.gov.tr"
COMMON_HEADERS = {
    "Referer": f"{BASE_URL}/Services/AirQuality",
    "Origin": BASE_URL,
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}

RETRYABLE_STATUS_CODES = frozenset({500, 502, 503, 504})
RETRY_DELAYS_SECONDS: tuple[float, ...] = (0.5, 1.0, 2.0)
DEFAULT_RATE_LIMIT_INTERVAL_SECONDS = 1.0


def _default_jitter() -> float:
    return random.uniform(-0.2, 0.2)


class UpstreamError(Exception):
    """Raised when UHKİA is unreachable or returns an unexpected response."""


class AirQualityProvider(Protocol):
    async def fetch_all_stations(self) -> list[Station]: ...

    async def fetch_station_history(
        self, station_id: str, end_date: datetime | None = None
    ) -> list[StationReading]: ...


class UhkiaProvider:
    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        rate_limit_interval: float = DEFAULT_RATE_LIMIT_INTERVAL_SECONDS,
        retry_delays: tuple[float, ...] = RETRY_DELAYS_SECONDS,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter: Callable[[], float] = _default_jitter,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._rate_limit_interval = rate_limit_interval
        self._retry_delays = retry_delays
        self._sleep = sleep
        self._jitter = jitter
        self._clock = clock
        self._throttle_lock = asyncio.Lock()
        self._last_request_at: float | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=BASE_URL,
                timeout=httpx.Timeout(10.0, connect=5.0),
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _throttle(self) -> None:
        """Cap outgoing traffic to about one request per second."""
        async with self._throttle_lock:
            now = self._clock()
            if self._last_request_at is not None:
                wait = self._rate_limit_interval - (
                    now - self._last_request_at
                )
                if wait > 0:
                    await self._sleep(wait)
            self._last_request_at = self._clock()

    async def _post(
        self, path: str, *, params: dict | None, data: dict
    ) -> str:
        last_message = "UHKİA sunucusuna bağlanılamadı."
        last_exc: Exception | None = None
        attempts = len(self._retry_delays) + 1

        for attempt in range(attempts):
            if attempt > 0:
                delay = self._retry_delays[attempt - 1]
                await self._sleep(max(0.0, delay + delay * self._jitter()))

            await self._throttle()
            try:
                response = await self._get_client().post(
                    path,
                    params=params,
                    data=data,
                    headers=COMMON_HEADERS,
                )
            except httpx.TimeoutException as exc:
                last_message = "UHKİA sunucusuna bağlanılamadı (zaman aşımı)."
                last_exc = exc
                continue
            except httpx.HTTPError as exc:
                last_message = "UHKİA sunucusuna bağlanılamadı."
                last_exc = exc
                continue

            if response.status_code in RETRYABLE_STATUS_CODES:
                last_message = (
                    "UHKİA beklenmeyen bir yanıt döndürdü "
                    f"(status={response.status_code})."
                )
                last_exc = None
                continue

            content_type = response.headers.get("content-type", "")
            if response.status_code != 200 or "json" not in content_type:
                raise UpstreamError(
                    "UHKİA beklenmeyen bir yanıt döndürdü "
                    f"(status={response.status_code})."
                )
            return response.text

        raise UpstreamError(
            f"{last_message} ({attempts} denemenin tümü başarısız oldu.)"
        ) from last_exc

    async def fetch_all_stations(self) -> list[Station]:
        text = await self._post(
            "/Services/GetAirQualityStations",
            params={"type": "0"},
            data={"Location": "", "Date": ""},
        )
        try:
            return parse_bulk_stations(text)
        except (ValueError, KeyError, TypeError, IndexError) as exc:
            raise UpstreamError(
                "UHKİA verisi işlenemedi (geçersiz yanıt formatı)."
            ) from exc

    async def fetch_station_history(
        self, station_id: str, end_date: datetime | None = None
    ) -> list[StationReading]:
        if end_date is None:
            text = await self._post(
                "/Services/GetDetailData",
                params=None,
                data={"stationId": station_id},
            )
        else:
            text = await self._post(
                "/Services/GetAirQualityStationDetail",
                params={"type": "0"},
                data={
                    "stationId": station_id,
                    "endDate": end_date.strftime("%d.%m.%Y %H:%M:%S"),
                },
            )
        try:
            return parse_historical_readings(text)
        except (ValueError, KeyError, TypeError, IndexError) as exc:
            raise UpstreamError(
                "UHKİA verisi işlenemedi (geçersiz yanıt formatı)."
            ) from exc
