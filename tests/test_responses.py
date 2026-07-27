from datetime import datetime, timedelta

from airquality_tr_mcp.models import ISTANBUL_TZ
from airquality_tr_mcp.normalization import (
    AmbiguousMatchError,
    NoMatchError,
)
from airquality_tr_mcp.parsing import parse_bulk_stations
from airquality_tr_mcp.responses import (
    resolution_error_payload,
    station_breakdown_row,
    station_detail_payload,
    station_ref_with_category,
    station_summary,
)


def _load_all_stations(load_fixture_text):
    raw = load_fixture_text(
        "GetAirQualityStations_bulk_tum_ag.network-response"
    )
    return parse_bulk_stations(raw)


def test_resolution_error_payload_for_no_match():
    exc = NoMatchError("Anlatayy", ["Antalya", "Ankara"])
    payload = resolution_error_payload(exc)
    assert payload["hata"] == "eslesme_bulunamadi"
    assert payload["girdi"] == "Anlatayy"
    assert payload["oneriler"] == ["Antalya", "Ankara"]
    assert "Antalya" in payload["mesaj"]


def test_resolution_error_payload_for_no_match_without_suggestions():
    exc = NoMatchError("Zzzzz", [])
    payload = resolution_error_payload(exc)
    assert payload["oneriler"] == []
    assert "Zzzzz" in payload["mesaj"]


def test_resolution_error_payload_for_ambiguous_match():
    exc = AmbiguousMatchError("Ereğli", ["Konya", "Zonguldak"])
    payload = resolution_error_payload(exc)
    assert payload["hata"] == "belirsiz_eslesme"
    assert payload["girdi"] == "Ereğli"
    assert payload["adaylar"] == ["Konya", "Zonguldak"]
    assert "Konya" in payload["mesaj"]
    assert "Zonguldak" in payload["mesaj"]


def test_station_breakdown_row_includes_category(load_fixture_text):
    stations = _load_all_stations(load_fixture_text)
    station = next(
        candidate
        for candidate in stations
        if candidate.current.aqi_status is not None
    )

    row = station_breakdown_row(station)

    assert row["kategori"]["ad"] in {
        "İyi",
        "Orta",
        "Hassas",
        "Sağlıksız",
        "Kötü",
        "Tehlikeli",
        "Ölçüm Yok",
    }


def test_station_ref_with_category_shape(load_fixture_text):
    stations = _load_all_stations(load_fixture_text)
    station = stations[0]

    ref = station_ref_with_category(station)

    assert ref["ad"] == station.name
    assert ref["hki"] == station.current.aqi_index
    assert ref["kategori"] == station_breakdown_row(station)["kategori"]


def test_station_detail_payload_marks_measured_value_as_olcum(
    load_fixture_text,
):
    stations = _load_all_stations(load_fixture_text)
    station = next(
        candidate
        for candidate in stations
        if candidate.current.pm10 is not None
        and "PM10" in candidate.parameters
    )

    payload = station_detail_payload(station)

    assert payload["olcumler"]["PM10"] == {
        "deger": station.current.pm10,
        "birim": "µg/m³",
        "durum": "olcum",
    }


def test_station_detail_payload_marks_missing_value_as_veri_yok(
    load_fixture_text,
):
    stations = _load_all_stations(load_fixture_text)
    station = next(
        candidate for candidate in stations if candidate.name == "Batman - 2"
    )

    payload = station_detail_payload(station)

    assert payload["olcumler"]["NO2"] == {
        "deger": None,
        "birim": "µg/m³",
        "durum": "veri_yok",
    }


def test_station_detail_payload_marks_unconfigured_pollutant_as_olculmuyor(
    load_fixture_text,
):
    stations = _load_all_stations(load_fixture_text)
    station = next(
        candidate for candidate in stations if "CO" not in candidate.parameters
    )

    payload = station_detail_payload(station)

    assert payload["olcumler"]["CO"] == {
        "deger": None,
        "birim": "µg/m³",
        "durum": "olculmuyor",
    }


def test_station_detail_payload_includes_category(load_fixture_text):
    stations = _load_all_stations(load_fixture_text)

    payload = station_detail_payload(stations[0])

    assert "kategori" in payload


def test_station_summary_adds_age_warning_when_data_is_stale(
    load_fixture_text,
):
    stations = _load_all_stations(load_fixture_text)
    station = stations[0].model_copy(deep=True)
    now = datetime(2026, 7, 27, 15, 0, tzinfo=ISTANBUL_TZ)
    station.current.measured_at = now - timedelta(hours=3)

    payload = station_summary(station, now=now)

    assert payload["veri_yasi_uyarisi"] == (
        "veri 3 saat önce güncellenmiş"
    )


def test_station_summary_omits_age_warning_when_data_is_fresh(
    load_fixture_text,
):
    stations = _load_all_stations(load_fixture_text)
    station = stations[0].model_copy(deep=True)
    now = datetime(2026, 7, 27, 15, 0, tzinfo=ISTANBUL_TZ)
    station.current.measured_at = now - timedelta(minutes=30)

    payload = station_summary(station, now=now)

    assert "veri_yasi_uyarisi" not in payload


def test_station_breakdown_row_adds_age_warning_when_stale(
    load_fixture_text,
):
    stations = _load_all_stations(load_fixture_text)
    station = stations[0].model_copy(deep=True)
    now = datetime(2026, 7, 27, 15, 0, tzinfo=ISTANBUL_TZ)
    station.current.measured_at = now - timedelta(hours=5)

    payload = station_breakdown_row(station, now=now)

    assert payload["veri_yasi_uyarisi"] == (
        "veri 5 saat önce güncellenmiş"
    )


def test_station_detail_payload_adds_age_warning_when_stale(
    load_fixture_text,
):
    stations = _load_all_stations(load_fixture_text)
    station = stations[0].model_copy(deep=True)
    now = datetime(2026, 7, 27, 15, 0, tzinfo=ISTANBUL_TZ)
    station.current.measured_at = now - timedelta(hours=4)

    payload = station_detail_payload(station, now=now)

    assert payload["veri_yasi_uyarisi"] == (
        "veri 4 saat önce güncellenmiş"
    )


def test_station_ref_with_category_adds_age_warning_when_stale(
    load_fixture_text,
):
    stations = _load_all_stations(load_fixture_text)
    station = stations[0].model_copy(deep=True)
    now = datetime(2026, 7, 27, 15, 0, tzinfo=ISTANBUL_TZ)
    station.current.measured_at = now - timedelta(hours=6)

    payload = station_ref_with_category(station, now=now)

    assert payload["veri_yasi_uyarisi"] == (
        "veri 6 saat önce güncellenmiş"
    )


def test_station_ref_with_category_omits_age_warning_when_fresh(
    load_fixture_text,
):
    stations = _load_all_stations(load_fixture_text)
    station = stations[0].model_copy(deep=True)
    now = datetime(2026, 7, 27, 15, 0, tzinfo=ISTANBUL_TZ)
    station.current.measured_at = now - timedelta(minutes=10)

    payload = station_ref_with_category(station, now=now)

    assert "veri_yasi_uyarisi" not in payload
