"""Driver selection, optimization, and map payloads."""

from __future__ import annotations

import logging
from dataclasses import asdict
from itertools import product
from time import perf_counter
from statistics import median
from typing import Any

from core.logging_config import LOG_INFO, LOG_DEBUG
from core.apca import APCAAlgorithm
from core.driver_selection import DriverSelector
from core.models import Location, Participant, RouteAssignment
from core.utils import haversine_distance
from core.config import CO2_KG_PER_KM, COST_PER_KM, DEFAULT_SOLVER, ESTIMATED_AVERAGE_SPEED_KMH, RETURN_PLAN_DISTANCE_WEIGHT

from app.costs import estimate_trip_costs, get_live_fuel_prices
from app.mapping import build_route_matrix, build_route_preview
from app.services.common import (
    build_domain_state,
    cache_optimization_result,
    get_cached_optimization_result,
    get_pickup_order_rules,
    get_ride_together_rules,
    validate_ready_state,
)
from app.services.participants import list_participants
from app.services.meetup_spots import list_saved_meetup_spots
from app.services.settings import get_app_settings
from app.time_utils import format_minutes_as_time
from app.time_utils import time_text_to_minutes

logger = logging.getLogger(__name__)


def _read_value(item, key: str):
    """Read a field from either a dataclass-like object or a dict payload."""
    if isinstance(item, dict):
        return item.get(key)
    return getattr(item, key)


def trip_result_rank_key(trip_result) -> tuple[float, int, float, float, float]:
    """Rank trip results by fitness first, then prefer shorter and cheaper complete plans."""
    return (
        trip_result.fitness,
        -len(trip_result.unassigned_passenger_indices),
        -trip_result.total_time_window_violation_min,
        -trip_result.return_total_time_window_violation_min,
        -trip_result.total_distance_km,
        -(trip_result.total_cost_eur + trip_result.return_total_cost_eur),
    )


def normalize_plan_preference(value: str | None) -> str:
    """Normalize the stored optimization preference to a supported value."""
    return "detour_aware" if value in {"balanced_load", "detour_aware"} else "efficiency"


def detour_aware_rank_key(trip_result) -> tuple[float, float, float, float, float, float, float, float, int, float]:
    """Rank results by worst-route km first, then by balanced km, then by relative detour and total overhead."""
    return (
        -len(trip_result.unassigned_passenger_indices),
        -trip_result.total_time_window_violation_min,
        -trip_result.return_total_time_window_violation_min,
        -trip_result.max_route_distance_km,
        -trip_result.route_distance_spread_km,
        -trip_result.max_detour_ratio,
        -trip_result.total_distance_km,
        -trip_result.max_route_duration_min,
        -trip_result.route_duration_spread_min,
        -len(trip_result.assignments),
        -(trip_result.total_cost_eur + trip_result.return_total_cost_eur),
    )


def select_best_result(trip_results, plan_preference: str):
    """Select the winning result according to the current optimization preference."""
    normalized_preference = normalize_plan_preference(plan_preference)
    if not trip_results:
        return None
    rank_key = detour_aware_rank_key if normalized_preference == "detour_aware" else trip_result_rank_key
    return max(trip_results, key=rank_key)


def _participant_route_location(participant: Participant, participant_index: int, driver_indices: set[int]) -> Location:
    """Return the location used for routing for a participant in this driver-set context."""

    if participant_index in driver_indices:
        return participant.home_location or participant.location
    return participant.effective_pickup_location


def _participants_for_selection(participants: list[Participant]) -> list[Participant]:
    """Return participants for driver selection, keeping potential drivers at home and passengers at pickup points."""

    prepared: list[Participant] = []
    for participant in participants:
        selection_location = (participant.home_location or participant.location) if participant.can_drive else participant.effective_pickup_location
        prepared.append(
            Participant(
                name=participant.name,
                location=selection_location,
                home_location=participant.home_location or participant.location,
                pickup_location=participant.pickup_location,
                pickup_mode=participant.pickup_mode,
                pickup_flexible=participant.pickup_flexible,
                car=participant.car,
                habit_score=participant.habit_score,
                force_drive_alone=participant.force_drive_alone,
                outbound_earliest_time_min=participant.outbound_earliest_time_min,
                outbound_latest_time_min=participant.outbound_latest_time_min,
                return_earliest_time_min=participant.return_earliest_time_min,
                return_latest_time_min=participant.return_latest_time_min,
            )
        )
    return prepared


def _participants_for_driver_set(participants: list[Participant], driver_indices: list[int]) -> list[Participant]:
    """Return per-driver-set participants where passenger locations use their effective pickup locations."""

    driver_index_set = set(driver_indices)
    routed_participants: list[Participant] = []
    for participant_index, participant in enumerate(participants):
        routed_participants.append(
            Participant(
                name=participant.name,
                location=_participant_route_location(participant, participant_index, driver_index_set),
                home_location=participant.home_location or participant.location,
                pickup_location=participant.pickup_location,
                pickup_mode=participant.pickup_mode,
                pickup_flexible=participant.pickup_flexible,
                car=participant.car,
                habit_score=participant.habit_score,
                force_drive_alone=participant.force_drive_alone,
                outbound_earliest_time_min=participant.outbound_earliest_time_min,
                outbound_latest_time_min=participant.outbound_latest_time_min,
                return_earliest_time_min=participant.return_earliest_time_min,
                return_latest_time_min=participant.return_latest_time_min,
            )
        )
    return routed_participants


