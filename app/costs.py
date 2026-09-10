

"""Live fuel pricing and trip cost estimation helpers."""

from __future__ import annotations

import csv
import io
import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.request import Request, urlopen

from core.logging_config import LOG_INFO, LOG_DEBUG
from app.database import get_setting, set_setting
from core.config import COST_PER_KM
from core.models import Car


logger = logging.getLogger(__name__)
MIMIT_PRICE_URL = "https://www.mimit.gov.it/images/exportCSV/prezzo_alle_8.csv"
USER_AGENT = "DMProjectLocalApp/0.1 (personal local planner)"
FUEL_PRICE_CACHE_KEY = "live_fuel_price_cache"
FUEL_PRICE_CACHE_HOURS = 12
FUEL_PRICE_FALLBACK_CACHE_HOURS = 6
FUEL_PRICE_SYNC_DAYS = 7

DEFAULT_FUEL_PRICE_PER_UNIT_EUR = {
    "gasoline": 1.673,
    "diesel": 1.728,
    "lpg": 0.690,
    "methane": 1.404,
    "hybrid": 1.673,
    "electric": 0.23,
}

FUEL_LABEL_TO_KEY = {
    "benzina": "gasoline",
    "gasolio": "diesel",
    "gpl": "lpg",
    "metano": "methane",
}

FUEL_EXPECTED_SERVICE_MODE = {
    "gasoline": "self",
    "diesel": "self",
    "lpg": "servito",
    "methane": "servito",
}


def get_live_fuel_prices() -> dict[str, float]:
    """Return cached prices only; syncing is handled separately from optimization."""
    cached = get_setting(FUEL_PRICE_CACHE_KEY, None)
    if isinstance(cached, dict):
        refreshed_at = cached.get("refreshed_at")
        prices = cached.get("prices")
        source = cached.get("source", "live")
        if isinstance(refreshed_at, str) and isinstance(prices, dict):
            try:
                age = datetime.now(timezone.utc) - datetime.fromisoformat(refreshed_at)
            except ValueError:
                age = timedelta.max
            max_age_hours = FUEL_PRICE_CACHE_HOURS if source == "live" else FUEL_PRICE_FALLBACK_CACHE_HOURS
            if age <= timedelta(hours=max_age_hours):
                return DEFAULT_FUEL_PRICE_PER_UNIT_EUR | {
                    key: float(value) for key, value in prices.items() if key in DEFAULT_FUEL_PRICE_PER_UNIT_EUR
                }
    return dict(DEFAULT_FUEL_PRICE_PER_UNIT_EUR)


def sync_fuel_prices(force: bool = False) -> dict[str, Any]:
    """Refresh official fuel prices when stale, outside the optimization flow."""
    LOG_INFO(f"sync_fuel_prices called — force={force}")
    cached = get_setting(FUEL_PRICE_CACHE_KEY, None)
    if not force and isinstance(cached, dict):
        refreshed_at = cached.get("refreshed_at")
        source = cached.get("source", "live")
        if isinstance(refreshed_at, str) and source == "live":
            try:
                age = datetime.now(timezone.utc) - datetime.fromisoformat(refreshed_at)
            except ValueError:
                age = timedelta.max
            if age <= timedelta(days=FUEL_PRICE_SYNC_DAYS):
                return {"source": "cache", "prices": get_live_fuel_prices()}

    try:
        fresh_prices = _fetch_mimit_prices()
        set_setting(
            FUEL_PRICE_CACHE_KEY,
            {
                "refreshed_at": datetime.now(timezone.utc).isoformat(),
                "prices": fresh_prices,
                "source": "live",
            },
        )
        return {"source": "live", "prices": DEFAULT_FUEL_PRICE_PER_UNIT_EUR | fresh_prices}
    except Exception as exc:  # pragma: no cover - network path
        logger.warning("Fuel price sync failed, keeping cached/default prices: %s", exc)
        fallback_prices = get_live_fuel_prices()
        set_setting(
            FUEL_PRICE_CACHE_KEY,
            {
                "refreshed_at": datetime.now(timezone.utc).isoformat(),
                "prices": fallback_prices,
                "source": "fallback",
            },
        )
        return {"source": "fallback", "prices": fallback_prices, "error": str(exc)}


def estimate_trip_costs(
    route_distance_km: float,
    route_duration_min: float | None,
    car: Car | None,
    *,
    leg_distances_km: list[float] | None = None,
    leg_durations_min: list[float] | None = None,
) -> dict[str, float]:
    """Estimate fuel and toll costs for one route."""
    LOG_DEBUG(f"estimating trip costs — distance={route_distance_km:.2f}km, fuel_type={car.fuel_type if car else 'N/A'}")
    if car is None:
        return {"fuel_eur": 0.0, "toll_eur": 0.0, "total_eur": 0.0}

    live_prices = get_live_fuel_prices()
    if car.fuel_type == "electric":
        fuel_cost = route_distance_km * COST_PER_KM.get("electric", 0.04)
    else:
        unit_price = live_prices.get(car.fuel_type, live_prices.get("gasoline", 1.83))
        fuel_cost = (route_distance_km * max(car.consumption_l_per_100km, 0.0) / 100.0) * unit_price
    toll_cost = estimate_toll_cost(
        route_distance_km,
        route_duration_min,
        leg_distances_km=leg_distances_km,
        leg_durations_min=leg_durations_min,
    )
    return {
        "fuel_eur": fuel_cost,
        "toll_eur": toll_cost,
        "total_eur": fuel_cost + toll_cost,
    }


