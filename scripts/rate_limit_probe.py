"""Faz 1.6 — rate-limit / IP-UA engelleme keşif scripti.

Bu script prod kod değildir; sim.csb.gov.tr'ye kademeli artan hızda istek
atarak rate-limit, IP/User-Agent bazlı engelleme veya CAPTCHA tetiklenip
tetiklenmediğini gözlemler. İlk engelleme belirtisinde hemen durur.
Bulgular docs/api-notes.md'ye elle işlenir.
"""

import asyncio
import itertools
import sys
import time

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
STATION_ID = "251a7ea1-e3ff-4b2f-a4d2-1231118f83fa"

# (istek/sn, kademe başına toplam istek sayısı)
RAMP_STAGES = [(1, 20), (5, 20), (10, 20)]


async def _call_next_endpoint(
    client: httpx.AsyncClient, endpoint: str
) -> httpx.Response:
    if endpoint == "bulk":
        return await client.post(
            "/Services/GetAirQualityStations?type=0",
            data={"Location": "", "Date": ""},
            headers=COMMON_HEADERS,
        )
    if endpoint == "detail_data":
        return await client.post(
            "/Services/GetDetailData",
            data={"stationId": STATION_ID},
            headers=COMMON_HEADERS,
        )
    # endpoint == "station_detail"
    return await client.post(
        "/Services/GetAirQualityStationDetail",
        data={"stationId": STATION_ID},
        headers=COMMON_HEADERS,
    )


def _looks_blocked(resp: httpx.Response) -> str | None:
    if resp.status_code == 429:
        return "HTTP 429 (Too Many Requests)"
    if resp.status_code == 403:
        return "HTTP 403 (Forbidden)"
    if resp.status_code in (500, 502, 503, 504):
        return (
            f"HTTP {resp.status_code} "
            "(WAF/yük dengeleyici bu kodlarla da engelleyebilir)"
        )
    content_type = resp.headers.get("content-type", "")
    if "json" not in content_type and resp.status_code == 200:
        return (
            f"beklenmeyen content-type: {content_type!r} "
            "(CAPTCHA/HTML sayfası olabilir)"
        )
    if resp.headers.get("retry-after"):
        return f"Retry-After header görüldü: {resp.headers['retry-after']}"
    return None


async def run_ramp_stage(
    client: httpx.AsyncClient,
    requests_per_sec: int,
    total_requests: int,
    cycle: itertools.cycle,
) -> tuple[int, str | None]:
    """Her 1 saniyelik pencerede `requests_per_sec` isteği asyncio.gather ile
    eşzamanlı ateşleyerek hedeflenen gerçek yükü üretir (sıralı/sequential
    modelin ağ gecikmesi yüzünden hedef RPS'e ulaşamadığı ilk koşuda görüldü).
    """
    print(
        f"\n=== Kademe: hedef {requests_per_sec} istek/sn, "
        f"toplam {total_requests} istek ==="
    )
    num_batches = total_requests // requests_per_sec
    completed = 0
    stage_start = time.monotonic()
    for batch_idx in range(num_batches):
        batch_start = time.monotonic()
        endpoint_names = [next(cycle) for _ in range(requests_per_sec)]
        results = await asyncio.gather(
            *(_call_next_endpoint(client, name) for name in endpoint_names),
            return_exceptions=True,
        )
        for endpoint_name, result in zip(endpoint_names, results):
            completed += 1
            if isinstance(result, httpx.RequestError):
                print(
                    f"  [batch {batch_idx + 1}] ENGELLENDİ (ağ seviyesi): "
                    f"{endpoint_name} isteğinde "
                    f"{type(result).__name__}: {result}"
                )
                return (
                    completed,
                    f"ağ seviyesi hata ({type(result).__name__}) — "
                    "muhtemel IP engeli (bağlantı reddedildi/zaman "
                    "aşımına uğradı)",
                )
            if isinstance(result, BaseException):
                raise result
            block_reason = _looks_blocked(result)
            if block_reason:
                print(
                    f"  [batch {batch_idx + 1}] ENGELLENDİ "
                    f"({endpoint_name}): {block_reason}"
                )
                return completed, f"{block_reason} (endpoint: {endpoint_name})"
        elapsed = time.monotonic() - batch_start
        remaining = 1.0 - elapsed
        if remaining > 0 and batch_idx < num_batches - 1:
            await asyncio.sleep(remaining)
    stage_elapsed = time.monotonic() - stage_start
    actual_rps = completed / stage_elapsed
    print(
        f"  {completed}/{total_requests} istek tamamlandı, engelleme yok — "
        f"fiili hız: {actual_rps:.1f} istek/sn "
        f"(hedef: {requests_per_sec})"
    )
    return completed, None


COOLDOWN_BETWEEN_STAGES_SEC = 5.0


async def main() -> None:
    endpoint_cycle = itertools.cycle(["bulk", "detail_data", "station_detail"])
    async with httpx.AsyncClient(
        base_url=BASE_URL, timeout=httpx.Timeout(10.0, connect=5.0)
    ) as client:
        for stage_index, (requests_per_sec, total_requests) in enumerate(
            RAMP_STAGES
        ):
            if stage_index > 0:
                print(
                    "\n... kademeler arası soğuma: "
                    f"{COOLDOWN_BETWEEN_STAGES_SEC}s bekleniyor ..."
                )
                await asyncio.sleep(COOLDOWN_BETWEEN_STAGES_SEC)
            completed, block_reason = await run_ramp_stage(
                client, requests_per_sec, total_requests, endpoint_cycle
            )
            if block_reason:
                print(
                    f"\n>>> DURDURULDU: hedef {requests_per_sec} istek/sn "
                    f"kademesinde, {completed}. istekte engellendi."
                )
                print(f">>> Sebep: {block_reason}")
                return
    print(
        "\n>>> Tüm kademeler (1, 5, 10 istek/sn) engelleme görülmeden "
        "tamamlandı."
    )


if __name__ == "__main__":
    asyncio.run(main())