def _candidate_meetup_choices(
    participants: list[Participant],
    driver_indices: list[int],
    meetup_spots: list[dict[str, Any]],
    *,
    max_self_transfer_km: float,
) -> list[tuple[int, list[dict[str, Any]]]]:
    """Return bounded meetup options for flexible passengers."""

    choices: list[tuple[int, list[dict[str, Any]]]] = []
    driver_index_set = set(driver_indices)
    destination_bias_origin = [
        participants[index].home_location or participants[index].location
        for index in driver_indices
    ]
    for participant_index, participant in enumerate(participants):
        if participant_index in driver_index_set:
            continue
        if participant.pickup_mode == "meetup":
            continue
        if participant.can_drive:
            continue
        home_location = participant.home_location or participant.location
        nearby: list[tuple[float, dict[str, Any]]] = []
        for spot in meetup_spots:
            distance_from_home = haversine_distance(
                home_location,
                Location(str(spot["name"]), float(spot["latitude"]), float(spot["longitude"])),
            )
            if distance_from_home > max_self_transfer_km:
                continue
            proximity_score = distance_from_home
            if destination_bias_origin:
                proximity_score += min(
                    haversine_distance(
                        Location(str(spot["name"]), float(spot["latitude"]), float(spot["longitude"])),
                        origin,
                    )
                    for origin in destination_bias_origin
                ) * 0.08
            nearby.append((proximity_score, spot))
        if not participant.pickup_flexible or not nearby:
            continue
        nearby.sort(key=lambda item: item[0])
        choices.append((participant_index, [spot for _score, spot in nearby[:2]]))
    return choices


def _build_meetup_variant(
    participants: list[Participant],
    meetup_assignments: dict[int, dict[str, Any]],
) -> tuple[list[Participant], list[str], float, dict[int, dict[str, Any]]]:
    """Apply suggested meetup spots to a participant list."""

    updated: list[Participant] = []
    instructions: list[str] = []
    total_self_transfer_km = 0.0
    pickup_locations: dict[int, dict[str, Any]] = {}
    for participant_index, participant in enumerate(participants):
        assigned_spot = meetup_assignments.get(participant_index)
        if not assigned_spot:
            updated.append(participant)
            continue
        home_location = participant.home_location or participant.location
        pickup_location = Location(
            str(assigned_spot["name"]),
            float(assigned_spot["latitude"]),
            float(assigned_spot["longitude"]),
        )
        self_transfer_km = haversine_distance(home_location, pickup_location)
        total_self_transfer_km += self_transfer_km
        spot_tags: list[str] = []
        if assigned_spot.get("is_free_parking"):
            spot_tags.append("free parking")
        if assigned_spot.get("has_ev_charging"):
            spot_tags.append("EV charging")
        tag_text = f" ({', '.join(spot_tags)})" if spot_tags else ""
        instructions.append(
            f"{participant.name} moves to {pickup_location.name}{tag_text} before pickup ({self_transfer_km:.1f} km)."
        )
        pickup_locations[participant_index] = {
            "name": pickup_location.name,
            "latitude": pickup_location.latitude,
            "longitude": pickup_location.longitude,
        }
        updated.append(
            Participant(
                name=participant.name,
                location=participant.location,
                home_location=home_location,
                pickup_location=pickup_location,
                pickup_mode="meetup",
                pickup_flexible=participant.pickup_flexible,
                car=participant.car,
                habit_score=participant.habit_score,
                force_drive_alone=participant.force_drive_alone,
                outbound_earliest_time_min=participant.outbound_earliest_time_min,
                outbound_latest_time_min=participant.outbound_latest_time_min,
                return_earliest_time_min=participant.return_earliest_time_min,
                return_latest_time_min=participant.return_latest_time_min,
            )
        )
    return updated, instructions, total_self_transfer_km, pickup_locations


def _meetup_variant_score(trip_result) -> tuple[float, float, float, int]:
    """Rank meetup variants by system efficiency plus self-transfer burden."""

    adjusted_distance = trip_result.total_distance_km + (trip_result.meetup_total_self_transfer_km * 1.35)
    return (
        -len(trip_result.unassigned_passenger_indices),
        -trip_result.total_time_window_violation_min,
        -adjusted_distance,
        -len(trip_result.assignments),
    )


def _format_time_offset(base_time_text: str | None, elapsed_min: float) -> str | None:
    """Format a HH:MM plus elapsed minutes."""
    if not base_time_text:
        return None
    base_minutes = time_text_to_minutes(base_time_text)
    if base_minutes is None:
        return None
    absolute = base_minutes + elapsed_min
    return format_minutes_as_time(absolute)


def _hydrate_missing_route_timings(assignments, participants, destination) -> None:
    """Infer a common outbound/return timeline for routes that lack explicit time anchors."""
    known_arrivals = [
        time_text_to_minutes(assignment.destination_arrival_time)
        for assignment in assignments
        if assignment.destination_arrival_time
    ]
    known_returns = [
        time_text_to_minutes(assignment.return_departure_time)
        for assignment in assignments
        if assignment.return_departure_time
    ]
    destination_target = getattr(destination, "target_arrival_time_min", None)
    target_arrival_min = median(known_arrivals) if known_arrivals else (
        destination_target if destination_target is not None else (20 * 60)
    )
    target_return_min = median(known_returns) if known_returns else None

    for assignment in assignments:
        if assignment.outbound_departure_time and assignment.destination_arrival_time:
            if assignment.return_departure_time is None and target_return_min is not None:
                assignment.return_departure_time = format_minutes_as_time(target_return_min)
            continue
        driver = participants[assignment.driver_index]
        stops = [driver.location]
        for passenger_index in assignment.passenger_indices:
            stops.append(participants[passenger_index].location)
        stops.append(destination)
        preview = build_route_preview(stops)
        duration_min = preview.get("duration_min")
        leg_durations = preview.get("leg_durations_min") or []
        if duration_min is None:
            continue
        inferred_departure_min = max(0.0, target_arrival_min - duration_min)
        assignment.outbound_departure_time = format_minutes_as_time(inferred_departure_min)
        assignment.destination_arrival_time = format_minutes_as_time(inferred_departure_min + duration_min)
        assignment.pickup_schedule = []
        elapsed = 0.0
        for order, passenger_index in enumerate(assignment.passenger_indices, start=1):
            if order - 1 < len(leg_durations):
                elapsed += leg_durations[order - 1]
            assignment.pickup_schedule.append(
                f"{participants[passenger_index].name} at {format_minutes_as_time(inferred_departure_min + elapsed)}"
            )
        if assignment.return_departure_time is None and target_return_min is not None:
            assignment.return_departure_time = format_minutes_as_time(target_return_min)


