import statistics
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from airquality_tr_mcp import server
from airquality_tr_mcp.cache import CachedProvider
from airquality_tr_mcp.geocoding import (
    CachedGeocoder,
    GeocodedPlace,
    GeocodingServiceError,
)
from airquality_tr_mcp.models import ISTANBUL_TZ, StationReading
from airquality_tr_mcp.parsing import parse_bulk_stations
from airquality_tr_mcp.provider import UpstreamError
from airquality_tr_mcp.ranking import RankingCache
from airquality_tr_mcp.server import mcp

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class _FakeProvider:
    def __init__(self, stations):
        self._stations = stations

    async def fetch_all_stations(self):
        return self._stations


class _FakeHistoryProvider(_FakeProvider):
    async def fetch_station_history(self, station_id, end_date=None):
        return [
            StationReading.model_validate(
                {
                    "StationId": station_id,
                    "Date": "2026-07-27T10:00:00",
                    "NO2": None,
                    "SO2": None,
                    "CO": None,
                    "O3": None,
                    "PM10": None,
                    "PM25": None,
                    "CO_1": None,
                    "O3_1": None,
                    "PM10_1": None,
                    "AQIIndex": 15.0,
                    "AQIStatus": 0,
                    "ContaminantParameter": "PM10",
                    "AQIType": 0,
                }
            )
        ]


class _FailingProvider:
    async def fetch_all_stations(self):
        from airquality_tr_mcp.provider import UpstreamError

        raise UpstreamError("UHKİA sunucusuna bağlanılamadı (zaman aşımı).")


class _ClosableProvider(_FakeProvider):
    def __init__(self, stations):
        super().__init__(stations)
        self.closed = False

    async def aclose(self):
        self.closed = True


def _load_all_stations(load_fixture_text):
    raw = load_fixture_text(
        "GetAirQualityStations_bulk_tum_ag.network-response"
    )
    return parse_bulk_stations(raw)


class _FakeGeocoder:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.queries = []
        self.closed = False

    async def geocode(self, query):
        self.queries.append(query)
        if self.error is not None:
            raise self.error
        return self.result

    async def aclose(self):
        self.closed = True


async def test_nearest_tool_is_listed():
    async with Client(server.mcp) as client:
        tools = await client.list_tools()
    assert any(tool.name == "get_nearest_air_quality" for tool in tools)


async def test_nearest_tool_coordinate_mode_does_not_call_geocoder(
    load_fixture_text, monkeypatch
):
    stations = _load_all_stations(load_fixture_text)
    fake_geocoder = _FakeGeocoder()
    monkeypatch.setattr(server, "provider", _FakeProvider(stations))
    monkeypatch.setattr(server, "geocoder", fake_geocoder)
    target = stations[0]

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "get_nearest_air_quality",
            {
                "latitude": target.coordinate.lat,
                "longitude": target.coordinate.lon,
            },
        )

    assert fake_geocoder.queries == []
    assert result.data["referans_istasyon"]["ad"] == target.name
    assert result.data["cozumlenen_konum"]["konum_kaynagi"] == (
        "kullanici_koordinati"
    )


async def test_nearest_tool_text_mode_uses_resolved_place(
    load_fixture_text, monkeypatch
):
    stations = _load_all_stations(load_fixture_text)
    target = stations[0]
    fake = _FakeGeocoder(
        GeocodedPlace(
            label="Çözümlenen Yer",
            latitude=target.coordinate.lat,
            longitude=target.coordinate.lon,
            importance=0.6,
        )
    )
    monkeypatch.setattr(server, "provider", _FakeProvider(stations))
    monkeypatch.setattr(server, "geocoder", fake)

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "get_nearest_air_quality", {"location": "Girilen Yer"}
        )

    assert fake.queries == ["Girilen Yer"]
    assert result.data["cozumlenen_konum"]["etiket"] == "Çözümlenen Yer"


async def test_nearest_tool_corrects_bare_province_typo_before_geocoding(
    load_fixture_text, monkeypatch
):
    stations = _load_all_stations(load_fixture_text)
    target = stations[0]
    fake = _FakeGeocoder(
        GeocodedPlace(
            label="Manisa, Türkiye",
            latitude=target.coordinate.lat,
            longitude=target.coordinate.lon,
            importance=0.6,
        )
    )
    monkeypatch.setattr(server, "provider", _FakeProvider(stations))
    monkeypatch.setattr(server, "geocoder", fake)

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "get_nearest_air_quality", {"location": "amnisa"}
        )

    assert fake.queries == ["Manisa"]
    assert result.data["girdi"]["duzeltilmis_sorgu"] == "Manisa"


async def test_nearest_tool_returns_geocoding_error_without_fetching_uhkia(
    monkeypatch,
):
    class _MustNotRunProvider:
        async def fetch_all_stations(self):
            raise AssertionError("UHKİA must not be called")

    fake = _FakeGeocoder(error=GeocodingServiceError("down"))
    monkeypatch.setattr(server, "provider", _MustNotRunProvider())
    monkeypatch.setattr(server, "geocoder", fake)

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "get_nearest_air_quality", {"location": "Ankara"}
        )

    assert result.data["hata"] == "geocoding_servis_hatasi"


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {
            "location": "Ankara",
            "latitude": 39.93,
            "longitude": 32.85,
        },
        {"latitude": 39.93},
        {"location": "Ankara", "limit": 0},
        {"location": "Ankara", "limit": 6},
        {"location": "Ankara", "max_distance_km": 0.0},
    ],
)
async def test_nearest_tool_rejects_invalid_arguments(arguments):
    async with Client(server.mcp) as client:
        result = await client.call_tool("get_nearest_air_quality", arguments)
    assert result.data["hata"] == "gecersiz_parametre"


async def test_nearest_tool_returns_structured_uhkia_error(
    monkeypatch,
):
    monkeypatch.setattr(server, "provider", _FailingProvider())
    monkeypatch.setattr(server, "geocoder", _FakeGeocoder())

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "get_nearest_air_quality",
            {"latitude": 39.93, "longitude": 32.85},
        )

    assert result.data["hata"] == "upstream_hatasi"


async def test_server_lifespan_closes_provider_and_geocoder(monkeypatch):
    closable_provider = _ClosableProvider([])
    closable_geocoder = _FakeGeocoder()
    monkeypatch.setattr(server, "provider", closable_provider)
    monkeypatch.setattr(server, "geocoder", closable_geocoder)

    async with Client(server.mcp):
        assert not closable_provider.closed
        assert not closable_geocoder.closed

    assert closable_provider.closed
    assert closable_geocoder.closed


