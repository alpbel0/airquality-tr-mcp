from datetime import datetime, timedelta

from airquality_tr_mcp.aggregation import summarize_aqi
from airquality_tr_mcp.categories import category_for_status
from airquality_tr_mcp.geocoding import (
    AmbiguousLocationError,
    GeocodedPlace,
)
from airquality_tr_mcp.historical import (
    DailySummary,
    InvalidDaysError,
    TrendResult,
)
from airquality_tr_mcp.models import ISTANBUL_TZ
from airquality_tr_mcp.normalization import (
    AmbiguousMatchError,
    NoMatchError,
)
from airquality_tr_mcp.parsing import parse_bulk_stations
from airquality_tr_mcp.provinces import ProvinceResolution
from airquality_tr_mcp.ranking import (
    InvalidLimitError,
    InvalidModeError,
    ProvinceRank,
)
from airquality_tr_mcp.responses import (
    air_quality_summary_payload,
    compare_cities_payload,
    daily_summary_row,
    geocoding_error_payload,
    invalid_days_payload,
    invalid_limit_payload,
    invalid_mode_payload,
    invalid_pollutant_payload,
    malformed_station_id_payload,
    nearest_air_quality_payload,
    province_ranking_row,
    resolution_error_payload,
    station_breakdown_row,
    station_detail_payload,
    station_history_payload,
    station_ranking_row,
    station_ref_with_category,
    station_summary,
    trend_summary_payload,
)
from airquality_tr_mcp.spatial import NearestStationsResult, StationDistance


def _load_all_stations(load_fixture_text):
    raw = load_fixture_text(
        "GetAirQualityStations_bulk_tum_ag.network-response"
    )
    return parse_bulk_stations(raw)


def _load_station(load_fixture_text):
    return _load_all_stations(load_fixture_text)[0].model_copy(deep=True)


def test_malformed_station_id_payload_explains_both_valid_input_forms():
    payload = malformed_station_id_payload("114b79f4")

    assert payload["hata"] == "eksik_veya_bozuk_id"
    assert payload["girdi"] == "114b79f4"
    assert "'114b79f4'" in payload["mesaj"]
    assert "ID" in payload["mesaj"].upper()
    assert "istasyon ad" in payload["mesaj"].lower()


def test_ambiguous_location_payload_lists_at_most_three_candidates():
    candidates = tuple(
        GeocodedPlace(
            label=f"Aday {index}",
            latitude=39.0 + index,
            longitude=32.0 + index,
            importance=0.5,
        )
        for index in range(4)
    )
    payload = geocoding_error_payload(
        AmbiguousLocationError("Atatürk", candidates)
    )

    assert payload["hata"] == "belirsiz_konum"
    assert len(payload["adaylar"]) == 3


def test_success_payload_states_reference_is_not_estimated(
    load_fixture_text,
):
    station = _load_station(load_fixture_text)
    station.current.aqi_index = 67.0
    selection = NearestStationsResult(
        reference=StationDistance(station, 8.4),
        rated=(StationDistance(station, 8.4),),
        unrated_closer=(),
        unrated_count=0,
        nearest_outside=None,
    )

    payload = nearest_air_quality_payload(
        input_payload={"location": "Ankara"},
        location_payload={
            "etiket": "Ankara, Türkiye",
            "lat": 39.93,
            "lon": 32.85,
            "onem_skoru": 0.6,
            "konum_kaynagi": "nominatim_osm",
        },
        selection=selection,
        max_distance_km=75.0,
        now=datetime(2026, 7, 27, 12, tzinfo=ISTANBUL_TZ),
    )

    assert payload["referans_hki"] == 67.0
    assert payload["referans_istasyon"]["mesafe_km"] == 8.4
    assert "hesaplanmamıştır" in payload["aciklama"]
    assert payload["kaynaklar"]["hava_kalitesi"] == "UHKİA"


