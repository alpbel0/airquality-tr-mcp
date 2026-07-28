from __future__ import annotations

import asyncio
import json
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import httpx

from .normalization import normalize_tr

NOMINATIM_SEARCH_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_USER_AGENT = (
    "airquality-tr-mcp/0.1 (https://github.com/alpbel0/airquality-tr-mcp)"
)
MIN_REQUEST_INTERVAL_SECONDS = 1.0
SUCCESS_TTL_SECONDS = 86400.0
NEGATIVE_TTL_SECONDS = 600.0
MAX_GEOCODING_CACHE_ENTRIES = 256


@dataclass(frozen=True)
class GeocodedPlace:
    label: str
    latitude: float
    longitude: float
    importance: float | None = None


class GeocodingError(Exception):
    pass


class GeocodingRateLimitError(GeocodingError):
    pass


class GeocodingTimeoutError(GeocodingError):
    pass


class GeocodingServiceError(GeocodingError):
    pass


class GeocodingResponseError(GeocodingError):
    pass


class LocationNotFoundError(GeocodingError):
    def __init__(self, query: str) -> None:
        super().__init__(query)
        self.query = query


class AmbiguousLocationError(GeocodingError):
    def __init__(
        self, query: str, candidates: tuple[GeocodedPlace, ...]
    ) -> None:
        super().__init__(query)
        self.query = query
        self.candidates = candidates


class Geocoder(Protocol):
    async def geocode(self, query: str) -> GeocodedPlace: ...


def parse_nominatim_candidates(
    payload: object,
) -> tuple[GeocodedPlace, ...]:
    if not isinstance(payload, list):
        raise GeocodingResponseError("Nominatim geçersiz bir yanıt döndürdü.")
    candidates = []
    for item in payload[:3]:
        try:
            if not isinstance(item, dict):
                raise TypeError
            label = item["display_name"]
            latitude = float(item["lat"])
            longitude = float(item["lon"])
            importance = item.get("importance")
            if not isinstance(label, str):
                raise TypeError
        except (KeyError, TypeError, ValueError) as exc:
            raise GeocodingResponseError(
                "Nominatim yanıtındaki konum verisi işlenemedi."
            ) from exc
        candidates.append(
            GeocodedPlace(
                label=label,
                latitude=latitude,
                longitude=longitude,
                importance=(
                    float(importance)
                    if isinstance(importance, (int, float))
                    else None
                ),
            )
        )
    return tuple(candidates)


def choose_nominatim_candidate(
    query: str, candidates: tuple[GeocodedPlace, ...]
) -> GeocodedPlace:
    if not candidates:
        raise LocationNotFoundError(query)
    distinct: dict[tuple[str, float, float], GeocodedPlace] = {}
    for candidate in candidates:
        key = (
            normalize_tr(candidate.label),
            round(candidate.latitude, 6),
            round(candidate.longitude, 6),
        )
        distinct.setdefault(key, candidate)
    usable = tuple(distinct.values())
    if not usable:
        raise LocationNotFoundError(query)
    if len(usable) > 1:
        raise AmbiguousLocationError(query, usable[:3])
    return usable[0]


class NominatimGeocoder:
    """Free, keyless OSM-based geocoder. The public Nominatim instance
    requires clients to self-throttle to at most 1 request/second and to
    send an identifying User-Agent - see
    https://operations.osmfoundation.org/policies/nominatim/."""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._client = client
        self._throttle_lock = asyncio.Lock()
        self._last_request_at: float = 0.0

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(10.0, connect=5.0)
            )
        return self._client

    async def _throttle(self) -> None:
        async with self._throttle_lock:
            wait = MIN_REQUEST_INTERVAL_SECONDS - (
                time.monotonic() - self._last_request_at
            )
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request_at = time.monotonic()

    async def geocode(self, query: str) -> GeocodedPlace:
        await self._throttle()
        try:
            response = await self._get_client().get(
                NOMINATIM_SEARCH_URL,
                params={
                    "q": query.strip(),
                    "countrycodes": "tr",
                    "format": "jsonv2",
                    "limit": 3,
                    "accept-language": "tr",
                },
                headers={"User-Agent": NOMINATIM_USER_AGENT},
            )
        except httpx.TimeoutException as exc:
            raise GeocodingTimeoutError(
                "Nominatim isteği zaman aşımına uğradı."
            ) from exc
        except httpx.HTTPError as exc:
            raise GeocodingServiceError(
                "Nominatim servisine bağlanılamadı."
            ) from exc

        if response.status_code == 429:
            raise GeocodingRateLimitError("Nominatim kullanım sınırı aşıldı.")
        if response.status_code != 200:
            raise GeocodingServiceError(
                "Nominatim beklenmeyen bir yanıt döndürdü "
                f"(status={response.status_code})."
            )
        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise GeocodingResponseError(
                "Nominatim yanıtı JSON olarak işlenemedi."
            ) from exc
        candidates = parse_nominatim_candidates(payload)
        return choose_nominatim_candidate(query, candidates)

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


@dataclass
class _GeocodingCacheEntry:
    value: GeocodedPlace | LocationNotFoundError
    expires_at: float


class CachedGeocoder:
    def __init__(
        self,
        inner: Geocoder,
        *,
        success_ttl_seconds: float = SUCCESS_TTL_SECONDS,
        negative_ttl_seconds: float = NEGATIVE_TTL_SECONDS,
        max_entries: int = MAX_GEOCODING_CACHE_ENTRIES,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._inner = inner
        self._success_ttl_seconds = success_ttl_seconds
        self._negative_ttl_seconds = negative_ttl_seconds
        self._max_entries = max_entries
        self._clock = clock
        self._entries: OrderedDict[str, _GeocodingCacheEntry] = OrderedDict()
        self._inflight: dict[str, asyncio.Task[GeocodedPlace]] = {}
        self._lock = asyncio.Lock()

    def _key(self, query: str) -> str:
        return f"tr|TUR|{normalize_tr(query.strip())}"

    def _cached(self, key: str) -> GeocodedPlace | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        if self._clock() >= entry.expires_at:
            del self._entries[key]
            return None
        self._entries.move_to_end(key)
        if isinstance(entry.value, LocationNotFoundError):
            raise LocationNotFoundError(entry.value.query)
        return entry.value

    async def _fetch(self, key: str, query: str) -> GeocodedPlace:
        current = asyncio.current_task()
        try:
            try:
                result = await self._inner.geocode(query)
            except LocationNotFoundError as exc:
                async with self._lock:
                    self._entries[key] = _GeocodingCacheEntry(
                        value=LocationNotFoundError(exc.query),
                        expires_at=self._clock() + self._negative_ttl_seconds,
                    )
                    self._entries.move_to_end(key)
                    self._trim()
                raise
            async with self._lock:
                self._entries[key] = _GeocodingCacheEntry(
                    value=result,
                    expires_at=self._clock() + self._success_ttl_seconds,
                )
                self._entries.move_to_end(key)
                self._trim()
            return result
        finally:
            async with self._lock:
                if self._inflight.get(key) is current:
                    del self._inflight[key]

    def _trim(self) -> None:
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)

    async def geocode(self, query: str) -> GeocodedPlace:
        key = self._key(query)
        async with self._lock:
            cached = self._cached(key)
            if cached is not None:
                return cached
            task = self._inflight.get(key)
            if task is None:
                task = asyncio.create_task(self._fetch(key, query.strip()))
                self._inflight[key] = task
        return await asyncio.shield(task)

    async def aclose(self) -> None:
        close = getattr(self._inner, "aclose", None)
        if close is not None:
            await close()
