"""Address geocoding with local SQLite caching and a Nominatim fallback."""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from core.logging_config import LOG_INFO, LOG_DEBUG
from app.database import execute, fetch_one


NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "DMProjectLocalApp/0.1 (personal local planner)"
logger = logging.getLogger(__name__)


def friendly_address_label(match: dict[str, Any]) -> str:
    """Build a short label like 'Street 12, City' instead of a bare house number."""
    address = match.get("address") or {}
    street = (
        address.get("road")
        or address.get("pedestrian")
        or address.get("footway")
        or address.get("cycleway")
        or address.get("residential")
        or address.get("path")
    )
    house_number = address.get("house_number")
    locality = (
        address.get("city")
        or address.get("town")
        or address.get("village")
        or address.get("municipality")
        or address.get("suburb")
    )
    if street and house_number:
        primary = f"{street} {house_number}"
    elif street:
        primary = street
    else:
        primary = (match.get("display_name") or "").split(",")[0].strip()
    if locality and locality.lower() not in primary.lower():
        return f"{primary}, {locality}"
    return primary or (match.get("display_name") or "Selected address")


def geocode_address(query: str) -> dict[str, Any]:
    """Resolve an address to coordinates, using the local cache when possible."""
    LOG_DEBUG(f"geocoding address — query={query!r}")
    normalized_query = _normalize_query(query)
    cached = fetch_one(
        """
        SELECT display_name, latitude, longitude
        FROM geocode_cache
        WHERE query = ?
        """,
        (normalized_query,),
    )
    if cached is not None:
        LOG_DEBUG(f"geocode cache hit for {query!r}")
        return {
            "display_name": cached["display_name"],
            "label": cached["display_name"].split(",")[0],
            "latitude": cached["latitude"],
            "longitude": cached["longitude"],
            "source": "cache",
        }

    params = urlencode({"q": query, "format": "jsonv2", "limit": 1, "addressdetails": 1})
    LOG_DEBUG(f"geocode cache miss — calling Nominatim for {query!r}")
    request = Request(
        f"{NOMINATIM_URL}?{params}",
        headers={"User-Agent": USER_AGENT},
    )

    try:
        with urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:  # pragma: no cover - runtime network path
        logger.warning("Geocoding lookup failed for query '%s': %s", query, exc)
        raise ValueError(
            "Address lookup failed. Check the address or enter coordinates manually."
        ) from exc

    if not payload:
        raise ValueError("No location was found for that address.")

    match = payload[0]
    result = {
        "display_name": match["display_name"],
        "label": friendly_address_label(match),
        "latitude": float(match["lat"]),
        "longitude": float(match["lon"]),
        "source": "nominatim",
    }
    execute(
        """
        INSERT OR REPLACE INTO geocode_cache(query, display_name, latitude, longitude)
        VALUES(?, ?, ?, ?)
        """,
        (
            normalized_query,
            result["display_name"],
            result["latitude"],
            result["longitude"],
        ),
    )
    return result


def search_addresses(query: str, limit: int = 5) -> list[dict[str, Any]]:
    """Return autocomplete-style address suggestions from Nominatim."""
    LOG_DEBUG(f"searching addresses — query={query!r}, limit={limit}")
    normalized_query = _normalize_query(query)
    if not normalized_query:
        return []

    params = urlencode(
        {
            "q": query,
            "format": "jsonv2",
            "limit": max(1, min(limit, 10)),
            "addressdetails": 1,
        }
    )
    request = Request(
        f"{NOMINATIM_URL}?{params}",
        headers={"User-Agent": USER_AGENT},
    )

    try:
        with urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:  # pragma: no cover - runtime network path
        logger.warning("Address autocomplete failed for query '%s': %s", query, exc)
        raise ValueError("Address search is temporarily unavailable.") from exc

    suggestions = []
    for match in payload:
        suggestions.append(
            {
                "display_name": match["display_name"],
                "label": friendly_address_label(match),
                "latitude": float(match["lat"]),
                "longitude": float(match["lon"]),
            }
        )
    return suggestions


def _normalize_query(query: str) -> str:
    """Normalize cache keys so equivalent addresses reuse the same result."""
    return " ".join(query.strip().lower().split())
