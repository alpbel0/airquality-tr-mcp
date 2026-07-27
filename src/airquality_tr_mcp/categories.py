from __future__ import annotations

from typing import NamedTuple


class AqiCategory(NamedTuple):
    key: int
    name: str
    min_value: float
    max_value: float
    color: str
    description: str


# Source: docs/api-notes.md, "GetAirQualityStatus?type=0". Thresholds are
# retained for reference only; category selection uses the source AQIStatus.
AQI_CATEGORIES: dict[int, AqiCategory] = {
    0: AqiCategory(
        0,
        "İyi",
        0,
        50,
        "#0d9255",
        "Hava kalitesi iyi seviyededir.",
    ),
    1: AqiCategory(
        1,
        "Orta",
        50,
        100,
        "#f8c90e",
        "Hava kalitesi uygun olup, hassas gruplar orta düzeyde etkilenebilir.",
    ),
    2: AqiCategory(
        2,
        "Hassas",
        100,
        150,
        "#ec7c1e",
        "Hassas gruplar için sağlık etkileri oluşabilir.",
    ),
    3: AqiCategory(
        3,
        "Sağlıksız",
        150,
        200,
        "#bc0101",
        "Hassas gruplar ciddi sağlık sorunları yaşayabilir.",
    ),
    4: AqiCategory(
        4,
        "Kötü",
        200,
        300,
        "#ae0b54",
        "Nüfusun tamamının etkilenme olasılığı yüksek.",
    ),
    5: AqiCategory(
        5,
        "Tehlikeli",
        300,
        500,
        "#950f35",
        "Herkes ciddi sağlık etkileri yaşayabilir.",
    ),
    99: AqiCategory(
        99,
        "Ölçüm Yok",
        500,
        545,
        "#afafaf",
        "Henüz mevcut saat için ölçüm yok.",
    ),
}


def category_for_status(aqi_status: int | None) -> dict | None:
    """Map the source AQIStatus code to its official category details."""
    if aqi_status is None:
        return None
    category = AQI_CATEGORIES.get(aqi_status)
    if category is None:
        return None
    return {
        "ad": category.name,
        "renk": category.color,
        "aciklama": category.description,
    }
