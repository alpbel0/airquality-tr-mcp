from __future__ import annotations

HEALTH_ADVISORIES: dict[int, str] = {
    0: (
        "Hava kalitesi iyi; açık hava aktiviteleri için herhangi bir "
        "kısıtlama yok."
    ),
    1: (
        "Hava kalitesi kabul edilebilir düzeyde; astım, kalp veya "
        "akciğer hastası, çocuk ve yaşlılar uzun süreli yoğun açık hava "
        "aktivitelerini azaltmayı düşünebilir."
    ),
    2: (
        "Hassas gruplar (astım, kalp/akciğer hastaları, çocuklar, "
        "yaşlılar) uzun süreli veya yoğun açık hava aktivitelerinden "
        "kaçınmalı; genel nüfus normal aktivitelerine devam edebilir."
    ),
    3: (
        "Hassas gruplar açık hava aktivitelerini ertelemeli; genel "
        "nüfus uzun süreli yoğun açık hava egzersizlerini sınırlamalı."
    ),
    4: (
        "Hassas gruplar dışarı çıkmaktan kaçınmalı; genel nüfus da "
        "uzun süreli açık hava aktivitelerini sınırlamalı ve mümkünse "
        "iç mekanda kalmalı."
    ),
    5: (
        "Herkes açık hava aktivitelerinden kaçınmalı, pencereler kapalı "
        "tutulmalı; hassas gruplar dışarı hiç çıkmamalı."
    ),
    99: (
        "Şu an geçerli bir ölçüm bulunmadığından sağlık tavsiyesi "
        "verilemiyor."
    ),
}

DEFAULT_ADVISORY = (
    "Bu istasyon için resmi bir HKİ durumu bulunmadığından sağlık "
    "tavsiyesi verilemiyor."
)


def advisory_for_status(aqi_status: int | None) -> str:
    """Return a rule-based Turkish health/activity recommendation."""
    if aqi_status is None:
        return DEFAULT_ADVISORY
    return HEALTH_ADVISORIES.get(aqi_status, DEFAULT_ADVISORY)
