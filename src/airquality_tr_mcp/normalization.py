from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher

FUZZY_THRESHOLD = 0.80
FUZZY_TIE_BREAK = 0.05

_TR_ASCII_MAP = str.maketrans(
    {
        "ı": "i",
        "İ": "i",
        "I": "i",
        "ğ": "g",
        "Ğ": "g",
        "ü": "u",
        "Ü": "u",
        "ş": "s",
        "Ş": "s",
        "ö": "o",
        "Ö": "o",
        "ç": "c",
        "Ç": "c",
    }
)


def normalize_tr(text: str) -> str:
    """Normalize Turkish text for case- and diacritic-insensitive matching."""
    return text.strip().translate(_TR_ASCII_MAP).lower()


class NoMatchError(Exception):
    def __init__(self, query: str, suggestions: list[str]) -> None:
        self.query = query
        self.suggestions = suggestions
        super().__init__(f"No match found for {query!r}")


class AmbiguousMatchError(Exception):
    def __init__(self, query: str, candidates: list[str]) -> None:
        self.query = query
        self.candidates = candidates
        super().__init__(f"{query!r} matches multiple candidates: {candidates}")


@dataclass
class FuzzyMatch:
    value: str
    score: float


def fuzzy_best_match(
    normalized_query: str, candidates: list[str]
) -> FuzzyMatch:
    """Return a confident fuzzy match or raise a structured match error."""
    if not candidates:
        raise NoMatchError(normalized_query, [])

    unique_candidates: dict[str, str] = {}
    for candidate in candidates:
        unique_candidates.setdefault(normalize_tr(candidate), candidate)

    scored = sorted(
        (
            (
                candidate,
                SequenceMatcher(None, normalized_query, normalized).ratio(),
            )
            for normalized, candidate in unique_candidates.items()
        ),
        key=lambda pair: pair[1],
        reverse=True,
    )
    best_value, best_score = scored[0]
    second_score = scored[1][1] if len(scored) > 1 else 0.0

    if best_score < FUZZY_THRESHOLD:
        raise NoMatchError(
            normalized_query, [value for value, _ in scored[:3]]
        )

    if best_score - second_score < FUZZY_TIE_BREAK:
        tied = [
            value
            for value, score in scored
            if best_score - score < FUZZY_TIE_BREAK
        ]
        raise AmbiguousMatchError(normalized_query, tied)

    return FuzzyMatch(value=best_value, score=best_score)
