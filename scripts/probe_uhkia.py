"""Faz 1.3 — manuel keşif scripti.

sim.csb.gov.tr'ye tarayıcısız (headless) erişimi doğrular.

Bulgular docs/api-notes.md'de belgelenmiştir. Bu script prod kod değildir —
sadece "Python ile, browser olmadan bu API'ye gerçekten erişebiliyor muyuz"
sorusunu cevaplamak için yazıldı. Faz 2'de gerçek server kodu src/ altında,
bu script'ten bağımsız olarak yazılacak.
"""

import asyncio
import sys

import httpx

sys.stdout.reconfigure(encoding="utf-8")

BASE_URL = "https://sim.csb.gov.tr"

COMMON_HEADERS = {
    "Referer": f"{BASE_URL}/Services/AirQuality",
    "Origin": BASE_URL,
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}

# docs/api-notes.md'den: Ankara - Bahçelievler istasyonu
ANKARA_BAHCELIEVLER_ID = "251a7ea1-e3ff-4b2f-a4d2-1231118f83fa"
INVALID_STATION_ID = "00000000-0000-0000-0000-000000000000"


async def test_bulk_stations(client: httpx.AsyncClient) -> None:
    print("\n=== GetAirQualityStations (bulk) ===")
    resp = await client.post(
        "/Services/GetAirQualityStations?type=0",
        data={"Location": "", "Date": ""},
        headers=COMMON_HEADERS,
    )
    print(
        "status:",
        resp.status_code,
        "| content-type:",
        resp.headers.get("content-type"),
    )
    data = resp.json()
    stations = data["objects"]
    print(f"toplam istasyon: {len(stations)}")
    sample = stations[0]
    print(
        f"örnek istasyon: {sample['Name']} ({sample['City_Title']}), "
        f"AQI={sample['Values']['AQIIndex']}"
    )


async def test_station_detail(client: httpx.AsyncClient) -> None:
    print("\n=== GetDetailData (istasyon geçmişi) ===")
    resp = await client.post(
        "/Services/GetDetailData",
        data={"stationId": ANKARA_BAHCELIEVLER_ID},
        headers=COMMON_HEADERS,
    )
    print(
        "status:",
        resp.status_code,
        "| content-type:",
        resp.headers.get("content-type"),
    )
    data = resp.json()
    values = data["objects"]["AQIValues"]
    print(
        f"kayıt sayısı: {len(values)} (ilk: {values[0]['Date']}, "
        f"son: {values[-1]['Date']})"
    )
    missing_n = sum(1 for v in values if v["PM25_N"] == -32768.0)
    print(f"PM25_N sentinel (-32768.0) sayısı: {missing_n}/{len(values)}")


async def test_category_and_params(client: httpx.AsyncClient) -> None:
    print("\n=== GetAirQualityStatus (resmi kategori tablosu) ===")
    resp = await client.post(
        "/Services/GetAirQualityStatus?type=0", headers=COMMON_HEADERS
    )
    categories = resp.json()["objects"]
    for c in categories:
        print(f"  {c['Value']:>10} ({c['Min']}-{c['Max']}): {c['Color']}")

    print("\n=== GetAirQualityParams (breakpoint tablosu) ===")
    resp = await client.post(
        "/Services/GetAirQualityParams?type=0", headers=COMMON_HEADERS
    )
    params = resp.json()["objects"]
    for pollutant, breakpoints in params.items():
        print(
            f"  {pollutant}: {len(breakpoints)} breakpoint"
            + (" (BOŞ)" if not breakpoints else "")
        )


async def test_error_scenarios(client: httpx.AsyncClient) -> None:
    print("\n=== Hata senaryosu: geçersiz stationId ===")
    resp = await client.post(
        "/Services/GetDetailData",
        data={"stationId": INVALID_STATION_ID},
        headers=COMMON_HEADERS,
    )
    print(
        "status:",
        resp.status_code,
        "| content-type:",
        resp.headers.get("content-type"),
    )
    if resp.status_code in (301, 302, 303, 307, 308):
        print("redirect location:", resp.headers.get("location"))
        follow = await client.get(
            resp.headers["location"], headers=COMMON_HEADERS
        )
        print(
            "  → redirect hedefi status:",
            follow.status_code,
            "| content-type:",
            follow.headers.get("content-type"),
        )
    is_json = "json" in resp.headers.get("content-type", "")
    print(
        "JSON mu?",
        is_json,
        "-> server kodumuz bunu 'beklenmeyen upstream yaniti' olarak "
        "sarmalamali",
    )

    print("\n=== Hata senaryosu: zaman aşımı (kısa timeout ile) ===")
    try:
        async with httpx.AsyncClient(
            base_url=BASE_URL, timeout=httpx.Timeout(0.001, connect=5.0)
        ) as short_client:
            await short_client.post(
                "/Services/GetAirQualityStations?type=0",
                data={"Location": "", "Date": ""},
                headers=COMMON_HEADERS,
            )
        print("beklenmedik: timeout oluşmadı")
    except httpx.ReadTimeout:
        print(
            "beklenen ReadTimeout hatası doğru şekilde fırlatıldı "
            "(httpx.Timeout mekanizması çalışıyor)"
        )


async def main() -> None:
    async with httpx.AsyncClient(
        base_url=BASE_URL, timeout=httpx.Timeout(10.0, connect=5.0)
    ) as client:
        await test_bulk_stations(client)
        await test_station_detail(client)
        await test_category_and_params(client)
        await test_error_scenarios(client)
    print(
        "\nTüm istekler tarayıcı açılmadan, saf Python/httpx ile tamamlandı."
    )


if __name__ == "__main__":
    asyncio.run(main())