def _apply_assignment_route_metrics(assignment, participants, destination) -> None:
    """Refresh route distance, duration, schedule, and costs from routed road data."""
    driver = participants[assignment.driver_index]
    stops = [driver.location]
    for passenger_index in assignment.passenger_indices:
        stops.append(participants[passenger_index].location)
    stops.append(destination)
    preview = build_route_preview(stops)
    assignment.route_distance_km = preview["distance_km"]
    timing_costs = estimate_trip_costs(
        preview["distance_km"],
        preview["duration_min"],
        driver.car,
        leg_distances_km=preview.get("leg_distances_km"),
        leg_durations_min=preview.get("leg_durations_min"),
    )
    assignment.route_duration_min = preview["duration_min"]
    assignment.fuel_cost_eur = timing_costs["fuel_eur"]
    assignment.toll_cost_eur = timing_costs["toll_eur"]
    assignment.route_cost_eur = timing_costs["total_eur"]
    travelers = 1 + len(assignment.passenger_indices)
    assignment.cost_per_person_eur = assignment.route_cost_eur / travelers if travelers else 0.0
    leg_durations = preview.get("leg_durations_min") or []
    pickup_schedule = []
    elapsed = 0.0
    for order, passenger_index in enumerate(assignment.passenger_indices, start=1):
        if order - 1 < len(leg_durations):
            elapsed += leg_durations[order - 1]
        pickup_time = _format_time_offset(assignment.outbound_departure_time, elapsed)
        passenger_name = participants[passenger_index].name
        pickup_schedule.append(
            f"{passenger_name} at {pickup_time}" if pickup_time else f"{passenger_name} pickup"
        )
    assignment.pickup_schedule = pickup_schedule
    if assignment.outbound_departure_time:
        assignment.destination_arrival_time = _format_time_offset(
            assignment.outbound_departure_time,
            preview["duration_min"] or 0.0,
        )


def _apply_return_assignment_route_metrics(assignment, participants, destination) -> None:
    """Refresh a separate return assignment from routed road data."""
    driver = participants[assignment.driver_index]
    stops = [destination]
    for passenger_index in assignment.passenger_indices:
        stops.append(participants[passenger_index].location)
    stops.append(driver.location)
    preview = build_route_preview(stops)
    assignment.route_distance_km = preview["distance_km"]
    cost_breakdown = estimate_trip_costs(
        preview["distance_km"],
        preview["duration_min"],
        driver.car,
        leg_distances_km=preview.get("leg_distances_km"),
        leg_durations_min=preview.get("leg_durations_min"),
    )
    assignment.route_duration_min = preview["duration_min"]
    assignment.fuel_cost_eur = cost_breakdown["fuel_eur"]
    assignment.toll_cost_eur = cost_breakdown["toll_eur"]
    assignment.route_cost_eur = cost_breakdown["total_eur"]
    travelers = 1 + len(assignment.passenger_indices)
    assignment.cost_per_person_eur = assignment.route_cost_eur / travelers if travelers else 0.0
    leg_durations = preview.get("leg_durations_min") or []
    elapsed = 0.0
    dropoff_schedule = []
    for order, passenger_index in enumerate(assignment.passenger_indices, start=1):
        if order - 1 < len(leg_durations):
            elapsed += leg_durations[order - 1]
        dropoff_time = _format_time_offset(assignment.return_departure_time, elapsed)
        passenger_name = participants[passenger_index].name
        dropoff_schedule.append(
            f"Drop off {passenger_name} at {dropoff_time}" if dropoff_time else f"Drop off {passenger_name}"
        )
    assignment.pickup_schedule = dropoff_schedule


def _best_common_return_departure(participants, member_indices: list[int]) -> tuple[float | None, float]:
    """Pick a common destination departure time that best fits the group's return windows."""
    candidates: list[float] = []
    for participant_index in member_indices:
        participant = participants[participant_index]
        for value in (participant.return_earliest_time_min, participant.return_latest_time_min):
            if value is not None:
                candidates.append(float(value))
    if not candidates:
        return None, 0.0
    unique_candidates = sorted({max(0.0, min(1439.0, candidate)) for candidate in candidates})

    def violation(departure_time: float) -> float:
        total = 0.0
        for participant_index in member_indices:
            participant = participants[participant_index]
            if participant.return_earliest_time_min is not None and departure_time < participant.return_earliest_time_min:
                total += participant.return_earliest_time_min - departure_time
            if participant.return_latest_time_min is not None and departure_time > participant.return_latest_time_min:
                total += departure_time - participant.return_latest_time_min
        return total

    best_candidate = unique_candidates[0]
    best_violation = violation(best_candidate)
    for candidate in unique_candidates[1:]:
        candidate_violation = violation(candidate)
        if candidate_violation < best_violation - 1e-6 or (
            abs(candidate_violation - best_violation) < 1e-6 and candidate < best_candidate
        ):
            best_candidate = candidate
            best_violation = candidate_violation
    return best_candidate, best_violation


def _nearest_neighbor_route(start_location, stop_indices: list[int], end_location, participants) -> tuple[list[int], float]:
    """Build a simple return route using nearest-neighbor dropoffs."""
    remaining = list(stop_indices)
    ordered: list[int] = []
    total_distance = 0.0
    current_location = start_location
    while remaining:
        next_index = min(
            remaining,
            key=lambda participant_index: haversine_distance(current_location, participants[participant_index].location),
        )
        total_distance += haversine_distance(current_location, participants[next_index].location)
        current_location = participants[next_index].location
        ordered.append(next_index)
        remaining.remove(next_index)
    total_distance += haversine_distance(current_location, end_location)
    return ordered, total_distance