async def test_geocoder_failure_does_not_break_other_tools(
    load_fixture_text, monkeypatch
):
    stations = _load_all_stations(load_fixture_text)
    monkeypatch.setattr(server, "provider", _FakeProvider(stations))
    monkeypatch.setattr(
        server,
        "geocoder",
        CachedGeocoder(_FakeGeocoder(error=GeocodingServiceError("down"))),
    )

    async with Client(server.mcp) as client:
        ping_result = await client.call_tool("ping", {})
        stations_result = await client.call_tool(
            "list_stations", {"province": "Batman"}
        )
        coordinate_result = await client.call_tool(
            "get_nearest_air_quality",
            {
                "latitude": stations[0].coordinate.lat,
                "longitude": stations[0].coordinate.lon,
            },
        )

    assert ping_result.data == "pong"
    assert stations_result.data["istasyonlar"]
    assert coordinate_result.data["referans_istasyon"] is not None


async def test_ping_tool_returns_pong():
    async with Client(mcp) as client:
        result = await client.call_tool("ping", {})
        assert result.data == "pong"


async def test_ping_tool_is_listed():
    async with Client(mcp) as client:
        tools = await client.list_tools()
        assert any(tool.name == "ping" for tool in tools)


async def test_server_runs_over_real_stdio_subprocess():
    transport = StdioTransport(
        command=sys.executable,
        args=["-m", "airquality_tr_mcp.server"],
        cwd=str(PROJECT_ROOT),
    )
    async with Client(transport) as client:
        tools = await client.list_tools()
        assert any(tool.name == "ping" for tool in tools)
        result = await client.call_tool("ping", {})
        assert result.data == "pong"


async def test_server_lifespan_closes_provider(monkeypatch):
    closable_provider = _ClosableProvider([])
    monkeypatch.setattr(server, "provider", closable_provider)

    async with Client(server.mcp):
        assert not closable_provider.closed

    assert closable_provider.closed


async def test_list_stations_returns_all_stations_when_no_province_given(
    load_fixture_text, monkeypatch
):
    stations = _load_all_stations(load_fixture_text)
    monkeypatch.setattr(server, "provider", _FakeProvider(stations))

    async with Client(server.mcp) as client:
        result = await client.call_tool("list_stations", {})
    assert len(result.data["istasyonlar"]) == 323


async def test_list_stations_filters_by_province(
    load_fixture_text, monkeypatch
):
    stations = _load_all_stations(load_fixture_text)
    monkeypatch.setattr(server, "provider", _FakeProvider(stations))

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "list_stations", {"province": "Batman"}
        )
    assert result.data["istasyonlar"]
    assert all(
        station["il"] == "Batman" for station in result.data["istasyonlar"]
    )
    assert result.data["istasyonlar"][0]["olcum_zamani"]


async def test_list_stations_narrows_district_input(
    load_fixture_text, monkeypatch
):
    # Regression: "Çankaya"/"Kadıköy" etc. get auto-detected as a
    # district of their province (note says so), but the result used to
    # silently return every station in the whole province instead of
    # just that district's stations.
    stations = _load_all_stations(load_fixture_text)
    monkeypatch.setattr(server, "provider", _FakeProvider(stations))

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "list_stations", {"province": "Kadıköy"}
        )

    assert result.data["il"] == "İstanbul"
    assert result.data["ilce"] == "Kadıköy"
    assert result.data["istasyonlar"]
    assert all(
        station["ilce"] == "Kadıköy" for station in result.data["istasyonlar"]
    )
    assert len(result.data["istasyonlar"]) < 37


async def test_list_stations_returns_error_payload_for_unknown_province(
    load_fixture_text, monkeypatch
):
    stations = _load_all_stations(load_fixture_text)
    monkeypatch.setattr(server, "provider", _FakeProvider(stations))

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "list_stations", {"province": "Zzzzzzz"}
        )
    assert result.data["hata"] == "eslesme_bulunamadi"


async def test_list_stations_warns_when_province_has_no_active_stations(
    load_fixture_text, monkeypatch
):
    stations = _load_all_stations(load_fixture_text)
    monkeypatch.setattr(server, "provider", _FakeProvider(stations))

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "list_stations", {"province": "Hakkari"}
        )
    assert result.data["istasyonlar"] == []
    assert "aktif istasyon" in result.data["uyari"]


async def test_list_stations_returns_structured_error_on_upstream_failure(
    monkeypatch,
):
    monkeypatch.setattr(server, "provider", _FailingProvider())

    async with Client(server.mcp) as client:
        result = await client.call_tool("list_stations", {})
    assert result.data["hata"] == "upstream_hatasi"


async def test_get_air_quality_returns_province_statistics_and_breakdown(
    load_fixture_text, monkeypatch
):
    stations = _load_all_stations(load_fixture_text)
    monkeypatch.setattr(server, "provider", _FakeProvider(stations))
    batman_values = [
        station.current.aqi_index
        for station in stations
        if station.city == "Batman" and station.current.aqi_index is not None
    ]

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "get_air_quality", {"province": "Batman"}
        )

    summary = result.data["il_ozeti"]
    assert result.data["il"] == "Batman"
    assert summary["en_yuksek_hki"] == max(batman_values)
    assert summary["en_dusuk_hki"] == min(batman_values)
    assert summary["ortalama_hki"] == round(statistics.fmean(batman_values), 1)
    assert summary["medyan_hki"] == round(statistics.median(batman_values), 1)
    assert summary["gecerli_istasyon_sayisi"] == len(batman_values)
    assert summary["en_kotu_istasyon"]["hki"] == max(batman_values)
    assert summary["en_iyi_istasyon"]["hki"] == min(batman_values)
    assert "temsili_hki" not in summary
    assert "temsili_kategori" not in summary
    assert "ilce_ozeti" not in result.data
    assert result.data["istasyonlar"]
    assert result.data["istasyonlar"][0]["olcum_zamani"]


async def test_get_air_quality_narrows_to_district(
    load_fixture_text, monkeypatch
):
    stations = _load_all_stations(load_fixture_text)
    monkeypatch.setattr(server, "provider", _FakeProvider(stations))

    expected_names = {
        station.name
        for station in stations
        if station.city == "Batman" and station.district == "Merkez"
    }
    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "get_air_quality",
            {"province": "Batman", "district": "Merkez"},
        )
    assert result.data["il"] == "Batman"
    assert {row["ad"] for row in result.data["istasyonlar"]} == expected_names


