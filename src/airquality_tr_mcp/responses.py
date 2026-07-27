from __future__ import annotations

from datetime import datetime, timedelta

from .categories import category_for_status
from .models import ISTANBUL_TZ, POLLUTANT_UNIT, Station
from .normalization import AmbiguousMatchError, NoMatchError
from .provider import UpstreamError

STALE_DATA_WARNING_THRESHOLD = timedelta(hours=2)


def _data_age_warning(measured_at: datetime, now: datetime) -> str | None:
    age = now - measured_at
    if age < STALE_DATA_WARNING_THRESHOLD:
        return None
    hours = int(age.total_seconds() // 3600)
    return f"veri {hours} saat önce güncellenmiş"


def station_summary(
    station: Station, *, now: datetime | None = None
) -> dict:
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


_POLLUTANT_FIELDS = {
    "NO2": "no2",
    "SO2": "so2",
    "CO": "co",
    "O3": "o3",
    "PM10": "pm10",
    "PM25": "pm25",
}


def _pollutant_reading(
    pollutant: str, attr: str, station: Station
) -> dict:
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
            for pollutant, attr in _POLLUTANT_FIELDS.items()
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
    return {
        "hata": "eslesme_bulunamadi",
        "girdi": exc.query,
        "oneriler": exc.suggestions,
        "mesaj": f"'{exc.query}' bulunamadı.{suggestions_text}",
    }