def test_null_reference_reports_coverage_without_country_claim(
    load_fixture_text,
):
    station = _load_station(load_fixture_text)
    station.current.measured_at = datetime(2026, 7, 27, 10, tzinfo=ISTANBUL_TZ)
    outside = StationDistance(station, 120.0)
    selection = NearestStationsResult(
        reference=None,
        rated=(),
        unrated_closer=(),
        unrated_count=0,
        nearest_outside=outside,
    )

    payload = nearest_air_quality_payload(
        input_payload={"latitude": 39.0, "longitude": 32.0},
        location_payload={
            "lat": 39.0,
            "lon": 32.0,
            "konum_kaynagi": "kullanici_koordinati",
        },
        selection=selection,
        max_distance_km=75.0,
        now=datetime(2026, 7, 27, 12, tzinfo=ISTANBUL_TZ),
    )

    assert payload["referans_hki"] is None
    assert payload["en_yakin_kapsama_disi_istasyon"]["mesafe_km"] == 120.0
    assert "Türkiye dışında" not in str(payload)
    assert (
        payload["en_yakin_kapsama_disi_istasyon"]["veri_yasi_uyarisi"]
        == "veri 2 saat önce güncellenmiş"
    )


def test_null_reference_with_rated_stations_reports_their_distance(
    load_fixture_text,
):
    station = _load_station(load_fixture_text)
    station.current.aqi_index = 54.5
    station.current.measured_at = datetime(
        2026, 7, 27, 11, 30, tzinfo=ISTANBUL_TZ
    )
    rated_but_outside_reference_cap = StationDistance(station, 118.9)
    selection = NearestStationsResult(
        reference=None,
        rated=(rated_but_outside_reference_cap,),
        unrated_closer=(),
        unrated_count=0,
        nearest_outside=None,
    )

    payload = nearest_air_quality_payload(
        input_payload={"latitude": 37.55, "longitude": 44.05},
        location_payload={
            "lat": 37.55,
            "lon": 44.05,
            "konum_kaynagi": "kullanici_koordinati",
        },
        selection=selection,
        max_distance_km=150.0,
        now=datetime(2026, 7, 27, 12, tzinfo=ISTANBUL_TZ),
    )

    assert payload["referans_hki"] is None
    assert payload["referans_istasyon"] is None
    assert len(payload["yakin_istasyonlar"]) == 1
    assert "118.9" in payload["aciklama"]
    assert "75 km" in payload["aciklama"]


def test_null_reference_with_valid_nearest_outside_reports_its_distance(
    load_fixture_text,
):
    station = _load_station(load_fixture_text)
    station.current.aqi_index = 40.856
    station.current.measured_at = datetime(
        2026, 7, 28, 11, 30, tzinfo=ISTANBUL_TZ
    )
    valid_but_outside_max_distance = StationDistance(station, 0.7)
    selection = NearestStationsResult(
        reference=None,
        rated=(),
        unrated_closer=(),
        unrated_count=0,
        nearest_outside=valid_but_outside_max_distance,
    )

    payload = nearest_air_quality_payload(
        input_payload={"latitude": 41.0082, "longitude": 28.9784},
        location_payload={
            "lat": 41.0082,
            "lon": 28.9784,
            "konum_kaynagi": "kullanici_koordinati",
        },
        selection=selection,
        max_distance_km=0.5,
        now=datetime(2026, 7, 28, 12, tzinfo=ISTANBUL_TZ),
    )

    assert payload["referans_hki"] is None
    assert payload["yakin_istasyonlar"] == []
    assert "0.7" in payload["aciklama"]


def test_null_reference_with_unrated_nearest_outside_keeps_flat_message(
    load_fixture_text,
):
    station = _load_station(load_fixture_text)
    station.current.aqi_index = None
    station.current.measured_at = datetime(
        2026, 7, 28, 11, 30, tzinfo=ISTANBUL_TZ
    )
    unrated_outside = StationDistance(station, 0.7)
    selection = NearestStationsResult(
        reference=None,
        rated=(),
        unrated_closer=(),
        unrated_count=0,
        nearest_outside=unrated_outside,
    )

    payload = nearest_air_quality_payload(
        input_payload={"latitude": 41.0082, "longitude": 28.9784},
        location_payload={
            "lat": 41.0082,
            "lon": 28.9784,
            "konum_kaynagi": "kullanici_koordinati",
        },
        selection=selection,
        max_distance_km=0.5,
        now=datetime(2026, 7, 28, 12, tzinfo=ISTANBUL_TZ),
    )

    assert payload["referans_hki"] is None
    assert "0.7" not in payload["aciklama"]
    assert "yoktur" in payload["aciklama"]


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


