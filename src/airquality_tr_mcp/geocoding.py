from __future__ import annotations

import asyncio
import json
import os
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol

import httpx

from .normalization import normalize_tr

MatchType = Literal["exact", "interpolated", "fallback"]
PELIAS_SEARCH_URL = "https://api.heigit.org/pelias/v1/search"
SUCCESS_TTL_SECONDS = 86400.0
NEGATIVE_TTL_SECONDS = 600.0
MAX_GEOCODING_CACHE_ENTRIES = 256


@dataclass(frozen=True)
class GeocodedPlace:
    label: str
    latitude: float
    longitude: float
    match_type: MatchType
    confidence: float | None = None


class GeocodingError(Exception):
    pass


class MissingApiKeyError(GeocodingError):
    pass


class GeocodingAuthError(GeocodingError):
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


def parse_pelias_candidates(
    payload: object,
) -> tuple[GeocodedPlace, ...]:
    if not isinstance(payload, dict) or not isinstance(
        payload.get("features"), list
    ):
        raise GeocodingResponseError(
            "HeiGIT/Pelias geçersiz bir yanıt döndürdü."
        )
    candidates = []
    for feature in payload["features"][:3]:
        try:
            properties = feature["properties"]
            coordinates = feature["geometry"]["coordinates"]
            label = properties["label"]
            match_type = properties["match_type"]
            longitude, latitude = coordinates[:2]
            confidence = properties.get("confidence")
            if (
                not isinstance(label, str)
                or match_type not in {"exact", "interpolated", "fallback"}
                or isinstance(longitude, bool)
                or not isinstance(longitude, (int, float))
                or isinstance(latitude, bool)
                or not isinstance(latitude, (int, float))
            ):
                raise TypeError
        except (KeyError, TypeError, ValueError) as exc:
            raise GeocodingResponseError(
                "HeiGIT/Pelias yanıtındaki konum verisi işlenemedi."
            ) from exc
        candidates.append(
            GeocodedPlace(
                label=label,
                longitude=float(longitude),
                latitude=float(latitude),
                match_type=match_type,
                confidence=(
                    float(confidence)
                    if isinstance(confidence, (int, float))
                    else None
                ),
            )
        )
    return tuple(candidates)


def choose_pelias_candidate(
    query: str, candidates: tuple[GeocodedPlace, ...]
) -> GeocodedPlace:
    if not candidates or candidates[0].match_type == "fallback":
        raise LocationNotFoundError(query)
    distinct: dict[tuple[str, float, float], GeocodedPlace] = {}
    for candidate in candidates:
        if candidate.match_type == "fallback":
            continue
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


class PeliasGeocoder:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._client = client

    def _resolved_api_key(self) -> str:
        key = self._api_key
        if key is None:
            key = os.getenv("ORS_API_KEY")
        if not key or not key.strip():
            raise MissingApiKeyError(
                "Metinle konum aramak için ORS_API_KEY gereklidir."
            )
        return key.strip()

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(10.0, connect=5.0)
            )
        return self._client

    async def geocode(self, query: str) -> GeocodedPlace:
        key = self._resolved_api_key()
        try:
            response = await self._get_client().get(
                PELIAS_SEARCH_URL,
                params={
                    "text": query.strip(),
                    "boundary.country": "TUR",
                    "lang": "tr",
                    "size": 3,
                },
                headers={"Authorization": key},
            )
        except httpx.TimeoutException as exc:
            raise GeocodingTimeoutError(
                "HeiGIT/Pelias isteği zaman aşımına uğradı."
            ) from exc
        except httpx.HTTPError as exc:
            raise GeocodingServiceError(
                "HeiGIT/Pelias servisine bağlanılamadı."
            ) from exc

        if response.status_code in {401, 403}:
            raise GeocodingAuthError(
                "HeiGIT API anahtarı kabul edilmedi."
            )
        if response.status_code == 429:
            raise GeocodingRateLimitError(
                "HeiGIT API kullanım sınırı aşıldı."
            )
        if response.status_code != 200:
            raise GeocodingServiceError(
                "HeiGIT/Pelias beklenmeyen bir yanıt döndürdü "
                f"(status={response.status_code})."
            )
        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise GeocodingResponseError(
                "HeiGIT/Pelias yanıtı JSON olarak işlenemedi."
            ) from exc
        candidates = parse_pelias_candidates(payload)
        return choose_pelias_candidate(query, candidates)

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