def build_return_plan(trip_result, participants, destination) -> None:
    """Create a separate return grouping plan that can redistribute passengers across the chosen drivers."""
    driver_indices = [assignment.driver_index for assignment in trip_result.assignments]
    if not driver_indices:
        return

    capacity_by_driver = {
        driver_index: participants[driver_index].car.available_seats
        for driver_index in driver_indices
    }
    groups = {driver_index: [] for driver_index in driver_indices}
    passenger_indices = [index for index in range(len(participants)) if index not in driver_indices]

    def score_group(driver_index: int, candidate_passengers: list[int]) -> tuple[float, float, float]:
        member_indices = [driver_index] + candidate_passengers
        departure_time, violation = _best_common_return_departure(participants, member_indices)
        ordered_dropoffs, route_distance = _nearest_neighbor_route(
            destination,
            candidate_passengers,
            participants[driver_index].location,
            participants,
        )
        return_score = violation + (route_distance * RETURN_PLAN_DISTANCE_WEIGHT)
        return return_score, departure_time or 0.0, route_distance

    passenger_indices.sort(
        key=lambda participant_index: (
            participants[participant_index].return_earliest_time_min is None
            and participants[participant_index].return_latest_time_min is None,
            participants[participant_index].return_latest_time_min or 1440,
            participants[participant_index].return_earliest_time_min or 0,
        )
    )

    for passenger_index in passenger_indices:
        best_driver = None
        best_score = None
        for driver_index in driver_indices:
            if len(groups[driver_index]) >= capacity_by_driver[driver_index]:
                continue
            score = score_group(driver_index, groups[driver_index] + [passenger_index])
            if best_score is None or score < best_score:
                best_driver = driver_index
                best_score = score
        if best_driver is not None:
            groups[best_driver].append(passenger_index)

    return_assignments: list[RouteAssignment] = []
    total_distance = 0.0
    total_cost = 0.0
    total_violation = 0.0
    for driver_index in driver_indices:
        assigned_passengers = groups[driver_index]
        ordered_dropoffs, route_distance = _nearest_neighbor_route(
            destination,
            assigned_passengers,
            participants[driver_index].location,
            participants,
        )
        departure_time, violation = _best_common_return_departure(
            participants,
            [driver_index] + ordered_dropoffs,
        )
        total_distance += route_distance
        total_violation += violation
        cost_breakdown = estimate_trip_costs(route_distance, None, participants[driver_index].car)
        route_cost = cost_breakdown["total_eur"]
        total_cost += route_cost
        travelers = 1 + len(ordered_dropoffs)
        pickup_schedule = []
        current_time = departure_time
        current_location = destination
        for passenger_index in ordered_dropoffs:
            if current_time is not None:
                current_time += (
                    haversine_distance(current_location, participants[passenger_index].location)
                    / ESTIMATED_AVERAGE_SPEED_KMH
                    * 60.0
                )
                pickup_schedule.append(
                    f"Drop off {participants[passenger_index].name} at {format_minutes_as_time(current_time)}"
                )
            current_location = participants[passenger_index].location
        route_nodes = [f"Return from {destination.name}"]
        route_nodes.extend(
            f"Drop off {participants[passenger_index].name} ({participants[passenger_index].location.name})"
            for passenger_index in ordered_dropoffs
        )
        route_nodes.append(f"Driver home {participants[driver_index].location.name}")
        return_assignments.append(
            RouteAssignment(
                driver_index=driver_index,
                passenger_indices=ordered_dropoffs,
                route_nodes=route_nodes,
                route_distance_km=route_distance,
                route_cost_eur=route_cost,
                cost_per_person_eur=route_cost / travelers if travelers else 0.0,
                fuel_cost_eur=cost_breakdown["fuel_eur"],
                toll_cost_eur=cost_breakdown["toll_eur"],
                return_departure_time=format_minutes_as_time(departure_time),
                pickup_schedule=pickup_schedule,
                time_window_violation_min=violation,
            )
        )

    trip_result.return_assignments = return_assignments
    trip_result.return_total_distance_km = total_distance
    trip_result.return_total_cost_eur = total_cost
    trip_result.return_total_time_window_violation_min = total_violation


def _update_trip_balance_metrics(trip_result, participants, destination) -> None:
    """Populate fairness metrics used by the balanced-load ranking mode."""
    route_distances = [assignment.route_distance_km for assignment in trip_result.assignments]
    route_durations = [
        assignment.route_duration_min
        for assignment in trip_result.assignments
        if assignment.route_duration_min is not None
    ]
    detour_ratios: list[float] = []
    for assignment in trip_result.assignments:
        driver = participants[assignment.driver_index]
        direct_distance = haversine_distance(driver.location, destination)
        denominator = max(direct_distance, 1.0)
        detour_ratios.append(assignment.route_distance_km / denominator)

    trip_result.max_route_distance_km = max(route_distances, default=0.0)
    trip_result.route_distance_spread_km = (
        max(route_distances) - min(route_distances) if len(route_distances) >= 2 else 0.0
    )
    trip_result.max_route_duration_min = max(route_durations, default=0.0)
    trip_result.route_duration_spread_min = (
        max(route_durations) - min(route_durations) if len(route_durations) >= 2 else 0.0
    )
    trip_result.max_detour_ratio = max(detour_ratios, default=0.0)


