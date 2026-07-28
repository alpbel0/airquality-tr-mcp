import math

import pytest

from airquality_tr_mcp.parsing import parse_bulk_stations
from airquality_tr_mcp.spatial import (
    InvalidNearestInputError,
    haversine_distance_km,
    select_nearest_stations,
    validate_nearest_input,
)


def _stations(load_fixture_text):
    return parse_bulk_stations(
        load_fixture_text("GetAirQualityStations_bulk_tum_ag.network-response")
    )


def test_haversine_returns_zero_for_same_point():
    assert haversine_distance_km(39.93, 32.85, 39.93, 32.85) == 0.0


def test_haversine_uses_lat_lon_order():
    distance = haversine_distance_km(39.9334, 32.8597, 41.0082, 28.9784)
    assert distance == pytest.approx(349.4, abs=2.0)


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        (
            {
                "location": "Göbeklitepe",
                "latitude": None,
                "longitude": None,
                "limit": 3,
                "max_distance_km": 75.0,
            },
            "text",
        ),
        (
            {
                "location": None,
                "latitude": 37.2232,
                "longitude": 38.9224,
                "limit": 3,
                "max_distance_km": 75.0,
            },
            "coordinates",
        ),
    ],
)
def test_validate_nearest_input_accepts_exactly_one_mode(kwargs, expected):
    assert validate_nearest_input(**kwargs) == expected


@pytest.mark.parametrize(
    "kwargs",
    [
        {
            "location": "Ankara",
            "latitude": 39.93,
            "longitude": 32.85,
            "limit": 3,
            "max_distance_km": 75.0,
        },
        {
            "location": None,
            "latitude": None,
            "longitude": None,
            "limit": 3,
            "max_distance_km": 75.0,
        },
        {
            "location": None,
            "latitude": 39.93,
            "longitude": None,
            "limit": 3,
            "max_distance_km": 75.0,
        },
        {
            "location": "   ",
            "latitude": None,
            "longitude": None,
            "limit": 3,
            "max_distance_km": 75.0,
        },
        {
            "location": "Ankara",
            "latitude": None,
            "longitude": None,
            "limit": 0,
            "max_distance_km": 75.0,
        },
        {
            "location": "Ankara",
            "latitude": None,
            "longitude": None,
            "limit": 6,
            "max_distance_km": 75.0,
        },
        {
            "location": None,
            "latitude": 91.0,
            "longitude": 32.85,
            "limit": 3,
            "max_distance_km": 75.0,
        },
        {
            "location": None,
            "latitude": True,
            "longitude": 32.85,
            "limit": 3,
            "max_distance_km": 75.0,
        },
        {
            "location": None,
            "latitude": 39.93,
            "longitude": 181.0,
            "limit": 3,
            "max_distance_km": 75.0,
        },
        {
            "location": "Ankara",
            "latitude": None,
            "longitude": None,
            "limit": 3,
            "max_distance_km": math.inf,
        },
        {
            "location": "Ankara",
            "latitude": None,
            "longitude": None,
            "limit": 3,
            "max_distance_km": True,
        },
    ],
)
def test_validate_nearest_input_rejects_invalid_arguments(kwargs):
    with pytest.raises(InvalidNearestInputError):
        validate_nearest_input(**kwargs)


def _station(template, *, station_id, lon, lat, aqi):
    station = template.model_copy(deep=True)
    station.id = station_id
    station.coordinate.lon = lon
    station.coordinate.lat = lat
    station.current.aqi_index = aqi
    return station


def test_reference_search_is_independent_from_limit(load_fixture_text):
    template = _stations(load_fixture_text)[0]
    stations = [
        _station(template, station_id="a", lon=32.01, lat=39.0, aqi=None),
        _station(template, station_id="b", lon=32.02, lat=39.0, aqi=None),
        _station(template, station_id="c", lon=32.03, lat=39.0, aqi=67.0),
    ]

    result = select_nearest_stations(
        stations,
        latitude=39.0,
        longitude=32.0,
        limit=1,
        max_distance_km=75.0,
    )

    assert result.reference.station.id == "c"
    assert [item.station.id for item in result.rated] == ["c"]
    assert [item.station.id for item in result.unrated_closer] == ["a"]
    assert result.unrated_count == 2


def test_reference_never_uses_station_beyond_75_km(load_fixture_text):
    template = _stations(load_fixture_text)[0]
    far = _station(template, station_id="far", lon=33.0, lat=39.0, aqi=42.0)

    result = select_nearest_stations(
        [far],
        latitude=39.0,
        longitude=32.0,
        limit=3,
        max_distance_km=200.0,
    )

    assert result.reference is None
    assert [item.station.id for item in result.rated] == ["far"]


def test_nearest_outside_is_reported_when_range_has_no_station(
    load_fixture_text,
):
    template = _stations(load_fixture_text)[0]
    station = _station(
        template, station_id="outside", lon=32.2, lat=39.0, aqi=25.0
    )

    result = select_nearest_stations(
        [station],
        latitude=39.0,
        longitude=32.0,
        limit=3,
        max_distance_km=1.0,
    )

    assert result.reference is None
    assert result.rated == ()
    assert result.nearest_outside.station.id == "outside"


def test_equal_distance_order_is_stable_by_station_id(load_fixture_text):
    template = _stations(load_fixture_text)[0]
    stations = [
        _station(template, station_id="z", lon=32.01, lat=39.0, aqi=10.0),
        _station(template, station_id="a", lon=31.99, lat=39.0, aqi=20.0),
    ]

    result = select_nearest_stations(
        stations,
        latitude=39.0,
        longitude=32.0,
        limit=2,
        max_distance_km=75.0,
    )

    assert [item.station.id for item in result.rated] == ["a", "z"]
