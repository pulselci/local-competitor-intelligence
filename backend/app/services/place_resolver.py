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