def explain_best_result(best_result, all_results, plan_preference: str = "efficiency") -> list[str]:
    """Return short user-facing reasons for why the leading plan won."""
    if best_result is None or not all_results:
        return []
    normalized_preference = normalize_plan_preference(plan_preference)
    if normalize_plan_preference(getattr(best_result, "plan_preference_used", None)) == "detour_aware":
        normalized_preference = "detour_aware"
    elif getattr(best_result, "driver_set_name", "") == "Detour-Aware":
        normalized_preference = "detour_aware"
    if normalized_preference == "detour_aware":
        reasons = ["Detour-Aware mode is active, so keeping each driver's km burden low matters more than squeezing into the fewest cars."]
    else:
        reasons = [f"Highest overall fitness score: {best_result.fitness:.3f}."]
    lowest_distance = min(result.total_distance_km for result in all_results)
    lowest_cost = min(result.total_cost_eur for result in all_results)
    fewest_cars = min(len(result.assignments) for result in all_results)
    lowest_time_violation = min(result.total_time_window_violation_min for result in all_results)
    lowest_max_route_distance = min(result.max_route_distance_km for result in all_results)
    lowest_max_route_duration = min(result.max_route_duration_min for result in all_results)
    lowest_route_spread = min(result.route_duration_spread_min for result in all_results)
    if abs(best_result.total_distance_km - lowest_distance) < 1e-6:
        reasons.append("It also gives the shortest total driving distance among the tested driver sets.")
    if abs(best_result.max_route_distance_km - lowest_max_route_distance) < 1e-6 and best_result.max_route_distance_km > 0:
        reasons.append(
            f"It keeps the longest single car to about {best_result.max_route_distance_km:.1f} km, the lowest among the tested plans."
        )
    if abs(best_result.total_cost_eur - lowest_cost) < 1e-6:
        reasons.append("It is tied for the lowest estimated total trip cost.")
    if len(best_result.assignments) == fewest_cars:
        reasons.append("It uses the minimum number of active cars needed to seat the selected group.")
    elif normalized_preference == "detour_aware":
        reasons.append("It keeps the worst single-car route shorter in km, even though it uses an extra car.")
    if abs(best_result.total_time_window_violation_min - lowest_time_violation) < 1e-6:
        if lowest_time_violation <= 1e-6:
            reasons.append("It satisfies the saved participant time preferences without any timing violations.")
        else:
            reasons.append("It produces the smallest total timing mismatch against the saved participant preferences.")
    if abs(best_result.max_route_duration_min - lowest_max_route_duration) < 1e-6 and best_result.max_route_duration_min > 0:
        reasons.append(
            f"It keeps the longest outbound route to about {best_result.max_route_duration_min:.0f} minutes, the lowest among the tested plans."
        )
    if abs(best_result.route_duration_spread_min - lowest_route_spread) < 1e-6 and best_result.route_duration_spread_min > 0:
        reasons.append(
            f"It gives the most even route split, with only about {best_result.route_duration_spread_min:.0f} minutes between the shortest and longest car."
        )
    if best_result.return_assignments:
        reasons.append(
            "Return planning is enabled, so the same drivers can regroup passengers separately for the way back."
        )
    if getattr(best_result, "plan_variant", "direct") == "meetup":
        reasons.append(
            f"It is the strongest meetup-pooling option, with about {best_result.meetup_total_self_transfer_km:.1f} km of passenger self-transfer."
        )
    return reasons


def _optimize_single_driver_set(
    participants: list[Participant],
    destination: Location,
    driver_set,
    *,
    defaults: dict[str, Any],
    pickup_order_rules: list[tuple[int, int]],
    ride_together_rules: list[tuple[int, int]],
    plan_return_separately: bool,
    plan_variant: str = "direct",
    meetup_instructions: list[str] | None = None,
    meetup_total_self_transfer_km: float = 0.0,
    meetup_pickup_locations: dict[int, dict[str, Any]] | None = None,
):
    """Optimize one driver set against a participant list."""

    driver_set_preference = "detour_aware" if driver_set.name == "Detour-Aware" else "efficiency"
    routed_participants = _participants_for_driver_set(participants, driver_set.driver_indices)
    ordered_locations = [routed_participants[index].location for index in driver_set.driver_indices]
    ordered_locations.extend(
        routed_participants[index].location for index in range(len(routed_participants)) if index not in driver_set.driver_indices
    )
    ordered_locations.append(destination)
    route_matrix = build_route_matrix(ordered_locations)
    apca = APCAAlgorithm(
        routed_participants,
        driver_set.driver_indices,
        destination,
        num_ants=defaults["num_ants"],
        num_iterations=defaults["num_iterations"],
        alpha=defaults["alpha"],
        beta=defaults["beta"],
        rho=defaults["rho"],
        pickup_order_rules=pickup_order_rules,
        ride_together_rules=ride_together_rules,
        road_distance_matrix_km=route_matrix["distance_km"],
        road_duration_matrix_min=route_matrix["duration_min"],
        plan_preference=driver_set_preference,
        solver=defaults.get("algorithm", DEFAULT_SOLVER),
    )
    trip_result = apca.solve()
    trip_result.driver_set_name = driver_set.name if plan_variant == "direct" else f"{driver_set.name} + Meetup Pooling"
    trip_result.plan_preference_used = driver_set_preference
    trip_result.plan_variant = plan_variant
    trip_result.meetup_instructions = list(meetup_instructions or [])
    trip_result.meetup_total_self_transfer_km = meetup_total_self_transfer_km
    trip_result.meetup_pickup_locations = dict(meetup_pickup_locations or {})
    for assignment in trip_result.assignments:
        _apply_assignment_route_metrics(assignment, routed_participants, destination)
    _hydrate_missing_route_timings(trip_result.assignments, routed_participants, destination)
    trip_result.total_distance_km = sum(assignment.route_distance_km for assignment in trip_result.assignments)
    trip_result.total_cost_eur = sum(assignment.route_cost_eur for assignment in trip_result.assignments)
    if plan_return_separately:
        build_return_plan(trip_result, routed_participants, destination)
        for assignment in trip_result.return_assignments:
            _apply_return_assignment_route_metrics(assignment, routed_participants, destination)
        trip_result.return_total_distance_km = sum(assignment.route_distance_km for assignment in trip_result.return_assignments)
        trip_result.return_total_cost_eur = sum(assignment.route_cost_eur for assignment in trip_result.return_assignments)
    _update_trip_balance_metrics(trip_result, routed_participants, destination)
    return trip_result


