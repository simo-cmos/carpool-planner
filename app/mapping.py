"""Mapping helpers for reverse geocoding and route visualization."""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from core.logging_config import LOG_INFO, LOG_DEBUG
from core.models import Location

from app.database import execute, fetch_one
from app.geocoding import friendly_address_label


NOMINATIM_REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"
OSRM_ROUTE_URL = "https://router.project-osrm.org/route/v1/driving"
OSRM_TABLE_URL = "https://router.project-osrm.org/table/v1/driving"
USER_AGENT = "DMProjectLocalApp/0.1 (personal local planner)"
logger = logging.getLogger(__name__)
_ROUTE_PREVIEW_CACHE: dict[str, dict[str, Any]] = {}
_ROUTE_MATRIX_CACHE: dict[str, dict[str, Any]] = {}


def reverse_geocode(latitude: float, longitude: float) -> dict[str, Any]:
    """Convert coordinates into a human-readable address, using cache first."""
    LOG_DEBUG(f"reverse geocode — ({latitude:.5f}, {longitude:.5f})")
    cache_key = f"{latitude:.6f},{longitude:.6f}"
    cached = fetch_one(
        """
        SELECT display_name
        FROM reverse_geocode_cache
        WHERE cache_key = ?
        """,
        (cache_key,),
    )
    if cached is not None:
        return {"display_name": cached["display_name"], "source": "cache"}

    params = urlencode(
        {
            "lat": latitude,
            "lon": longitude,
            "format": "jsonv2",
            "zoom": 18,
            "addressdetails": 1,
        }
    )
    request = Request(
        f"{NOMINATIM_REVERSE_URL}?{params}",
        headers={"User-Agent": USER_AGENT},
    )

    try:
        with urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:  # pragma: no cover - runtime network path
        logger.warning(
            "Reverse geocoding failed for coordinates (%s, %s): %s",
            latitude,
            longitude,
            exc,
        )
        raise ValueError("Reverse geocoding failed for the selected map point.") from exc

    display_name = payload.get("display_name")
    if not display_name:
        raise ValueError("No address was found for the selected map point.")

    execute(
        """
        INSERT OR REPLACE INTO reverse_geocode_cache(cache_key, display_name)
        VALUES(?, ?)
        """,
        (cache_key, display_name),
    )
    return {
        "display_name": display_name,
        "label": friendly_address_label(payload),
        "source": "nominatim",
    }


def build_route_preview(stops: list[Location]) -> dict[str, Any]:
    """Request a drivable route geometry and duration from OSRM."""
    LOG_DEBUG(f"building route preview — {len(stops)} stops")
    if len(stops) < 2:
        return {
            "geometry": [[stops[0].latitude, stops[0].longitude]] if stops else [],
            "distance_km": 0.0,
            "duration_min": 0.0,
        }

    cache_key = _route_cache_key(stops)
    if cache_key in _ROUTE_PREVIEW_CACHE:
        return _ROUTE_PREVIEW_CACHE[cache_key]

    coord_string = ";".join(f"{stop.longitude},{stop.latitude}" for stop in stops)
    params = urlencode({"overview": "full", "geometries": "geojson", "steps": "false"})
    request = Request(
        f"{OSRM_ROUTE_URL}/{coord_string}?{params}",
        headers={"User-Agent": USER_AGENT},
    )

    try:
        with urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        logger.warning("OSRM route preview failed, falling back to straight lines: %s", exc)
        # Fallback: keep the route visible even if OSRM is unreachable.
        result = {
            "geometry": [[stop.latitude, stop.longitude] for stop in stops],
            "distance_km": sum(
                _euclidean_km(stops[index], stops[index + 1])
                for index in range(len(stops) - 1)
            ),
            "duration_min": _fallback_duration_minutes(stops),
            "leg_durations_min": _fallback_leg_durations(stops),
            "leg_distances_km": [
                _euclidean_km(stops[index], stops[index + 1])
                for index in range(len(stops) - 1)
            ],
        }
        _remember_preview(cache_key, result)
        return result

    routes = payload.get("routes") or []
    if not routes:
        result = {
            "geometry": [[stop.latitude, stop.longitude] for stop in stops],
            "distance_km": sum(
                _euclidean_km(stops[index], stops[index + 1])
                for index in range(len(stops) - 1)
            ),
            "duration_min": _fallback_duration_minutes(stops),
            "leg_durations_min": _fallback_leg_durations(stops),
            "leg_distances_km": [
                _euclidean_km(stops[index], stops[index + 1])
                for index in range(len(stops) - 1)
            ],
        }
        _remember_preview(cache_key, result)
        return result

    route = routes[0]
    coordinates = route["geometry"]["coordinates"]
    legs = route.get("legs") or []
    result = {
        "geometry": [[lat, lon] for lon, lat in coordinates],
        "distance_km": route["distance"] / 1000.0,
        "duration_min": route["duration"] / 60.0,
        "leg_durations_min": [leg["duration"] / 60.0 for leg in legs],
        "leg_distances_km": [leg["distance"] / 1000.0 for leg in legs],
    }
    _remember_preview(cache_key, result)
    return result