def test_no_match_notes_possible_unmonitored_place():
    exc = NoMatchError("Datça", ["Adana", "Hatay", "Batman"])
    payload = resolution_error_payload(exc)

    assert "yazım hatası" in payload["mesaj"]
    assert "aktif bir hava kalitesi istasyonu" in payload["mesaj"]


def test_station_no_match_has_no_place_caveat():
    exc = NoMatchError("Zzzzzzzzzzz", [])
    payload = resolution_error_payload(exc, entity_label="istasyon")

    assert "aktif bir hava kalitesi istasyonu" not in payload["mesaj"]


def test_resolution_error_payload_for_ambiguous_match():
    exc = AmbiguousMatchError("Ereğli", ["Konya", "Zonguldak"])
    payload = resolution_error_payload(exc)
    assert payload["hata"] == "belirsiz_eslesme"
    assert payload["girdi"] == "Ereğli"
    assert payload["adaylar"] == ["Konya", "Zonguldak"]
    assert "Konya" in payload["mesaj"]
    assert "Zonguldak" in payload["mesaj"]


def test_resolution_error_payload_no_match_exposes_parameter_name():
    # Regression: compare_cities passes parameter_name="province1"/"province2"
    # to disambiguate which field failed, but the NoMatchError branch used
    # to silently ignore it - callers had no structured way to tell which
    # of the two province parameters actually caused the failure.
    exc = NoMatchError("xyzqwe", ["Rize", "Yenimahalle"])
    payload = resolution_error_payload(exc, parameter_name="province2")
    assert payload["parametre"] == "province2"


def test_resolution_error_payload_ambiguous_exposes_parameter_name():
    exc = AmbiguousMatchError("Ereğli", ["Konya", "Zonguldak"])
    payload = resolution_error_payload(exc, parameter_name="province1")
    assert payload["parametre"] == "province1"


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

    assert payload["veri_yasi_uyarisi"] == ("veri 3 saat önce güncellenmiş")


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

    assert payload["veri_yasi_uyarisi"] == ("veri 5 saat önce güncellenmiş")


def test_station_detail_payload_adds_age_warning_when_stale(
    load_fixture_text,
):
    stations = _load_all_stations(load_fixture_text)
    station = stations[0].model_copy(deep=True)
    now = datetime(2026, 7, 27, 15, 0, tzinfo=ISTANBUL_TZ)
    station.current.measured_at = now - timedelta(hours=4)

    payload = station_detail_payload(station, now=now)

    assert payload["veri_yasi_uyarisi"] == ("veri 4 saat önce güncellenmiş")


def test_station_ref_with_category_adds_age_warning_when_stale(
    load_fixture_text,
):
    stations = _load_all_stations(load_fixture_text)
    station = stations[0].model_copy(deep=True)
    now = datetime(2026, 7, 27, 15, 0, tzinfo=ISTANBUL_TZ)
    station.current.measured_at = now - timedelta(hours=6)

    payload = station_ref_with_category(station, now=now)

    assert payload["veri_yasi_uyarisi"] == ("veri 6 saat önce güncellenmiş")


def test_station_ref_with_category_omits_age_warning_when_fresh(
    load_fixture_text,
):
    stations = _load_all_stations(load_fixture_text)
    station = stations[0].model_copy(deep=True)
    now = datetime(2026, 7, 27, 15, 0, tzinfo=ISTANBUL_TZ)
    station.current.measured_at = now - timedelta(minutes=10)

    payload = station_ref_with_category(station, now=now)

    assert "veri_yasi_uyarisi" not in payload


