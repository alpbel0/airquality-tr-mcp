from airquality_tr_mcp.aggregation import summarize_aqi
from airquality_tr_mcp.parsing import parse_bulk_stations


def _templates(load_fixture_text):
    return parse_bulk_stations(
        load_fixture_text("GetAirQualityStations_bulk_tum_ag.network-response")
    )


def _station(template, *, station_id, name, aqi):
    station = template.model_copy(deep=True)
    station.id = station_id
    station.name = name
    station.current.aqi_index = aqi
    return station


def test_summarize_aqi_calculates_high_average_median_and_low(
    load_fixture_text,
):
    templates = _templates(load_fixture_text)
    stations = [
        _station(
            templates[0],
            station_id="sihhiye",
            name="Sıhhiye",
            aqi=42.0,
        ),
        _station(
            templates[1],
            station_id="kecioren",
            name="Keçiören",
            aqi=67.0,
        ),
        _station(
            templates[2],
            station_id="cankaya",
            name="Çankaya",
            aqi=91.0,
        ),
        _station(
            templates[3],
            station_id="batikent",
            name="Batıkent",
            aqi=None,
        ),
    ]

    result = summarize_aqi(stations)

    assert result is not None
    assert result.highest == 91.0
    assert result.average == 66.7
    assert result.median == 67.0
    assert result.lowest == 42.0
    assert result.valid_station_count == 3
    assert result.worst_station.name == "Çankaya"
    assert result.best_station.name == "Sıhhiye"


def test_summarize_aqi_uses_midpoint_median_for_even_count(
    load_fixture_text,
):
    templates = _templates(load_fixture_text)
    stations = [
        _station(templates[0], station_id="a", name="A", aqi=20.0),
        _station(templates[1], station_id="b", name="B", aqi=41.0),
    ]

    result = summarize_aqi(stations)

    assert result is not None
    assert result.average == 30.5
    assert result.median == 30.5


def test_summarize_aqi_rounds_average_and_median_to_one_decimal(
    load_fixture_text,
):
    templates = _templates(load_fixture_text)
    stations = [
        _station(templates[0], station_id="a", name="A", aqi=1.0),
        _station(templates[1], station_id="b", name="B", aqi=2.0),
        _station(templates[2], station_id="c", name="C", aqi=2.0),
    ]

    result = summarize_aqi(stations)

    assert result is not None
    assert result.average == 1.7
    assert result.median == 2.0


def test_summarize_aqi_returns_none_when_every_hki_is_missing(
    load_fixture_text,
):
    templates = _templates(load_fixture_text)
    stations = [
        _station(templates[0], station_id="a", name="A", aqi=None),
        _station(templates[1], station_id="b", name="B", aqi=None),
    ]

    assert summarize_aqi(stations) is None


def test_summarize_aqi_does_not_mutate_input_order(load_fixture_text):
    templates = _templates(load_fixture_text)
    stations = [
        _station(templates[0], station_id="a", name="A", aqi=80.0),
        _station(templates[1], station_id="b", name="B", aqi=20.0),
    ]
    original_ids = [station.id for station in stations]

    summarize_aqi(stations)

    assert [station.id for station in stations] == original_ids
