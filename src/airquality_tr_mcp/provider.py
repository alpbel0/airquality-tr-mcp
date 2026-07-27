from __future__ import annotations

from typing import Protocol

import httpx

from .models import Station
from .parsing import parse_bulk_stations

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


class UpstreamError(Exception):
    """Raised when UHKİA is unreachable or returns an unexpected response."""


class AirQualityProvider(Protocol):
    async def fetch_all_stations(self) -> list[Station]: ...


class UhkiaProvider:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client

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

    async def fetch_all_stations(self) -> list[Station]:
        try:
            response = await self._get_client().post(
                "/Services/GetAirQualityStations",
                params={"type": "0"},
                data={"Location": "", "Date": ""},
                headers=COMMON_HEADERS,
            )
        except httpx.TimeoutException as exc:
            raise UpstreamError(
                "UHKİA sunucusuna bağlanılamadı (zaman aşımı)."
            ) from exc
        except httpx.HTTPError as exc:
            raise UpstreamError(
                "UHKİA sunucusuna bağlanılamadı."
            ) from exc

        content_type = response.headers.get("content-type", "")
        if response.status_code != 200 or "json" not in content_type:
            raise UpstreamError(
                "UHKİA beklenmeyen bir yanıt döndürdü "
                f"(status={response.status_code})."
            )
        try:
            return parse_bulk_stations(response.text)
        except (ValueError, KeyError, TypeError, IndexError) as exc:
            raise UpstreamError(
                "UHKİA verisi işlenemedi (geçersiz yanıt formatı)."
            ) from exc
