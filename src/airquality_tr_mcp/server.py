from __future__ import annotations

import logging
import re
import sys

from fastmcp import FastMCP
from fastmcp.server.lifespan import lifespan

from .advisories import advisory_for_status
from .aggregation import summarize_aqi
from .cache import CachedProvider
from .categories import category_for_status
from .comparison import (
    common_measured_pollutants,
    province_worst_pollutant_value,
)
from .geocoding import (
    CachedGeocoder,
    Geocoder,
    GeocodingError,
    PeliasGeocoder,
)
from .historical import (
    InvalidDaysError,
    SequentialThrottle,
    compute_trend,
    daily_summaries,
    fetch_station_window,
    validate_history_days,
    validate_trend_days,
)
from .models import POLLUTANT_UNIT, Station
from .normalization import (
    AmbiguousMatchError,
    NoMatchError,
    fuzzy_best_match,
    normalize_tr,
)
from .provider import AirQualityProvider, UhkiaProvider, UpstreamError
from .pollutants import POLLUTANT_FIELDS, resolve_pollutant
from .provinces import (
    match_district,
    resolve_province_input,
    stations_in_province,
)
from .ranking import (
    InvalidLimitError,
    InvalidModeError,
    RankingCache,
    rank_provinces,
    rank_stations,
    validate_ranking_args,
)
from .responses import (
    air_quality_summary_payload,
    compare_cities_payload,
    district_error_payload,
    geocoding_error_payload,
    invalid_days_payload,
    invalid_limit_payload,
    invalid_mode_payload,
    invalid_nearest_input_payload,
    invalid_pollutant_payload,
    malformed_station_id_payload,
    missing_station_id_payload,
    nearest_air_quality_payload,
    province_ranking_row,
    resolution_error_payload,
    station_breakdown_row,
    station_detail_payload,
    station_history_payload,
    station_ranking_row,
    station_summary,
    trend_summary_payload,
    upstream_error_payload,
)
from .spatial import (
    InvalidNearestInputError,
    select_nearest_stations,
    validate_nearest_input,
)

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

