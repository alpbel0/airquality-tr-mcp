import json

import httpx
import pytest

from airquality_tr_mcp.provider import UhkiaProvider, UpstreamError


def _mock_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://sim.csb.gov.tr",
    )


async def test_fetch_all_stations_parses_bulk_response(load_fixture_text):
    raw = load_fixture_text(
        "GetAirQualityStations_bulk_tum_ag.network-response"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/Services/GetAirQualityStations"
        assert request.url.params["type"] == "0"
        return httpx.Response(
            200, text=raw, headers={"content-type": "application/json"}
        )

    provider = UhkiaProvider(client=_mock_client(handler))
    stations = await provider.fetch_all_stations()
    assert len(stations) == 323


async def test_fetch_all_stations_raises_upstream_error_on_non_json_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            text="<html>error</html>",
            headers={"content-type": "text/html"},
        )

    provider = UhkiaProvider(client=_mock_client(handler))
    with pytest.raises(UpstreamError):
        await provider.fetch_all_stations()


async def test_fetch_all_stations_raises_upstream_error_on_timeout():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("simulated timeout", request=request)

    provider = UhkiaProvider(client=_mock_client(handler))
    with pytest.raises(UpstreamError):
        await provider.fetch_all_stations()


async def test_fetch_all_stations_wraps_malformed_json_as_upstream_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text="{not-json",
            headers={"content-type": "application/json"},
        )

    provider = UhkiaProvider(client=_mock_client(handler))
    with pytest.raises(UpstreamError, match="geçersiz yanıt formatı"):
        await provider.fetch_all_stations()


async def test_fetch_station_history_calls_get_detail_data_without_end_date():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = request.content.decode()
        payload = {
            "result": True,
            "message": None,
            "objects": {
                "AQIValues": [
                    {
                        "StationId": "abc",
                        "Date": "2026-07-27T10:00:00",
                        "NO2": 10.0,
                        "SO2": None,
                        "CO": None,
                        "O3": None,
                        "PM10": None,
                        "PM25": None,
                        "CO_1": None,
                        "O3_1": None,
                        "PM10_1": None,
                        "AQIIndex": 20.0,
                        "AQIStatus": 0,
                        "ContaminantParameter": "NO2",
                        "AQIType": 0,
                    }
                ]
            },
        }
        return httpx.Response(
            200,
            text=json.dumps(payload),
            headers={"content-type": "application/json"},
        )

    provider = UhkiaProvider(client=_mock_client(handler))
    readings = await provider.fetch_station_history("abc")

    assert captured["path"] == "/Services/GetDetailData"
    assert "stationId=abc" in captured["body"]
    assert "endDate" not in captured["body"]
    assert len(readings) == 1
    assert readings[0].station_id == "abc"


async def test_fetch_station_history_calls_station_detail_with_end_date():
    from datetime import datetime

    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["params"] = dict(request.url.params)
        captured["body"] = request.content.decode()
        return httpx.Response(
            200,
            text=json.dumps(
                {
                    "result": True,
                    "message": None,
                    "objects": {"AQIValues": []},
                }
            ),
            headers={"content-type": "application/json"},
        )

    provider = UhkiaProvider(client=_mock_client(handler))
    await provider.fetch_station_history(
        "abc", end_date=datetime(2026, 7, 20, 12, 0, 0)
    )

    assert captured["path"] == "/Services/GetAirQualityStationDetail"
    assert captured["params"]["type"] == "0"
    assert "stationId=abc" in captured["body"]
    assert "endDate=20.07.2026+12%3A00%3A00" in captured["body"]


async def test_fetch_station_history_raises_upstream_error_on_non_json_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            text="<html>error</html>",
            headers={"content-type": "text/html"},
        )

    provider = UhkiaProvider(client=_mock_client(handler))
    with pytest.raises(UpstreamError):
        await provider.fetch_station_history("abc")


async def test_fetch_station_history_raises_upstream_error_on_timeout():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("simulated timeout", request=request)

    provider = UhkiaProvider(client=_mock_client(handler))
    with pytest.raises(UpstreamError):
        await provider.fetch_station_history("abc")
