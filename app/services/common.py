"""Shared service helpers and workspace snapshot logic."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict
from typing import Any

from core.logging_config import LOG_INFO, LOG_DEBUG
from core.models import Car, Location, Participant
from core.utils import get_sample_data

from app.database import execute, fetch_one, get_setting, set_setting
from app.geocoding import geocode_address
from app.services.participants import list_participants, save_participant
from app.services.settings import get_app_settings
from app.time_utils import time_text_to_minutes


TRIP_PRESETS = [
    {"name": "Weekday Commute", "trip_name": "Weekday Commute", "notes": "Regular work or study trip"},
    {"name": "Friday Dinner", "trip_name": "Friday Dinner", "notes": "Evening meetup with the group"},
    {"name": "Weekend Trip", "trip_name": "Weekend Trip", "notes": "Flexible weekend plan"},
    {"name": "Concert Night", "trip_name": "Concert Night", "notes": "Late return expected"},
]

OPTIMIZATION_CACHE_VERSION = 5
CURRENT_DATASET_CONTEXT_KEY = "current_dataset_context"
LEGACY_GROUP_CONTEXT_KEY = "current_group_context"


def _serialize_driver_score(score) -> dict[str, Any]:
    """Convert a driver-score object into a JSON-friendly dict with derived totals."""
    payload = asdict(score)
    payload["total_score"] = score.total_score if hasattr(score, "total_score") else payload.get("total_score", 0.0)
    return payload


def get_pickup_order_rules() -> list[dict[str, int]]:
    """Return outbound pickup-order preferences for the current workspace."""
    rules = get_setting("pickup_order_rules", [])
    if not isinstance(rules, list):
        return []
    normalized_rules: list[dict[str, int]] = []
    for rule in rules:
        try:
            before_id = int(rule["before_id"])
            after_id = int(rule["after_id"])
        except (KeyError, TypeError, ValueError):
            continue
        if before_id == after_id:
            continue
        normalized_rules.append({"before_id": before_id, "after_id": after_id})
    return normalized_rules


def get_ride_together_rules() -> list[dict[str, int]]:
    """Return same-car requirements for the current workspace."""
    rules = get_setting("ride_together_rules", [])
    if not isinstance(rules, list):
        return []
    normalized_rules: list[dict[str, int]] = []
    for rule in rules:
        try:
            first_id = int(rule["first_id"])
            second_id = int(rule["second_id"])
        except (KeyError, TypeError, ValueError):
            continue
        if first_id == second_id:
            continue
        first_id, second_id = sorted((first_id, second_id))
        candidate = {"first_id": first_id, "second_id": second_id}
        if candidate not in normalized_rules:
            normalized_rules.append(candidate)
    return normalized_rules


def save_pickup_order_rule(before_id: int, after_id: int) -> None:
    """Persist one outbound pickup-order preference."""
    if before_id == after_id:
        raise ValueError("Pickup-order rules need two different participants.")
    rules = get_pickup_order_rules()
    candidate = {"before_id": before_id, "after_id": after_id}
    if candidate not in rules:
        rules.append(candidate)
    set_setting("pickup_order_rules", rules)


def save_ride_together_rule(first_id: int, second_id: int) -> None:
    """Persist one same-car requirement."""
    if first_id == second_id:
        raise ValueError("Ride-together rules need two different participants.")
    first_id, second_id = sorted((first_id, second_id))
    rules = get_ride_together_rules()
    candidate = {"first_id": first_id, "second_id": second_id}
    if candidate not in rules:
        rules.append(candidate)
    set_setting("ride_together_rules", rules)


def delete_pickup_order_rule(rule_index: int) -> None:
    """Delete one stored pickup-order preference by index."""
    rules = get_pickup_order_rules()
    if 0 <= rule_index < len(rules):
        del rules[rule_index]
        set_setting("pickup_order_rules", rules)


def delete_ride_together_rule(rule_index: int) -> None:
    """Delete one stored same-car requirement by index."""
    rules = get_ride_together_rules()
    if 0 <= rule_index < len(rules):
        del rules[rule_index]
        set_setting("ride_together_rules", rules)


def clear_pickup_order_rules() -> None:
    """Clear all trip-specific pickup-order preferences."""
    set_setting("pickup_order_rules", [])


def clear_ride_together_rules() -> None:
    """Clear all trip-specific same-car requirements."""
    set_setting("ride_together_rules", [])


def save_destination(
    *,
    name: str,
    latitude: float,
    longitude: float,
    address_text: str | None = None,
    target_arrival_time: str | None = None,
) -> None:
    """Store the current destination."""
    LOG_INFO(f"destination saved — {name} ({latitude:.4f}, {longitude:.4f})")
    set_setting(
        "destination",
        {
            "name": name,
            "latitude": latitude,
            "longitude": longitude,
            "address_text": address_text,
            "target_arrival_time": target_arrival_time,
        },
    )


def get_destination() -> dict[str, Any] | None:
    """Return the stored destination."""
    return get_setting("destination")


def clear_destination() -> None:
    """Clear the active destination."""
    set_setting("destination", None)


def set_current_dataset_context(
    *,
    dataset_type: str,
    dataset_id: int | None,
    dataset_name: str,
    customized: bool = False,
    response_count: int | None = None,
) -> None:
    """Remember which dataset the live workspace currently comes from."""
    set_setting(
        CURRENT_DATASET_CONTEXT_KEY,
        {
            "dataset_type": dataset_type.strip(),
            "dataset_id": dataset_id,
            "dataset_name": dataset_name.strip(),
            "customized": bool(customized),
            "response_count": response_count,
        },
    )


def get_current_dataset_context() -> dict[str, Any] | None:
    """Return metadata about the dataset currently loaded in the live workspace."""
    context = get_setting(CURRENT_DATASET_CONTEXT_KEY, None)
    if not isinstance(context, dict):
        legacy = get_setting(LEGACY_GROUP_CONTEXT_KEY, None)
        if isinstance(legacy, dict):
            group_name = str(legacy.get("group_name", "")).strip()
            group_id = legacy.get("group_id")
            try:
                normalized_group_id = int(group_id) if group_id is not None else None
            except (TypeError, ValueError):
                normalized_group_id = None
            if group_name:
                return {
                    "dataset_type": "snapshot",
                    "dataset_id": normalized_group_id,
                    "dataset_name": group_name,
                    "customized": False,
                    "response_count": None,
                }
        return None
    if not isinstance(context, dict):
        return None
    dataset_name = str(context.get("dataset_name", "")).strip()
    dataset_type = str(context.get("dataset_type", "")).strip()
    if not dataset_name or not dataset_type:
        return None
    dataset_id = context.get("dataset_id")
    try:
        normalized_dataset_id = int(dataset_id) if dataset_id is not None else None
    except (TypeError, ValueError):
        normalized_dataset_id = None
    response_count = context.get("response_count")
    try:
        normalized_response_count = int(response_count) if response_count is not None else None
    except (TypeError, ValueError):
        normalized_response_count = None
    return {
        "dataset_type": dataset_type,
        "dataset_id": normalized_dataset_id,
        "dataset_name": dataset_name,
        "customized": bool(context.get("customized", False)),
        "response_count": normalized_response_count,
    }


def mark_current_dataset_context_customized() -> None:
    """Mark the live workspace as a customized copy of its source dataset."""
    context = get_current_dataset_context()
    if context is None:
        return
    set_current_dataset_context(
        dataset_type=str(context["dataset_type"]),
        dataset_id=context["dataset_id"],
        dataset_name=str(context["dataset_name"]),
        customized=True,
        response_count=context.get("response_count"),
    )


def clear_current_dataset_context() -> None:
    """Clear the dataset context when the workspace no longer tracks a specific source."""
    set_setting(CURRENT_DATASET_CONTEXT_KEY, None)
    set_setting(LEGACY_GROUP_CONTEXT_KEY, None)


def set_current_group_context(*, group_id: int | None, group_name: str) -> None:
    """Backward-compatible wrapper for old group-context callers."""
    set_current_dataset_context(dataset_type="snapshot", dataset_id=group_id, dataset_name=group_name)


def get_current_group_context() -> dict[str, Any] | None:
    """Backward-compatible wrapper for old group-context callers."""
    context = get_current_dataset_context()
    if context is None or context.get("dataset_type") != "snapshot":
        return None
    return {"group_id": context.get("dataset_id"), "group_name": context.get("dataset_name")}


def clear_current_group_context() -> None:
    """Backward-compatible wrapper for callers still using the old name."""
    clear_current_dataset_context()


def build_participants_export_filename() -> str:
    """Return a friendly CSV filename based on the active dataset, if any."""
    context = get_current_dataset_context()
    if context is None:
        return "participants.csv"
    slug = re.sub(r"[^a-z0-9]+", "-", context["dataset_name"].strip().lower()).strip("-")
    return f"{slug or 'participants'}.csv"


def load_sample_dataset() -> None:
    """Replace current data with the bundled sample dataset."""
    LOG_INFO("clearing existing participants and loading sample data")
    execute("DELETE FROM participants")
    clear_pickup_order_rules()
    clear_ride_together_rules()
    clear_current_dataset_context()
    participants, destination = get_sample_data()
    for order, participant in enumerate(participants, start=1):
        save_participant(
            participant_id=None,
            name=participant.name,
            address_text=None,
            location_name=participant.location.name,
            latitude=participant.location.latitude,
            longitude=participant.location.longitude,
            pickup_mode="home",
            has_car=participant.can_drive,
            fuel_type=participant.car.fuel_type if participant.car else None,
            consumption_l_per_100km=participant.car.consumption_l_per_100km if participant.car else None,
            total_seats=participant.car.total_seats if participant.car else None,
            habit_score=participant.habit_score,
            force_drive_alone=participant.force_drive_alone,
            priority_rank=order,
            active_in_trip=True,
        )
    save_destination(
        name=destination.name,
        latitude=destination.latitude,
        longitude=destination.longitude,
    )


def resolve_location_input(
    *,
    name: str,
    address_text: str | None,
    latitude: float | None,
    longitude: float | None,
    fallback_name: str,
) -> dict[str, Any]:
    """Resolve form input into a concrete location, using geocoding when needed."""
    LOG_DEBUG(f"resolving location — name={name!r}, address={address_text!r}, lat={latitude}, lon={longitude}")
    cleaned_name = name.strip()
    cleaned_address = (address_text or "").strip()
    if latitude is not None and longitude is not None:
        return {
            "name": cleaned_name or fallback_name,
            "latitude": latitude,
            "longitude": longitude,
            "address_text": cleaned_address or None,
            "source": "manual",
        }
    if cleaned_address:
        resolved = geocode_address(cleaned_address)
        return {
            "name": cleaned_name or resolved["display_name"],
            "latitude": resolved["latitude"],
            "longitude": resolved["longitude"],
            "address_text": cleaned_address,
            "source": resolved["source"],
        }
    raise ValueError("Enter either an address or both latitude and longitude.")


def build_domain_state() -> tuple[list[Participant], Location | None]:
    """Translate persisted rows into the existing domain model objects."""
    LOG_DEBUG("building domain state from database")
    participants: list[Participant] = []
    participant_rows = sorted(list_participants(include_inactive=False), key=lambda row: row["id"])
    for row in participant_rows:
        car = None
        if row["has_car"]:
            car = Car(
                fuel_type=row["fuel_type"],
                consumption_l_per_100km=row["consumption_l_per_100km"] or 0.0,
                total_seats=row["total_seats"],
                vehicle_type=row.get("vehicle_type", "car"),
            )
            if row.get("force_drive_alone"):
                car = Car(
                    fuel_type=row["fuel_type"],
                    consumption_l_per_100km=row["consumption_l_per_100km"] or 0.0,
                    total_seats=1,
                    vehicle_type=row.get("vehicle_type", "car"),
                )
        participants.append(
            Participant(
                name=row["name"],
                location=Location(row["location_name"], row["latitude"], row["longitude"]),
                home_location=Location(row["location_name"], row["latitude"], row["longitude"]),
                pickup_location=(
                    Location(
                        row["pickup_location_name"] or row["location_name"],
                        row["pickup_latitude"],
                        row["pickup_longitude"],
                    )
                    if row.get("pickup_latitude") is not None and row.get("pickup_longitude") is not None
                    else None
                ),
                pickup_mode=(row.get("pickup_mode") or "home"),
                pickup_flexible=bool(row.get("pickup_flexible", 1)),
                car=car,
                habit_score=row["habit_score"],
                force_drive_alone=bool(row.get("force_drive_alone", 0)),
                outbound_earliest_time_min=time_text_to_minutes(row.get("outbound_earliest_time")),
                outbound_latest_time_min=time_text_to_minutes(row.get("outbound_latest_time")),
                return_earliest_time_min=time_text_to_minutes(row.get("return_earliest_time")),
                return_latest_time_min=time_text_to_minutes(row.get("return_latest_time")),
            )
        )
    destination_data = get_destination()
    destination = None
    if destination_data:
        destination = Location(
            destination_data["name"],
            destination_data["latitude"],
            destination_data["longitude"],
            target_arrival_time_min=time_text_to_minutes(destination_data.get("target_arrival_time")),
        )
    return participants, destination


def current_workspace_snapshot() -> dict[str, Any]:
    """Return a JSON-serializable snapshot of the active planner workspace."""
    return {
        "participants": list_participants(),
        "destination": get_destination(),
        # settings intentionally isn't restored on import (see load_workspace_snapshot) —
        # it would import a stranger's password hash and public URLs. Kept here only
        # because this snapshot also backs the auto workspace-backup/restore feature.
        "settings": get_app_settings(),
        "pickup_order_rules": get_pickup_order_rules(),
        "ride_together_rules": get_ride_together_rules(),
    }


def current_workspace_key() -> str:
    """Return a stable hash for the current live workspace and planner settings."""
    payload = json.dumps(
        {
            "cache_version": OPTIMIZATION_CACHE_VERSION,
            "workspace": current_workspace_snapshot(),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def backup_current_workspace(reason: str) -> None:
    """Persist a lightweight safety backup before destructive workspace restores."""
    LOG_INFO(f"creating workspace backup — reason: {reason}")
    set_setting(
        "workspace_backup",
        {
            "reason": reason,
            "snapshot": current_workspace_snapshot(),
        },
    )


def get_workspace_backup() -> dict[str, Any] | None:
    """Return the last automatic workspace backup, if any."""
    return get_setting("workspace_backup", None)


def restore_workspace_backup() -> None:
    """Restore the last automatic workspace backup into the live workspace."""
    LOG_INFO("restoring workspace from automatic backup")
    backup = get_workspace_backup()
    if not backup or "snapshot" not in backup:
        raise ValueError("No automatic workspace backup is available yet.")
    load_workspace_snapshot(backup["snapshot"])


def load_workspace_snapshot(snapshot: dict[str, Any]) -> None:
    """Replace current app state with a previously serialized snapshot."""
    LOG_INFO(f"loading workspace snapshot — {len(snapshot.get('participants', []))} participants")
    execute("DELETE FROM participants")
    clear_current_dataset_context()
    for participant in snapshot.get("participants", []):
        save_participant(
            participant_id=None,
            name=participant["name"],
            address_text=participant.get("address_text"),
            location_name=participant["location_name"],
            latitude=participant["latitude"],
            longitude=participant["longitude"],
            pickup_mode=participant.get("pickup_mode", "home"),
            pickup_location_name=participant.get("pickup_location_name"),
            pickup_address_text=participant.get("pickup_address_text"),
            pickup_latitude=participant.get("pickup_latitude"),
            pickup_longitude=participant.get("pickup_longitude"),
            has_car=bool(participant["has_car"]),
            vehicle_type=participant.get("vehicle_type", "car"),
            fuel_type=participant.get("fuel_type"),
            consumption_l_per_100km=participant.get("consumption_l_per_100km"),
            total_seats=participant.get("total_seats"),
            habit_score=participant["habit_score"],
            role_tag=participant.get("role_tag", "standard"),
            availability_tag=participant.get("availability_tag", "available"),
            pickup_flexible=bool(participant.get("pickup_flexible", 1)),
            priority_rank=participant.get("priority_rank", 100),
            active_in_trip=bool(participant.get("active_in_trip", 1)),
            force_drive_alone=bool(participant.get("force_drive_alone", 0)),
            outbound_earliest_time=participant.get("outbound_earliest_time"),
            outbound_latest_time=participant.get("outbound_latest_time"),
            return_earliest_time=participant.get("return_earliest_time"),
            return_latest_time=participant.get("return_latest_time"),
        )
    destination = snapshot.get("destination")
    if destination:
        save_destination(
            name=destination["name"],
            latitude=destination["latitude"],
            longitude=destination["longitude"],
            address_text=destination.get("address_text"),
            target_arrival_time=destination.get("target_arrival_time"),
        )
    else:
        clear_destination()
    set_setting("pickup_order_rules", snapshot.get("pickup_order_rules", []))
    set_setting("ride_together_rules", snapshot.get("ride_together_rules", []))


def duplicate_last_trip() -> None:
    """Load the latest trip snapshot back into the active workspace."""
    LOG_INFO("duplicating latest trip from history")
    row = fetch_one(
        """
        SELECT snapshot_json
        FROM trip_history
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """
    )
    if row is None:
        raise ValueError("No saved trip history to duplicate.")
    backup_current_workspace("duplicate_last_trip")
    load_workspace_snapshot(json.loads(row["snapshot_json"]))


def restore_trip_history_entry(trip_id: int) -> None:
    """Restore a specific history entry into the live workspace."""
    row = fetch_one("SELECT snapshot_json FROM trip_history WHERE id = ?", (trip_id,))
    if row is None:
        raise ValueError("Trip history entry not found.")
    backup_current_workspace(f"restore_trip_history_entry:{trip_id}")
    load_workspace_snapshot(json.loads(row["snapshot_json"]))


def validate_ready_state(
    participants: list[Participant], destination: Location | None
) -> None:
    """Validate that the user entered enough information to run the algorithms."""
    if len(participants) < 2:
        raise ValueError("Add at least two participants before running the planner.")
    if destination is None:
        raise ValueError("Set a destination before running the planner.")
    if not any(participant.can_drive for participant in participants):
        raise ValueError("At least one participant must have a car.")


def serialize_optimization(payload: dict[str, Any]) -> dict[str, Any]:
    """Convert optimization objects into JSON-friendly data."""
    def serialize_assignment(assignment) -> dict[str, Any]:
        return {
            "driver_index": assignment.driver_index,
            "passenger_indices": assignment.passenger_indices,
            "route_nodes": assignment.route_nodes,
            "route_distance_km": assignment.route_distance_km,
            "route_cost_eur": assignment.route_cost_eur,
            "cost_per_person_eur": assignment.cost_per_person_eur,
            "route_duration_min": assignment.route_duration_min,
            "fuel_cost_eur": assignment.fuel_cost_eur,
            "toll_cost_eur": assignment.toll_cost_eur,
            "outbound_departure_time": assignment.outbound_departure_time,
            "destination_arrival_time": assignment.destination_arrival_time,
            "return_departure_time": assignment.return_departure_time,
            "pickup_schedule": assignment.pickup_schedule,
            "time_window_violation_min": assignment.time_window_violation_min,
        }

    def serialize_trip_result(result) -> dict[str, Any]:
        return {
            "driver_set_name": result.driver_set_name,
            "fitness": result.fitness,
            "total_distance_km": result.total_distance_km,
            "total_cost_eur": result.total_cost_eur,
            "plan_preference_used": result.plan_preference_used,
            "plan_variant": getattr(result, "plan_variant", "direct"),
            "meetup_total_self_transfer_km": getattr(result, "meetup_total_self_transfer_km", 0.0),
            "meetup_instructions": list(getattr(result, "meetup_instructions", [])),
            "meetup_pickup_locations": dict(getattr(result, "meetup_pickup_locations", {})),
            "return_total_distance_km": result.return_total_distance_km,
            "return_total_cost_eur": result.return_total_cost_eur,
            "max_route_distance_km": result.max_route_distance_km,
            "route_distance_spread_km": result.route_distance_spread_km,
            "max_route_duration_min": result.max_route_duration_min,
            "route_duration_spread_min": result.route_duration_spread_min,
            "max_detour_ratio": result.max_detour_ratio,
            "assignments": [serialize_assignment(assignment) for assignment in result.assignments],
            "total_time_window_violation_min": result.total_time_window_violation_min,
            "return_total_time_window_violation_min": result.return_total_time_window_violation_min,
            "solver_used": getattr(result, "solver_used", "local_search"),
            "return_assignments": [serialize_assignment(assignment) for assignment in result.return_assignments],
            "unassigned_passenger_indices": result.unassigned_passenger_indices,
        }

    return {
        "scores": [_serialize_driver_score(score) for score in payload["scores"]],
        "driver_sets": [asdict(driver_set) for driver_set in payload["driver_sets"]],
        "participants": [asdict(participant) for participant in payload["participants"]],
        "destination": asdict(payload["destination"]),
        "best_result": serialize_trip_result(payload["best_result"]) if payload["best_result"] else None,
        "trip_results": [serialize_trip_result(result) for result in payload["trip_results"]],
        "best_result_reasons": payload.get("best_result_reasons", []),
        "plan_preference": payload.get("plan_preference", "efficiency"),
        "fuel_prices": payload.get("fuel_prices", {}),
        "impact": payload.get("impact"),
    }


def cache_selection_result(payload: dict[str, Any]) -> str:
    """Store the latest driver-selection output for the current workspace."""
    workspace_key = current_workspace_key()
    LOG_DEBUG(f"caching selection result — workspace_key={workspace_key[:12]}")
    set_setting(
        "selection_cache",
        {
            "workspace_key": workspace_key,
            "payload": {
                "scores": [_serialize_driver_score(score) for score in payload["scores"]],
                "driver_sets": [asdict(driver_set) for driver_set in payload["driver_sets"]],
                "participants": [asdict(participant) for participant in payload["participants"]],
                "destination": asdict(payload["destination"]),
            },
        },
    )
    return workspace_key


def get_cached_selection_result() -> dict[str, Any] | None:
    """Return the latest cached driver-selection result if it still matches the workspace."""
    cached = get_setting("selection_cache", None)
    if not isinstance(cached, dict):
        return None
    if cached.get("workspace_key") != current_workspace_key():
        return None
    payload = cached.get("payload")
    return payload if isinstance(payload, dict) else None


def cache_optimization_result(payload: dict[str, Any]) -> str:
    """Store the latest optimization output for the current workspace."""
    workspace_key = current_workspace_key()
    LOG_DEBUG(f"caching optimization result — workspace_key={workspace_key[:12]}")
    set_setting(
        "optimization_cache",
        {
            "workspace_key": workspace_key,
            "payload": serialize_optimization(payload),
        },
    )
    return workspace_key


def get_cached_optimization_result() -> dict[str, Any] | None:
    """Return the latest cached optimization result if it still matches the workspace."""
    cached = get_setting("optimization_cache", None)
    if not isinstance(cached, dict):
        return None
    if cached.get("workspace_key") != current_workspace_key():
        return None
    payload = cached.get("payload")
    return payload if isinstance(payload, dict) else None
