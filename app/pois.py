"""Food points-of-interest helpers for route overlays."""

from __future__ import annotations

import json
import logging
from collections import OrderedDict
from math import cos, radians
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from core.config import ESTIMATED_AVERAGE_SPEED_KMH
from core.logging_config import LOG_DEBUG


logger = logging.getLogger(__name__)


class FoodLookupUnavailable(RuntimeError):
    """Raised when every Overpass mirror fails, as opposed to a genuine empty result."""
OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
USER_AGENT = "DMProjectLocalApp/0.1 (personal local planner)"
_FOOD_CACHE_LIMIT = 12
_food_overlay_cache: "OrderedDict[str, list[dict[str, Any]]]" = OrderedDict()


def find_food_along_route(route_geometry: list[list[float]], mode: str, max_distance_km: float = 3.0) -> list[dict[str, Any]]:
    """Return selected food locations close to the current route."""
    LOG_DEBUG(f"finding food along route — mode={mode}, max_distance={max_distance_km}km, points={len(route_geometry)}")
    if not route_geometry or mode == "off":
        return []

    cache_key = json.dumps(
        {
            "mode": mode,
            "max_distance_km": round(max_distance_km, 2),
            "route": [[round(point[0], 4), round(point[1], 4)] for point in route_geometry],
        },
        sort_keys=True,
    )
    cached = _food_overlay_cache.get(cache_key)
    if cached is not None:
        _food_overlay_cache.move_to_end(cache_key)
        return cached

    south = min(point[0] for point in route_geometry)
    north = max(point[0] for point in route_geometry)
    west = min(point[1] for point in route_geometry)
    east = max(point[1] for point in route_geometry)
    lat_padding = max_distance_km / 111.32
    lon_padding = max_distance_km / (111.32 * max(0.2, abs(cos(radians((south + north) / 2.0)))))
    bbox = (
        south - lat_padding,
        west - lon_padding,
        north + lat_padding,
        east + lon_padding,
    )

    query = f"""
    [out:json][timeout:20];
    (
      node["amenity"="fast_food"]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});
      way["amenity"="fast_food"]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});
      relation["amenity"="fast_food"]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});
      node["amenity"="restaurant"]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});
      way["amenity"="restaurant"]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});
      relation["amenity"="restaurant"]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});
      node["amenity"="cafe"]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});
      way["amenity"="cafe"]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});
      relation["amenity"="cafe"]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});
    );
    out center;
    """
    payload = None
    last_error: Exception | None = None
    for overpass_url in OVERPASS_URLS:
        request = Request(
            f"{overpass_url}?{urlencode({'data': query})}",
            headers={"User-Agent": USER_AGENT},
        )
        try:
            with urlopen(request, timeout=14) as response:  # pragma: no cover - network path
                payload = json.loads(response.read().decode("utf-8"))
                break
        except Exception as exc:  # pragma: no cover - network path
            last_error = exc
            logger.warning("Food overlay lookup failed via %s: %s", overpass_url, exc)
    if payload is None:
        logger.warning("Food overlay lookup failed on all mirrors: %s", last_error)
        raise FoodLookupUnavailable(str(last_error or "no Overpass mirror reachable"))

    points: list[dict[str, Any]] = []
    for element in payload.get("elements", []):
        tags = element.get("tags") or {}
        lat = element.get("lat", element.get("center", {}).get("lat"))
        lon = element.get("lon", element.get("center", {}).get("lon"))
        if lat is None or lon is None:
            continue
        poi_kind = _classify_food(tags)
        if poi_kind is None or not _matches_mode(poi_kind, mode):
            continue
        distance_km = _distance_to_polyline_km(float(lat), float(lon), route_geometry)
        if distance_km > max_distance_km:
            continue
        # Rough detour estimate: leave the route, reach the stop, and come back.
        detour_km = round(2.0 * distance_km, 2)
        detour_min = round((detour_km / max(ESTIMATED_AVERAGE_SPEED_KMH, 1.0)) * 60.0, 1)
        points.append(
            {
                "name": tags.get("name") or _default_name_for_kind(poi_kind),
                "kind": poi_kind,
                "latitude": float(lat),
                "longitude": float(lon),
                "distance_to_route_km": round(distance_km, 2),
                "detour_km": detour_km,
                "detour_min": detour_min,
            }
        )

    deduped_points: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, float, float]] = set()
    for point in points:
        point_key = (point["kind"], round(point["latitude"], 4), round(point["longitude"], 4))
        if point_key in seen_keys:
            continue
        seen_keys.add(point_key)
        deduped_points.append(point)

    deduped_points.sort(key=lambda point: (point["distance_to_route_km"], point["name"].lower()))
    _food_overlay_cache[cache_key] = deduped_points
    _food_overlay_cache.move_to_end(cache_key)
    while len(_food_overlay_cache) > _FOOD_CACHE_LIMIT:
        _food_overlay_cache.popitem(last=False)
    return deduped_points