def test_invalid_days_payload_shape_for_range():
    exc = InvalidDaysError(120, minimum=1, maximum=90)

    payload = invalid_days_payload(exc)

    assert payload["hata"] == "gecersiz_days"
    assert payload["girdi"] == 120
    assert "1" in payload["mesaj"] and "90" in payload["mesaj"]
    assert "arasında" in payload["mesaj"]


def test_invalid_days_payload_shape_for_discrete_allowed_values():
    exc = InvalidDaysError(4, minimum=3, maximum=6, allowed_values=(3, 6))

    payload = invalid_days_payload(exc)

    assert payload["hata"] == "gecersiz_days"
    assert payload["girdi"] == 4
    assert "sadece 3 veya 6" in payload["mesaj"]
    assert "arasında" not in payload["mesaj"]


def test_daily_summary_row_shape():
    summary = DailySummary("2026-07-27", 10.0, 30.0, 20.0, "NO2")

    row = daily_summary_row(summary)

    assert row == {
        "tarih": "2026-07-27",
        "hki_min": 10.0,
        "hki_max": 30.0,
        "hki_ortalama": 20.0,
        "baskin_kirletici": "NO2",
    }


def test_station_history_payload_shape(load_fixture_text):
    station = _load_all_stations(load_fixture_text)[0]
    summaries = [DailySummary("2026-07-27", 10.0, 30.0, 20.0, "NO2")]

    payload = station_history_payload(station, summaries)

    assert payload["ad"] == station.name
    assert payload["gunluk_ozet"] == [daily_summary_row(summaries[0])]


def test_trend_summary_payload_shape(load_fixture_text):
    station = _load_all_stations(load_fixture_text)[0]
    trend = TrendResult("kotulesiyor", 3, 10.0, 30.0, 20.0)

    payload = trend_summary_payload(station, trend)

    assert payload == {
        "istasyon": station.name,
        "pencere_gun": 3,
        "yon": "kotulesiyor",
        "ilk_yari_ortalama_hki": 10.0,
        "son_yari_ortalama_hki": 30.0,
        "fark": 20.0,
    }


def test_compare_cities_payload_shape_and_better_province(
    load_fixture_text,
):
    stations = _load_all_stations(load_fixture_text)
    batman = [s for s in stations if s.city == "Batman"]
    kayseri = [s for s in stations if s.city == "Kayseri"]
    worst1 = max(batman, key=lambda s: s.current.aqi_index)
    best1 = min(batman, key=lambda s: s.current.aqi_index)
    worst2 = max(kayseri, key=lambda s: s.current.aqi_index)
    best2 = min(kayseri, key=lambda s: s.current.aqi_index)
    resolution1 = ProvinceResolution(
        province="Batman", district=None, note=None
    )
    resolution2 = ProvinceResolution(
        province="Kayseri", district=None, note=None
    )
    common = {
        "PM10": {
            "il1_en_kotu": 10.0,
            "il2_en_kotu": 90.0,
            "birim": "µg/m³",
        }
    }

    payload = compare_cities_payload(
        resolution1,
        worst1,
        best1,
        batman,
        resolution2,
        worst2,
        best2,
        kayseri,
        common,
    )

    assert payload["il1"]["il"] == "Batman"
    assert payload["il1"]["temsili_hki"] == worst1.current.aqi_index
    assert payload["il1"]["istasyon_sayisi"] == len(batman)
    assert payload["il2"]["il"] == "Kayseri"
    assert payload["ortak_kirleticiler"] == common
    if worst1.current.aqi_index < worst2.current.aqi_index:
        assert "Batman" in payload["fark_cumlesi"].split(",")[0]
    assert "en_kotu_istasyon" in payload["il1"]
    assert "en_iyi_istasyon" in payload["il1"]


