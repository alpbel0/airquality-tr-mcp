from airquality_tr_mcp.categories import AQI_CATEGORIES, category_for_status


def test_category_for_status_maps_iyi():
    result = category_for_status(0)
    assert result == {
        "ad": "İyi",
        "renk": "#0d9255",
        "aciklama": "Hava kalitesi iyi seviyededir.",
    }


def test_category_for_status_maps_olcum_yok_code():
    result = category_for_status(99)
    assert result["ad"] == "Ölçüm Yok"
    assert result["renk"] == "#afafaf"


def test_category_for_status_returns_none_for_missing_status():
    assert category_for_status(None) is None


def test_category_for_status_returns_none_for_unknown_code():
    assert category_for_status(7) is None


def test_aqi_categories_has_all_seven_official_entries():
    assert set(AQI_CATEGORIES.keys()) == {0, 1, 2, 3, 4, 5, 99}
