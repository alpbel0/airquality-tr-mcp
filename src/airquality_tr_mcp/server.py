from __future__ import annotations

import logging
import re
import sys

from fastmcp import FastMCP
from fastmcp.server.lifespan import lifespan

from .cache import CachedProvider
from .normalization import (
    AmbiguousMatchError,
    NoMatchError,
    fuzzy_best_match,
    normalize_tr,
)
from .provider import AirQualityProvider, UhkiaProvider, UpstreamError
from .provinces import resolve_province_input, stations_in_province
from .responses import (
    district_error_payload,
    missing_station_id_payload,
    resolution_error_payload,
    station_breakdown_row,
    station_detail_payload,
    station_ref_with_category,
    station_summary,
    upstream_error_payload,
)

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

provider: AirQualityProvider = CachedProvider(UhkiaProvider())
STATION_ID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _pop_staleness_warning() -> str | None:
    pop = getattr(provider, "pop_staleness_warning", None)
    return pop() if pop is not None else None


def _attach_staleness_warning(
    payload: dict, warning: str | None
) -> dict:
    if warning:
        payload["veri_bayat_uyarisi"] = warning
    return payload


@lifespan
async def _provider_lifespan(_server):
    active_provider = provider
    yield
    close = getattr(active_provider, "aclose", None)
    if close is not None:
        await close()


mcp = FastMCP(
    name="airquality-tr-mcp",
    lifespan=_provider_lifespan,
)


@mcp.tool
def ping() -> str:
    """Sunucunun ayakta olup olmadığını kontrol eden geçici araç."""
    return "pong"


@mcp.tool
async def list_stations(province: str | None = None) -> dict:
    """List UHKİA stations, optionally restricted to one province."""
    try:
        stations = await provider.fetch_all_stations()
    except UpstreamError as exc:
        return upstream_error_payload(exc)
    stale_warning = _pop_staleness_warning()

    if province is None:
        return _attach_staleness_warning(
            {
                "istasyonlar": [
                    station_summary(station) for station in stations
                ]
            },
            stale_warning,
        )

    try:
        resolution = resolve_province_input(province, None, stations)
    except (NoMatchError, AmbiguousMatchError) as exc:
        return _attach_staleness_warning(
            resolution_error_payload(exc), stale_warning
        )

    matched = stations_in_province(resolution.province, stations)
    if not matched:
        return _attach_staleness_warning(
            {
                "il": resolution.province,
                "istasyonlar": [],
                "uyari": (
                    f"'{resolution.province}' ilinde şu an aktif "
                    "istasyon bulunmuyor."
                ),
            },
            stale_warning,
        )

    return _attach_staleness_warning(
        {
            "il": resolution.province,
            "not": resolution.note,
            "istasyonlar": [
                station_summary(station) for station in matched
            ],
        },
        stale_warning,
    )


@mcp.tool
async def get_air_quality(
    province: str, district: str | None = None
) -> dict:
    """Return a province AQI summary and per-station breakdown."""
    try:
        stations = await provider.fetch_all_stations()
    except UpstreamError as exc:
        return upstream_error_payload(exc)
    stale_warning = _pop_staleness_warning()

    try:
        resolution = resolve_province_input(province, district, stations)
    except (NoMatchError, AmbiguousMatchError) as exc:
        return _attach_staleness_warning(
            resolution_error_payload(exc), stale_warning
        )

    province_stations = stations_in_province(
        resolution.province, stations
    )
    if not province_stations:
        return _attach_staleness_warning(
            {
                "il": resolution.province,
                "uyari": (
                    f"'{resolution.province}' ilinde şu an aktif "
                    "istasyon bulunmuyor."
                ),
                "not": resolution.note,
            },
            stale_warning,
        )

    breakdown_source = province_stations
    if resolution.district:
        normalized_district = normalize_tr(resolution.district)
        breakdown_source = [
            station
            for station in province_stations
            if normalize_tr(station.district) == normalized_district
        ]
        if not breakdown_source:
            return _attach_staleness_warning(
                district_error_payload(
                    resolution.province,
                    resolution.district,
                    province_stations,
                ),
                stale_warning,
            )

    rated_stations = [
        station
        for station in province_stations
        if station.current.aqi_index is not None
    ]
    if not rated_stations:
        return _attach_staleness_warning(
            {
                "il": resolution.province,
                "not": resolution.note,
                "uyari": (
                    f"'{resolution.province}' ilindeki hiçbir istasyonda "
                    "şu an geçerli bir HKİ ölçümü yok."
                ),
                "istasyonlar": [
                    station_breakdown_row(station)
                    for station in breakdown_source
                ],
            },
            stale_warning,
        )

    worst = max(
        rated_stations, key=lambda station: station.current.aqi_index
    )
    best = min(
        rated_stations, key=lambda station: station.current.aqi_index
    )
    return _attach_staleness_warning(
        {
            "il": resolution.province,
            "not": resolution.note,
            "il_ozeti": {
                "temsili_hki": worst.current.aqi_index,
                "temsili_kategori": station_ref_with_category(worst)[
                    "kategori"
                ],
                "en_kotu_istasyon": station_ref_with_category(worst),
                "en_iyi_istasyon": station_ref_with_category(best),
            },
            "istasyonlar": [
                station_breakdown_row(station)
                for station in breakdown_source
            ],
        },
        stale_warning,
    )


@mcp.tool
async def get_station_detail(station: str) -> dict:
    """Return the current full reading for a station ID or name."""
    try:
        stations = await provider.fetch_all_stations()
    except UpstreamError as exc:
        return upstream_error_payload(exc)
    stale_warning = _pop_staleness_warning()

    query = station.strip()
    if STATION_ID_RE.match(query):
        match = next(
            (
                candidate
                for candidate in stations
                if candidate.id.lower() == query.lower()
            ),
            None,
        )
        if match is None:
            return _attach_staleness_warning(
                missing_station_id_payload(query), stale_warning
            )
        return _attach_staleness_warning(
            station_detail_payload(match), stale_warning
        )

    normalized_query = normalize_tr(query)
    exact = next(
        (
            candidate
            for candidate in stations
            if normalize_tr(candidate.name) == normalized_query
        ),
        None,
    )
    if exact is not None:
        return _attach_staleness_warning(
            station_detail_payload(exact), stale_warning
        )

    try:
        fuzzy = fuzzy_best_match(
            normalized_query,
            [candidate.name for candidate in stations],
        )
    except NoMatchError as exc:
        return _attach_staleness_warning(
            resolution_error_payload(
                NoMatchError(query, exc.suggestions)
            ),
            stale_warning,
        )
    except AmbiguousMatchError as exc:
        return _attach_staleness_warning(
            resolution_error_payload(
                AmbiguousMatchError(query, exc.candidates),
                entity_label="istasyon",
                parameter_name="station",
            ),
            stale_warning,
        )

    matched_station = next(
        candidate
        for candidate in stations
        if candidate.name == fuzzy.value
    )
    payload = station_detail_payload(matched_station)
    payload["not"] = (
        f"'{query}' bulunamadı, "
        f"'{fuzzy.value}' için sonuçlar gösteriliyor."
    )
    return _attach_staleness_warning(payload, stale_warning)


if __name__ == "__main__":
    mcp.run(show_banner=False)
