"""
Place ID resolver — given a business name + city + state,
returns the Google Place ID, Maps URL, current rating, and review count.

Uses the Google Places Text Search (legacy) API which is simpler for
name-based lookups than the newer Places API.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import re

import requests

from app.core.config import settings

logger = logging.getLogger(__name__)

# Place types that aren't useful for competitor category searches
_GENERIC_TYPES = {
    "point_of_interest", "establishment", "food", "store", "finance",
    "health", "local_government_office", "political", "geocode",
    "route", "locality", "sublocality", "neighborhood",
    "administrative_area_level_1", "administrative_area_level_2",
    "administrative_area_level_3", "country", "postal_code",
}


def _clean_place_name(name: str) -> str:
    """Strip Google Maps UI artifacts from place names.
    e.g. '🔲 See photos Broadway Dental Co.' → 'Broadway Dental Co.'
    """
    if not name:
        return name
    # Remove leading emoji/symbols + 'See photos' prefix Google sometimes includes
    name = re.sub(r'^[\U00010000-\U0010ffff -⿿　-〿\s]*', '', name)
    name = re.sub(r'^See photos\s*', '', name, flags=re.IGNORECASE)
    return name.strip()


@dataclass
class PlaceResult:
    place_id: str
    name: str
    formatted_address: str
    google_maps_url: str
    rating: Optional[float]
    review_count: Optional[int]


def suggest_competitors(
    business_name: str,
    city: str,
    state: str,
    *,
    count: int = 3,
    timeout_s: int = 10,
) -> list[str]:
    """
    Given a business name + location, return up to `count` likely competitor names
    by resolving the business type then searching for similar businesses nearby.
    """
    api_key = getattr(settings, "GOOGLE_PLACES_API_KEY", None)
    if not api_key:
        return []

    url = "https://maps.googleapis.com/maps/api/place/textsearch/json"

    # Step 1: find the target business and its types
    try:
        r = requests.get(url, params={"query": f"{business_name} {city} {state}", "key": api_key}, timeout=timeout_s)
        r.raise_for_status()
        results = r.json().get("results") or []
    except Exception as exc:
        logger.error("suggest_competitors target lookup failed: %s", exc)
        return []

    if not results:
        return []

    target = results[0]
    target_place_id = target.get("place_id")
    target_name_lower = (target.get("name") or business_name).lower()

    # Step 2: pick a specific type for the competitor search
    types = target.get("types") or []
    category = next((t for t in types if t not in _GENERIC_TYPES), None)

    if category:
        search_term = category.replace("_", " ")
    else:
        # Fall back to first meaningful word(s) of business name
        words = [w for w in business_name.split() if len(w) > 3 and w.lower() not in {"the", "and", "for", "llc", "inc"}]
        search_term = " ".join(words[:2]) if words else business_name

    # Step 3: search for similar businesses in the same city
    try:
        r2 = requests.get(url, params={"query": f"{search_term} {city} {state}", "key": api_key}, timeout=timeout_s)
        r2.raise_for_status()
        comp_results = r2.json().get("results") or []
    except Exception as exc:
        logger.error("suggest_competitors category search failed: %s", exc)
        return []

    # Step 4: filter out the target, return top names
    suggestions = []
    for item in comp_results:
        if item.get("place_id") == target_place_id:
            continue
        name = _clean_place_name(item.get("name") or "")
        if not name or name.lower() == target_name_lower:
            continue
        suggestions.append(name)
        if len(suggestions) >= count:
            break

    return suggestions


def resolve_place_id(
    name: str,
    city: str,
    state: str,
    *,
    timeout_s: int = 10,
) -> Optional[PlaceResult]:
    """
    Look up a business by name + city + state and return its Place ID
    plus current rating/review count.

    Returns None if no result found or API is unavailable.
    """
    api_key = getattr(settings, "GOOGLE_PLACES_API_KEY", None)
    if not api_key:
        logger.warning("GOOGLE_PLACES_API_KEY not set — place resolution disabled")
        return None

    query = f"{name} {city} {state}"

    url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
    params = {
        "query": query,
        "key": api_key,
    }

    try:
        r = requests.get(url, params=params, timeout=timeout_s)
        r.raise_for_status()
        data = r.json()
    except Exception as exc:
        logger.error("Place Text Search failed for %r: %s", query, exc)
        return None

    results = data.get("results") or []
    if not results:
        logger.warning("No Place results for query: %r", query)
        return None

    top = results[0]
    place_id = top.get("place_id")
    if not place_id:
        return None

    rating_raw = top.get("rating")
    review_count_raw = top.get("user_ratings_total")

    return PlaceResult(
        place_id=place_id,
        name=_clean_place_name(top.get("name") or name),
        formatted_address=top.get("formatted_address") or f"{city}, {state}",
        google_maps_url=f"https://www.google.com/maps/place/?q=place_id:{place_id}",
        rating=float(rating_raw) if rating_raw is not None else None,
        review_count=int(review_count_raw) if review_count_raw is not None else None,
    )
