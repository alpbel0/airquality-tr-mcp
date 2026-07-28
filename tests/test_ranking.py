import pytest

from airquality_tr_mcp.parsing import parse_bulk_stations
from airquality_tr_mcp.ranking import (
    InvalidLimitError,
    InvalidModeError,
    RankingCache,
    rank_provinces,
    rank_stations,
    validate_ranking_args,
)


def _load_all_stations(load_fixture_text):
    raw = load_fixture_text(
        "GetAirQualityStations_bulk_tum_ag.network-response"
    )
    return parse_bulk_stations(raw)


def test_validate_ranking_args_rejects_invalid_mode():
    with pytest.raises(InvalidModeError) as exc_info:
        validate_ranking_args("okay", 5)

    assert exc_info.value.mode == "okay"


def test_validate_ranking_args_rejects_non_positive_limit():
    with pytest.raises(InvalidLimitError) as exc_info:
        validate_ranking_args("best", 0)

    assert exc_info.value.limit == 0


def test_validate_ranking_args_accepts_valid_input():
    validate_ranking_args("worst", 10)


def test_rank_provinces_worst_mode_orders_descending(load_fixture_text):
    stations = _load_all_stations(load_fixture_text)
    ranked = rank_provinces(stations, "worst", 5)
    values = [
        rank.representative_station.current.aqi_index for rank in ranked
    ]

    assert values == sorted(values, reverse=True)
    assert len(ranked) == 5


def test_rank_provinces_best_mode_orders_ascending(load_fixture_text):
    stations = _load_all_stations(load_fixture_text)
    ranked = rank_provinces(stations, "best", 5)
    values = [
        rank.representative_station.current.aqi_index for rank in ranked
    ]

    assert values == sorted(values)


def test_rank_provinces_picks_worst_station_per_province(
    load_fixture_text,
):
    stations = _load_all_stations(load_fixture_text)
    ranked = rank_provinces(stations, "worst", 200)
    batman_rank = next(
        rank for rank in ranked if rank.province == "Batman"
    )
    batman_stations = [s for s in stations if s.city == "Batman"]
    expected = max(batman_stations, key=lambda s: s.current.aqi_index)

    assert batman_rank.representative_station.id == expected.id


def test_rank_provinces_ignores_unrated_stations(load_fixture_text):
    stations = _load_all_stations(load_fixture_text)
    unrated = stations[0].model_copy(deep=True)
    unrated.city = "Zzztest"
    unrated.current.aqi_index = None
    ranked = rank_provinces([unrated, *stations], "worst", 500)

    assert all(rank.province != "Zzztest" for rank in ranked)


def test_rank_stations_worst_mode_orders_descending(load_fixture_text):
    stations = _load_all_stations(load_fixture_text)
    ranked = rank_stations(stations, "worst", 10)
    values = [station.current.aqi_index for station in ranked]

    assert values == sorted(values, reverse=True)
    assert len(ranked) == 10


def test_rank_stations_best_mode_orders_ascending(load_fixture_text):
    stations = _load_all_stations(load_fixture_text)
    ranked = rank_stations(stations, "best", 10)
    values = [station.current.aqi_index for station in ranked]

    assert values == sorted(values)


def test_rank_stations_excludes_unrated_stations(load_fixture_text):
    stations = _load_all_stations(load_fixture_text)
    unrated = stations[0].model_copy(deep=True)
    unrated.current.aqi_index = None
    ranked = rank_stations([unrated, *stations[1:]], "worst", 1000)

    assert all(
        station.current.aqi_index is not None for station in ranked
    )


def test_rank_stations_limit_larger_than_available_returns_all_rated(
    load_fixture_text,
):
    stations = _load_all_stations(load_fixture_text)
    rated_count = sum(
        1 for station in stations if station.current.aqi_index is not None
    )
    ranked = rank_stations(stations, "worst", rated_count + 500)

    assert len(ranked) == rated_count


def test_ranking_cache_returns_none_when_empty():
    cache = RankingCache()

    assert cache.get(("worst", 5)) is None


def test_ranking_cache_returns_cached_value_within_ttl():
    clock = {"now": 0.0}
    cache = RankingCache(ttl_seconds=3600.0, clock=lambda: clock["now"])
    cache.set(("worst", 5), ["placeholder"])
    clock["now"] += 1800

    assert cache.get(("worst", 5)) == ["placeholder"]


def test_ranking_cache_expires_after_ttl():
    clock = {"now": 0.0}
    cache = RankingCache(ttl_seconds=3600.0, clock=lambda: clock["now"])
    cache.set(("worst", 5), ["placeholder"])
    clock["now"] += 3601

    assert cache.get(("worst", 5)) is None