async def test_get_air_quality_returns_error_for_unmatched_district(
    load_fixture_text, monkeypatch
):
    stations = _load_all_stations(load_fixture_text)
    monkeypatch.setattr(server, "provider", _FakeProvider(stations))

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "get_air_quality",
            {"province": "Batman", "district": "Zzzzzz"},
        )
    assert result.data["hata"] == "ilce_eslesmedi"
    assert result.data["ildeki_ilceler"]


async def test_get_air_quality_rejects_common_district_as_province(
    load_fixture_text, monkeypatch
):
    stations = _load_all_stations(load_fixture_text)
    monkeypatch.setattr(server, "provider", _FakeProvider(stations))

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "get_air_quality", {"province": "Merkez"}
        )
    assert result.data["hata"] == "eslesme_bulunamadi"


async def test_get_air_quality_ignores_null_aqi_when_picking_best(
    load_fixture_text, monkeypatch
):
    stations = _load_all_stations(load_fixture_text)
    null_aqi_station = stations[0].model_copy(deep=True)
    null_aqi_station.current.aqi_index = None
    null_aqi_station.city = "Batman"
    healthy_station = stations[1].model_copy(deep=True)
    healthy_station.city = "Batman"
    healthy_station.current.aqi_index = 12.0
    monkeypatch.setattr(
        server,
        "provider",
        _FakeProvider([null_aqi_station, healthy_station]),
    )

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "get_air_quality", {"province": "Batman"}
        )
    assert result.data["il_ozeti"]["en_iyi_istasyon"]["hki"] == 12.0


async def test_get_air_quality_returns_structured_error_on_upstream_failure(
    monkeypatch,
):
    monkeypatch.setattr(server, "provider", _FailingProvider())

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "get_air_quality", {"province": "Ankara"}
        )
    assert result.data["hata"] == "upstream_hatasi"


async def test_get_air_quality_adds_independent_district_summary(
    load_fixture_text, monkeypatch
):
    stations = _load_all_stations(load_fixture_text)
    province_station = stations[0].model_copy(deep=True)
    province_station.city = "Batman"
    province_station.district = "Merkez"
    province_station.name = "Merkez İstasyonu"
    province_station.current.aqi_index = 40.0
    other_district_station = stations[1].model_copy(deep=True)
    other_district_station.city = "Batman"
    other_district_station.district = "Kozluk"
    other_district_station.name = "Kozluk İstasyonu"
    other_district_station.current.aqi_index = 90.0
    monkeypatch.setattr(
        server,
        "provider",
        _FakeProvider([province_station, other_district_station]),
    )

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "get_air_quality",
            {"province": "Batman", "district": "Merkez"},
        )

    assert result.data["il_ozeti"]["en_yuksek_hki"] == 90.0
    assert result.data["ilce_ozeti"]["ilce"] == "Merkez"
    assert result.data["ilce_ozeti"]["en_yuksek_hki"] == 40.0
    assert result.data["ilce_ozeti"]["ortalama_hki"] == 40.0
    assert [row["ad"] for row in result.data["istasyonlar"]] == [
        "Merkez İstasyonu"
    ]


async def test_get_air_quality_returns_empty_summary_for_ratedless_district(
    load_fixture_text, monkeypatch
):
    stations = _load_all_stations(load_fixture_text)
    rated = stations[0].model_copy(deep=True)
    rated.city = "Batman"
    rated.district = "Kozluk"
    rated.current.aqi_index = 80.0
    unrated = stations[1].model_copy(deep=True)
    unrated.city = "Batman"
    unrated.district = "Merkez"
    unrated.current.aqi_index = None
    monkeypatch.setattr(server, "provider", _FakeProvider([rated, unrated]))

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "get_air_quality",
            {"province": "Batman", "district": "Merkez"},
        )

    district_summary = result.data["ilce_ozeti"]
    assert district_summary["ilce"] == "Merkez"
    assert district_summary["gecerli_istasyon_sayisi"] == 0
    assert district_summary["en_yuksek_hki"] is None
    assert district_summary["ortalama_hki"] is None
    assert district_summary["medyan_hki"] is None
    assert district_summary["en_dusuk_hki"] is None
    assert district_summary["en_kotu_istasyon"] is None
    assert district_summary["en_iyi_istasyon"] is None
    assert "uyari" in district_summary


async def test_get_station_detail_finds_station_by_exact_id(
    load_fixture_text, monkeypatch
):
    stations = _load_all_stations(load_fixture_text)
    monkeypatch.setattr(server, "provider", _FakeProvider(stations))
    target = stations[0]

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "get_station_detail", {"station": target.id}
        )
    assert result.data["station_id"] == target.id
    assert result.data["ad"] == target.name


async def test_get_station_detail_returns_error_for_unknown_id(
    load_fixture_text, monkeypatch
):
    stations = _load_all_stations(load_fixture_text)
    monkeypatch.setattr(server, "provider", _FakeProvider(stations))

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "get_station_detail",
            {"station": "00000000-0000-0000-0000-000000000000"},
        )
    assert result.data["hata"] == "istasyon_id_bulunamadi"


async def test_get_station_detail_finds_station_by_exact_name(
    load_fixture_text, monkeypatch
):
    stations = _load_all_stations(load_fixture_text)
    monkeypatch.setattr(server, "provider", _FakeProvider(stations))
    target = stations[0]

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "get_station_detail", {"station": target.name}
        )
    assert result.data["station_id"] == target.id


async def test_get_station_detail_auto_corrects_typo_in_name(
    load_fixture_text, monkeypatch
):
    stations = _load_all_stations(load_fixture_text)
    monkeypatch.setattr(server, "provider", _FakeProvider(stations))
    target = next(
        station for station in stations if station.name == "Batman - 2"
    )

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "get_station_detail", {"station": "Batman - 22"}
        )
    assert result.data["station_id"] == target.id
    assert "bulunamadı" in result.data["not"]


async def test_get_station_detail_returns_error_for_unmatched_name(
    load_fixture_text, monkeypatch
):
    stations = _load_all_stations(load_fixture_text)
    monkeypatch.setattr(server, "provider", _FakeProvider(stations))

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "get_station_detail", {"station": "Zzzzzzzzzzz"}
        )
    assert result.data["hata"] == "eslesme_bulunamadi"


