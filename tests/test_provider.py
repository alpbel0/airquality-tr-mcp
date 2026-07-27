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
