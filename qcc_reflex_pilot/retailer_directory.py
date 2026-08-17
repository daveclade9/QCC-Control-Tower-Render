"""Clade9 store-locator matching for the Retail Availability workspace."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


_DIRECTORY_PATH = Path(__file__).with_name("clade9_locations.json")
_GENERIC_WORDS = {
    "adult", "cannabis", "co", "company", "corp", "corporation",
    "dispensaries", "dispensary", "inc", "jersey", "llc", "marijuana",
    "medical", "new", "nj", "recreational", "shop", "store", "the",
    "wellness",
}


def _load_locations() -> list[dict[str, Any]]:
    try:
        payload = json.loads(_DIRECTORY_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return []
    return [
        dict(row) for row in payload.get("locations", [])
        if isinstance(row, dict) and str(row.get("name", "")).strip()
    ]


CLADE9_LOCATIONS = _load_locations()


def _normalized(value: Any) -> str:
    text = str(value or "").lower().replace("&", " and ")
    return " ".join(re.findall(r"[a-z0-9]+", text))


def _significant_tokens(value: Any) -> set[str]:
    return {
        token for token in _normalized(value).split()
        if token not in _GENERIC_WORDS
    }


def find_clade9_location(retailer_name: str) -> dict[str, Any]:
    """Return the strongest safe name match from the Clade9 locator."""
    normalized_name = _normalized(retailer_name)
    query_tokens = _significant_tokens(retailer_name)
    if not normalized_name:
        return {}

    best: dict[str, Any] = {}
    best_score = 0.0
    for location in CLADE9_LOCATIONS:
        candidate_name = str(location.get("name", ""))
        normalized_candidate = _normalized(candidate_name)
        candidate_tokens = _significant_tokens(candidate_name)
        score = 0.0
        if normalized_name == normalized_candidate:
            score = 1.0
        elif query_tokens and query_tokens == candidate_tokens:
            score = 0.95
        elif query_tokens and candidate_tokens:
            overlap = len(query_tokens & candidate_tokens)
            union = len(query_tokens | candidate_tokens)
            similarity = overlap / union if union else 0.0
            if similarity >= 0.8 and overlap >= min(2, len(query_tokens)):
                score = similarity
            elif (
                len(query_tokens) >= 2
                and (query_tokens.issubset(candidate_tokens)
                     or candidate_tokens.issubset(query_tokens))
            ):
                score = 0.82
        if score > best_score:
            best = location
            best_score = score

    # Avoid mapping a retailer to a similarly named but different business.
    return dict(best) if best_score >= 0.8 else {}
