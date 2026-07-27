from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator

ISTANBUL_TZ = ZoneInfo("Europe/Istanbul")
POLLUTANT_UNIT = "µg/m³"


class Coordinate(BaseModel):
    lon: float
    lat: float

    @classmethod
    def from_wkt(cls, wkt: str) -> "Coordinate":
        inner = wkt.strip().removeprefix("POINT").strip().strip("()")
        lon_str, lat_str = inner.split()
        return cls(lon=float(lon_str), lat=float(lat_str))


class StationReading(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    station_id: str = Field(alias="StationId")
    measured_at: datetime = Field(alias="Date")
    no2: float | None = Field(alias="NO2")
    so2: float | None = Field(alias="SO2")
    co: float | None = Field(alias="CO")
    o3: float | None = Field(alias="O3")
    pm10: float | None = Field(alias="PM10")
    pm25: float | None = Field(alias="PM25")
    co_1h: float | None = Field(alias="CO_1")
    o3_1h: float | None = Field(alias="O3_1")
    pm10_1h: float | None = Field(alias="PM10_1")
    aqi_index: float | None = Field(alias="AQIIndex")
    aqi_status: int | None = Field(alias="AQIStatus")
    dominant_pollutant: str | None = Field(alias="ContaminantParameter")
    aqi_type: int | None = Field(alias="AQIType")

    @field_validator("measured_at", mode="before")
    @classmethod
    def _attach_istanbul_tz(cls, value: str | datetime) -> datetime:
        if isinstance(value, str):
            value = datetime.fromisoformat(value)
        return value.replace(tzinfo=ISTANBUL_TZ)


class Station(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str = Field(alias="Name")
    city: str = Field(alias="City_Title")
    city_id: str = Field(alias="CityId")
    district: str = Field(alias="Town_Title")
    last_data_date: datetime = Field(alias="LastDataDate")
    coordinate: Coordinate
    parameters: list[str]
    current: StationReading = Field(alias="Values")

    @field_validator("last_data_date", mode="before")
    @classmethod
    def _attach_istanbul_tz(cls, value: str | datetime) -> datetime:
        if isinstance(value, str):
            value = datetime.fromisoformat(value)
        return value.replace(tzinfo=ISTANBUL_TZ)

    @classmethod
    def from_raw(cls, raw: dict) -> "Station":
        return cls.model_validate(
            {
                **raw,
                "coordinate": Coordinate.from_wkt(raw["Location"]),
                "parameters": [
                    p.strip()
                    for p in raw["Config"]["parameters"].split(",")
                    if p.strip()
                ],
            }
        )
