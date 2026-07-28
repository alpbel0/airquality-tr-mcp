from __future__ import annotations

from datetime import datetime, timedelta

from .aggregation import AqiSummary
from .categories import category_for_status
from .geocoding import (
    AmbiguousLocationError,
    GeocodingError,
    GeocodingRateLimitError,
    GeocodingResponseError,
    GeocodingServiceError,
    GeocodingTimeoutError,
    LocationNotFoundError,
)
from .historical import DailySummary, InvalidDaysError, TrendResult
from .models import ISTANBUL_TZ, POLLUTANT_UNIT, Station
from .normalization import AmbiguousMatchError, NoMatchError
from .pollutants import ALERT_POLLUTANTS, POLLUTANT_FIELDS
from .provider import UpstreamError
from .ranking import InvalidLimitError, InvalidModeError, ProvinceRank
from .spatial import (
    InvalidNearestInputError,
    NearestStationsResult,
    StationDistance,
)

STALE_DATA_WARNING_THRESHOLD = timedelta(hours=2)


def _data_age_warning(measured_at: datetime, now: datetime) -> str | None:
    age = now - measured_at
    if age < STALE_DATA_WARNING_THRESHOLD:
        return None
    hours = int(age.total_seconds() // 3600)
    return f"veri {hours} saat önce güncellenmiş"


def station_summary(station: Station, *, now: datetime | None = None) -> dict:
    now = now or datetime.now(ISTANBUL_TZ)
    payload = {
        "station_id": station.id,
        "ad": station.name,
        "il": station.city,
        "ilce": station.district,
        "lat": station.coordinate.lat,
        "lon": station.coordinate.lon,
        "parametreler": station.parameters,
        "olcum_zamani": station.current.measured_at.isoformat(),
    }
    warning = _data_age_warning(station.current.measured_at, now)
    if warning:
        payload["veri_yasi_uyarisi"] = warning
    return payload


def station_breakdown_row(
    station: Station, *, now: datetime | None = None
) -> dict:
    now = now or datetime.now(ISTANBUL_TZ)
    payload = {
        "ad": station.name,
        "hki": station.current.aqi_index,
        "kategori": category_for_status(station.current.aqi_status),
        "baskin_kirletici": station.current.dominant_pollutant,
        "olcum_zamani": station.current.measured_at.isoformat(),
    }
    warning = _data_age_warning(station.current.measured_at, now)
    if warning:
        payload["veri_yasi_uyarisi"] = warning
    return payload


def station_ref_with_category(
    station: Station, *, now: datetime | None = None
) -> dict:
    now = now or datetime.now(ISTANBUL_TZ)
    payload = {
        "ad": station.name,
        "hki": station.current.aqi_index,
        "kategori": category_for_status(station.current.aqi_status),
    }
    warning = _data_age_warning(station.current.measured_at, now)
    if warning:
        payload["veri_yasi_uyarisi"] = warning
    return payload


def _pollutant_reading(pollutant: str, attr: str, station: Station) -> dict:
    if pollutant not in station.parameters:
        return {
            "deger": None,
            "birim": POLLUTANT_UNIT,
            "durum": "olculmuyor",
        }
    value = getattr(station.current, attr)
    if value is None:
        return {
            "deger": None,
            "birim": POLLUTANT_UNIT,
            "durum": "veri_yok",
        }
    return {
        "deger": value,
        "birim": POLLUTANT_UNIT,
        "durum": "olcum",
    }


def station_detail_payload(
    station: Station, *, now: datetime | None = None
) -> dict:
    now = now or datetime.now(ISTANBUL_TZ)
    reading = station.current
    payload = {
        "station_id": station.id,
        "ad": station.name,
        "il": station.city,
        "ilce": station.district,
        "lat": station.coordinate.lat,
        "lon": station.coordinate.lon,
        "olcum_zamani": reading.measured_at.isoformat(),
        "hki": reading.aqi_index,
        "kategori": category_for_status(reading.aqi_status),
        "baskin_kirletici": reading.dominant_pollutant,
        "olcumler": {
            pollutant: _pollutant_reading(pollutant, attr, station)
            for pollutant, attr in POLLUTANT_FIELDS.items()
        },
        "not": None,
    }
    warning = _data_age_warning(reading.measured_at, now)
    if warning:
        payload["veri_yasi_uyarisi"] = warning
    return payload


def upstream_error_payload(exc: UpstreamError) -> dict:
    return {"hata": "upstream_hatasi", "mesaj": str(exc)}


def district_error_payload(
    province: str, district: str, stations: list[Station]
) -> dict:
    districts = sorted({station.district for station in stations})
    return {
        "hata": "ilce_eslesmedi",
        "il": province,
        "girilen_ilce": district,
        "ildeki_ilceler": districts,
        "mesaj": (
            f"'{district}' ilçesiyle eşleşen istasyon bulunamadı. "
            f"{province} ilindeki ilçeler: {', '.join(districts)}"
        ),
    }


def missing_station_id_payload(query: str) -> dict:
    return {
        "hata": "istasyon_id_bulunamadi",
        "girdi": query,
        "mesaj": (
            "Bu istasyon ID'si mevcut ağ snapshot'ında bulunamadı; "
            "istasyon listesini yenileyin veya il/istasyon adıyla "
            "yeniden arayın."
        ),
    }


def malformed_station_id_payload(query: str) -> dict:
    return {
        "hata": "eksik_veya_bozuk_id",
        "girdi": query,
        "mesaj": (
            f"'{query}' eksik veya bozuk bir istasyon ID'sine benziyor "
            "(yalnızca onaltılık karakterler ve tire içeriyor). Tam "
            "istasyon ID'sini (örn. "
            "'114b79f4-ede4-44d6-8154-c5dece13f499') veya istasyon "
            "adını (örn. 'Manisa - Akhisar') kullanın."
        ),
    }


def resolution_error_payload(
    exc: NoMatchError | AmbiguousMatchError,
    *,
    entity_label: str = "il",
    parameter_name: str = "province",
) -> dict:
    """Build a user-facing Turkish payload for a resolution failure."""
    if isinstance(exc, AmbiguousMatchError):
        candidates_text = ", ".join(exc.candidates)
        return {
            "hata": "belirsiz_eslesme",
            "parametre": parameter_name,
            "girdi": exc.query,
            "adaylar": exc.candidates,
            "mesaj": (
                f"'{exc.query}' birden fazla {entity_label} ile eşleşiyor: "
                f"{candidates_text}. Lütfen {parameter_name} parametresini "
                "bunlardan biriyle belirtin."
            ),
        }

    suggestions_text = (
        f" Şunu mu demek istediniz: {', '.join(exc.suggestions)}?"
        if exc.suggestions
        else ""
    )
    place_caveat = (
        " Bu bir yazım hatası olabilir, ya da gerçek bir il/ilçe olup şu "
        "anda aktif bir hava kalitesi istasyonu olmayabilir."
        if entity_label == "il"
        else ""
    )
    return {
        "hata": "eslesme_bulunamadi",
        "parametre": parameter_name,
        "girdi": exc.query,
        "oneriler": exc.suggestions,
        "mesaj": f"'{exc.query}' bulunamadı.{suggestions_text}{place_caveat}",
    }


def invalid_days_payload(exc: InvalidDaysError) -> dict:
    if exc.allowed_values is not None:
        choices = " veya ".join(str(value) for value in exc.allowed_values)
        detail = f"days sadece {choices} olabilir"
    else:
        detail = f"days {exc.minimum} ile {exc.maximum} arasında olmalı"
    return {
        "hata": "gecersiz_days",
        "girdi": exc.days,
        "mesaj": f"{detail}, {exc.days} verildi.",
    }


def daily_summary_row(summary: DailySummary) -> dict:
    return {
        "tarih": summary.date,
        "hki_min": summary.hki_min,
        "hki_max": summary.hki_max,
        "hki_ortalama": summary.hki_ortalama,
        "baskin_kirletici": summary.baskin_kirletici,
    }


def station_history_payload(
    station: Station, summaries: list[DailySummary]
) -> dict:
    return {
        "ad": station.name,
        "gunluk_ozet": [daily_summary_row(summary) for summary in summaries],
    }


def trend_summary_payload(station: Station, trend: TrendResult) -> dict:
    return {
        "istasyon": station.name,
        "pencere_gun": trend.window_days,
        "yon": trend.direction,
        "ilk_yari_ortalama_hki": trend.first_half_avg,
        "son_yari_ortalama_hki": trend.second_half_avg,
        "fark": trend.difference,
    }


def compare_cities_payload(
    resolution1,
    worst1: Station,
    best1: Station,
    stations1: list[Station],
    resolution2,
    worst2: Station,
    best2: Station,
    stations2: list[Station],
    common_pollutants: dict,
) -> dict:
    label1 = resolution1.district or resolution1.province
    label2 = resolution2.district or resolution2.province
    hki1 = worst1.current.aqi_index
    hki2 = worst2.current.aqi_index
    if hki1 < hki2:
        difference_sentence = (
            f"{label1}'nin hava kalitesi şu an "
            f"{label2}'dan daha iyi "
            f"(HKİ {hki1:.0f}'e karşı {hki2:.0f})."
        )
    elif hki1 > hki2:
        difference_sentence = (
            f"{label2}'nin hava kalitesi şu an "
            f"{label1}'dan daha iyi "
            f"(HKİ {hki2:.0f}'e karşı {hki1:.0f})."
        )
    else:
        difference_sentence = (
            f"{label1} ve {label2}'nin "
            f"hava kalitesi şu an aynı seviyede (HKİ {hki1:.0f})."
        )

    il1_payload = {
        "il": resolution1.province,
        "not": resolution1.note,
        "temsili_hki": hki1,
        "temsili_kategori": category_for_status(worst1.current.aqi_status),
        "en_kotu_istasyon": station_ref_with_category(worst1),
        "en_iyi_istasyon": station_ref_with_category(best1),
        "istasyon_sayisi": len(stations1),
    }
    if resolution1.district:
        il1_payload["ilce"] = resolution1.district

    il2_payload = {
        "il": resolution2.province,
        "not": resolution2.note,
        "temsili_hki": hki2,
        "temsili_kategori": category_for_status(worst2.current.aqi_status),
        "en_kotu_istasyon": station_ref_with_category(worst2),
        "en_iyi_istasyon": station_ref_with_category(best2),
        "istasyon_sayisi": len(stations2),
    }
    if resolution2.district:
        il2_payload["ilce"] = resolution2.district

    return {
        "il1": il1_payload,
        "il2": il2_payload,
        "ortak_kirleticiler": common_pollutants,
        "fark_cumlesi": difference_sentence,
    }


def invalid_mode_payload(exc: InvalidModeError) -> dict:
    return {
        "hata": "gecersiz_mode",
        "girdi": exc.mode,
        "mesaj": (
            f"mode sadece 'best' veya 'worst' olabilir, '{exc.mode}' verildi."
        ),
    }


def invalid_limit_payload(exc: InvalidLimitError) -> dict:
    return {
        "hata": "gecersiz_limit",
        "girdi": exc.limit,
        "mesaj": (
            f"limit 1 veya daha büyük bir tam sayı olmalı, "
            f"{exc.limit} verildi."
        ),
    }


def province_ranking_row(rank: ProvinceRank) -> dict:
    station = rank.representative_station
    return {
        "il": rank.province,
        "temsili_hki": station.current.aqi_index,
        "temsili_kategori": category_for_status(station.current.aqi_status),
    }


def station_ranking_row(
    station: Station, *, now: datetime | None = None
) -> dict:
    now = now or datetime.now(ISTANBUL_TZ)
    payload = {
        "il": station.city,
        "ilce": station.district,
        "ad": station.name,
        "hki": station.current.aqi_index,
        "kategori": category_for_status(station.current.aqi_status),
        "baskin_kirletici": station.current.dominant_pollutant,
        "olcum_zamani": station.current.measured_at.isoformat(),
    }
    warning = _data_age_warning(station.current.measured_at, now)
    if warning:
        payload["veri_yasi_uyarisi"] = warning
    return payload


def air_quality_summary_payload(
    summary: AqiSummary | None,
    *,
    scope_label: str | None = None,
    now: datetime | None = None,
) -> dict:
    payload = {}
    if scope_label is not None:
        payload["ilce"] = scope_label

    if summary is None:
        payload.update(
            {
                "en_yuksek_hki": None,
                "ortalama_hki": None,
                "medyan_hki": None,
                "en_dusuk_hki": None,
                "gecerli_istasyon_sayisi": 0,
                "en_kotu_istasyon": None,
                "en_iyi_istasyon": None,
                "uyari": (
                    "Bu kapsamdaki hiçbir istasyonda şu an geçerli "
                    "bir HKİ ölçümü yok."
                ),
            }
        )
        return payload

    payload.update(
        {
            "en_yuksek_hki": summary.highest,
            "ortalama_hki": summary.average,
            "medyan_hki": summary.median,
            "en_dusuk_hki": summary.lowest,
            "gecerli_istasyon_sayisi": summary.valid_station_count,
            "en_kotu_istasyon": station_ref_with_category(
                summary.worst_station, now=now
            ),
            "en_iyi_istasyon": station_ref_with_category(
                summary.best_station, now=now
            ),
        }
    )
    return payload


def invalid_pollutant_payload(pollutant: str) -> dict:
    return {
        "hata": "gecersiz_kirletici",
        "girdi": pollutant,
        "mesaj": (
            f"'{pollutant}' geçerli bir kirletici değil. Kullanılabilir "
            f"değerler: {', '.join(ALERT_POLLUTANTS)}."
        ),
    }


def nearest_station_row(
    item: StationDistance, *, now: datetime | None = None
) -> dict:
    now = now or datetime.now(ISTANBUL_TZ)
    station = item.station
    row = {
        "ad": station.name,
        "il": station.city,
        "ilce": station.district,
        "hki": station.current.aqi_index,
        "kategori": category_for_status(
            station.current.aqi_status
            if station.current.aqi_index is not None
            else None
        ),
        "baskin_kirletici": station.current.dominant_pollutant,
        "mesafe_km": round(item.distance_km, 1),
        "olcum_zamani": station.current.measured_at.isoformat(),
    }
    warning = _data_age_warning(station.current.measured_at, now)
    if warning:
        row["veri_yasi_uyarisi"] = warning
    return row


def _nearest_air_quality_aciklama(
    selection: NearestStationsResult,
) -> str:
    if selection.reference is not None:
        return (
            "Referans HKİ hesaplanmamıştır; en yakın geçerli "
            "istasyonun resmi UHKİA değeridir."
        )
    if selection.rated:
        nearest_rated = selection.rated[0]
        return (
            "75 km referans sınırı içinde geçerli HKİ ölçümü bulunan "
            "istasyon yoktur; belirtilen mesafe içinde en yakın geçerli "
            f"istasyon {nearest_rated.distance_km:.1f} km uzaklıkta "
            "bulunmuştur (75 km sınırı dışında olduğu için referans "
            "olarak sayılmamaktadır)."
        )
    nearest_outside = selection.nearest_outside
    if (
        nearest_outside is not None
        and nearest_outside.station.current.aqi_index is not None
    ):
        return (
            "Belirtilen mesafe içinde geçerli HKİ ölçümü bulunan "
            "istasyon yoktur; en yakın geçerli istasyon "
            f"{nearest_outside.distance_km:.1f} km uzaklıkta bulunmuştur "
            "(belirtilen mesafe sınırı dışında olduğu için "
            "listelenmemiştir)."
        )
    return (
        "Belirlenen mesafe ve 75 km referans sınırı içinde geçerli "
        "HKİ ölçümü bulunan istasyon yoktur."
    )


def nearest_air_quality_payload(
    *,
    input_payload: dict,
    location_payload: dict,
    selection: NearestStationsResult,
    max_distance_km: float,
    now: datetime | None = None,
) -> dict:
    now = now or datetime.now(ISTANBUL_TZ)
    reference_row = (
        nearest_station_row(selection.reference, now=now)
        if selection.reference is not None
        else None
    )
    payload = {
        "girdi": input_payload,
        "cozumlenen_konum": location_payload,
        "referans_hki": (
            selection.reference.station.current.aqi_index
            if selection.reference is not None
            else None
        ),
        "referans_istasyon": reference_row,
        "yakin_istasyonlar": [
            nearest_station_row(item, now=now) for item in selection.rated
        ],
        "verisi_olmayan_yakin_istasyonlar": [
            nearest_station_row(item, now=now)
            for item in selection.unrated_closer
        ],
        "verisiz_istasyon_sayisi": selection.unrated_count,
        "max_mesafe_km": max_distance_km,
        "aciklama": _nearest_air_quality_aciklama(selection),
        "kaynaklar": {
            "konum": (
                "Nominatim/OpenStreetMap"
                if location_payload["konum_kaynagi"] == "nominatim_osm"
                else "Kullanıcı koordinatı"
            ),
            "hava_kalitesi": "UHKİA",
            "mesafe": "Yerel Haversine hesabı",
        },
    }
    if selection.nearest_outside is not None:
        payload["en_yakin_kapsama_disi_istasyon"] = nearest_station_row(
            selection.nearest_outside, now=now
        )
    return payload


def invalid_nearest_input_payload(
    exc: InvalidNearestInputError,
) -> dict:
    return {
        "hata": "gecersiz_parametre",
        "alan": exc.field,
        "girdi": exc.value,
        "mesaj": str(exc),
    }


def geocoding_error_payload(exc: GeocodingError) -> dict:
    if isinstance(exc, AmbiguousLocationError):
        return {
            "hata": "belirsiz_konum",
            "girdi": exc.query,
            "adaylar": [
                {
                    "etiket": item.label,
                    "lat": item.latitude,
                    "lon": item.longitude,
                    "onem_skoru": item.importance,
                }
                for item in exc.candidates[:3]
            ],
            "mesaj": (
                "Birden fazla olası konum bulundu. İl veya ilçe "
                "ekleyerek tekrar deneyin."
            ),
        }
    mapping = {
        LocationNotFoundError: (
            "konum_bulunamadi",
            "Girilen konum Türkiye içinde eşleştirilemedi.",
        ),
        GeocodingRateLimitError: (
            "geocoding_kota_asildi",
            "Nominatim kullanım sınırı aşıldı; daha sonra tekrar deneyin.",
        ),
        GeocodingTimeoutError: (
            "geocoding_zaman_asimi",
            "Konum servisi zamanında yanıt vermedi.",
        ),
        GeocodingResponseError: (
            "geocoding_servis_hatasi",
            "Konum servisinin yanıtı işlenemedi.",
        ),
        GeocodingServiceError: (
            "geocoding_servis_hatasi",
            "Konum servisine şu anda ulaşılamıyor.",
        ),
    }
    for error_type, (code, message) in mapping.items():
        if isinstance(exc, error_type):
            return {"hata": code, "mesaj": message}
    return {
        "hata": "geocoding_servis_hatasi",
        "mesaj": "Konum çözümleme sırasında beklenmeyen bir hata oluştu.",
    }
