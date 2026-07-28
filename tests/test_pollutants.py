from airquality_tr_mcp.pollutants import (
    ALERT_POLLUTANTS,
    POLLUTANT_FIELDS,
    resolve_pollutant,
)


def test_pollutant_fields_covers_all_measured_pollutants():
    assert set(POLLUTANT_FIELDS) == {
        "NO2",
        "SO2",
        "CO",
        "O3",
        "PM10",
        "PM25",
    }


def test_alert_pollutants_includes_hki_and_pm25_dot_notation():
    assert "HKI" in ALERT_POLLUTANTS
    assert "PM2.5" in ALERT_POLLUTANTS
    assert "PM25" not in ALERT_POLLUTANTS


def test_resolve_pollutant_accepts_hki():
    assert resolve_pollutant("HKI") == "HKI"


def test_resolve_pollutant_maps_pm25_dot_notation():
    assert resolve_pollutant("PM2.5") == "PM25"


def test_resolve_pollutant_accepts_lowercase_input():
    assert resolve_pollutant("so2") == "SO2"


def test_resolve_pollutant_returns_none_for_unknown_string():
    assert resolve_pollutant("XYZ") is None


def test_resolve_pollutant_returns_none_for_empty_string():
    assert resolve_pollutant("") is None
