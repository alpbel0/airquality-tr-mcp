import asyncio

import httpx
import pytest

from airquality_tr_mcp.geocoding import (
    AmbiguousLocationError,
    CachedGeocoder,
    GeocodedPlace,
    GeocodingAuthError,
    GeocodingRateLimitError,
    GeocodingResponseError,
    GeocodingServiceError,
    GeocodingTimeoutError,
    LocationNotFoundError,
    MissingApiKeyError,
    PeliasGeocoder,
    choose_pelias_candidate,
    parse_pelias_candidates,
)


def _feature(label, match_type, lon, lat, confidence=0.9):
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": {
            "label": label,
            "match_type": match_type,
            "confidence": confidence,
        },
    }


def test_parser_preserves_geojson_lon_lat_order():
    candidates = parse_pelias_candidates(
        {
            "type": "FeatureCollection",
            "features": [
                _feature("Göbeklitepe, Şanlıurfa", "exact", 38.9224, 37.2232)
            ],
        }
    )

    assert candidates[0].longitude == 38.9224
    assert candidates[0].latitude == 37.2232


def test_parser_ignores_optional_geojson_altitude():
    feature = _feature(
        "Göbeklitepe, Şanlıurfa", "exact", 38.9224, 37.2232
    )
    feature["geometry"]["coordinates"].append(760.0)

    candidates = parse_pelias_candidates({"features": [feature]})

    assert candidates[0].longitude == 38.9224
    assert candidates[0].latitude == 37.2232


def test_parser_rejects_boolean_coordinates():
    feature = _feature("Broken", "exact", True, 37.2232)

    with pytest.raises(GeocodingResponseError):
        parse_pelias_candidates({"features": [feature]})


def test_lone_exact_candidate_is_accepted():
    candidates = parse_pelias_candidates(
        {
            "features": [
                _feature("Göbeklitepe, Şanlıurfa", "exact", 38.9224, 37.2232),
                _feature("Şanlıurfa, Türkiye", "fallback", 38.79, 37.16),
            ]
        }
    )

    selected = choose_pelias_candidate("Göbeklitepe", candidates)

    assert selected.label == "Göbeklitepe, Şanlıurfa"


def test_two_distinct_non_fallback_candidates_are_ambiguous():
    candidates = parse_pelias_candidates(
        {
            "features": [
                _feature("Atatürk Mah., Ankara", "exact", 32.8, 39.9),
                _feature("Atatürk Mah., İzmir", "exact", 27.2, 38.4),
            ]
        }
    )

    with pytest.raises(AmbiguousLocationError) as caught:
        choose_pelias_candidate("Atatürk Mahallesi", candidates)

    assert len(caught.value.candidates) == 2


def test_duplicate_candidate_is_not_treated_as_ambiguity():
    duplicate = _feature("Ankara, Türkiye", "exact", 32.85, 39.93)
    candidates = parse_pelias_candidates({"features": [duplicate, duplicate]})

    selected = choose_pelias_candidate("Ankara", candidates)

    assert selected.label == "Ankara, Türkiye"


def test_fallback_only_result_is_not_accepted():
    candidates = parse_pelias_candidates(
        {
            "features": [
                _feature("Şanlıurfa, Türkiye", "fallback", 38.79, 37.16)
            ]
        }
    )

    with pytest.raises(LocationNotFoundError):
        choose_pelias_candidate("Olmayan Yer", candidates)


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"features": "not-a-list"},
        {
            "features": [
                {
                    "geometry": {"coordinates": [32.85]},
                    "properties": {"label": "Broken", "match_type": "exact"},
                }
            ]
        },
    ],
)
def test_malformed_pelias_response_is_structured_failure(payload):
    with pytest.raises(GeocodingResponseError):
        parse_pelias_candidates(payload)


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_client_sends_fixed_search_parameters_and_auth_header():
    captured = {}

    def handler(request):
        captured["request"] = request
        return httpx.Response(
            200,
            json={
                "features": [
                    _feature("Göbeklitepe, Şanlıurfa", "exact", 38.9224, 37.2232)
                ]
            },
        )

    geocoder = PeliasGeocoder(
        api_key="secret-test-key", client=_client(handler)
    )
    result = await geocoder.geocode("Göbeklitepe")

    assert result.label == "Göbeklitepe, Şanlıurfa"
    request = captured["request"]
    assert str(request.url).startswith(
        "https://api.heigit.org/pelias/v1/search?"
    )
    assert request.url.params["text"] == "Göbeklitepe"
    assert request.url.params["boundary.country"] == "TUR"
    assert request.url.params["lang"] == "tr"
    assert request.url.params["size"] == "3"
    assert request.headers["Authorization"] == "secret-test-key"
    assert "secret-test-key" not in str(request.url)


async def test_missing_key_fails_before_http_call():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    geocoder = PeliasGeocoder(api_key="", client=_client(handler))

    with pytest.raises(MissingApiKeyError):
        await geocoder.geocode("Ankara")

    assert calls == 0


@pytest.mark.parametrize("status", [401, 403])
async def test_auth_statuses_map_to_auth_error(status):
    geocoder = PeliasGeocoder(
        api_key="test",
        client=_client(lambda request: httpx.Response(status)),
    )
    with pytest.raises(GeocodingAuthError):
        await geocoder.geocode("Ankara")


async def test_429_maps_to_rate_limit_error():
    geocoder = PeliasGeocoder(
        api_key="test",
        client=_client(lambda request: httpx.Response(429)),
    )
    with pytest.raises(GeocodingRateLimitError):
        await geocoder.geocode("Ankara")


async def test_timeout_does_not_expose_key():
    def handler(request):
        raise httpx.ReadTimeout("timeout", request=request)

    geocoder = PeliasGeocoder(
        api_key="do-not-leak", client=_client(handler)
    )
    with pytest.raises(GeocodingTimeoutError) as caught:
        await geocoder.geocode("Ankara")

    assert "do-not-leak" not in str(caught.value)


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
        match_type="exact",
        confidence=0.9,
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
        MissingApiKeyError("missing"),
        GeocodingAuthError("auth"),
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
