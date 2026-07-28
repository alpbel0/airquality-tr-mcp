from airquality_tr_mcp.parsing import (
    parse_bulk_stations,
    parse_historical_readings,
)


def test_parse_bulk_stations_returns_all_323_stations(load_fixture_text):
    raw_json = load_fixture_text(
        "GetAirQualityStations_bulk_tum_ag.network-response"
    )
    stations = parse_bulk_stations(raw_json)
    assert len(stations) == 323


def test_parse_bulk_stations_finds_batman2_with_partial_sensors(
    load_fixture_text,
):
    raw_json = load_fixture_text(
        "GetAirQualityStations_bulk_tum_ag.network-response"
    )
    stations = parse_bulk_stations(raw_json)
    batman2 = next(s for s in stations if s.name == "Batman - 2")
    assert batman2.current.no2 is None
    assert batman2.current.pm10 == 62.196
    assert batman2.parameters == ["NO2", "O3", "PM10", "SO2", "CO", "PM25"]


def test_parse_historical_readings_returns_145_hours(load_fixture_text):
    raw_json = load_fixture_text(
        "GetDetailData_ankara_bahcelievler_temiz.network-response"
    )
    readings = parse_historical_readings(raw_json)
    assert len(readings) == 145
    first = readings[0]
    assert first.station_id == "251a7ea1-e3ff-4b2f-a4d2-1231118f83fa"
    assert first.no2 == 37.939
    assert first.o3 is None


def test_parse_historical_readings_handles_missing_gas_sensors(
    load_fixture_text,
):
    raw_json = load_fixture_text(
        "GetDetailData_batman2_eksik_olcum.network-response"
    )
    readings = parse_historical_readings(raw_json)
    assert len(readings) == 145
    # NO2/SO2/CO/O3 are intermittently null across the 145h window (this
    # station's gas sensors drop out periodically, they are not permanently
    # absent — the bulk snapshot in test_models.py just happened to catch a
    # null hour). PM10/PM25 are the two pollutants this station reports for
    # every single hour in the fixture.
    assert any(r.no2 is None for r in readings)
    assert any(r.no2 is not None for r in readings)
    assert all(r.pm10 is not None for r in readings)
    assert all(r.pm25 is not None for r in readings)