async def test_get_station_detail_unmatched_name_uses_station_entity_label(
    load_fixture_text, monkeypatch
):
    stations = _load_all_stations(load_fixture_text)
    monkeypatch.setattr(server, "provider", _FakeProvider(stations))

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "get_station_detail", {"station": "Zzzzzzzzzzz"}
        )

    assert result.data["hata"] == "eslesme_bulunamadi"
    assert "ilçe" not in result.data["mesaj"]
    assert "aktif bir hava kalitesi istasyonu" not in result.data["mesaj"]


async def test_get_station_detail_rejects_partial_id_without_fuzzy_matching(
    load_fixture_text, monkeypatch
):
    stations = _load_all_stations(load_fixture_text)
    monkeypatch.setattr(server, "provider", _FakeProvider(stations))

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "get_station_detail", {"station": "114b79f4"}
        )
    assert result.data["hata"] == "eksik_veya_bozuk_id"
    assert result.data["girdi"] == "114b79f4"


async def test_get_station_detail_rejects_partial_id_with_dashes(
    load_fixture_text, monkeypatch
):
    stations = _load_all_stations(load_fixture_text)
    monkeypatch.setattr(server, "provider", _FakeProvider(stations))

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "get_station_detail", {"station": "114b79f4-ede4"}
        )
    assert result.data["hata"] == "eksik_veya_bozuk_id"
    assert result.data["girdi"] == "114b79f4-ede4"


async def test_get_station_detail_still_fuzzy_matches_real_names(
    load_fixture_text, monkeypatch
):
    stations = _load_all_stations(load_fixture_text)
    monkeypatch.setattr(server, "provider", _FakeProvider(stations))
    target = next(
        station for station in stations if station.name == "Batman - 2"
    )

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "get_station_detail", {"station": "Batman - 22"}
        )
    assert result.data["station_id"] == target.id
    assert "bulunamadı" in result.data["not"]


async def test_get_station_detail_ambiguity_message_names_station_parameter(
    load_fixture_text, monkeypatch
):
    stations = _load_all_stations(load_fixture_text)
    first = stations[0].model_copy(deep=True)
    first.name = "Aydın"
    second = stations[1].model_copy(deep=True)
    second.name = "Adana"
    monkeypatch.setattr(server, "provider", _FakeProvider([first, second]))

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "get_station_detail", {"station": "Adina"}
        )
    assert result.data["hata"] == "belirsiz_eslesme"
    assert "station parametresini" in result.data["mesaj"]
    assert "province parametresini" not in result.data["mesaj"]


async def test_get_station_detail_returns_structured_error_on_upstream_failure(
    monkeypatch,
):
    monkeypatch.setattr(server, "provider", _FailingProvider())

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "get_station_detail", {"station": "Ankara"}
        )
    assert result.data["hata"] == "upstream_hatasi"


class _FlakyThenFailingProvider:
    def __init__(self, stations):
        self._stations = stations
        self.call_count = 0

    async def fetch_all_stations(self):
        self.call_count += 1
        if self.call_count == 1:
            return self._stations
        raise UpstreamError("simulated failure")


async def test_list_stations_attaches_staleness_warning_after_upstream_failure(
    load_fixture_text, monkeypatch
):
    stations = _load_all_stations(load_fixture_text)
    clock = {"now": 0.0}
    cached = CachedProvider(
        _FlakyThenFailingProvider(stations),
        ttl_seconds=600.0,
        stale_seconds=3600.0,
        clock=lambda: clock["now"],
    )
    monkeypatch.setattr(server, "provider", cached)

    async with Client(server.mcp) as client:
        await client.call_tool("list_stations", {})
        clock["now"] += 601
        result = await client.call_tool("list_stations", {})

    assert result.data["veri_bayat_uyarisi"]


async def test_air_quality_attaches_staleness_warning(
    load_fixture_text, monkeypatch
):
    stations = _load_all_stations(load_fixture_text)
    clock = {"now": 0.0}
    cached = CachedProvider(
        _FlakyThenFailingProvider(stations),
        ttl_seconds=600.0,
        stale_seconds=3600.0,
        clock=lambda: clock["now"],
    )
    monkeypatch.setattr(server, "provider", cached)

    async with Client(server.mcp) as client:
        await client.call_tool("get_air_quality", {"province": "Batman"})
        clock["now"] += 601
        result = await client.call_tool(
            "get_air_quality", {"province": "Batman"}
        )

    assert result.data["veri_bayat_uyarisi"]


async def test_station_detail_attaches_staleness_warning(
    load_fixture_text, monkeypatch
):
    stations = _load_all_stations(load_fixture_text)
    target = stations[0]
    clock = {"now": 0.0}
    cached = CachedProvider(
        _FlakyThenFailingProvider(stations),
        ttl_seconds=600.0,
        stale_seconds=3600.0,
        clock=lambda: clock["now"],
    )
    monkeypatch.setattr(server, "provider", cached)

    async with Client(server.mcp) as client:
        await client.call_tool("get_station_detail", {"station": target.id})
        clock["now"] += 601
        result = await client.call_tool(
            "get_station_detail", {"station": target.id}
        )

    assert result.data["veri_bayat_uyarisi"]


async def test_default_provider_is_wrapped_in_cached_provider():
    assert isinstance(server.provider, CachedProvider)


async def test_list_stations_keeps_staleness_warning_on_resolution_error(
    load_fixture_text, monkeypatch
):
    stations = _load_all_stations(load_fixture_text)
    clock = {"now": 0.0}
    cached = CachedProvider(
        _FlakyThenFailingProvider(stations),
        ttl_seconds=600.0,
        stale_seconds=3600.0,
        clock=lambda: clock["now"],
    )
    monkeypatch.setattr(server, "provider", cached)

    async with Client(server.mcp) as client:
        await client.call_tool("list_stations", {"province": "Batman"})
        clock["now"] += 601
        result = await client.call_tool(
            "list_stations", {"province": "Zzzzzzz"}
        )

    assert result.data["hata"] == "eslesme_bulunamadi"
    assert result.data["veri_bayat_uyarisi"]


async def test_get_station_detail_keeps_staleness_warning_on_missing_id(
    load_fixture_text, monkeypatch
):
    stations = _load_all_stations(load_fixture_text)
    clock = {"now": 0.0}
    cached = CachedProvider(
        _FlakyThenFailingProvider(stations),
        ttl_seconds=600.0,
        stale_seconds=3600.0,
        clock=lambda: clock["now"],
    )
    monkeypatch.setattr(server, "provider", cached)

    async with Client(server.mcp) as client:
        await client.call_tool(
            "get_station_detail", {"station": stations[0].id}
        )
        clock["now"] += 601
        result = await client.call_tool(
            "get_station_detail",
            {"station": "00000000-0000-0000-0000-000000000000"},
        )

    assert result.data["hata"] == "istasyon_id_bulunamadi"
    assert result.data["veri_bayat_uyarisi"]


