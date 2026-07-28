import pytest

from airquality_tr_mcp.models import Station
from airquality_tr_mcp.normalization import (
    AmbiguousMatchError,
    NoMatchError,
)
from airquality_tr_mcp.provinces import (
    DistrictMatch,
    ProvinceResolution,
    correct_bare_province_typo,
    load_provinces,
    match_district,
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


def test_resolve_province_input_fuzzy_corrects_typo_district():
    result = resolve_province_input(
        "Kadikuyy", None, ISTANBUL_STATIONS
    )
    assert result.province == "İstanbul"
    assert result.district == "Kadıköy"
    assert "ilçe" in result.note


def test_resolve_province_input_fuzzy_district_typo_still_flags_ambiguous():
    with pytest.raises(AmbiguousMatchError) as exc_info:
        resolve_province_input(
            "Eregliy", None, KONYA_ZONGULDAK_STATIONS
        )
    assert set(exc_info.value.candidates) == {"Konya", "Zonguldak"}


def test_resolve_province_input_fuzzy_district_ignores_generic_name():
    with pytest.raises(NoMatchError):
        resolve_province_input(
            "Merkezz", None, COMMON_DISTRICT_STATIONS
        )


def test_stations_in_province_filters_by_city():
    stations = ISTANBUL_STATIONS + KONYA_ZONGULDAK_STATIONS
    assert (
        stations_in_province("İstanbul", stations) == ISTANBUL_STATIONS
    )
    assert stations_in_province("Sivas", stations) == []


def test_resolve_province_input_accepts_common_abbreviation_urfa():
    result = resolve_province_input("Urfa", None, [])
    assert result.province == "Şanlıurfa"
    assert result.note is not None
    assert "bulunamadı" in result.note


def test_resolve_province_input_accepts_common_abbreviation_maras():
    result = resolve_province_input("Maras", None, [])
    assert result.province == "Kahramanmaraş"


def test_resolve_province_input_accepts_typo_with_extra_letter():
    result = resolve_province_input("Ursa", None, [])
    assert result.province == "Bursa"


def test_resolve_province_input_flags_ambiguous_for_short_substring_query():
    with pytest.raises(AmbiguousMatchError):
        resolve_province_input("ur", None, [])


def test_resolve_province_input_rejects_short_non_substring_query():
    with pytest.raises(NoMatchError) as exc_info:
        resolve_province_input("bkn", None, [])
    assert exc_info.value.suggestions


ISTANBUL_MULTI_DISTRICT_STATIONS = [
    _station("İstanbul", "Kadıköy", "ist-kadikoy"),
    _station("İstanbul", "Üsküdar", "ist-uskudar"),
    _station("İstanbul", "Beşiktaş", "ist-besiktas"),
]


def test_match_district_returns_exact_match_without_note():
    result = match_district("Kadıköy", ISTANBUL_MULTI_DISTRICT_STATIONS)
    assert result == DistrictMatch(
        stations=[ISTANBUL_MULTI_DISTRICT_STATIONS[0]],
        matched_name="Kadıköy",
        note=None,
    )


def test_match_district_is_case_and_diacritic_insensitive():
    result = match_district("kadikoy", ISTANBUL_MULTI_DISTRICT_STATIONS)
    assert result is not None
    assert result.matched_name == "Kadıköy"
    assert result.note is None


def test_match_district_fuzzy_corrects_typo():
    result = match_district("Kadikoyy", ISTANBUL_MULTI_DISTRICT_STATIONS)
    assert result is not None
    assert result.matched_name == "Kadıköy"
    assert result.stations == [ISTANBUL_MULTI_DISTRICT_STATIONS[0]]
    assert result.note is not None
    assert "Kadıköy" in result.note


def test_match_district_returns_none_when_nothing_matches():
    result = match_district("Zzzzzz", ISTANBUL_MULTI_DISTRICT_STATIONS)
    assert result is None


def test_match_district_returns_none_for_wrong_citys_district():
    result = match_district("Konak", ISTANBUL_MULTI_DISTRICT_STATIONS)
    assert result is None


def test_correct_bare_province_typo_fixes_single_word_typo():
    assert correct_bare_province_typo("amnisa") == "Manisa"
    assert correct_bare_province_typo("ursa") == "Bursa"


def test_correct_bare_province_typo_leaves_multi_word_query_untouched():
    # WRatio would happily match "Bursa" as a substring of the whole
    # phrase, silently discarding the district/POI specificity - so
    # multi-word queries must never be corrected.
    assert (
        correct_bare_province_typo("bursa hürriyet") == "bursa hürriyet"
    )
    assert (
        correct_bare_province_typo("Göbeklitepe, Şanlıurfa")
        == "Göbeklitepe, Şanlıurfa"
    )


def test_correct_bare_province_typo_leaves_unmatched_word_untouched():
    assert correct_bare_province_typo("kepez") == "kepez"


def test_correct_bare_province_typo_leaves_exact_match_untouched():
    assert correct_bare_province_typo("Ankara") == "Ankara"
