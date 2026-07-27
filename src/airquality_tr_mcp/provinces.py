from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources

from .models import Station
from .normalization import (
    AmbiguousMatchError,
    NoMatchError,
    fuzzy_best_match,
    normalize_tr,
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


@dataclass
class ProvinceResolution:
    province: str
    district: str | None
    note: str | None


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

    try:
        match = fuzzy_best_match(normalized_query, canonical)
    except NoMatchError as exc:
        raise NoMatchError(province_input, exc.suggestions) from exc
    except AmbiguousMatchError as exc:
        raise AmbiguousMatchError(province_input, exc.candidates) from exc

    note = (
        f"'{province_input}' bulunamadı, "
        f"'{match.value}' için sonuçlar gösteriliyor."
    )
    return ProvinceResolution(
        province=match.value,
        district=district_input,
        note=note,
    )