async def test_get_historical_data_rejects_invalid_days(
    load_fixture_text, monkeypatch
):
    stations = _load_all_stations(load_fixture_text)
    monkeypatch.setattr(server, "provider", _FakeProvider(stations))

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "get_historical_data", {"province": "Batman", "days": 0}
        )

    assert result.data["hata"] == "gecersiz_days"


async def test_get_historical_data_returns_worst_station_when_no_district(
    load_fixture_text, monkeypatch
):
    stations = _load_all_stations(load_fixture_text)

    class _FakeHistoryProvider(_FakeProvider):
        async def fetch_station_history(self, station_id, end_date=None):
            return [
                StationReading.model_validate(
                    {
                        "StationId": station_id,
                        "Date": (
                            datetime.now(ISTANBUL_TZ) - timedelta(minutes=1)
                        ).isoformat(),
                        "NO2": None,
                        "SO2": None,
                        "CO": None,
                        "O3": None,
                        "PM10": None,
                        "PM25": None,
                        "CO_1": None,
                        "O3_1": None,
                        "PM10_1": None,
                        "AQIIndex": 15.0,
                        "AQIStatus": 0,
                        "ContaminantParameter": "PM10",
                        "AQIType": 0,
                    }
                )
            ]

    monkeypatch.setattr(server, "provider", _FakeHistoryProvider(stations))

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "get_historical_data", {"province": "Batman", "days": 2}
        )

    assert result.data["il"] == "Batman"
    assert result.data["gun_sayisi"] == 2
    assert len(result.data["istasyonlar"]) == 1
    assert result.data["istasyonlar"][0]["gunluk_ozet"]


async def test_get_historical_data_returns_district_matches(
    load_fixture_text, monkeypatch
):
    stations = _load_all_stations(load_fixture_text)

    class _FakeHistoryProvider(_FakeProvider):
        async def fetch_station_history(self, station_id, end_date=None):
            return [
                StationReading.model_validate(
                    {
                        "StationId": station_id,
                        "Date": "2026-07-27T10:00:00",
                        "NO2": None,
                        "SO2": None,
                        "CO": None,
                        "O3": None,
                        "PM10": None,
                        "PM25": None,
                        "CO_1": None,
                        "O3_1": None,
                        "PM10_1": None,
                        "AQIIndex": 15.0,
                        "AQIStatus": 0,
                        "ContaminantParameter": "PM10",
                        "AQIType": 0,
                    }
                )
            ]

    monkeypatch.setattr(server, "provider", _FakeHistoryProvider(stations))
    expected_count = len(
        [
            station
            for station in stations
            if station.city == "Batman" and station.district == "Merkez"
        ]
    )

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "get_historical_data",
            {
                "province": "Batman",
                "days": 2,
                "district": "Merkez",
            },
        )

    assert len(result.data["istasyonlar"]) == expected_count


async def test_get_historical_data_returns_error_for_unmatched_district(
    load_fixture_text, monkeypatch
):
    stations = _load_all_stations(load_fixture_text)
    monkeypatch.setattr(server, "provider", _FakeProvider(stations))

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "get_historical_data",
            {
                "province": "Batman",
                "days": 2,
                "district": "Zzzzzz",
            },
        )

    assert result.data["hata"] == "ilce_eslesmedi"


async def test_historical_data_returns_upstream_error(
    monkeypatch,
):
    monkeypatch.setattr(server, "provider", _FailingProvider())

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "get_historical_data", {"province": "Ankara", "days": 2}
        )

    assert result.data["hata"] == "upstream_hatasi"


async def test_get_trend_summary_rejects_invalid_days(
    load_fixture_text, monkeypatch
):
    stations = _load_all_stations(load_fixture_text)
    monkeypatch.setattr(server, "provider", _FakeProvider(stations))

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "get_trend_summary", {"province": "Batman", "days": 4}
        )

    assert result.data["hata"] == "gecersiz_days"


async def test_get_trend_summary_defaults_to_three_days(
    load_fixture_text, monkeypatch
):
    stations = _load_all_stations(load_fixture_text)

    class _FakeHistoryProvider(_FakeProvider):
        async def fetch_station_history(self, station_id, end_date=None):
            return [
                StationReading.model_validate(
                    {
                        "StationId": station_id,
                        "Date": "2026-07-27T10:00:00",
                        "NO2": None,
                        "SO2": None,
                        "CO": None,
                        "O3": None,
                        "PM10": None,
                        "PM25": None,
                        "CO_1": None,
                        "O3_1": None,
                        "PM10_1": None,
                        "AQIIndex": 15.0,
                        "AQIStatus": 0,
                        "ContaminantParameter": "PM10",
                        "AQIType": 0,
                    }
                )
            ]

    monkeypatch.setattr(server, "provider", _FakeHistoryProvider(stations))

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "get_trend_summary", {"province": "Batman"}
        )

    assert result.data["istasyonlar"][0]["pencere_gun"] == 3


async def test_get_trend_summary_accepts_six_days(
    load_fixture_text, monkeypatch
):
    stations = _load_all_stations(load_fixture_text)

    class _FakeHistoryProvider(_FakeProvider):
        async def fetch_station_history(self, station_id, end_date=None):
            return [
                StationReading.model_validate(
                    {
                        "StationId": station_id,
                        "Date": "2026-07-27T10:00:00",
                        "NO2": None,
                        "SO2": None,
                        "CO": None,
                        "O3": None,
                        "PM10": None,
                        "PM25": None,
                        "CO_1": None,
                        "O3_1": None,
                        "PM10_1": None,
                        "AQIIndex": 15.0,
                        "AQIStatus": 0,
                        "ContaminantParameter": "PM10",
                        "AQIType": 0,
                    }
                )
            ]

    monkeypatch.setattr(server, "provider", _FakeHistoryProvider(stations))

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "get_trend_summary",
            {"province": "Batman", "days": 6},
        )

    assert result.data["istasyonlar"][0]["pencere_gun"] == 6


async def test_get_trend_summary_returns_structured_error_on_upstream_failure(
    monkeypatch,
):
    monkeypatch.setattr(server, "provider", _FailingProvider())

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "get_trend_summary", {"province": "Ankara"}
        )

    assert result.data["hata"] == "upstream_hatasi"