def _best_meetup_variant(
    participants: list[Participant],
    destination: Location,
    driver_sets,
    *,
    defaults: dict[str, Any],
    pickup_order_rules: list[tuple[int, int]],
    ride_together_rules: list[tuple[int, int]],
    plan_return_separately: bool,
):
    """Return the strongest meetup-pooling variant across the candidate driver sets."""

    meetup_spots = list_saved_meetup_spots()
    if not meetup_spots or not defaults.get("allow_meetup_pooling", True):
        return None
    max_self_transfer_km = float(defaults.get("max_meetup_self_transfer_km", 3.0) or 3.0)
    best_variant = None
    best_rank = None
    for driver_set in driver_sets:
        candidate_choices = _candidate_meetup_choices(
            participants,
            driver_set.driver_indices,
            meetup_spots,
            max_self_transfer_km=max_self_transfer_km,
        )
        if not candidate_choices:
            continue
        option_sets = []
        for participant_index, spots in candidate_choices[:4]:
            option_sets.append([(participant_index, None)] + [(participant_index, spot) for spot in spots])
        for combination in product(*option_sets):
            assignment_map = {participant_index: spot for participant_index, spot in combination if spot is not None}
            if not assignment_map:
                continue
            meetup_participants, meetup_instructions, total_self_transfer_km, meetup_pickup_locations = _build_meetup_variant(participants, assignment_map)
            trip_result = _optimize_single_driver_set(
                meetup_participants,
                destination,
                driver_set,
                defaults=defaults,
                pickup_order_rules=pickup_order_rules,
                ride_together_rules=ride_together_rules,
                plan_return_separately=plan_return_separately,
                plan_variant="meetup",
                meetup_instructions=meetup_instructions,
                meetup_total_self_transfer_km=total_self_transfer_km,
                meetup_pickup_locations=meetup_pickup_locations,
            )
            rank = _meetup_variant_score(trip_result)
            if best_rank is None or rank > best_rank:
                best_variant = trip_result
                best_rank = rank
    return best_variant


def run_driver_selection() -> dict[str, Any]:
    """Compute driver scores and candidate driver sets for the current data."""
    LOG_INFO("starting driver selection")
    start = perf_counter()
    participants, destination = build_domain_state()
    validate_ready_state(participants, destination)
    LOG_DEBUG(f"domain state built — {len(participants)} participants, destination={destination}")
    selector = DriverSelector(_participants_for_selection(participants), destination)
    scores = selector.calculate_all_scores()
    driver_sets = selector.select_driver_sets()
    elapsed = perf_counter() - start
    LOG_INFO(f"driver selection completed in {elapsed:.3f}s — {len(driver_sets)} candidate sets")
    return {
        "scores": scores,
        "driver_sets": driver_sets,
        "participants": participants,
        "destination": destination,
    }


def build_impact_summary(participants, destination, best_result) -> dict[str, Any] | None:
    """Compare the optimized plan with everyone driving to the destination alone.

    Distances use the same haversine basis as the solver's reference distance,
    so saved km/cost/CO2 are conservative like-for-like estimates.
    """
    if best_result is None or not participants:
        return None
    default_cost_per_km = COST_PER_KM.get("gasoline", 0.10)
    baseline_km = 0.0
    baseline_cost_eur = 0.0
    for participant in participants:
        origin = _read_value(participant, "home_location") or _read_value(participant, "location")
        if isinstance(origin, dict):
            origin = Location(origin["name"], origin["latitude"], origin["longitude"])
        distance_km = haversine_distance(origin, destination)
        baseline_km += distance_km
        car = _read_value(participant, "car")
        fuel_type = (car.get("fuel_type") if isinstance(car, dict) else getattr(car, "fuel_type", None)) if car else None
        baseline_cost_eur += distance_km * COST_PER_KM.get(fuel_type, default_cost_per_km)
    plan_km = float(_read_value(best_result, "total_distance_km") or 0.0)
    plan_cost_eur = float(_read_value(best_result, "total_cost_eur") or 0.0)
    cars_used = len(_read_value(best_result, "assignments") or [])
    km_saved = max(baseline_km - plan_km, 0.0)
    return {
        "baseline_km": round(baseline_km, 1),
        "plan_km": round(plan_km, 1),
        "km_saved": round(km_saved, 1),
        "cost_saved_eur": round(max(baseline_cost_eur - plan_cost_eur, 0.0), 2),
        "co2_saved_kg": round(km_saved * CO2_KG_PER_KM, 1),
        "cars_saved": max(len(participants) - cars_used, 0),
    }


