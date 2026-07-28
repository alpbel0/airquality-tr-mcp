from airquality_tr_mcp.comparison import (
    common_measured_pollutants,
    province_worst_pollutant_value,
)
from airquality_tr_mcp.parsing import parse_bulk_stations


def _load_all_stations(load_fixture_text):
    raw = load_fixture_text(
        "GetAirQualityStations_bulk_tum_ag.network-response"
    )
    return parse_bulk_stations(raw)


def test_common_measured_pollutants_returns_canonical_order(
    load_fixture_text,
):
    stations = _load_all_stations(load_fixture_text)
    batman = [s for s in stations if s.city == "Batman"]
    ankara = [s for s in stations if s.city == "Ankara"]

    common = common_measured_pollutants(batman, ankara)

    assert common == sorted(
        common,
        key=("PM10", "PM25", "SO2", "NO2", "CO", "O3").index,
    )
    assert "PM10" in common


def test_common_measured_pollutants_excludes_unmatched_pollutant(
    load_fixture_text,
):
    stations = _load_all_stations(load_fixture_text)
    station_a = stations[0].model_copy(deep=True)
    station_a.parameters = ["PM10", "SO2"]
    station_b = stations[1].model_copy(deep=True)
    station_b.parameters = ["PM10", "NO2"]

    common = common_measured_pollutants([station_a], [station_b])

    assert common == ["PM10"]


def test_common_measured_pollutants_returns_empty_when_no_overlap(
    load_fixture_text,
):
    stations = _load_all_stations(load_fixture_text)
    station_a = stations[0].model_copy(deep=True)
    station_a.parameters = ["SO2"]
    station_b = stations[1].model_copy(deep=True)
    station_b.parameters = ["NO2"]

    assert common_measured_pollutants([station_a], [station_b]) == []


def test_province_worst_pollutant_value_returns_max_non_null(
    load_fixture_text,
):
    stations = _load_all_stations(load_fixture_text)
    station_a = stations[0].model_copy(deep=True)
    station_a.current.pm10 = 20.0
    station_b = stations[1].model_copy(deep=True)
    station_b.current.pm10 = 55.0

    assert (
        province_worst_pollutant_value("PM10", [station_a, station_b]) == 55.0
    )


def test_province_worst_pollutant_value_returns_none_when_all_missing(
    load_fixture_text,
):
    stations = _load_all_stations(load_fixture_text)
    station_a = stations[0].model_copy(deep=True)
    station_a.current.co = None

    assert province_worst_pollutant_value("CO", [station_a]) is None
