import asyncio

import httpx
import pytest

from airquality_tr_mcp.geocoding import (
    AmbiguousLocationError,
    CachedGeocoder,
    GeocodedPlace,
    GeocodingRateLimitError,
    GeocodingResponseError,
    GeocodingServiceError,
    GeocodingTimeoutError,
    LocationNotFoundError,
    NominatimGeocoder,
    choose_nominatim_candidate,
    parse_nominatim_candidates,
)


def _item(display_name, lat, lon, importance=0.5):
    return {
        "display_name": display_name,
        "lat": str(lat),
        "lon": str(lon),
        "importance": importance,
    }


def test_parser_converts_string_lat_lon_to_float():
    candidates = parse_nominatim_candidates(
        [_item("Göbeklitepe, Şanlıurfa", 37.2232, 38.9224)]
    )

    assert candidates[0].latitude == 37.2232
    assert candidates[0].longitude == 38.9224


def test_parser_keeps_importance_when_present():
    candidates = parse_nominatim_candidates(
        [_item("Ankara, Türkiye", 39.93, 32.85, importance=0.6)]
    )

    assert candidates[0].importance == 0.6


def test_parser_defaults_importance_to_none_when_missing():
    item = _item("Ankara, Türkiye", 39.93, 32.85)
    del item["importance"]

    candidates = parse_nominatim_candidates([item])

    assert candidates[0].importance is None


def test_parser_rejects_unparseable_coordinates():
    item = _item("Broken", 0, 0)
    item["lat"] = "not-a-number"

    with pytest.raises(GeocodingResponseError):
        parse_nominatim_candidates([item])


def test_single_candidate_is_accepted():
    candidates = parse_nominatim_candidates(
        [_item("Ankara, Türkiye", 39.93, 32.85, importance=0.6)]
    )

    selected = choose_nominatim_candidate("Ankara", candidates)

    assert selected.label == "Ankara, Türkiye"


def test_two_distinct_candidates_are_ambiguous():
    candidates = parse_nominatim_candidates(
        [
            _item("Kadıköy, Türkiye", 40.98, 29.06),
            _item("Kadıköy, Türkiye", 41.11, 29.90),
        ]
    )

    with pytest.raises(AmbiguousLocationError) as caught:
        choose_nominatim_candidate("Kadıköy", candidates)

    assert len(caught.value.candidates) == 2


def test_duplicate_candidate_is_not_treated_as_ambiguity():
    duplicate = _item("Ankara, Türkiye", 39.93, 32.85)
    candidates = parse_nominatim_candidates([duplicate, duplicate])

    selected = choose_nominatim_candidate("Ankara", candidates)

    assert selected.label == "Ankara, Türkiye"


def test_empty_candidate_list_is_not_accepted():
    with pytest.raises(LocationNotFoundError):
        choose_nominatim_candidate(
            "Olmayan Yer", parse_nominatim_candidates([])
        )


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        "not-a-list",
        [{"lat": "39.9", "lon": "32.8"}],
        [{"display_name": "Broken", "lat": "not-a-number", "lon": "32.8"}],
    ],
)
def test_malformed_nominatim_response_is_structured_failure(payload):
    with pytest.raises(GeocodingResponseError):
        parse_nominatim_candidates(payload)


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_client_sends_fixed_search_parameters_and_user_agent():
    captured = {}

    def handler(request):
        captured["request"] = request
        return httpx.Response(
            200,
            json=[_item("Göbeklitepe, Şanlıurfa", 37.2232, 38.9224)],
        )

    geocoder = NominatimGeocoder(client=_client(handler))
    result = await geocoder.geocode("Göbeklitepe")

    assert result.label == "Göbeklitepe, Şanlıurfa"
    request = captured["request"]
    assert str(request.url).startswith(
        "https://nominatim.openstreetmap.org/search?"
    )
    assert request.url.params["q"] == "Göbeklitepe"
    assert request.url.params["countrycodes"] == "tr"
    assert request.url.params["format"] == "jsonv2"
    assert request.url.params["limit"] == "3"
    assert "airquality-tr-mcp" in request.headers["User-Agent"]


async def test_429_maps_to_rate_limit_error():
    geocoder = NominatimGeocoder(
        client=_client(lambda request: httpx.Response(429)),
    )
    with pytest.raises(GeocodingRateLimitError):
        await geocoder.geocode("Ankara")


async def test_unexpected_status_maps_to_service_error():
    geocoder = NominatimGeocoder(
        client=_client(lambda request: httpx.Response(500)),
    )
    with pytest.raises(GeocodingServiceError):
        await geocoder.geocode("Ankara")


