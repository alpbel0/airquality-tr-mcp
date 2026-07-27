import pytest

from airquality_tr_mcp.normalization import (
    AmbiguousMatchError,
    NoMatchError,
    fuzzy_best_match,
    normalize_tr,
)


def test_normalize_tr_strips_whitespace():
    assert normalize_tr("  İstanbul  ") == "istanbul"


def test_normalize_tr_handles_ascii_i_as_dotted_i():
    assert normalize_tr("Istanbul") == normalize_tr("İstanbul") == "istanbul"


def test_normalize_tr_folds_turkish_diacritics():
    assert normalize_tr("Ağrı") == "agri"
    assert normalize_tr("Şanlıurfa") == "sanliurfa"
    assert normalize_tr("Çanakkale") == "canakkale"
    assert normalize_tr("Muğla") == "mugla"
    assert normalize_tr("Kütahya") == "kutahya"


def test_normalize_tr_lowercases_dotless_i_provinces():
    assert normalize_tr("Adıyaman") == "adiyaman"


def test_fuzzy_best_match_finds_typo_above_threshold():
    match = fuzzy_best_match(
        normalize_tr("Anatlya"), ["Antalya", "Ankara", "Adana"]
    )
    assert match.value == "Antalya"
    assert match.score >= 0.80


def test_fuzzy_best_match_rejects_prefix_abbreviation():
    with pytest.raises(NoMatchError) as exc_info:
        fuzzy_best_match(normalize_tr("Afyon"), ["Afyonkarahisar", "Ankara"])
    assert exc_info.value.query == normalize_tr("Afyon")


def test_fuzzy_best_match_raises_no_match_below_threshold():
    with pytest.raises(NoMatchError) as exc_info:
        fuzzy_best_match(normalize_tr("Zzzzzz"), ["Antalya", "Ankara"])
    assert exc_info.value.suggestions


def test_fuzzy_best_match_raises_ambiguous_when_top_two_are_close():
    with pytest.raises(AmbiguousMatchError) as exc_info:
        fuzzy_best_match(normalize_tr("Adina"), ["Aydın", "Adana", "Ankara"])
    assert len(exc_info.value.candidates) >= 2


def test_fuzzy_best_match_raises_no_match_for_empty_candidates():
    with pytest.raises(NoMatchError) as exc_info:
        fuzzy_best_match(normalize_tr("Antalya"), [])
    assert exc_info.value.suggestions == []


def test_fuzzy_best_match_ignores_duplicate_candidates():
    match = fuzzy_best_match(
        normalize_tr("Batman - 22"),
        ["Batman - 2", "Batman - 2"],
    )
    assert match.value == "Batman - 2"