def test_compare_cities_payload_equal_hki_states_same_level(
    load_fixture_text,
):
    stations = _load_all_stations(load_fixture_text)
    station_a = stations[0].model_copy(deep=True)
    station_a.city = "TestIl1"
    station_a.current.aqi_index = 50.0
    station_b = stations[1].model_copy(deep=True)
    station_b.city = "TestIl2"
    station_b.current.aqi_index = 50.0
    resolution1 = ProvinceResolution(
        province="TestIl1", district=None, note=None
    )
    resolution2 = ProvinceResolution(
        province="TestIl2", district=None, note=None
    )

    payload = compare_cities_payload(
        resolution1,
        station_a,
        station_a,
        [station_a],
        resolution2,
        station_b,
        station_b,
        [station_b],
        {},
    )

    assert "aynı seviyede" in payload["fark_cumlesi"]


def test_compare_cities_payload_includes_ilce_when_district_resolved(
    load_fixture_text,
):
    stations = _load_all_stations(load_fixture_text)
    batman = [s for s in stations if s.city == "Batman"]
    kayseri = [s for s in stations if s.city == "Kayseri"]
    worst1 = max(batman, key=lambda s: s.current.aqi_index)
    best1 = min(batman, key=lambda s: s.current.aqi_index)
    worst2 = max(kayseri, key=lambda s: s.current.aqi_index)
    best2 = min(kayseri, key=lambda s: s.current.aqi_index)
    resolution1 = ProvinceResolution(
        province="Batman",
        district="Merkez",
        note="'Merkez' bir ilçe olarak algılandı, bağlı olduğu il: Batman.",
    )
    resolution2 = ProvinceResolution(
        province="Kayseri", district=None, note=None
    )

    payload = compare_cities_payload(
        resolution1,
        worst1,
        best1,
        batman,
        resolution2,
        worst2,
        best2,
        kayseri,
        {},
    )

    assert payload["il1"]["ilce"] == "Merkez"
    assert "ilce" not in payload["il2"]


def test_compare_cities_payload_sentence_names_districts_not_shared_province(
    load_fixture_text,
):
    stations = _load_all_stations(load_fixture_text)
    station_a = stations[0].model_copy(deep=True)
    station_a.city = "İstanbul"
    station_a.district = "Kadıköy"
    station_a.current.aqi_index = 30.0
    station_b = stations[1].model_copy(deep=True)
    station_b.city = "İstanbul"
    station_b.district = "Beşiktaş"
    station_b.current.aqi_index = 60.0
    resolution1 = ProvinceResolution(
        province="İstanbul",
        district="Kadıköy",
        note=(
            "'Kadıköy' bir ilçe olarak algılandı, bağlı olduğu il: İstanbul."
        ),
    )
    resolution2 = ProvinceResolution(
        province="İstanbul",
        district="Beşiktaş",
        note=(
            "'Beşiktaş' bir ilçe olarak algılandı, bağlı olduğu il: İstanbul."
        ),
    )

    payload = compare_cities_payload(
        resolution1,
        station_a,
        station_a,
        [station_a],
        resolution2,
        station_b,
        station_b,
        [station_b],
        {},
    )

    assert "Kadıköy" in payload["fark_cumlesi"]
    assert "Beşiktaş" in payload["fark_cumlesi"]
    assert "İstanbul ve İstanbul" not in payload["fark_cumlesi"]


def test_invalid_mode_payload_shape():
    payload = invalid_mode_payload(InvalidModeError("okay"))

    assert payload["hata"] == "gecersiz_mode"
    assert payload["girdi"] == "okay"
    assert "best" in payload["mesaj"] and "worst" in payload["mesaj"]


def test_invalid_limit_payload_shape():
    payload = invalid_limit_payload(InvalidLimitError(0))

    assert payload["hata"] == "gecersiz_limit"
    assert payload["girdi"] == 0
    assert "0" in payload["mesaj"]


def test_province_ranking_row_shape(load_fixture_text):
    station = _load_all_stations(load_fixture_text)[0]
    rank = ProvinceRank(province=station.city, representative_station=station)

    assert province_ranking_row(rank) == {
        "il": station.city,
        "temsili_hki": station.current.aqi_index,
        "temsili_kategori": category_for_status(station.current.aqi_status),
    }


