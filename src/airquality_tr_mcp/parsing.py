from __future__ import annotations

import json

from .models import Station, StationReading


def parse_bulk_stations(raw_json: str) -> list[Station]:
    payload = json.loads(raw_json)
    return [Station.from_raw(item) for item in payload["objects"]]


def parse_historical_readings(raw_json: str) -> list[StationReading]:
    payload = json.loads(raw_json)
    return [
        StationReading.model_validate(item)
        for item in payload["objects"]["AQIValues"]
    ]
