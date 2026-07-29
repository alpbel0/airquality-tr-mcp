from fastapi.testclient import TestClient

from airquality_tr_mcp import server
from airquality_tr_mcp.api import app
from airquality_tr_mcp.parsing import parse_bulk_stations


class _FakeProvider:
    def __init__(self, stations):
        self._stations = stations

    async def fetch_all_stations(self):
        return self._stations


class _FailingProvider:
    async def fetch_all_stations(self):
        from airquality_tr_mcp.provider import UpstreamError

        raise UpstreamError("UHKİA sunucusuna bağlanılamadı (zaman aşımı).")


def _load_all_stations(load_fixture_text):
    raw = load_fixture_text(
        "GetAirQualityStations_bulk_tum_ag.network-response"
    )
    return parse_bulk_stations(raw)


def test_ping_returns_pong():
    with TestClient(app) as client:
        response = client.get("/ping")
    assert response.status_code == 200
    assert response.json() == {"mesaj": "pong"}


def test_list_stations_filters_by_province(load_fixture_text, monkeypatch):
    stations = _load_all_stations(load_fixture_text)
    monkeypatch.setattr(server, "provider", _FakeProvider(stations))

    with TestClient(app) as client:
        response = client.get("/stations", params={"province": "Batman"})

    assert response.status_code == 200
    body = response.json()
    assert body["istasyonlar"]
    assert all(
        station["il"] == "Batman" for station in body["istasyonlar"]
    )


def test_air_quality_upstream_error_maps_to_502(monkeypatch):
    monkeypatch.setattr(server, "provider", _FailingProvider())

    with TestClient(app) as client:
        response = client.get("/air-quality", params={"province": "Ankara"})

    assert response.status_code == 502
    assert response.json()["hata"] == "upstream_hatasi"


def test_historical_data_invalid_days_maps_to_400(
    load_fixture_text, monkeypatch
):
    stations = _load_all_stations(load_fixture_text)
    monkeypatch.setattr(server, "provider", _FakeProvider(stations))

    with TestClient(app) as client:
        response = client.get(
            "/historical-data",
            params={"province": "Ankara", "days": 0},
        )

    assert response.status_code == 400
    assert response.json()["hata"] == "gecersiz_days"


def test_ranking_invalid_mode_maps_to_400(load_fixture_text, monkeypatch):
    stations = _load_all_stations(load_fixture_text)
    monkeypatch.setattr(server, "provider", _FakeProvider(stations))

    with TestClient(app) as client:
        response = client.get(
            "/ranking", params={"mode": "invalid", "limit": 5}
        )

    assert response.status_code == 400
    assert response.json()["hata"] == "gecersiz_mode"


def test_station_detail_missing_id_maps_to_404(
    load_fixture_text, monkeypatch
):
    stations = _load_all_stations(load_fixture_text)
    monkeypatch.setattr(server, "provider", _FakeProvider(stations))

    with TestClient(app) as client:
        response = client.get(
            "/station",
            params={
                "station": "00000000-0000-0000-0000-000000000000"
            },
        )

    assert response.status_code == 404
    assert response.json()["hata"] == "istasyon_id_bulunamadi"