def test_station_ranking_row_shape(load_fixture_text):
    station = _load_all_stations(load_fixture_text)[0]

    assert station_ranking_row(station, now=station.current.measured_at) == {
        "il": station.city,
        "ilce": station.district,
        "ad": station.name,
        "hki": station.current.aqi_index,
        "kategori": category_for_status(station.current.aqi_status),
        "baskin_kirletici": station.current.dominant_pollutant,
        "olcum_zamani": station.current.measured_at.isoformat(),
    }


def test_station_ranking_row_adds_age_warning_when_stale(
    load_fixture_text,
):
    station = _load_all_stations(load_fixture_text)[0].model_copy(deep=True)
    now = datetime(2026, 7, 27, 15, 0, tzinfo=ISTANBUL_TZ)
    station.current.measured_at = now - timedelta(hours=5)

    row = station_ranking_row(station, now=now)

    assert row["veri_yasi_uyarisi"] == "veri 5 saat önce güncellenmiş"


def test_invalid_pollutant_payload_shape():
    payload = invalid_pollutant_payload("XYZ")

    assert payload["hata"] == "gecersiz_kirletici"
    assert payload["girdi"] == "XYZ"
    assert "HKI" in payload["mesaj"]
    assert "PM2.5" in payload["mesaj"]


def test_air_quality_summary_payload_uses_explicit_statistical_names(
    load_fixture_text,
):
    stations = _load_all_stations(load_fixture_text)
    selected = [
        stations[0].model_copy(deep=True),
        stations[1].model_copy(deep=True),
        stations[2].model_copy(deep=True),
    ]
    for station, value in zip(selected, (42.0, 67.0, 91.0)):
        station.current.aqi_index = value

    payload = air_quality_summary_payload(summarize_aqi(selected))

    assert payload["en_yuksek_hki"] == 91.0
    assert payload["ortalama_hki"] == 66.7
    assert payload["medyan_hki"] == 67.0
    assert payload["en_dusuk_hki"] == 42.0
    assert payload["gecerli_istasyon_sayisi"] == 3
    assert payload["en_kotu_istasyon"]["hki"] == 91.0
    assert payload["en_iyi_istasyon"]["hki"] == 42.0
    assert "temsili_hki" not in payload
    assert "temsili_kategori" not in payload


def test_air_quality_summary_payload_adds_district_label(
    load_fixture_text,
):
    station = _load_all_stations(load_fixture_text)[0].model_copy(deep=True)
    station.current.aqi_index = 67.0

    payload = air_quality_summary_payload(
        summarize_aqi([station]), scope_label="Keçiören"
    )

    assert payload["ilce"] == "Keçiören"


def test_air_quality_summary_payload_represents_empty_district():
    payload = air_quality_summary_payload(None, scope_label="Keçiören")

    assert payload == {
        "ilce": "Keçiören",
        "en_yuksek_hki": None,
        "ortalama_hki": None,
        "medyan_hki": None,
        "en_dusuk_hki": None,
        "gecerli_istasyon_sayisi": 0,
        "en_kotu_istasyon": None,
        "en_iyi_istasyon": None,
        "uyari": (
            "Bu kapsamdaki hiçbir istasyonda şu an geçerli bir HKİ ölçümü yok."
        ),
    }


def test_air_quality_summary_station_refs_keep_data_age_warning(
    load_fixture_text,
):
    station = _load_all_stations(load_fixture_text)[0].model_copy(deep=True)
    station.current.aqi_index = 42.0
    station.current.measured_at = datetime(2026, 7, 27, 8, tzinfo=ISTANBUL_TZ)
    now = datetime(2026, 7, 27, 12, tzinfo=ISTANBUL_TZ)

    payload = air_quality_summary_payload(summarize_aqi([station]), now=now)

    assert payload["en_kotu_istasyon"]["veri_yasi_uyarisi"]
    assert payload["en_iyi_istasyon"]["veri_yasi_uyarisi"]
