from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from . import server, service

HATA_STATUS_CODES: dict[str, int] = {
    "upstream_hatasi": 502,
    "ilce_eslesmedi": 404,
    "istasyon_id_bulunamadi": 404,
    "eksik_veya_bozuk_id": 400,
    "belirsiz_eslesme": 409,
    "eslesme_bulunamadi": 404,
    "gecersiz_days": 400,
    "gecersiz_mode": 400,
    "gecersiz_limit": 400,
    "gecersiz_kirletici": 400,
    "gecersiz_parametre": 400,
    "belirsiz_konum": 409,
    "konum_bulunamadi": 404,
    "geocoding_kota_asildi": 429,
    "geocoding_zaman_asimi": 504,
    "geocoding_servis_hatasi": 502,
}


def _respond(payload: dict) -> JSONResponse:
    hata = payload.get("hata")
    status_code = HATA_STATUS_CODES.get(hata, 400) if hata else 200
    return JSONResponse(content=payload, status_code=status_code)


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    yield
    for resource in (server.provider, server.geocoder):
        close = getattr(resource, "aclose", None)
        if close is not None:
            try:
                await close()
            except Exception:
                logging.getLogger(__name__).exception(
                    "Resource cleanup failed"
                )


app = FastAPI(
    title="airquality-tr-api",
    description=(
        "Türkiye'nin resmi UHKİA hava kalitesi verisi için REST API "
        "(airquality-tr-mcp MCP sunucusuyla aynı iş mantığını paylaşır)."
    ),
    version="1.0.0",
    lifespan=_lifespan,
)


@app.get("/ping")
async def ping() -> dict:
    return {"mesaj": "pong"}


@app.get("/stations")
async def list_stations(province: str | None = None) -> JSONResponse:
    payload = await service.list_stations(server.provider, province)
    return _respond(payload)


@app.get("/air-quality")
async def get_air_quality(
    province: str, district: str | None = None
) -> JSONResponse:
    payload = await service.get_air_quality(
        server.provider, province, district
    )
    return _respond(payload)


@app.get("/nearest-air-quality")
async def get_nearest_air_quality(
    location: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
    limit: int = 3,
    max_distance_km: float = 75.0,
) -> JSONResponse:
    payload = await service.get_nearest_air_quality(
        server.provider,
        server.geocoder,
        location,
        latitude,
        longitude,
        limit,
        max_distance_km,
    )
    return _respond(payload)


@app.get("/station")
async def get_station_detail(station: str) -> JSONResponse:
    payload = await service.get_station_detail(server.provider, station)
    return _respond(payload)


@app.get("/historical-data")
async def get_historical_data(
    province: str, days: int, district: str | None = None
) -> JSONResponse:
    payload = await service.get_historical_data(
        server.provider, province, days, district
    )
    return _respond(payload)


@app.get("/trend-summary")
async def get_trend_summary(
    province: str, days: int = 3, district: str | None = None
) -> JSONResponse:
    payload = await service.get_trend_summary(
        server.provider, province, days, district
    )
    return _respond(payload)


@app.get("/compare-cities")
async def compare_cities(province1: str, province2: str) -> JSONResponse:
    payload = await service.compare_cities(
        server.provider, province1, province2
    )
    return _respond(payload)


@app.get("/ranking")
async def get_ranking(mode: str, limit: int) -> JSONResponse:
    payload = await service.get_ranking(server.provider, mode, limit)
    return _respond(payload)


@app.get("/detailed-ranking")
async def get_detailed_ranking(mode: str, limit: int) -> JSONResponse:
    payload = await service.get_detailed_ranking(
        server.provider, server._detailed_ranking_cache, mode, limit
    )
    return _respond(payload)


@app.get("/health-advisory")
async def get_health_advisory(province: str) -> JSONResponse:
    payload = await service.get_health_advisory(server.provider, province)
    return _respond(payload)


@app.get("/alert")
async def check_alert(
    province: str,
    threshold: float,
    pollutant: str = "HKI",
    district: str | None = None,
) -> JSONResponse:
    payload = await service.check_alert(
        server.provider, province, threshold, pollutant, district
    )
    return _respond(payload)


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
