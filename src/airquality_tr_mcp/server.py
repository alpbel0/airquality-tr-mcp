from __future__ import annotations

import logging
import sys

from dotenv import load_dotenv
from fastmcp import FastMCP
from fastmcp.server.lifespan import lifespan

from . import service
from .cache import CachedProvider
from .geocoding import CachedGeocoder, Geocoder, NominatimGeocoder
from .provider import AirQualityProvider, UhkiaProvider
from .ranking import RankingCache

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

provider: AirQualityProvider = CachedProvider(UhkiaProvider())
geocoder: Geocoder = CachedGeocoder(NominatimGeocoder())
_detailed_ranking_cache = RankingCache()


@lifespan
async def _provider_lifespan(_server):
    active_resources = (provider, geocoder)
    yield
    for resource in active_resources:
        close = getattr(resource, "aclose", None)
        if close is not None:
            try:
                await close()
            except Exception:
                logging.getLogger(__name__).exception(
                    "Resource cleanup failed"
                )


mcp = FastMCP(
    name="airquality-tr-mcp",
    lifespan=_provider_lifespan,
)


@mcp.tool
def ping() -> str:
    """Sunucunun ayakta olup olmadığını kontrol eden geçici araç."""
    return "pong"


@mcp.tool
async def list_stations(province: str | None = None) -> dict:
    """List UHKİA stations, optionally restricted to one province.

    province may also be a district name (e.g. "Kadıköy") - it is
    auto-detected and the result is narrowed to that district's
    stations, not the whole province.
    """
    return await service.list_stations(provider, province)


@mcp.tool
async def get_air_quality(province: str, district: str | None = None) -> dict:
    """Return a province AQI summary and per-station breakdown."""
    return await service.get_air_quality(provider, province, district)


@mcp.tool
async def get_nearest_air_quality(
    location: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
    limit: int = 3,
    max_distance_km: float = 75.0,
) -> dict:
    """Konuma en yakın resmi UHKİA istasyonlarının güncel verisini döndürür.

    location kullanımı metni OpenStreetMap/Nominatim'e gönderir (API
    anahtarı gerekmez). latitude/longitude kullanımı geocoder çağırmaz.
    referans_hki tahmin veya ortalama değil, en yakın geçerli istasyonun
    resmi HKİ'sidir.

    Bu tool bir ADRES/NOKTA (sokak, mahalle, koordinat) için en yakın
    istasyonu bulmaya yöneliktir. Sadece bir ilin veya ilçenin genel hava
    kalitesi soruluyorsa (adres/nokta değil), bunun yerine get_air_quality
    veya list_stations kullanılmalıdır — location parametresi il/ilçe
    isimlerindeki yazım hatalarını get_air_quality kadar iyi tolere etmez
    (Nominatim serbest metin araması yapar, il/ilçe fuzzy düzeltmesi
    yapmaz).

    limit parametresi 'yakin_istasyonlar' (geçerli HKİ verisi olan) ve
    'verisi_olmayan_yakin_istasyonlar' (veri gelmeyen) listelerine AYRI
    AYRI uygulanır; toplam yanıt en fazla 2×limit istasyon içerebilir.
    """
    return await service.get_nearest_air_quality(
        provider,
        geocoder,
        location,
        latitude,
        longitude,
        limit,
        max_distance_km,
    )


@mcp.tool
async def get_station_detail(station: str) -> dict:
    """Return the current full reading for a station ID or name."""
    return await service.get_station_detail(provider, station)


@mcp.tool
async def get_historical_data(
    province: str, days: int, district: str | None = None
) -> dict:
    """Geçmiş hava kalitesini istasyon bazında günlük HKİ özetiyle döndürür.

    days 1 ile 90 arasında olmalıdır. İlçe verilmezse ilin güncel en
    kötü istasyonu, ilçe verilirse eşleşen tüm istasyonlar kullanılır.

    days büyüdükçe yanıt süresi de uzar: geçmiş veri sabit ~3 günlük
    sayfalar halinde, sayfalar arası 1 saniye beklemeyle çekiliyor
    (örn. days=87 tek istasyon için ~36 saniye sürer). İlçede birden
    fazla istasyon varsa süreler istasyon başına toplanır. Hızlı bir
    yanıt gerekiyorsa küçük bir days değeri (örn. 7-14) tercih edin.
    """
    return await service.get_historical_data(
        provider, province, days, district
    )


@mcp.tool
async def get_trend_summary(
    province: str, days: int = 3, district: str | None = None
) -> dict:
    """Hava kalitesinin 3 veya 6 günlük kural tabanlı trendini döndürür.

    İlçe verilmezse ilin güncel en kötü istasyonu, ilçe verilirse eşleşen
    tüm istasyonlar ayrı ayrı değerlendirilir. yon alanı 'iyilesiyor',
    'kotulesiyor', 'stabil' değerlerinin yanı sıra 'yetersiz_veri' de
    olabilir — bu, pencerenin ilk veya ikinci yarısında hiç ölçüm
    bulunmadığı, dolayısıyla bir trend hesaplanamadığı anlamına gelir.
    """
    return await service.get_trend_summary(provider, province, days, district)


@mcp.tool
async def compare_cities(province1: str, province2: str) -> dict:
    """İki ilin hava kalitesi özetini yan yana karşılaştırır.

    province1/province2 bir ilçe adı da olabilir (ör. "Kadıköy") - bu
    durumda otomatik olarak bağlı olduğu ile yönlendirilir ve
    karşılaştırma o ilçenin istasyonlarıyla sınırlı yapılır (sonuçtaki
    "ilce" alanı ve "not" ile belirtilir), tüm il yerine.

    İstasyon bazlı döküm için get_air_quality kullanılmalıdır.
    """
    return await service.compare_cities(provider, province1, province2)


@mcp.tool
async def get_ranking(mode: str, limit: int) -> dict:
    """İllerin hızlı sıralaması (il başına tek temsili değer).

    Genel en kirli/en temiz iller soruları için bunu kullanın.
    """
    return await service.get_ranking(provider, mode, limit)


@mcp.tool
async def get_detailed_ranking(mode: str, limit: int) -> dict:
    """İstasyon seviyesinde, il bazında özetlenmemiş derin sıralama."""
    return await service.get_detailed_ranking(
        provider, _detailed_ranking_cache, mode, limit
    )


@mcp.tool
async def get_health_advisory(province: str) -> dict:
    """İlin temsili hava kalitesine göre kural tabanlı tavsiye döndürür.

    province bir ilçe adı da olabilir (ör. "Kadıköy") - bu durumda
    otomatik olarak bağlı olduğu ile yönlendirilir ve tavsiye o ilçenin
    istasyonlarıyla sınırlı hesaplanır (sonuçtaki "ilce" alanıyla
    belirtilir), tüm il yerine.
    """
    return await service.get_health_advisory(provider, province)


@mcp.tool
async def check_alert(
    province: str,
    threshold: float,
    pollutant: str = "HKI",
    district: str | None = None,
) -> dict:
    """Verilen hava kalitesi eşiğinin aşılıp aşılmadığını kontrol eder."""
    return await service.check_alert(
        provider, province, threshold, pollutant, district
    )


def main() -> None:
    mcp.run(show_banner=False)


if __name__ == "__main__":
    main()