provider: AirQualityProvider = CachedProvider(UhkiaProvider())
geocoder: Geocoder = CachedGeocoder(PeliasGeocoder())
_detailed_ranking_cache = RankingCache()
STATION_ID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
PARTIAL_STATION_ID_RE = re.compile(r"^[0-9a-fA-F-]{6,}$")


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
    active_resources = (provider, geocoder)
    yield
    for resource in active_resources:
        close = getattr(resource, "aclose", None)
        if close is not None:
            try:
                await close()
            except Exception:
                logging.getLogger(__name__).exception(
                    "Resource cleanup failed"
                )


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
    district_note = None
    district_label = resolution.district
    if resolution.district:
        district_match = match_district(
            resolution.district, province_stations
        )
        if district_match is None:
            return _attach_staleness_warning(
                district_error_payload(
                    resolution.province,
                    resolution.district,
                    province_stations,
                ),
                stale_warning,
            )
        breakdown_source = district_match.stations
        district_note = district_match.note
        district_label = district_match.matched_name

    combined_note = (
        f"{resolution.note} {district_note}".strip()
        if resolution.note and district_note
        else district_note or resolution.note
    )
    province_summary = summarize_aqi(province_stations)
    if province_summary is None:
        return _attach_staleness_warning(
            {
                "il": resolution.province,
                "not": combined_note,
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

    payload = {
        "il": resolution.province,
        "not": combined_note,
        "il_ozeti": air_quality_summary_payload(province_summary),
        "istasyonlar": [
            station_breakdown_row(station)
            for station in breakdown_source
        ],
    }
    if resolution.district:
        payload["ilce_ozeti"] = air_quality_summary_payload(
            summarize_aqi(breakdown_source),
            scope_label=district_label,
        )
    return _attach_staleness_warning(payload, stale_warning)


@mcp.tool
async def get_nearest_air_quality(
    location: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
    limit: int = 3,
    max_distance_km: float = 75.0,
) -> dict:
    """Konuma en yakın resmi UHKİA istasyonlarının güncel verisini döndürür.

    location kullanımı ORS_API_KEY gerektirir ve metni HeiGIT/Pelias'a
    gönderir. latitude/longitude kullanımı geocoder çağırmaz. referans_hki
    tahmin veya ortalama değil, en yakın geçerli istasyonun resmi HKİ'sidir.

    limit parametresi 'yakin_istasyonlar' (geçerli HKİ verisi olan) ve
    'verisi_olmayan_yakin_istasyonlar' (veri gelmeyen) listelerine AYRI
    AYRI uygulanır; toplam yanıt en fazla 2×limit istasyon içerebilir.
    """
    try:
        mode = validate_nearest_input(
            location=location,
            latitude=latitude,
            longitude=longitude,
            limit=limit,
            max_distance_km=max_distance_km,
        )
    except InvalidNearestInputError as exc:
        return invalid_nearest_input_payload(exc)

    if mode == "text":
        assert location is not None
        try:
            place = await geocoder.geocode(location.strip())
        except GeocodingError as exc:
            return geocoding_error_payload(exc)
        resolved_latitude = place.latitude
        resolved_longitude = place.longitude
        input_payload = {"location": location}
        location_payload = {
            "etiket": place.label,
            "lat": place.latitude,
            "lon": place.longitude,
            "eslesme_tipi": place.match_type,
            "konum_kaynagi": "heigit_pelias",
        }
    else:
        resolved_latitude = latitude
        resolved_longitude = longitude
        input_payload = {
            "latitude": latitude,
            "longitude": longitude,
        }
        location_payload = {
            "lat": latitude,
            "lon": longitude,
            "konum_kaynagi": "kullanici_koordinati",
        }

    assert resolved_latitude is not None
    assert resolved_longitude is not None

    try:
        stations = await provider.fetch_all_stations()
    except UpstreamError as exc:
        return upstream_error_payload(exc)
    stale_warning = _pop_staleness_warning()
    selection = select_nearest_stations(
        stations,
        latitude=resolved_latitude,
        longitude=resolved_longitude,
        limit=limit,
        max_distance_km=max_distance_km,
    )
    return _attach_staleness_warning(
        nearest_air_quality_payload(
            input_payload=input_payload,
            location_payload=location_payload,
            selection=selection,
            max_distance_km=max_distance_km,
        ),
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

    if PARTIAL_STATION_ID_RE.match(query):
        return _attach_staleness_warning(
            malformed_station_id_payload(query), stale_warning
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
                NoMatchError(query, exc.suggestions),
                entity_label="istasyon",
                parameter_name="station",
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


def _select_history_stations(
    resolution, province_stations: list[Station]
) -> tuple[list[Station], str | None] | None:
    """Select district matches or the province's worst rated station."""
    if resolution.district:
        district_match = match_district(
            resolution.district, province_stations
        )
        if district_match is None:
            return None
        return district_match.stations, district_match.note

    rated = [
        station
        for station in province_stations
        if station.current.aqi_index is not None
    ]
    if not rated:
        return None
    return [
        max(rated, key=lambda station: station.current.aqi_index)
    ], None


def _select_alert_stations(
    resolution, province_stations: list[Station], value_getter
) -> tuple[list[Station], str | None] | None:
    """Select district matches or the province's highest metric station."""
    if resolution.district:
        district_match = match_district(
            resolution.district, province_stations
        )
        if district_match is None:
            return None
        return district_match.stations, district_match.note

    rated = [
        station
        for station in province_stations
        if value_getter(station.current) is not None
    ]
    if not rated:
        return None
    return [
        max(rated, key=lambda station: value_getter(station.current))
    ], None


@mcp.tool
async def get_historical_data(
    province: str, days: int, district: str | None = None
) -> dict:
    """Geçmiş hava kalitesini istasyon bazında günlük HKİ özetiyle döndürür.

    days 1 ile 90 arasında olmalıdır. İlçe verilmezse ilin güncel en
    kötü istasyonu, ilçe verilirse eşleşen tüm istasyonlar kullanılır.

    days büyüdükçe yanıt süresi de uzar: geçmiş veri sabit ~3 günlük
    sayfalar halinde, sayfalar arası 1 saniye beklemeyle çekiliyor
    (örn. days=87 tek istasyon için ~36 saniye sürer). İlçede birden
    fazla istasyon varsa süreler istasyon başına toplanır. Hızlı bir
    yanıt gerekiyorsa küçük bir days değeri (örn. 7-14) tercih edin.
    """
    try:
        validate_history_days(days)
    except InvalidDaysError as exc:
        return invalid_days_payload(exc)

    try:
        stations = await provider.fetch_all_stations()
    except UpstreamError as exc:
        return upstream_error_payload(exc)
    stale_warning = _pop_staleness_warning()

    try:
        resolution = resolve_province_input(
            province, district, stations
        )
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

    selection = _select_history_stations(
        resolution, province_stations
    )
    if selection is None:
        if resolution.district:
            return _attach_staleness_warning(
                district_error_payload(
                    resolution.province,
                    resolution.district,
                    province_stations,
                ),
                stale_warning,
            )
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
                    for station in province_stations
                ],
            },
            stale_warning,
        )

    target_stations, district_note = selection
    combined_note = (
        f"{resolution.note} {district_note}".strip()
        if resolution.note and district_note
        else district_note or resolution.note
    )
    throttle = SequentialThrottle()
    station_payloads = []
    for station in target_stations:
        try:
            readings = await fetch_station_window(
                provider, station.id, days, throttle=throttle
            )
        except UpstreamError as exc:
            return _attach_staleness_warning(
                upstream_error_payload(exc), stale_warning
            )
        station_payloads.append(
            station_history_payload(
                station, daily_summaries(readings)
            )
        )

    return _attach_staleness_warning(
        {
            "il": resolution.province,
            "not": combined_note,
            "gun_sayisi": days,
            "istasyonlar": station_payloads,
        },
        stale_warning,
    )


@mcp.tool
async def get_trend_summary(
    province: str, days: int = 3, district: str | None = None
) -> dict:
    """Hava kalitesinin 3 veya 6 günlük kural tabanlı trendini döndürür.

    İlçe verilmezse ilin güncel en kötü istasyonu, ilçe verilirse eşleşen
    tüm istasyonlar ayrı ayrı değerlendirilir. yon alanı 'iyilesiyor',
    'kotulesiyor', 'stabil' değerlerinin yanı sıra 'yetersiz_veri' de
    olabilir — bu, pencerenin ilk veya ikinci yarısında hiç ölçüm
    bulunmadığı, dolayısıyla bir trend hesaplanamadığı anlamına gelir.
    """
    try:
        validate_trend_days(days)
    except InvalidDaysError as exc:
        return invalid_days_payload(exc)

    try:
        stations = await provider.fetch_all_stations()
    except UpstreamError as exc:
        return upstream_error_payload(exc)
    stale_warning = _pop_staleness_warning()

    try:
        resolution = resolve_province_input(
            province, district, stations
        )
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

    selection = _select_history_stations(
        resolution, province_stations
    )
    if selection is None:
        if resolution.district:
            return _attach_staleness_warning(
                district_error_payload(
                    resolution.province,
                    resolution.district,
                    province_stations,
                ),
                stale_warning,
            )
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
                    for station in province_stations
                ],
            },
            stale_warning,
        )

    target_stations, district_note = selection
    combined_note = (
        f"{resolution.note} {district_note}".strip()
        if resolution.note and district_note
        else district_note or resolution.note
    )
    throttle = SequentialThrottle()
    trend_payloads = []
    for station in target_stations:
        try:
            readings = await fetch_station_window(
                provider, station.id, days, throttle=throttle
            )
        except UpstreamError as exc:
            return _attach_staleness_warning(
                upstream_error_payload(exc), stale_warning
            )
        trend_payloads.append(
            trend_summary_payload(
                station, compute_trend(readings, days)
            )
        )

    return _attach_staleness_warning(
        {
            "il": resolution.province,
            "not": combined_note,
            "istasyonlar": trend_payloads,
        },
        stale_warning,
    )


