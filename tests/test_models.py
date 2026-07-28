from datetime import datetime, timedelta

from airquality_tr_mcp.models import (
    ISTANBUL_TZ,
    Coordinate,
    Station,
    StationReading,
)


def test_coordinate_from_wkt_parses_lon_before_lat():
    # Source format is "POINT (lon lat)" — longitude comes first, which is
    # easy to get backwards (see docs/api-notes.md).
    coord = Coordinate.from_wkt("POINT (41.141073 37.899706000000023)")
    assert coord.lon == 41.141073
    assert coord.lat == 37.899706000000023


# Real values from the Ankara/Bahçelievler GetDetailData fixture, first record
# Source: docs/api-notes.md and the Ankara/Bahçelievler detail fixture.
ANKARA_FIRST_READING_RAW = {
    "StationId": "251a7ea1-e3ff-4b2f-a4d2-1231118f83fa",
    "Date": "2026-07-27T00:00:00",
    "NO2": 37.939,
    "SO2": 7.006,
    "CO": 479.859,
    "O3": None,
    "PM10": 9.766,
    "PM25": 5.004,
    "CO_1": 629.829,
    "O3_1": None,
    "PM10_1": 14.868,
    "SO2_N": 3.573,
    "NO2_N": 19.349,
    "CO_N": 4.45,
    "O3_N": None,
    "PM10_N": 10.743,
    "PM25_N": -32768.0,
    "AQIIndex": 19.349,
    "AQIStatus": 0,
    "ContaminantParameter": "NO2",
    "AQIType": 0,
}


def test_station_reading_parses_pascal_case_aliases():
    reading = StationReading.model_validate(ANKARA_FIRST_READING_RAW)
    assert reading.station_id == "251a7ea1-e3ff-4b2f-a4d2-1231118f83fa"
    assert reading.no2 == 37.939
    assert reading.co == 479.859
    assert reading.o3 is None  # raw null stays None, never 0
    assert reading.co_1h == 629.829
    assert reading.o3_1h is None
    assert reading.dominant_pollutant == "NO2"
    assert reading.aqi_index == 19.349


def test_station_reading_ignores_unmodeled_N_fields():
    # _N fields are present in the raw payload but out of scope (see plan's
    # Global Constraints) — StationReading must not fail or require them.
    reading = StationReading.model_validate(ANKARA_FIRST_READING_RAW)
    assert not hasattr(reading, "pm25_n")
    assert not hasattr(reading, "no2_n")


def test_station_reading_attaches_istanbul_timezone():
    reading = StationReading.model_validate(ANKARA_FIRST_READING_RAW)
    assert reading.measured_at == datetime(
        2026, 7, 27, 0, 0, tzinfo=ISTANBUL_TZ
    )
    assert reading.measured_at.utcoffset() == timedelta(hours=3)


def test_pollutant_unit_is_micrograms_per_cubic_meter():
    # REQUIREMENTS.md §6.1: "Every measurement value carries its unit" —
    # verified in Phase 1 that every pollutant, including CO, is µg/m³.
    from airquality_tr_mcp.models import POLLUTANT_UNIT

    assert POLLUTANT_UNIT == "µg/m³"


# Real, trimmed record from the bulk stations network response —
# the "Batman - 2" station, a real example of a station whose Config.parameters
# lists a pollutant (NO2/SO2/CO/O3) that is reporting null right now.
# This is ROADMAP §1.4's concrete "eksik ölçüm" example.
BATMAN2_STATION_RAW = {
    "id": "23ac6ffc-1067-4c8e-ad2d-2ba5da78b876",
    "Location": "POINT (41.108308 37.889110999999971)",
    "Name": "Batman - 2",
    "City_Title": "Batman",
    "CityId": "c72746cb-42f4-4f99-b761-1688530ac84d",
    "Town_Title": "Merkez",
    "LastDataDate": "2026-07-26T12:00:00",
    "Values": {
        "StationId": "23ac6ffc-1067-4c8e-ad2d-2ba5da78b876",
        "Date": "2026-07-27T00:00:00",
        "NO2": None,
        "SO2": None,
        "CO": None,
        "O3": None,
        "PM10": 62.196,
        "PM25": 21.186,
        "CO_1": None,
        "O3_1": None,
        "PM10_1": None,
        "SO2_N": None,
        "NO2_N": None,
        "CO_N": None,
        "O3_N": None,
        "PM10_N": 55.244,
        "PM25_N": -32768.0,
        "AQIIndex": 55.244,
        "AQIStatus": 1,
        "ContaminantParameter": "PM10",
        "AQIType": 0,
    },
    "Config": {"parameters": "NO2,O3,PM10,SO2,CO,PM25"},
}


def test_station_from_raw_parses_identity_and_coordinate():
    station = Station.from_raw(BATMAN2_STATION_RAW)
    assert station.id == "23ac6ffc-1067-4c8e-ad2d-2ba5da78b876"
    assert station.name == "Batman - 2"
    assert station.city == "Batman"
    assert station.district == "Merkez"
    assert station.coordinate.lon == 41.108308
    assert station.coordinate.lat == 37.889110999999971


def test_station_from_raw_parses_comma_separated_parameters():
    station = Station.from_raw(BATMAN2_STATION_RAW)
    assert station.parameters == ["NO2", "O3", "PM10", "SO2", "CO", "PM25"]


def test_station_from_raw_embeds_current_reading_with_nulls_preserved():
    station = Station.from_raw(BATMAN2_STATION_RAW)
    # NO2/SO2/CO/O3 are listed in Config.parameters (sensor exists) but are
    # currently null — that is a "veri yok" case, not "not measured here".
    # This model layer just preserves None; status text is a Phase 2 concern.
    assert station.current.no2 is None
    assert station.current.so2 is None
    assert station.current.pm10 == 62.196
    assert station.current.pm25 == 21.186
