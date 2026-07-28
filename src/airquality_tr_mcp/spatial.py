from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from .models import Station

EARTH_RADIUS_KM = 6371.0088
REFERENCE_DISTANCE_LIMIT_KM = 75.0


class InvalidNearestInputError(ValueError):
    def __init__(self, field: str, value: object, message: str) -> None:
        super().__init__(message)
        self.field = field
        self.value = value


@dataclass(frozen=True)
class StationDistance:
    station: Station
    distance_km: float


@dataclass(frozen=True)
class NearestStationsResult:
    reference: StationDistance | None
    rated: tuple[StationDistance, ...]
    unrated_closer: tuple[StationDistance, ...]
    unrated_count: int
    nearest_outside: StationDistance | None


def validate_nearest_input(
    *,
    location: str | None,
    latitude: float | None,
    longitude: float | None,
    limit: int,
    max_distance_km: float,
) -> Literal["text", "coordinates"]:
    has_text = location is not None
    has_any_coordinate = latitude is not None or longitude is not None
    has_coordinates = latitude is not None and longitude is not None

    if has_text == has_any_coordinate:
        raise InvalidNearestInputError(
            "input",
            {
                "location": location,
                "latitude": latitude,
                "longitude": longitude,
            },
            (
                "location veya latitude/longitude çiftinden yalnızca biri "
                "verilmelidir."
            ),
        )
    if has_any_coordinate and not has_coordinates:
        raise InvalidNearestInputError(
            "coordinates",
            {"latitude": latitude, "longitude": longitude},
            "latitude ve longitude birlikte verilmelidir.",
        )
    if has_text and not location.strip():
        raise InvalidNearestInputError(
            "location", location, "location boş olamaz."
        )
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= 5
    ):
        raise InvalidNearestInputError(
            "limit", limit, "limit 1 ile 5 arasında bir tam sayı olmalıdır."
        )
    if (
        isinstance(max_distance_km, bool)
        or not math.isfinite(max_distance_km)
        or max_distance_km <= 0
    ):
        raise InvalidNearestInputError(
            "max_distance_km",
            max_distance_km,
            "max_distance_km sıfırdan büyük ve sonlu olmalıdır.",
        )
    if has_coordinates:
        if (
            isinstance(latitude, bool)
            or not math.isfinite(latitude)
            or not -90 <= latitude <= 90
        ):
            raise InvalidNearestInputError(
                "latitude", latitude, "latitude -90 ile 90 arasında olmalıdır."
            )
        if (
            isinstance(longitude, bool)
            or not math.isfinite(longitude)
            or not -180 <= longitude <= 180
        ):
            raise InvalidNearestInputError(
                "longitude",
                longitude,
                "longitude -180 ile 180 arasında olmalıdır.",
            )
        return "coordinates"
    return "text"


def haversine_distance_km(
    lat1: float, lon1: float, lat2: float, lon2: float
) -> float:
    lat1_rad, lon1_rad, lat2_rad, lon2_rad = map(
        math.radians, (lat1, lon1, lat2, lon2)
    )
    delta_lat = lat2_rad - lat1_rad
    delta_lon = lon2_rad - lon1_rad
    value = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1_rad)
        * math.cos(lat2_rad)
        * math.sin(delta_lon / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(min(1.0, value)))


def select_nearest_stations(
    stations: list[Station],
    *,
    latitude: float,
    longitude: float,
    limit: int,
    max_distance_km: float,
) -> NearestStationsResult:
    ranked = sorted(
        (
            StationDistance(
                station=station,
                distance_km=haversine_distance_km(
                    latitude,
                    longitude,
                    station.coordinate.lat,
                    station.coordinate.lon,
                ),
            )
            for station in stations
        ),
        key=lambda item: (item.distance_km, item.station.id),
    )
    within_range = [
        item for item in ranked if item.distance_km <= max_distance_km
    ]
    reference_limit = min(max_distance_km, REFERENCE_DISTANCE_LIMIT_KM)
    reference = next(
        (
            item
            for item in ranked
            if item.distance_km <= reference_limit
            and item.station.current.aqi_index is not None
        ),
        None,
    )
    rated = tuple(
        item
        for item in within_range
        if item.station.current.aqi_index is not None
    )[:limit]
    unrated = [
        item for item in within_range if item.station.current.aqi_index is None
    ]
    closer_cutoff = (
        reference.distance_km if reference is not None else math.inf
    )
    unrated_closer = tuple(
        item for item in unrated if item.distance_km < closer_cutoff
    )[:limit]
    nearest_outside = next(
        (item for item in ranked if item.distance_km > max_distance_km),
        None,
    )
    return NearestStationsResult(
        reference=reference,
        rated=rated,
        unrated_closer=unrated_closer,
        unrated_count=len(unrated),
        nearest_outside=nearest_outside,
    )