async def test_compare_cities_returns_two_province_summaries(
    load_fixture_text, monkeypatch
):
    stations = _load_all_stations(load_fixture_text)
    monkeypatch.setattr(server, "provider", _FakeProvider(stations))

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "compare_cities",
            {"province1": "Batman", "province2": "Kayseri"},
        )

    assert result.data["il1"]["il"] == "Batman"
    assert result.data["il2"]["il"] == "Kayseri"
    assert "fark_cumlesi" in result.data
    assert "ortak_kirleticiler" in result.data
    assert "istasyonlar" not in result.data


async def test_compare_cities_returns_error_for_unknown_province1(
    load_fixture_text, monkeypatch
):
    stations = _load_all_stations(load_fixture_text)
    monkeypatch.setattr(server, "provider", _FakeProvider(stations))

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "compare_cities",
            {"province1": "Zzzzzzz", "province2": "Kayseri"},
        )

    assert result.data["hata"] == "eslesme_bulunamadi"
    assert "province1" not in result.data["mesaj"]


async def test_compare_cities_names_province2_in_ambiguity_message(
    load_fixture_text, monkeypatch
):
    stations = _load_all_stations(load_fixture_text)
    monkeypatch.setattr(server, "provider", _FakeProvider(stations))

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "compare_cities",
            {"province1": "Batman", "province2": "Ereğli"},
        )

    assert result.data["hata"] == "belirsiz_eslesme"
    assert "province2 parametresini" in result.data["mesaj"]
    assert "province1 parametresini" not in result.data["mesaj"]


async def test_compare_cities_warns_when_a_province_has_no_stations(
    load_fixture_text, monkeypatch
):
    stations = _load_all_stations(load_fixture_text)
    monkeypatch.setattr(server, "provider", _FakeProvider(stations))

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "compare_cities",
            {"province1": "Batman", "province2": "Hakkari"},
        )

    assert "uyari" in result.data
    assert "Hakkari" in result.data["uyari"]


async def test_compare_cities_filters_by_detected_district(
    load_fixture_text, monkeypatch
):
    stations = _load_all_stations(load_fixture_text)
    istanbul = [s for s in stations if s.city == "İstanbul"]
    districts = {s.district for s in istanbul}
    assert len(districts) >= 2, (
        "fixture must contain at least two İstanbul districts for this "
        "test to be meaningful"
    )
    district_a, district_b = sorted(districts)[:2]
    monkeypatch.setattr(server, "provider", _FakeProvider(stations))

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "compare_cities",
            {"province1": district_a, "province2": district_b},
        )

    assert result.data["il1"]["ilce"] == district_a
    assert result.data["il2"]["ilce"] == district_b
    expected_count_a = len([s for s in istanbul if s.district == district_a])
    expected_count_b = len([s for s in istanbul if s.district == district_b])
    assert result.data["il1"]["istasyon_sayisi"] == expected_count_a
    assert result.data["il2"]["istasyon_sayisi"] == expected_count_b


async def test_compare_cities_distinguishes_districts(
    load_fixture_text, monkeypatch
):
    stations = _load_all_stations(load_fixture_text)
    istanbul = [s for s in stations if s.city == "İstanbul"]
    districts = {s.district for s in istanbul}
    assert len(districts) >= 2
    district_a, district_b = sorted(districts)[:2]
    monkeypatch.setattr(server, "provider", _FakeProvider(stations))

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "compare_cities",
            {"province1": district_a, "province2": district_b},
        )

    assert result.data["il1"]["istasyon_sayisi"] != len(istanbul)
    assert result.data["il2"]["istasyon_sayisi"] != len(istanbul)


async def test_compare_cities_returns_structured_error_on_upstream_failure(
    monkeypatch,
):
    monkeypatch.setattr(server, "provider", _FailingProvider())

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "compare_cities",
            {"province1": "Ankara", "province2": "Kayseri"},
        )

    assert result.data["hata"] == "upstream_hatasi"


async def test_get_ranking_worst_mode_orders_descending(
    load_fixture_text, monkeypatch
):
    stations = _load_all_stations(load_fixture_text)
    monkeypatch.setattr(server, "provider", _FakeProvider(stations))

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "get_ranking", {"mode": "worst", "limit": 5}
        )

    values = [row["temsili_hki"] for row in result.data["siralama"]]
    assert values == sorted(values, reverse=True)
    assert len(result.data["siralama"]) == 5
    assert result.data["mode"] == "worst"


async def test_get_ranking_best_mode_orders_ascending(
    load_fixture_text, monkeypatch
):
    stations = _load_all_stations(load_fixture_text)
    monkeypatch.setattr(server, "provider", _FakeProvider(stations))

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "get_ranking", {"mode": "best", "limit": 5}
        )

    values = [row["temsili_hki"] for row in result.data["siralama"]]
    assert values == sorted(values)


async def test_get_ranking_rejects_invalid_mode(
    load_fixture_text, monkeypatch
):
    stations = _load_all_stations(load_fixture_text)
    monkeypatch.setattr(server, "provider", _FakeProvider(stations))

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "get_ranking", {"mode": "medium", "limit": 5}
        )

    assert result.data["hata"] == "gecersiz_mode"


async def test_get_ranking_rejects_non_positive_limit(
    load_fixture_text, monkeypatch
):
    stations = _load_all_stations(load_fixture_text)
    monkeypatch.setattr(server, "provider", _FakeProvider(stations))

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "get_ranking", {"mode": "worst", "limit": 0}
        )

    assert result.data["hata"] == "gecersiz_limit"


async def test_get_ranking_returns_structured_error_on_upstream_failure(
    monkeypatch,
):
    monkeypatch.setattr(server, "provider", _FailingProvider())

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "get_ranking", {"mode": "worst", "limit": 5}
        )

    assert result.data["hata"] == "upstream_hatasi"


async def test_get_detailed_ranking_worst_mode_orders_descending(
    load_fixture_text, monkeypatch
):
    stations = _load_all_stations(load_fixture_text)
    monkeypatch.setattr(server, "provider", _FakeProvider(stations))
    monkeypatch.setattr(server, "_detailed_ranking_cache", RankingCache())

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "get_detailed_ranking", {"mode": "worst", "limit": 10}
        )

    values = [row["hki"] for row in result.data["siralama"]]
    assert values == sorted(values, reverse=True)
    assert len(result.data["siralama"]) == 10
    assert "il" in result.data["siralama"][0]
    assert "ad" in result.data["siralama"][0]