@mcp.tool
async def compare_cities(province1: str, province2: str) -> dict:
    """İki ilin hava kalitesi özetini yan yana karşılaştırır.

    İstasyon bazlı döküm için get_air_quality kullanılmalıdır.
    """
    try:
        stations = await provider.fetch_all_stations()
    except UpstreamError as exc:
        return upstream_error_payload(exc)
    stale_warning = _pop_staleness_warning()

    try:
        resolution1 = resolve_province_input(province1, None, stations)
    except (NoMatchError, AmbiguousMatchError) as exc:
        return _attach_staleness_warning(
            resolution_error_payload(exc, parameter_name="province1"),
            stale_warning,
        )

    try:
        resolution2 = resolve_province_input(province2, None, stations)
    except (NoMatchError, AmbiguousMatchError) as exc:
        return _attach_staleness_warning(
            resolution_error_payload(exc, parameter_name="province2"),
            stale_warning,
        )

    province_stations1 = stations_in_province(
        resolution1.province, stations
    )
    province_stations2 = stations_in_province(
        resolution2.province, stations
    )
    if not province_stations1 or not province_stations2:
        empty_province = (
            resolution1.province
            if not province_stations1
            else resolution2.province
        )
        return _attach_staleness_warning(
            {
                "uyari": (
                    f"'{empty_province}' ilinde şu an aktif istasyon "
                    "bulunmuyor, karşılaştırma yapılamıyor."
                )
            },
            stale_warning,
        )

    stations1 = province_stations1
    if resolution1.district:
        normalized_district1 = normalize_tr(resolution1.district)
        stations1 = [
            station
            for station in province_stations1
            if normalize_tr(station.district) == normalized_district1
        ]
        if not stations1:
            return _attach_staleness_warning(
                district_error_payload(
                    resolution1.province,
                    resolution1.district,
                    province_stations1,
                ),
                stale_warning,
            )

    stations2 = province_stations2
    if resolution2.district:
        normalized_district2 = normalize_tr(resolution2.district)
        stations2 = [
            station
            for station in province_stations2
            if normalize_tr(station.district) == normalized_district2
        ]
        if not stations2:
            return _attach_staleness_warning(
                district_error_payload(
                    resolution2.province,
                    resolution2.district,
                    province_stations2,
                ),
                stale_warning,
            )

    rated1 = [
        station
        for station in stations1
        if station.current.aqi_index is not None
    ]
    rated2 = [
        station
        for station in stations2
        if station.current.aqi_index is not None
    ]
    if not rated1 or not rated2:
        empty_province = (
            resolution1.province if not rated1 else resolution2.province
        )
        return _attach_staleness_warning(
            {
                "uyari": (
                    f"'{empty_province}' ilindeki hiçbir istasyonda şu "
                    "an geçerli bir HKİ ölçümü yok, karşılaştırma "
                    "yapılamıyor."
                )
            },
            stale_warning,
        )

    worst1 = max(rated1, key=lambda station: station.current.aqi_index)
    best1 = min(rated1, key=lambda station: station.current.aqi_index)
    worst2 = max(rated2, key=lambda station: station.current.aqi_index)
    best2 = min(rated2, key=lambda station: station.current.aqi_index)
    common_pollutants = {
        pollutant: {
            "il1_en_kotu": province_worst_pollutant_value(
                pollutant, stations1
            ),
            "il2_en_kotu": province_worst_pollutant_value(
                pollutant, stations2
            ),
            "birim": POLLUTANT_UNIT,
        }
        for pollutant in common_measured_pollutants(stations1, stations2)
    }

    return _attach_staleness_warning(
        compare_cities_payload(
            resolution1,
            worst1,
            best1,
            stations1,
            resolution2,
            worst2,
            best2,
            stations2,
            common_pollutants,
        ),
        stale_warning,
    )