def estimate_toll_cost(
    route_distance_km: float,
    route_duration_min: float | None,
    *,
    leg_distances_km: list[float] | None = None,
    leg_durations_min: list[float] | None = None,
) -> float:
    """Estimate motorway tolls for Italian trips using recent tariff references."""
    if route_duration_min is None or route_distance_km < 25.0:
        return 0.0
    if leg_distances_km and leg_durations_min and len(leg_distances_km) == len(leg_durations_min):
        tolled_km = sum(
            distance_km * _motorway_share_for_leg(distance_km, duration_min)
            for distance_km, duration_min in zip(leg_distances_km, leg_durations_min)
        )
    else:
        tolled_km = route_distance_km * _motorway_share_for_leg(route_distance_km, route_duration_min)
    plain_rate_per_km = 0.0815
    return round(tolled_km * plain_rate_per_km, 2)


def _fetch_mimit_prices() -> dict[str, float]:
    """Download and aggregate the latest official Italian fuel prices from MIMIT."""
    request = Request(MIMIT_PRICE_URL, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=20) as response:  # pragma: no cover - network path
        raw_text = response.read().decode("utf-8-sig", errors="replace")
    reader = _build_mimit_reader(raw_text)
    sums: dict[str, float] = {}
    counts: dict[str, int] = {}
    for row in reader:
        normalized_row = {_normalize_key(key): value for key, value in row.items() if key}
        fuel_name = (
            normalized_row.get("desccarburante")
            or normalized_row.get("carburante")
            or normalized_row.get("descprodotto")
            or ""
        ).strip().lower()
        fuel_key = FUEL_LABEL_TO_KEY.get(fuel_name)
        if fuel_key is None:
            continue
        service_label = (
            normalized_row.get("isself")
            or normalized_row.get("self")
            or normalized_row.get("tipologiaimpainto")
            or normalized_row.get("tipologiaimpianto")
            or ""
        ).strip().lower()
        if not _matches_expected_service_mode(fuel_key, service_label):
            continue
        raw_price = (
            normalized_row.get("prezzo")
            or normalized_row.get("price")
            or normalized_row.get("prezzoalle8")
            or ""
        ).replace(",", ".").strip()
        if not raw_price:
            continue
        try:
            price = float(raw_price)
        except ValueError:
            continue
        if price <= 0.0:
            continue
        sums[fuel_key] = sums.get(fuel_key, 0.0) + price
        counts[fuel_key] = counts.get(fuel_key, 0) + 1

    prices = {
        fuel_key: round(sums[fuel_key] / counts[fuel_key], 3)
        for fuel_key in sums
        if counts.get(fuel_key, 0)
    }
    if not prices:
        raise ValueError("No fuel prices were parsed from the MIMIT dataset.")
    prices["hybrid"] = prices.get("gasoline", DEFAULT_FUEL_PRICE_PER_UNIT_EUR["hybrid"])
    prices["electric"] = DEFAULT_FUEL_PRICE_PER_UNIT_EUR["electric"]
    return prices


def _build_mimit_reader(raw_text: str) -> csv.DictReader:
    """Create a CSV reader even if the official export includes preamble rows."""
    lines = [line for line in raw_text.splitlines() if line.strip()]
    header_index = 0
    delimiter = ";"
    for index, line in enumerate(lines):
        lowered = line.lower()
        if "prezzo" in lowered and ("carburante" in lowered or "desc" in lowered):
            header_index = index
            delimiter = "|" if line.count("|") >= line.count(";") else ";"
            break
    return csv.DictReader(io.StringIO("\n".join(lines[header_index:])), delimiter=delimiter)


def _normalize_key(key: str) -> str:
    """Normalize header names so small official-format changes do not break parsing."""
    return "".join(character for character in key.strip().lower() if character.isalnum())


def _matches_expected_service_mode(fuel_key: str, service_label: str) -> bool:
    """Match the official pricing mode used by MIMIT for each fuel family."""
    expected_mode = FUEL_EXPECTED_SERVICE_MODE.get(fuel_key)
    if expected_mode == "self":
        return service_label in {
            "1",
            "true",
            "s",
            "self",
            "self service",
            "impianto stradale self service",
        }
    if expected_mode == "servito":
        return service_label in {
            "0",
            "false",
            "n",
            "no",
            "servito",
            "impianto stradale servito",
        }
    return False


def _motorway_share_for_leg(distance_km: float, duration_min: float | None) -> float:
    """Estimate how much of a leg is likely tolled motorway travel."""
    if duration_min is None or distance_km < 6.0:
        return 0.0
    average_speed = distance_km / max(duration_min / 60.0, 0.01)
    if average_speed >= 78.0:
        return 1.0
    if average_speed >= 70.0:
        return 0.9
    if average_speed >= 62.0:
        return 0.7
    if average_speed >= 55.0:
        return 0.4
    return 0.0