def build_route_matrix(stops: list[Location]) -> dict[str, Any]:
    """Request road distance and duration matrices for a small set of points."""
    LOG_DEBUG(f"building route matrix — {len(stops)} stops")
    if not stops:
        return {"distance_km": [], "duration_min": []}
    cache_key = _route_cache_key(stops)
    if cache_key in _ROUTE_MATRIX_CACHE:
        return _ROUTE_MATRIX_CACHE[cache_key]
    coord_string = ";".join(f"{stop.longitude},{stop.latitude}" for stop in stops)
    params = urlencode({"annotations": "duration,distance"})
    request = Request(
        f"{OSRM_TABLE_URL}/{coord_string}?{params}",
        headers={"User-Agent": USER_AGENT},
    )
    try:
        with urlopen(request, timeout=20) as response:  # pragma: no cover - network path
            payload = json.loads(response.read().decode("utf-8"))
        durations = payload.get("durations") or []
        distances = payload.get("distances") or []
        if durations and distances:
            result = {
                "duration_min": [
                    [float(value) / 60.0 if value is not None else None for value in row]
                    for row in durations
                ],
                "distance_km": [
                    [float(value) / 1000.0 if value is not None else None for value in row]
                    for row in distances
                ],
            }
            _remember_matrix(cache_key, result)
            return result
    except Exception as exc:
        logger.warning("OSRM matrix lookup failed, falling back to geometric estimates: %s", exc)

    fallback_distance = []
    fallback_duration = []
    for start in stops:
        distance_row = []
        duration_row = []
        for end in stops:
            distance_km = _euclidean_km(start, end) if start != end else 0.0
            distance_row.append(distance_km)
            duration_row.append((distance_km / 42.0) * 60.0 if distance_km > 0 else 0.0)
        fallback_distance.append(distance_row)
        fallback_duration.append(duration_row)
    result = {"distance_km": fallback_distance, "duration_min": fallback_duration}
    _remember_matrix(cache_key, result)
    return result


def _euclidean_km(first: Location, second: Location) -> float:
    """Very small fallback approximation if routing is unavailable."""
    lat_factor = 111.32
    lon_factor = 111.32 * max(0.2, abs(__import__("math").cos(__import__("math").radians(first.latitude))))
    dlat = (second.latitude - first.latitude) * lat_factor
    dlon = (second.longitude - first.longitude) * lon_factor
    return (dlat ** 2 + dlon ** 2) ** 0.5


def _fallback_duration_minutes(stops: list[Location]) -> float:
    """Fallback route duration derived from geometric distance."""
    return sum(_fallback_leg_durations(stops))


def _fallback_leg_durations(stops: list[Location]) -> list[float]:
    """Fallback leg durations when routed timing is unavailable."""
    return [
        (_euclidean_km(stops[index], stops[index + 1]) / 42.0) * 60.0
        for index in range(len(stops) - 1)
    ]


def _route_cache_key(stops: list[Location]) -> str:
    """Build a stable in-process cache key for a stop sequence."""
    return "|".join(f"{stop.latitude:.5f},{stop.longitude:.5f}" for stop in stops)


def _remember_preview(cache_key: str, payload: dict[str, Any]) -> None:
    """Keep a small preview cache for repeated local optimizations."""
    _ROUTE_PREVIEW_CACHE[cache_key] = payload
    if len(_ROUTE_PREVIEW_CACHE) > 64:
        _ROUTE_PREVIEW_CACHE.pop(next(iter(_ROUTE_PREVIEW_CACHE)))


def _remember_matrix(cache_key: str, payload: dict[str, Any]) -> None:
    """Keep a small matrix cache for repeated local optimizations."""
    _ROUTE_MATRIX_CACHE[cache_key] = payload
    if len(_ROUTE_MATRIX_CACHE) > 32:
        _ROUTE_MATRIX_CACHE.pop(next(iter(_ROUTE_MATRIX_CACHE)))