def _classify_food(tags: dict[str, str]) -> str | None:
    """Classify an OSM POI into one of the supported food overlay groups."""
    name = (tags.get("name") or "").lower()
    brand = (tags.get("brand") or "").lower()
    operator = (tags.get("operator") or "").lower()
    cuisine = (tags.get("cuisine") or "").lower()
    amenity = (tags.get("amenity") or "").lower()
    if "mcdonald" in name or "mcdonald" in brand or "mcdonald" in operator:
        return "mcdonalds"
    if "burger king" in name or "burger king" in brand or "burger king" in operator:
        return "burger_king"
    if "kfc" in name or "kfc" in brand or "kfc" in operator:
        return "kfc"
    if "kebab" in cuisine or "kebab" in name or "doner" in name or "doner" in cuisine:
        return "kebab"
    if _is_quick_pizza_stop(tags, amenity, cuisine, name):
        return "pizza"
    if amenity == "cafe" or "coffee" in cuisine:
        return "cafe"
    return None


def _matches_mode(kind: str, mode: str) -> bool:
    """Check whether a POI kind belongs to the selected overlay mode."""
    if mode == "all":
        return kind in {"kebab", "kfc", "mcdonalds", "burger_king", "pizza", "cafe"}
    return kind == mode


def _default_name_for_kind(kind: str) -> str:
    """Fallback names for unnamed POIs."""
    return {
        "kfc": "KFC",
        "kebab": "Kebab shop",
        "mcdonalds": "McDonald's",
        "burger_king": "Burger King",
        "pizza": "Pizza place",
        "cafe": "Coffee stop",
    }.get(kind, "Food stop")


def _is_quick_pizza_stop(tags: dict[str, str], amenity: str, cuisine: str, name: str) -> bool:
    """Return True only for pizza places that behave like quick stops along a route."""
    if "pizza" not in cuisine and "pizza" not in name and "pizzeria" not in name:
        return False
    takeaway = (tags.get("takeaway") or "").lower()
    delivery = (tags.get("delivery") or "").lower()
    if amenity == "fast_food":
        return True
    return takeaway in {"yes", "only"} or delivery == "yes"


def _distance_to_polyline_km(lat: float, lon: float, geometry: list[list[float]]) -> float:
    """Approximate the shortest distance from a point to a route polyline in km."""
    if len(geometry) < 2:
        return 9999.0
    lat_factor = 111.32
    lon_factor = 111.32 * max(0.2, abs(cos(radians(lat))))
    point_x = lon * lon_factor
    point_y = lat * lat_factor
    best = float("inf")
    for start, end in zip(geometry, geometry[1:]):
        x1 = start[1] * lon_factor
        y1 = start[0] * lat_factor
        x2 = end[1] * lon_factor
        y2 = end[0] * lat_factor
        dx = x2 - x1
        dy = y2 - y1
        if abs(dx) < 1e-9 and abs(dy) < 1e-9:
            candidate = ((point_x - x1) ** 2 + (point_y - y1) ** 2) ** 0.5
        else:
            t = max(0.0, min(1.0, ((point_x - x1) * dx + (point_y - y1) * dy) / (dx * dx + dy * dy)))
            proj_x = x1 + t * dx
            proj_y = y1 + t * dy
            candidate = ((point_x - proj_x) ** 2 + (point_y - proj_y) ** 2) ** 0.5
        best = min(best, candidate)
    return best
