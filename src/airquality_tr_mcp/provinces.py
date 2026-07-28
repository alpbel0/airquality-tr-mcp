from __future__ import annotations

import json
import re
from dataclasses import dataclass
from importlib import resources

from .models import Station
from .normalization import (
    AmbiguousMatchError,
    NoMatchError,
    fuzzy_best_match,
    normalize_tr,
    weighted_ratio_scorer,
)

MAX_DISTRICT_PROVINCES = 3


def load_provinces() -> list[str]:
    """Load the canonical list of all 81 Turkish provinces."""
    raw = (
        resources.files("airquality_tr_mcp")
        .joinpath("data", "provinces.json")
        .read_text(encoding="utf-8")
    )
    return json.loads(raw)


def correct_bare_province_typo(
    text: str, provinces: list[str] | None = None
) -> str:
    """Best-effort local typo fix for a free-text geocoding query.

    Free-text geocoders (Nominatim) do no fuzzy spell-correction, so a
    bare province typo like "amnisa" returns zero results even though
    "Manisa" clearly resolves via our own fuzzy matcher. Only applies
    when the input is a single word/token: WRatio's length-aware
    matching will happily find a province name as a substring of a
    longer phrase (e.g. "bursa hürriyet" -> "Bursa",
    "Göbeklitepe, Şanlıurfa" -> "Şanlıurfa"), silently discarding the
    district/POI specificity a multi-word query was carrying. No
    station data is needed here, so this never triggers a network
    call."""
    tokens = [token for token in re.split(r"[\s,]+", text.strip()) if token]
    if len(tokens) != 1:
        return text
    canonical = provinces if provinces is not None else load_provinces()
    try:
        match = fuzzy_best_match(
            normalize_tr(tokens[0]), canonical, scorer=weighted_ratio_scorer
        )
    except (NoMatchError, AmbiguousMatchError):
        return text
    return match.value


@dataclass
class ProvinceResolution:
    province: str
    district: str | None
    note: str | None


@dataclass
class DistrictMatch:
    stations: list[Station]
    matched_name: str
    note: str | None


def match_district(
    district_input: str, stations: list[Station]
) -> DistrictMatch | None:
    """Resolve a district against the names present in the station set."""
    normalized_query = normalize_tr(district_input)
    exact = [
        station
        for station in stations
        if normalize_tr(station.district) == normalized_query
    ]
    if exact:
        return DistrictMatch(
            stations=exact,
            matched_name=exact[0].district,
            note=None,
        )

    district_names = sorted({station.district for station in stations})
    try:
        match = fuzzy_best_match(
            normalized_query,
            district_names,
            scorer=weighted_ratio_scorer,
        )
    except (NoMatchError, AmbiguousMatchError):
        return None

    matched = [
        station for station in stations if station.district == match.value
    ]
    note = (
        f"'{district_input}' ilçesi bulunamadı, "
        f"'{match.value}' için sonuçlar gösteriliyor."
    )
    return DistrictMatch(
        stations=matched,
        matched_name=match.value,
        note=note,
    )


def stations_in_province(
    province: str, stations: list[Station]
) -> list[Station]:
    return [station for station in stations if station.city == province]


def _district_candidates(
    normalized_query: str, stations: list[Station]
) -> list[Station]:
    return [
        station
        for station in stations
        if normalize_tr(station.district) == normalized_query
    ]


def _resolve_via_district(
    original_query: str,
    normalized_query: str,
    stations: list[Station],
) -> tuple[str, str] | None:
    matches = _district_candidates(normalized_query, stations)
    if not matches:
        return None

    cities = sorted({station.city for station in matches})
    if len(cities) >= MAX_DISTRICT_PROVINCES:
        return None
    if len(cities) > 1:
        raise AmbiguousMatchError(original_query, cities)
    return cities[0], matches[0].district


def _fuzzy_district_pool(stations: list[Station]) -> dict[str, list[str]]:
    """District name -> owning cities, excluding names too generic to
    identify a single province (same threshold as the exact-match path)."""
    district_to_cities: dict[str, set[str]] = {}
    for station in stations:
        district_to_cities.setdefault(station.district, set()).add(
            station.city
        )
    return {
        district: sorted(cities)
        for district, cities in district_to_cities.items()
        if len(cities) < MAX_DISTRICT_PROVINCES
    }


def resolve_province_input(
    province_input: str,
    district_input: str | None,
    stations: list[Station],
    provinces: list[str] | None = None,
) -> ProvinceResolution:
    canonical = provinces if provinces is not None else load_provinces()
    normalized_query = normalize_tr(province_input)
    canonical_by_normalized = {
        normalize_tr(province): province for province in canonical
    }

    if normalized_query in canonical_by_normalized:
        return ProvinceResolution(
            province=canonical_by_normalized[normalized_query],
            district=district_input,
            note=None,
        )

    district_result = _resolve_via_district(
        province_input, normalized_query, stations
    )
    if district_result is not None:
        city, detected_district = district_result
        note = (
            f"'{province_input}' bir ilçe olarak algılandı, "
            f"bağlı olduğu il: {city}."
        )
        return ProvinceResolution(
            province=city,
            district=district_input or detected_district,
            note=note,
        )

    district_pool = _fuzzy_district_pool(stations)
    combined_candidates = list(canonical) + list(district_pool.keys())

    try:
        match = fuzzy_best_match(
            normalized_query,
            combined_candidates,
            scorer=weighted_ratio_scorer,
        )
    except NoMatchError as exc:
        raise NoMatchError(province_input, exc.suggestions) from exc
    except AmbiguousMatchError as exc:
        raise AmbiguousMatchError(province_input, exc.candidates) from exc

    if match.value in canonical_by_normalized.values():
        note = (
            f"'{province_input}' bulunamadı, "
            f"'{match.value}' için sonuçlar gösteriliyor."
        )
        return ProvinceResolution(
            province=match.value,
            district=district_input,
            note=note,
        )

    owning_cities = district_pool[match.value]
    if len(owning_cities) > 1:
        raise AmbiguousMatchError(province_input, owning_cities)

    city = owning_cities[0]
    note = (
        f"'{province_input}' bir ilçe olarak algılandı "
        f"(yazım hatası '{match.value}' olarak düzeltildi), "
        f"bağlı olduğu il: {city}."
    )
    return ProvinceResolution(
        province=city,
        district=district_input or match.value,
        note=note,
    )