def run_optimization() -> dict[str, Any]:
    """Run driver selection followed by the route solver on each candidate set."""

    LOG_INFO("starting full optimization pipeline")
    start = perf_counter()
    selection = run_driver_selection()
    participants = selection["participants"]
    destination = selection["destination"]
    defaults = get_app_settings()["optimization"]
    plan_return_separately = bool(defaults.get("plan_return_separately"))
    active_rows_by_id = {
        row["id"]: row
        for row in sorted(list_participants(include_inactive=False), key=lambda row: row["id"])
    }
    domain_index_by_participant_id = {
        participant_id: index
        for index, participant_id in enumerate(active_rows_by_id)
    }
    pickup_order_rules = []
    for rule in get_pickup_order_rules():
        before_index = domain_index_by_participant_id.get(rule["before_id"])
        after_index = domain_index_by_participant_id.get(rule["after_id"])
        if before_index is None or after_index is None:
            continue
        pickup_order_rules.append((before_index, after_index))
    ride_together_rules = []
    for rule in get_ride_together_rules():
        first_index = domain_index_by_participant_id.get(rule["first_id"])
        second_index = domain_index_by_participant_id.get(rule["second_id"])
        if first_index is None or second_index is None or first_index == second_index:
            continue
        ride_together_rules.append(tuple(sorted((first_index, second_index))))
    fuel_prices = get_live_fuel_prices()
    results = []
    for driver_set in selection["driver_sets"]:
        LOG_INFO(f"optimising driver set '{driver_set.name}' - {len(driver_set.driver_indices)} drivers")
        trip_result = _optimize_single_driver_set(
            participants,
            destination,
            driver_set,
            defaults=defaults,
            pickup_order_rules=pickup_order_rules,
            ride_together_rules=ride_together_rules,
            plan_return_separately=plan_return_separately,
        )
        results.append(trip_result)
    meetup_result = _best_meetup_variant(
        participants,
        destination,
        selection["driver_sets"],
        defaults=defaults,
        pickup_order_rules=pickup_order_rules,
        ride_together_rules=ride_together_rules,
        plan_return_separately=plan_return_separately,
    )
    if meetup_result is not None:
        results.append(meetup_result)
    elapsed = perf_counter() - start
    LOG_INFO(f"full optimization completed in {elapsed:.3f}s - {len(results)} trip results")
    best_result = select_best_result(results, "efficiency")
    if best_result:
        LOG_INFO(f"best result: '{best_result.driver_set_name}' - fitness={best_result.fitness:.4f}, distance={best_result.total_distance_km:.2f}km, cost=EUR {best_result.total_cost_eur:.2f}")
    return {
        **selection,
        "trip_results": results,
        "best_result": best_result,
        "best_result_reasons": explain_best_result(best_result, results, best_result.driver_set_name if best_result else "efficiency"),
        "plan_preference": "efficiency",
        "destination_data": asdict(destination),
        "fuel_prices": fuel_prices,
        "impact": build_impact_summary(participants, destination, best_result),
    }


def run_sandbox_optimization(
    participants,
    destination,
    *,
    plan_return_separately: bool = False,
    plan_preference: str = "efficiency",
) -> dict[str, Any]:
    """Run the normal optimization pipeline on a transient participant pool."""

    validate_ready_state(participants, destination)
    defaults = get_app_settings()["optimization"]
    selector = DriverSelector(_participants_for_selection(participants), destination)
    scores = selector.calculate_all_scores()
    driver_sets = selector.select_driver_sets()
    fuel_prices = get_live_fuel_prices()
    results = []
    for driver_set in driver_sets:
        results.append(
            _optimize_single_driver_set(
                participants,
                destination,
                driver_set,
                defaults=defaults,
                pickup_order_rules=[],
                ride_together_rules=[],
                plan_return_separately=plan_return_separately,
            )
        )
    meetup_result = _best_meetup_variant(
        participants,
        destination,
        driver_sets,
        defaults=defaults,
        pickup_order_rules=[],
        ride_together_rules=[],
        plan_return_separately=plan_return_separately,
    )
    if meetup_result is not None:
        results.append(meetup_result)
    best_result = select_best_result(results, plan_preference)
    return {
        "scores": scores,
        "driver_sets": driver_sets,
        "participants": participants,
        "destination": destination,
        "trip_results": results,
        "best_result": best_result,
        "best_result_reasons": explain_best_result(best_result, results, plan_preference),
        "plan_preference": normalize_plan_preference(plan_preference),
        "destination_data": asdict(destination),
        "fuel_prices": fuel_prices,
    }