@mcp.tool
async def get_ranking(mode: str, limit: int) -> dict:
    """İllerin hızlı sıralaması (il başına tek temsili değer).

    Genel en kirli/en temiz iller soruları için bunu kullanın.
    """
    try:
        validate_ranking_args(mode, limit)
    except InvalidModeError as exc:
        return invalid_mode_payload(exc)
    except InvalidLimitError as exc:
        return invalid_limit_payload(exc)

    try:
        stations = await provider.fetch_all_stations()
    except UpstreamError as exc:
        return upstream_error_payload(exc)
    stale_warning = _pop_staleness_warning()
    ranked = rank_provinces(stations, mode, limit)
    return _attach_staleness_warning(
        {
            "mode": mode,
            "siralama": [
                province_ranking_row(rank) for rank in ranked
            ],
        },
        stale_warning,
    )


@mcp.tool
async def get_detailed_ranking(mode: str, limit: int) -> dict:
    """İstasyon seviyesinde, il bazında özetlenmemiş derin sıralama."""
    try:
        validate_ranking_args(mode, limit)
    except InvalidModeError as exc:
        return invalid_mode_payload(exc)
    except InvalidLimitError as exc:
        return invalid_limit_payload(exc)

    try:
        stations = await provider.fetch_all_stations()
    except UpstreamError as exc:
        return upstream_error_payload(exc)
    stale_warning = _pop_staleness_warning()

    cache_key = (mode, limit)
    ranked = _detailed_ranking_cache.get(cache_key)
    if ranked is None:
        ranked = rank_stations(stations, mode, limit)
        _detailed_ranking_cache.set(cache_key, ranked)

    return _attach_staleness_warning(
        {
            "mode": mode,
            "siralama": [
                station_ranking_row(station) for station in ranked
            ],
        },
        stale_warning,
    )


