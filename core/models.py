"""
Drivers Manager Project - Data Models
======================================
Data classes representing participants, cars, locations, scores, and results.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Location:
    """A geographic location with name and coordinates."""

    name: str
    latitude: float
    longitude: float
    target_arrival_time_min: Optional[int] = None

    def __str__(self):
        return f"{self.name} ({self.latitude:.4f}, {self.longitude:.4f})"


@dataclass
class Car:
    """Vehicle information for a potential driver."""

    fuel_type: str
    consumption_l_per_100km: float
    total_seats: int
    vehicle_type: str = "car"

    @property
    def available_seats(self) -> int:
        """Seats available for passengers (total minus the driver)."""

        return max(self.total_seats - 1, 0)

    def __str__(self):
        return f"{self.fuel_type.title()} | {self.total_seats} seats"


@dataclass
class Participant:
    """A person participating in the trip."""

    name: str
    location: Location
    home_location: Optional[Location] = None
    pickup_location: Optional[Location] = None
    pickup_mode: str = "home"
    pickup_flexible: bool = True
    car: Optional[Car] = None
    habit_score: float = 0.0
    force_drive_alone: bool = False
    outbound_earliest_time_min: Optional[int] = None
    outbound_latest_time_min: Optional[int] = None
    return_earliest_time_min: Optional[int] = None
    return_latest_time_min: Optional[int] = None

    def __post_init__(self):
        if self.home_location is None:
            self.home_location = self.location
        if self.pickup_mode not in {"home", "meetup"}:
            self.pickup_mode = "home"

    @property
    def can_drive(self) -> bool:
        return self.car is not None

    @property
    def effective_pickup_location(self) -> Location:
        """Return the location where this participant should be collected."""

        if self.pickup_mode == "meetup" and self.pickup_location is not None:
            return self.pickup_location
        return self.home_location or self.location

    def __str__(self):
        role = "Driver" if self.can_drive else "Passenger"
        return f"{self.name} ({role})"


@dataclass
class DriverScore:
    """All scoring details for a single potential driver."""

    participant_index: int
    name: str
    score_distance_passengers: float = 0.0
    score_environmental: float = 0.0
    score_distance_destination: float = 0.0
    score_available_seats: float = 0.0
    score_habit: float = 0.0

    @property
    def total_score(self) -> float:
        """Sum of all partial scores."""

        return (
            self.score_distance_passengers
            + self.score_environmental
            + self.score_distance_destination
            + self.score_available_seats
            + self.score_habit
        )


@dataclass
class DriverSet:
    """A candidate set of drivers selected by the scoring algorithm."""

    name: str
    driver_indices: List[int]
    total_seats: int
    scores: Dict[int, float] = field(default_factory=dict)


@dataclass
class RouteAssignment:
    """Route details for one driver after APCA optimisation."""

    driver_index: int
    passenger_indices: List[int]
    route_nodes: List[str]
    route_distance_km: float
    route_cost_eur: float
    cost_per_person_eur: float
    route_duration_min: Optional[float] = None
    fuel_cost_eur: float = 0.0
    toll_cost_eur: float = 0.0
    outbound_departure_time: Optional[str] = None
    destination_arrival_time: Optional[str] = None
    return_departure_time: Optional[str] = None
    pickup_schedule: List[str] = field(default_factory=list)
    time_window_violation_min: float = 0.0


@dataclass
class TripResult:
    """Complete result of one APCA run on a driver set."""

    driver_set_name: str
    assignments: List[RouteAssignment]
    unassigned_passenger_indices: List[int]
    fitness: float
    total_distance_km: float
    total_cost_eur: float
    plan_preference_used: str = "efficiency"
    total_time_window_violation_min: float = 0.0
    max_route_distance_km: float = 0.0
    route_distance_spread_km: float = 0.0
    max_route_duration_min: float = 0.0
    route_duration_spread_min: float = 0.0
    max_detour_ratio: float = 0.0
    return_assignments: List[RouteAssignment] = field(default_factory=list)
    return_total_distance_km: float = 0.0
    return_total_cost_eur: float = 0.0
    return_total_time_window_violation_min: float = 0.0
    plan_variant: str = "direct"
    meetup_total_self_transfer_km: float = 0.0
    meetup_instructions: List[str] = field(default_factory=list)
    meetup_pickup_locations: Dict[int, Dict[str, Any]] = field(default_factory=dict)
    solver_used: str = "local_search"
