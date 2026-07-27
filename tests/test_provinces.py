import pytest

from airquality_tr_mcp.models import Station
from airquality_tr_mcp.normalization import (
    AmbiguousMatchError,
    NoMatchError,
)
from airquality_tr_mcp.provinces import (
    ProvinceResolution,
    load_provinces,
    resolve_province_input,
    stations_in_province,
)


def test_load_provinces_returns_all_81_provinces():
    provinces = load_provinces()
    assert len(provinces) == 81
    assert len(set(provinces)) == 81


def test_load_provinces_includes_provinces_without_active_stations():
    provinces = load_provinces()
    assert "Hakkari" in provinces
    assert "Malatya" in provinces


def test_load_provinces_includes_dotted_and_dotless_i_provinces():
    provinces = load_provinces()
    assert "İstanbul" in provinces
    assert "Adıyaman" in provinces
    assert "Kırıkkale" in provinces


def _station(city: str, district: str, station_id: str = "id") -> Station:
    return Station.from_raw(
        {
            "id": station_id,
            "Location": "POINT (0.0 0.0)",
            "Name": f"{city} test station {station_id}",
            "City_Title": city,
            "CityId": "city-id",
            "Town_Title": district,
            "LastDataDate": "2026-07-27T00:00:00",
            "Values": {
                "StationId": station_id,
                "Date": "2026-07-27T00:00:00",
                "NO2": None,
                "SO2": None,
                "CO": None,
                "O3": None,
                "PM10": 10.0,
                "PM25": 5.0,
                "CO_1": None,
                "O3_1": None,
                "PM10_1": None,
                "AQIIndex": 42.0,
                "AQIStatus": 1,
                "ContaminantParameter": "PM10",
                "AQIType": 0,
            },
            "Config": {"parameters": "PM10,PM25"},
        }
    )


ISTANBUL_STATIONS = [_station("İstanbul", "Kadıköy", "ist-1")]
KONYA_ZONGULDAK_STATIONS = [
    _station("Konya", "Ereğli", "konya-1"),
    _station("Zonguldak", "Ereğli", "zong-1"),
]
COMMON_DISTRICT_STATIONS = [
    _station("Samsun", "Merkez", "s-1"),
    _station("Mersin", "Merkez", "m-1"),
    _station("Kars", "Merkez", "k-1"),
]


def test_resolve_province_input_matches_canonical_province_exactly():
    result = resolve_province_input("Ankara", None, [])
    assert result == ProvinceResolution(
        province="Ankara", district=None, note=None
    )


def test_resolve_province_input_matches_case_and_ascii_insensitively():
    result = resolve_province_input("  istanbul  ", None, [])
    assert result.province == "İstanbul"
    assert result.note is None


def test_resolve_province_input_auto_detects_single_matching_district():
    result = resolve_province_input("Kadıköy", None, ISTANBUL_STATIONS)
    assert result.province == "İstanbul"
    assert result.district == "Kadıköy"
    assert "ilçe" in result.note


def test_resolve_province_input_explicit_district_wins_over_detected_one():
    result = resolve_province_input("Kadıköy", "Moda", ISTANBUL_STATIONS)
    assert result.province == "İstanbul"
    assert result.district == "Moda"


def test_resolve_province_input_raises_ambiguous_for_two_province_district():
    with pytest.raises(AmbiguousMatchError) as exc_info:
        resolve_province_input(
            "Ereğli", None, KONYA_ZONGULDAK_STATIONS
        )
    assert set(exc_info.value.candidates) == {"Konya", "Zonguldak"}


def test_resolve_province_input_falls_through_common_district_to_fuzzy():
    with pytest.raises(NoMatchError):
        resolve_province_input("Merkez", None, COMMON_DISTRICT_STATIONS)


def test_resolve_province_input_auto_corrects_typo_via_fuzzy_match():
    result = resolve_province_input("Anatlya", None, [])
    assert result.province == "Antalya"
    assert "bulunamadı" in result.note


def test_stations_in_province_filters_by_city():
    stations = ISTANBUL_STATIONS + KONYA_ZONGULDAK_STATIONS
    assert (
        stations_in_province("İstanbul", stations) == ISTANBUL_STATIONS
    )
    assert stations_in_province("Sivas", stations) == []