def build_map_payload(include_routes: bool = False) -> dict[str, Any]:
    """Build the frontend map payload for markers and optional optimized routes."""
    LOG_DEBUG(f"building map payload — include_routes={include_routes}")
    start = perf_counter()
    participants, destination = build_domain_state()
    participant_rows = list_participants(include_inactive=False)
    payload: dict[str, Any] = {
        "participants": [
            {
                "id": row["id"],
                "name": row["name"],
                "address_text": row["address_text"],
                "latitude": (
                    row["pickup_latitude"]
                    if not bool(row["has_car"]) and row.get("pickup_mode") == "meetup" and row.get("pickup_latitude") is not None
                    else row["latitude"]
                ),
                "longitude": (
                    row["pickup_longitude"]
                    if not bool(row["has_car"]) and row.get("pickup_mode") == "meetup" and row.get("pickup_longitude") is not None
                    else row["longitude"]
                ),
                "location_name": (
                    row["pickup_location_name"]
                    if not bool(row["has_car"]) and row.get("pickup_mode") == "meetup" and row.get("pickup_location_name")
                    else row["location_name"]
                ),
                "home_location_name": row["location_name"],
                "pickup_mode": row.get("pickup_mode", "home"),
                "pickup_location_name": row.get("pickup_location_name"),
                "pickup_address_text": row.get("pickup_address_text"),
                "pickup_latitude": row.get("pickup_latitude"),
                "pickup_longitude": row.get("pickup_longitude"),
                "can_drive": bool(row["has_car"]),
                "role_tag": row["role_tag"],
                "availability_tag": row["availability_tag"],
                "pickup_flexible": bool(row["pickup_flexible"]),
                "force_drive_alone": bool(row.get("force_drive_alone", 0)),
                "priority_rank": row["priority_rank"],
                "active_in_trip": bool(row["active_in_trip"]),
            }
            for row in participant_rows
        ],
        "destination": (
            {
                "name": destination.name,
                "latitude": destination.latitude,
                "longitude": destination.longitude,
                "target_arrival_time": format_minutes_as_time(destination.target_arrival_time_min),
            }
            if destination
            else None
        ),
        "meetup_spots": [],
        "routes": [],
        "route_sets": [],
        "summary": None,
        "comparisons": [],
        "settings": get_app_settings()["map_center"],
    }
    meetup_points: dict[tuple[float, float, str], dict[str, Any]] = {}
    for row in participant_rows:
        if not bool(row["active_in_trip"]):
            continue
        if bool(row["has_car"]) or row.get("pickup_mode") != "meetup":
            continue
        if row.get("pickup_latitude") is None or row.get("pickup_longitude") is None:
            continue
        name = row.get("pickup_location_name") or row.get("location_name") or row["name"]
        key = (float(row["pickup_latitude"]), float(row["pickup_longitude"]), str(name))
        if key not in meetup_points:
            meetup_points[key] = {
                "name": name,
                "address_text": row.get("pickup_address_text"),
                "latitude": float(row["pickup_latitude"]),
                "longitude": float(row["pickup_longitude"]),
                "participant_names": [],
            }
        meetup_points[key]["participant_names"].append(row["name"])
    payload["meetup_spots"] = list(meetup_points.values())
    if not include_routes:
        LOG_DEBUG(f"map payload (no routes) built in {perf_counter() - start:.3f}s")
        return payload
    optimization = get_cached_optimization_result()
    if optimization is None:
        try:
            optimization = run_optimization()
        except ValueError:
            logger.info("map payload route build skipped due to invalid state")
            return payload
        cache_optimization_result(optimization)
        optimization = get_cached_optimization_result() or optimization

    palette = ["#d1495b", "#00798c", "#edae49", "#30638e", "#4f772d", "#8f2d56"]
    for trip_index, trip_result in enumerate(optimization["trip_results"]):
        payload["comparisons"].append(
            {
                "route_set_index": trip_index,
                "driver_set_name": _read_value(trip_result, "driver_set_name"),
                "plan_variant": _read_value(trip_result, "plan_variant") or "direct",
                "fitness": _read_value(trip_result, "fitness"),
                "total_distance_km": _read_value(trip_result, "total_distance_km"),
                "total_cost_eur": _read_value(trip_result, "total_cost_eur"),
                "total_time_window_violation_min": _read_value(trip_result, "total_time_window_violation_min"),
                "meetup_total_self_transfer_km": _read_value(trip_result, "meetup_total_self_transfer_km") or 0.0,
            }
        )
    best_result = optimization["best_result"]
    if best_result is None:
        return payload

    for trip_index, trip_result in enumerate(optimization["trip_results"]):
        route_set_routes = []
        total_duration = 0.0
        total_distance = 0.0
        destination_location = optimization["destination"]
        if isinstance(destination_location, dict):
            destination_location = Location(
                destination_location["name"],
                destination_location["latitude"],
                destination_location["longitude"],
            )
        for index, assignment in enumerate(_read_value(trip_result, "assignments")):
            driver = optimization["participants"][_read_value(assignment, "driver_index")]
            if isinstance(driver, dict):
                driver_home = driver.get("home_location") or driver.get("location")
                driver_location = Location(
                    driver_home["name"],
                    driver_home["latitude"],
                    driver_home["longitude"],
                )
                driver_name = driver["name"]
            else:
                driver_location = driver.home_location or driver.location
                driver_name = driver.name
            stops = [driver_location]
            pickup_markers = []
            for order, passenger_index in enumerate(_read_value(assignment, "passenger_indices"), start=1):
                passenger = optimization["participants"][passenger_index]
                if isinstance(passenger, dict):
                    meetup_pickup_locations = _read_value(trip_result, "meetup_pickup_locations") or {}
                    meetup_pickup = meetup_pickup_locations.get(passenger_index)
                    pickup_payload = meetup_pickup or (
                        passenger.get("pickup_location")
                        if passenger.get("pickup_mode") == "meetup" and passenger.get("pickup_location")
                        else passenger["location"]
                    )
                    passenger_location = Location(
                        pickup_payload["name"],
                        pickup_payload["latitude"],
                        pickup_payload["longitude"],
                    )
                    passenger_name = passenger["name"]
                else:
                    passenger_location = passenger.effective_pickup_location
                    passenger_name = passenger.name
                stops.append(passenger_location)
                pickup_markers.append(
                    {
                        "order": order,
                        "name": passenger_name,
                        "latitude": passenger_location.latitude,
                        "longitude": passenger_location.longitude,
                    }
                )
            stops.append(destination_location)
            preview = build_route_preview(stops)
            total_distance += preview["distance_km"]
            if preview["duration_min"] is not None:
                total_duration += preview["duration_min"]
            route_set_routes.append(
                {
                    "driver_name": driver_name,
                    "passenger_names": [
                        optimization["participants"][passenger_index]["name"]
                        if isinstance(optimization["participants"][passenger_index], dict)
                        else optimization["participants"][passenger_index].name
                        for passenger_index in _read_value(assignment, "passenger_indices")
                    ],
                    "geometry": preview["geometry"],
                    "distance_km": preview["distance_km"],
                    "duration_min": preview["duration_min"],
                    "color": palette[index % len(palette)],
                    "pickup_markers": pickup_markers,
                }
            )
        route_set_payload = {
            "route_set_index": trip_index,
            "driver_set_name": _read_value(trip_result, "driver_set_name"),
            "plan_variant": _read_value(trip_result, "plan_variant") or "direct",
            "selected": _read_value(trip_result, "driver_set_name") == _read_value(best_result, "driver_set_name"),
            "fitness": _read_value(trip_result, "fitness"),
            "total_distance_km": total_distance,
            "total_duration_min": total_duration if any(route["duration_min"] is not None for route in route_set_routes) else None,
            "total_time_window_violation_min": _read_value(trip_result, "total_time_window_violation_min"),
            "meetup_total_self_transfer_km": _read_value(trip_result, "meetup_total_self_transfer_km") or 0.0,
            "routes": route_set_routes,
        }
        payload["route_sets"].append(route_set_payload)
        if route_set_payload["selected"]:
            payload["routes"] = route_set_routes
            payload["summary"] = {
                "driver_set_name": _read_value(trip_result, "driver_set_name"),
                "fitness": _read_value(trip_result, "fitness"),
                "total_distance_km": total_distance,
                "total_duration_min": route_set_payload["total_duration_min"],
                "total_time_window_violation_min": _read_value(trip_result, "total_time_window_violation_min"),
                "plan_variant": _read_value(trip_result, "plan_variant") or "direct",
                "meetup_total_self_transfer_km": _read_value(trip_result, "meetup_total_self_transfer_km") or 0.0,
            }
    LOG_DEBUG(f"map payload with routes built in {perf_counter() - start:.3f}s")
    return payload