async def test_get_detailed_ranking_best_mode_orders_ascending(
    load_fixture_text, monkeypatch
):
    stations = _load_all_stations(load_fixture_text)
    monkeypatch.setattr(server, "provider", _FakeProvider(stations))
    monkeypatch.setattr(server, "_detailed_ranking_cache", RankingCache())

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "get_detailed_ranking", {"mode": "best", "limit": 10}
        )

    values = [row["hki"] for row in result.data["siralama"]]
    assert values == sorted(values)


async def test_get_detailed_ranking_rejects_invalid_mode(
    load_fixture_text, monkeypatch
):
    stations = _load_all_stations(load_fixture_text)
    monkeypatch.setattr(server, "provider", _FakeProvider(stations))
    monkeypatch.setattr(server, "_detailed_ranking_cache", RankingCache())

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "get_detailed_ranking", {"mode": "medium", "limit": 5}
        )

    assert result.data["hata"] == "gecersiz_mode"


async def test_get_detailed_ranking_serves_cached_result_within_ttl(
    load_fixture_text, monkeypatch
):
    stations = _load_all_stations(load_fixture_text)
    monkeypatch.setattr(server, "provider", _FakeProvider(stations))
    clock = {"now": 0.0}
    cache = RankingCache(ttl_seconds=3600.0, clock=lambda: clock["now"])
    monkeypatch.setattr(server, "_detailed_ranking_cache", cache)

    async with Client(server.mcp) as client:
        first = await client.call_tool(
            "get_detailed_ranking", {"mode": "worst", "limit": 3}
        )
        monkeypatch.setattr(
            server, "provider", _FakeProvider(list(reversed(stations)))
        )
        clock["now"] += 1800
        second = await client.call_tool(
            "get_detailed_ranking", {"mode": "worst", "limit": 3}
        )

    assert first.data["siralama"] == second.data["siralama"]


async def test_detailed_ranking_returns_upstream_error(
    monkeypatch,
):
    monkeypatch.setattr(server, "provider", _FailingProvider())
    monkeypatch.setattr(server, "_detailed_ranking_cache", RankingCache())

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "get_detailed_ranking", {"mode": "worst", "limit": 5}
        )

    assert result.data["hata"] == "upstream_hatasi"


async def test_get_health_advisory_returns_advisory_for_worst_station(
    load_fixture_text, monkeypatch
):
    stations = _load_all_stations(load_fixture_text)
    monkeypatch.setattr(server, "provider", _FakeProvider(stations))

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "get_health_advisory", {"province": "Batman"}
        )

    assert result.data["il"] == "Batman"
    assert "temsili_hki" in result.data
    assert "temsili_kategori" in result.data
    assert result.data["tavsiye"]


async def test_health_advisory_narrows_district_input(
    load_fixture_text, monkeypatch
):
    # Regression: same bug as list_stations - the note claimed the
    # district was detected but temsili_hki used to be computed from
    # every station in the whole province, not just that district.
    stations = _load_all_stations(load_fixture_text)
    monkeypatch.setattr(server, "provider", _FakeProvider(stations))

    district_stations = [
        s
        for s in stations
        if s.city == "İstanbul"
        and s.district == "Kadıköy"
        and s.current.aqi_index is not None
    ]
    expected_worst = max(district_stations, key=lambda s: s.current.aqi_index)

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "get_health_advisory", {"province": "Kadıköy"}
        )

    assert result.data["il"] == "İstanbul"
    assert result.data["ilce"] == "Kadıköy"
    assert result.data["temsili_hki"] == expected_worst.current.aqi_index


async def test_get_health_advisory_returns_error_for_unknown_province(
    load_fixture_text, monkeypatch
):
    stations = _load_all_stations(load_fixture_text)
    monkeypatch.setattr(server, "provider", _FakeProvider(stations))

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "get_health_advisory", {"province": "Zzzzzzz"}
        )

    assert result.data["hata"] == "eslesme_bulunamadi"


async def test_get_health_advisory_warns_when_province_has_no_stations(
    load_fixture_text, monkeypatch
):
    stations = _load_all_stations(load_fixture_text)
    monkeypatch.setattr(server, "provider", _FakeProvider(stations))

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "get_health_advisory", {"province": "Hakkari"}
        )

    assert "uyari" in result.data


async def test_get_health_advisory_returns_default_text_when_no_valid_hki(
    load_fixture_text, monkeypatch
):
    station = _load_all_stations(load_fixture_text)[0].model_copy(deep=True)
    station.city = "Kayseri"
    station.current.aqi_index = None
    monkeypatch.setattr(server, "provider", _FakeProvider([station]))

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "get_health_advisory", {"province": "Kayseri"}
        )

    assert "uyari" in result.data
    assert "verilemiyor" in result.data["tavsiye"]


async def test_health_advisory_returns_upstream_error(
    monkeypatch,
):
    monkeypatch.setattr(server, "provider", _FailingProvider())

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "get_health_advisory", {"province": "Ankara"}
        )

    assert result.data["hata"] == "upstream_hatasi"


async def test_check_alert_default_hki_exceeds_threshold(
    load_fixture_text, monkeypatch
):
    stations = _load_all_stations(load_fixture_text)
    monkeypatch.setattr(server, "provider", _FakeProvider(stations))
    batman_worst = max(
        (s for s in stations if s.city == "Batman"),
        key=lambda s: s.current.aqi_index,
    )

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "check_alert",
            {
                "province": "Batman",
                "threshold": batman_worst.current.aqi_index - 1,
            },
        )

    assert result.data["kirletici"] == "HKI"
    assert result.data["esik_asildi"] is True
    assert result.data["istasyonlar"][0]["ad"] == batman_worst.name


async def test_check_alert_default_hki_does_not_exceed_high_threshold(
    load_fixture_text, monkeypatch
):
    stations = _load_all_stations(load_fixture_text)
    monkeypatch.setattr(server, "provider", _FakeProvider(stations))

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "check_alert",
            {"province": "Batman", "threshold": 100000.0},
        )

    assert result.data["esik_asildi"] is False


