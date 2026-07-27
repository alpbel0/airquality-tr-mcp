import sys
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from airquality_tr_mcp import server
from airquality_tr_mcp.cache import CachedProvider
from airquality_tr_mcp.parsing import parse_bulk_stations
from airquality_tr_mcp.provider import UpstreamError
from airquality_tr_mcp.server import mcp

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class _FakeProvider:
    def __init__(self, stations):
        self._stations = stations

    async def fetch_all_stations(self):
        return self._stations


class _FailingProvider:
    async def fetch_all_stations(self):
        from airquality_tr_mcp.provider import UpstreamError

        raise UpstreamError(
            "UHKİA sunucusuna bağlanılamadı (zaman aşımı)."
        )


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
        station["il"] == "Batman"
        for station in result.data["istasyonlar"]
    )
    assert result.data["istasyonlar"][0]["olcum_zamani"]


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


async def test_get_air_quality_returns_province_summary_and_breakdown(
    load_fixture_text, monkeypatch
):
    stations = _load_all_stations(load_fixture_text)
    monkeypatch.setattr(server, "provider", _FakeProvider(stations))

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "get_air_quality", {"province": "Batman"}
        )

    assert result.data["il"] == "Batman"
    assert "temsili_hki" in result.data["il_ozeti"]
    assert "en_kotu_istasyon" in result.data["il_ozeti"]
    assert "en_iyi_istasyon" in result.data["il_ozeti"]
    assert result.data["istasyonlar"]
    assert "kategori" in result.data["istasyonlar"][0]
    assert "temsili_kategori" in result.data["il_ozeti"]
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


async def test_get_station_detail_ambiguity_message_names_station_parameter(
    load_fixture_text, monkeypatch
):
    stations = _load_all_stations(load_fixture_text)
    first = stations[0].model_copy(deep=True)
    first.name = "Aydın"
    second = stations[1].model_copy(deep=True)
    second.name = "Adana"
    monkeypatch.setattr(
        server, "provider", _FakeProvider([first, second])
    )

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


async def test_get_air_quality_attaches_staleness_warning_after_upstream_failure(
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


async def test_get_station_detail_attaches_staleness_warning_after_upstream_failure(
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
