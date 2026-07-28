from airquality_tr_mcp.advisories import (
    HEALTH_ADVISORIES,
    advisory_for_status,
)


def test_advisory_for_status_iyi_mentions_no_restriction():
    assert "kısıtlama yok" in advisory_for_status(0)


def test_advisory_for_status_tehlikeli_mentions_everyone():
    assert "Herkes" in advisory_for_status(5)


def test_advisory_for_status_hassas_mentions_hassas_gruplar():
    assert "Hassas gruplar" in advisory_for_status(2)


def test_advisory_for_status_returns_default_for_none():
    assert "verilemiyor" in advisory_for_status(None)


def test_advisory_for_status_returns_default_for_unknown_code():
    assert "verilemiyor" in advisory_for_status(7)


def test_advisory_for_status_covers_all_seven_official_codes():
    assert set(HEALTH_ADVISORIES) == {0, 1, 2, 3, 4, 5, 99}