async def test_check_alert_pm25_dot_notation_maps_to_pm25_field(
    load_fixture_text, monkeypatch
):
    station = _load_all_stations(load_fixture_text)[0].model_copy(deep=True)
    station.city = "Kayseri"
    station.parameters = ["PM25"]
    station.current.pm25 = 42.0
    monkeypatch.setattr(server, "provider", _FakeProvider([station]))

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "check_alert",
            {
                "province": "Kayseri",
                "threshold": 10.0,
                "pollutant": "PM2.5",
            },
        )

    assert result.data["kirletici"] == "PM25"
    assert result.data["istasyonlar"][0]["deger"] == 42.0
    assert result.data["esik_asildi"] is True
    assert result.data["istasyonlar"][0]["birim"] == "µg/m³"


async def test_check_alert_rejects_invalid_pollutant(
    load_fixture_text, monkeypatch
):
    stations = _load_all_stations(load_fixture_text)
    monkeypatch.setattr(server, "provider", _FakeProvider(stations))

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "check_alert",
            {
                "province": "Batman",
                "threshold": 50.0,
                "pollutant": "XYZ",
            },
        )

    assert result.data["hata"] == "gecersiz_kirletici"


async def test_check_alert_district_checks_each_matching_station(
    load_fixture_text, monkeypatch
):
    stations = _load_all_stations(load_fixture_text)
    monkeypatch.setattr(server, "provider", _FakeProvider(stations))
    expected_count = len(
        [
            station
            for station in stations
            if station.city == "Batman" and station.district == "Merkez"
        ]
    )

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "check_alert",
            {
                "province": "Batman",
                "threshold": 50.0,
                "district": "Merkez",
            },
        )

    assert len(result.data["istasyonlar"]) == expected_count


async def test_check_alert_returns_error_for_unmatched_district(
    load_fixture_text, monkeypatch
):
    stations = _load_all_stations(load_fixture_text)
    monkeypatch.setattr(server, "provider", _FakeProvider(stations))

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "check_alert",
            {
                "province": "Batman",
                "threshold": 50.0,
                "district": "Zzzzzz",
            },
        )

    assert result.data["hata"] == "ilce_eslesmedi"


async def test_check_alert_warns_when_pollutant_has_no_valid_reading(
    load_fixture_text, monkeypatch
):
    station = _load_all_stations(load_fixture_text)[0].model_copy(deep=True)
    station.city = "Kayseri"
    station.current.co = None
    monkeypatch.setattr(server, "provider", _FakeProvider([station]))

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "check_alert",
            {
                "province": "Kayseri",
                "threshold": 10.0,
                "pollutant": "CO",
            },
        )

    assert "uyari" in result.data
    assert "CO" in result.data["uyari"]


async def test_check_alert_marks_missing_district_reading_as_no_data(
    load_fixture_text, monkeypatch
):
    station = _load_all_stations(load_fixture_text)[0].model_copy(deep=True)
    station.city = "Kayseri"
    station.district = "Melikgazi"
    station.current.co = None
    monkeypatch.setattr(server, "provider", _FakeProvider([station]))

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "check_alert",
            {
                "province": "Kayseri",
                "district": "Melikgazi",
                "threshold": 10.0,
                "pollutant": "CO",
            },
        )

    row = result.data["istasyonlar"][0]
    assert row["deger"] is None
    assert row["durum"] == "veri_yok"
    assert row["esik_asildi"] is False


async def test_check_alert_warns_when_province_has_no_stations(
    load_fixture_text, monkeypatch
):
    stations = _load_all_stations(load_fixture_text)
    monkeypatch.setattr(server, "provider", _FakeProvider(stations))

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "check_alert",
            {"province": "Hakkari", "threshold": 50.0},
        )

    assert "uyari" in result.data


async def test_check_alert_returns_structured_error_on_upstream_failure(
    monkeypatch,
):
    monkeypatch.setattr(server, "provider", _FailingProvider())

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "check_alert",
            {"province": "Ankara", "threshold": 50.0},
        )

    assert result.data["hata"] == "upstream_hatasi"


async def test_statistical_summary_does_not_change_health_advisory_contract(
    load_fixture_text, monkeypatch
):
    stations = _load_all_stations(load_fixture_text)
    monkeypatch.setattr(server, "provider", _FakeProvider(stations))

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "get_health_advisory", {"province": "Batman"}
        )

    assert "temsili_hki" in result.data
    assert "temsili_kategori" in result.data


async def test_statistical_summary_does_not_change_ranking_contract(
    load_fixture_text, monkeypatch
):
    stations = _load_all_stations(load_fixture_text)
    monkeypatch.setattr(server, "provider", _FakeProvider(stations))

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "get_ranking", {"mode": "worst", "limit": 1}
        )

    assert "temsili_hki" in result.data["siralama"][0]


async def test_get_air_quality_fuzzy_corrects_district_typo(
    load_fixture_text, monkeypatch
):
    stations = _load_all_stations(load_fixture_text)
    monkeypatch.setattr(server, "provider", _FakeProvider(stations))

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "get_air_quality",
            {"province": "İstanbul", "district": "Kadikoyy"},
        )

    assert result.data.get("hata") is None
    assert "Kadıköy" in result.data["not"]
    assert result.data["ilce_ozeti"] is not None


async def test_get_historical_data_fuzzy_corrects_district_typo(
    load_fixture_text, monkeypatch
):
    stations = _load_all_stations(load_fixture_text)
    monkeypatch.setattr(server, "provider", _FakeHistoryProvider(stations))

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "get_historical_data",
            {
                "province": "İstanbul",
                "district": "Kadikoyy",
                "days": 2,
            },
        )

    assert result.data.get("hata") is None
    assert "Kadıköy" in result.data["not"]
    assert result.data["istasyonlar"]


async def test_get_trend_summary_fuzzy_corrects_district_typo(
    load_fixture_text, monkeypatch
):
    stations = _load_all_stations(load_fixture_text)
    monkeypatch.setattr(server, "provider", _FakeHistoryProvider(stations))

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "get_trend_summary",
            {
                "province": "İstanbul",
                "district": "Kadikoyy",
            },
        )

    assert result.data.get("hata") is None
    assert "Kadıköy" in result.data["not"]
    assert result.data["istasyonlar"]


async def test_check_alert_fuzzy_corrects_district_typo(
    load_fixture_text, monkeypatch
):
    stations = _load_all_stations(load_fixture_text)
    monkeypatch.setattr(server, "provider", _FakeProvider(stations))

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "check_alert",
            {
                "province": "İstanbul",
                "district": "Kadikoyy",
                "threshold": 50.0,
            },
        )

    assert result.data.get("hata") is None
    assert "Kadıköy" in result.data["not"]
    assert result.data["istasyonlar"]