@mcp.tool
async def get_health_advisory(province: str) -> dict:
    """İlin temsili hava kalitesine göre kural tabanlı tavsiye döndürür."""
    try:
        stations = await provider.fetch_all_stations()
    except UpstreamError as exc:
        return upstream_error_payload(exc)
    stale_warning = _pop_staleness_warning()

    try:
        resolution = resolve_province_input(province, None, stations)
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
                "not": resolution.note,
                "uyari": (
                    f"'{resolution.province}' ilinde şu an aktif "
                    "istasyon bulunmuyor."
                ),
            },
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
                    f"'{resolution.province}' ilindeki hiçbir "
                    "istasyonda şu an geçerli bir HKİ ölçümü yok."
                ),
                "tavsiye": advisory_for_status(None),
            },
            stale_warning,
        )

    worst = max(
        rated_stations, key=lambda station: station.current.aqi_index
    )
    return _attach_staleness_warning(
        {
            "il": resolution.province,
            "not": resolution.note,
            "temsili_hki": worst.current.aqi_index,
            "temsili_kategori": category_for_status(
                worst.current.aqi_status
            ),
            "tavsiye": advisory_for_status(worst.current.aqi_status),
        },
        stale_warning,
    )


@mcp.tool
async def check_alert(
    province: str,
    threshold: float,
    pollutant: str = "HKI",
    district: str | None = None,
) -> dict:
    """Verilen hava kalitesi eşiğinin aşılıp aşılmadığını kontrol eder."""
    canonical_pollutant = resolve_pollutant(pollutant)
    if canonical_pollutant is None:
        return invalid_pollutant_payload(pollutant)

    if canonical_pollutant == "HKI":

        def value_getter(reading):
            return reading.aqi_index

        unit = None
    else:
        field = POLLUTANT_FIELDS[canonical_pollutant]

        def value_getter(reading, field=field):
            return getattr(reading, field)

        unit = POLLUTANT_UNIT

    try:
        stations = await provider.fetch_all_stations()
    except UpstreamError as exc:
        return upstream_error_payload(exc)
    stale_warning = _pop_staleness_warning()

    try:
        resolution = resolve_province_input(
            province, district, stations
        )
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
                "not": resolution.note,
                "uyari": (
                    f"'{resolution.province}' ilinde şu an aktif "
                    "istasyon bulunmuyor."
                ),
            },
            stale_warning,
        )

    selection = _select_alert_stations(
        resolution, province_stations, value_getter
    )
    if selection is None:
        if resolution.district:
            return _attach_staleness_warning(
                district_error_payload(
                    resolution.province,
                    resolution.district,
                    province_stations,
                ),
                stale_warning,
            )
        return _attach_staleness_warning(
            {
                "il": resolution.province,
                "not": resolution.note,
                "uyari": (
                    f"'{resolution.province}' ilindeki hiçbir "
                    f"istasyonda {canonical_pollutant} için şu an "
                    "geçerli bir ölçüm yok."
                ),
            },
            stale_warning,
        )

    target_stations, district_note = selection
    combined_note = (
        f"{resolution.note} {district_note}".strip()
        if resolution.note and district_note
        else district_note or resolution.note
    )
    rows = []
    exceeded_any = False
    for station in target_stations:
        value = value_getter(station.current)
        exceeded = value is not None and value > threshold
        exceeded_any = exceeded_any or exceeded
        row = {
            "ad": station.name,
            "il": station.city,
            "ilce": station.district,
            "deger": value,
            "esik_asildi": exceeded,
            "olcum_zamani": station.current.measured_at.isoformat(),
        }
        if value is None:
            row["durum"] = "veri_yok"
        if unit is not None:
            row["birim"] = unit
        rows.append(row)

    return _attach_staleness_warning(
        {
            "il": resolution.province,
            "not": combined_note,
            "kirletici": canonical_pollutant,
            "esik": threshold,
            "esik_asildi": exceeded_any,
            "istasyonlar": rows,
        },
        stale_warning,
    )


if __name__ == "__main__":
    mcp.run(show_banner=False)