async def test_timeout_maps_to_timeout_error():
    def handler(request):
        raise httpx.ReadTimeout("timeout", request=request)

    geocoder = NominatimGeocoder(client=_client(handler))
    with pytest.raises(GeocodingTimeoutError):
        await geocoder.geocode("Ankara")


async def test_client_throttles_to_one_request_per_second():
    calls = []

    def handler(request):
        calls.append(asyncio.get_event_loop().time())
        return httpx.Response(
            200, json=[_item("Ankara, Türkiye", 39.93, 32.85)]
        )

    geocoder = NominatimGeocoder(client=_client(handler))
    await geocoder.geocode("Ankara")
    await geocoder.geocode("Ankara")

    assert calls[1] - calls[0] >= 1.0


class _Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class _FakeGeocoder:
    def __init__(self, outcomes, delay=0.0):
        self.outcomes = list(outcomes)
        self.delay = delay
        self.calls = 0

    async def geocode(self, query):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        outcome = self.outcomes[min(self.calls - 1, len(self.outcomes) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _place(label="Ankara"):
    return GeocodedPlace(
        label=label,
        latitude=39.93,
        longitude=32.85,
        importance=0.6,
    )


async def test_success_is_cached_for_24_hours():
    clock = _Clock()
    inner = _FakeGeocoder([_place("first"), _place("second")])
    cached = CachedGeocoder(inner, clock=clock)

    assert (await cached.geocode(" Ankara ")).label == "first"
    clock.now += 86399
    assert (await cached.geocode("ankara")).label == "first"
    assert inner.calls == 1


async def test_no_result_is_negatively_cached_for_10_minutes():
    clock = _Clock()
    inner = _FakeGeocoder(
        [LocationNotFoundError("yok"), _place("later")]
    )
    cached = CachedGeocoder(inner, clock=clock)

    with pytest.raises(LocationNotFoundError):
        await cached.geocode("Yok")
    clock.now += 599
    with pytest.raises(LocationNotFoundError):
        await cached.geocode("yok")

    assert inner.calls == 1


@pytest.mark.parametrize(
    "error",
    [
        GeocodingRateLimitError("quota"),
        GeocodingTimeoutError("timeout"),
        GeocodingServiceError("service"),
        AmbiguousLocationError("ambiguous", (_place("a"), _place("b"))),
    ],
)
async def test_transient_and_ambiguous_failures_are_not_cached(error):
    inner = _FakeGeocoder([error, _place()])
    cached = CachedGeocoder(inner)

    with pytest.raises(type(error)):
        await cached.geocode("Ankara")
    assert (await cached.geocode("Ankara")).label == "Ankara"
    assert inner.calls == 2


async def test_concurrent_duplicates_share_one_inflight_request():
    inner = _FakeGeocoder([_place()], delay=0.05)
    cached = CachedGeocoder(inner)

    results = await asyncio.gather(
        cached.geocode("Ankara"),
        cached.geocode("ankara"),
        cached.geocode(" ANKARA "),
    )

    assert [result.label for result in results] == [
        "Ankara", "Ankara", "Ankara"
    ]
    assert inner.calls == 1


async def test_failure_removes_inflight_entry_for_retry():
    inner = _FakeGeocoder(
        [GeocodingTimeoutError("timeout"), _place()]
    )
    cached = CachedGeocoder(inner)

    with pytest.raises(GeocodingTimeoutError):
        await cached.geocode("Ankara")
    assert (await cached.geocode("Ankara")).label == "Ankara"


async def test_cache_evicts_least_recently_used_entry():
    clock = _Clock()
    inner = _FakeGeocoder([_place("a"), _place("b"), _place("c"), _place("a2")])
    cached = CachedGeocoder(inner, max_entries=2, clock=clock)

    await cached.geocode("a")
    clock.now += 1
    await cached.geocode("b")
    await cached.geocode("a")
    clock.now += 1
    await cached.geocode("c")
    await cached.geocode("b")

    assert inner.calls == 4


async def test_refreshed_negative_entry_moves_to_lru_end():
    clock = _Clock()
    inner = _FakeGeocoder(
        [
            LocationNotFoundError("a"),
            LocationNotFoundError("b"),
            LocationNotFoundError("a"),
            LocationNotFoundError("c"),
            LocationNotFoundError("b"),
        ]
    )
    cached = CachedGeocoder(inner, max_entries=2, clock=clock)

    with pytest.raises(LocationNotFoundError):
        await cached.geocode("a")
    clock.now += 1
    with pytest.raises(LocationNotFoundError):
        await cached.geocode("b")
    clock.now = 600
    with pytest.raises(LocationNotFoundError):
        await cached.geocode("a")
    with pytest.raises(LocationNotFoundError):
        await cached.geocode("c")
    with pytest.raises(LocationNotFoundError):
        await cached.geocode("b")

    assert inner.calls == 5
