"""
Drivers Manager Project - APCA Algorithm
==========================================
Ant Path-oriented Carpooling Allocation Approach (APCA)
based on Huang et al. (2019).

Simplified for the **many-origins-to-one-destination** case:
    • Every participant starts from their own location.
    • All passengers must be picked up and brought to a single destination.
    • Drivers start at their location, pick up assigned passengers in order,
      then proceed to the destination.

The class holds the problem model (nodes, distances, capacities, time windows, rules) and the
objective; ``solve()`` runs one of four interchangeable search strategies selected by ``solver``:
``local_search`` (default), ``annealing``, ``ant_colony`` (the original APCA) and ``exhaustive``.
See docs/superpowers/specs/2026-09-10-route-solver-design.md for the audit and the choice.
"""

import math
import random
from itertools import permutations, product
from typing import List, Dict, Tuple, Optional, Callable

from core.logging_config import LOG_INFO, LOG_DEBUG
from core.models import (
    Participant, Location, RouteAssignment, TripResult,
)
from core.utils import haversine_distance
from core.config import (
    APCA_NUM_ANTS, APCA_NUM_ITERATIONS,
    APCA_ALPHA, APCA_BETA, APCA_RHO,
    APCA_Q, APCA_TAU0, APCA_SIGMA,
    COST_PER_KM, ESTIMATED_AVERAGE_SPEED_KMH,
    PICKUP_STOP_MINUTES, TIME_WINDOW_PENALTY_PER_MINUTE,
    RETURN_WINDOW_PREFERENCE_WEIGHT,
    PICKUP_ORDER_RULE_PENALTY,
    DEFAULT_SOLVER, SOLVER_CHOICES,
)
from app.time_utils import format_minutes_as_time

# Iterated-local-search restarts per plan preference (detour-aware evaluation is ~20x dearer),
# kick size, and the largest cars**passengers the exhaustive solver enumerates before falling
# back to local search (about 8 passengers).
LOCAL_SEARCH_RESTARTS = {"efficiency": 60, "detour_aware": 12}
LOCAL_SEARCH_KICKS = (2, 5)  # ride groups moved per kick, drawn uniformly
EXHAUSTIVE_MAX_COMBINATIONS = 20_000


class APCAAlgorithm:
    """
    Solve the Carpool Service Problem with Time Windows (CSPTW)
    using an Ant Colony Optimisation approach.

    In the simplified many-to-one-destination variant the node set is:
        • m  driver-start nodes      (indices 0 … m-1)
        • n  passenger-pickup nodes   (indices m … m+n-1)
        • 1  shared destination node  (index m+n)
    """

    def __init__(
        self,
        participants: List[Participant],
        driver_indices: List[int],
        destination: Location,
        *,
        num_ants: int = APCA_NUM_ANTS,
        num_iterations: int = APCA_NUM_ITERATIONS,
        alpha: float = APCA_ALPHA,
        beta: float = APCA_BETA,
        rho: float = APCA_RHO,
        Q: float = APCA_Q,
        tau0: float = APCA_TAU0,
        sigma: float = APCA_SIGMA,
        average_speed_kmh: float = ESTIMATED_AVERAGE_SPEED_KMH,
        pickup_stop_minutes: float = PICKUP_STOP_MINUTES,
        time_window_penalty_per_minute: float = TIME_WINDOW_PENALTY_PER_MINUTE,
        return_window_preference_weight: float = RETURN_WINDOW_PREFERENCE_WEIGHT,
        pickup_order_rules: Optional[List[Tuple[int, int]]] = None,
        ride_together_rules: Optional[List[Tuple[int, int]]] = None,
        pickup_order_rule_penalty: float = PICKUP_ORDER_RULE_PENALTY,
        road_distance_matrix_km: Optional[List[List[float]]] = None,
        road_duration_matrix_min: Optional[List[List[float]]] = None,
        plan_preference: str = "efficiency",
        solver: str = DEFAULT_SOLVER,
    ):
        self.participants = participants
        self.solver = solver if solver in SOLVER_CHOICES else DEFAULT_SOLVER
        self.solver_used = self.solver
        self.driver_indices = list(driver_indices)
        self.passenger_indices = [
            i for i in range(len(participants)) if i not in self.driver_indices
        ]
        self.destination = destination

        self.m = len(self.driver_indices)       # number of drivers
        self.n = len(self.passenger_indices)    # number of passengers

        # ACO hyper-parameters
        self.num_ants = num_ants
        self.num_iterations = num_iterations
        self.alpha = alpha
        self.beta = beta
        self.rho = rho
        self.Q = Q
        self.tau0 = tau0
        self.sigma = sigma
        self.average_speed_kmh = max(average_speed_kmh, 1.0)
        self.pickup_stop_minutes = max(pickup_stop_minutes, 0.0)
        self.time_window_penalty_per_minute = max(time_window_penalty_per_minute, 0.0)
        self.return_window_preference_weight = max(return_window_preference_weight, 0.0)
        self.pickup_order_rules = list(pickup_order_rules or [])
        self.ride_together_rules = [tuple(sorted(rule)) for rule in (ride_together_rules or [])]
        self.pickup_order_rule_penalty = max(pickup_order_rule_penalty, 0.0)
        self.road_distance_matrix_km = road_distance_matrix_km
        self.road_duration_matrix_min = road_duration_matrix_min
        self.plan_preference = (
            "detour_aware"
            if plan_preference in {"balanced_load", "detour_aware"}
            else "efficiency"
        )
        self._route_time_cache: Dict[Tuple[int, Tuple[int, ...]], dict] = {}
        self._order_cache: Dict[Tuple[int, Tuple[int, ...]], List[int]] = {}

        # Build internal problem representation
        LOG_DEBUG(f"APCA init — {self.m} drivers, {self.n} passengers, ants={num_ants}, iter={num_iterations}")
        self._build_nodes_and_distances()
        self.reference_total_distance = self._reference_total_distance()
        LOG_DEBUG(f"APCA reference distance: {self.reference_total_distance:.2f}km, total nodes: {self.total_nodes}")

    # ==================================================================
    # Problem setup (ACOC – Constitution)
    # ==================================================================

    def _build_nodes_and_distances(self):
        """Create the node list and pre-compute the distance matrix."""
        self.locations: List[Location] = []

        # Driver-start nodes  (0 … m-1)
        for d_idx in self.driver_indices:
            self.locations.append(self.participants[d_idx].location)

        # Passenger-pickup nodes  (m … m+n-1)
        for p_idx in self.passenger_indices:
            self.locations.append(self.participants[p_idx].location)

        # Destination node  (m+n)
        self.locations.append(self.destination)

        self.dest_node: int = self.m + self.n
        self.total_nodes: int = self.m + self.n + 1

        # Distance matrix
        self.dist: List[List[float]] = [
            [0.0] * self.total_nodes for _ in range(self.total_nodes)
        ]
        for i in range(self.total_nodes):
            for j in range(i + 1, self.total_nodes):
                d = haversine_distance(self.locations[i], self.locations[j])
                self.dist[i][j] = d
                self.dist[j][i] = d
        if self.road_distance_matrix_km and len(self.road_distance_matrix_km) == self.total_nodes:
            for i in range(self.total_nodes):
                for j in range(self.total_nodes):
                    value = self.road_distance_matrix_km[i][j]
                    if value is not None:
                        self.dist[i][j] = value

        # Driver capacities
        self.capacity: List[int] = [
            self.participants[d].car.available_seats for d in self.driver_indices
        ]

    def _init_pheromones(self):
        """
        Initialise per-driver pheromone matrices Ri  (Section 5.5.2).

        Ri has two parts:
            RF – column: pheromone from driver i's start to each passenger pickup
            RN – matrix: pheromone between passenger pickups (and to destination)

        Stored as self.tau[d][u][v] for driver-local index d.
        """
        self.tau: Dict[int, List[List[float]]] = {}

        for d in range(self.m):
            mat = [[0.0] * self.total_nodes for _ in range(self.total_nodes)]
            drv_node = d  # driver-start node

            # RF: driver start → passenger pickups
            for p in range(self.n):
                p_node = self.m + p
                mat[drv_node][p_node] = self.tau0

            # RN: passenger pickup ↔ passenger pickup
            for p1 in range(self.n):
                for p2 in range(self.n):
                    if p1 != p2:
                        mat[self.m + p1][self.m + p2] = self.tau0

            # Passenger pickup → destination
            for p in range(self.n):
                mat[self.m + p][self.dest_node] = self.tau0

            self.tau[d] = mat

    def _heuristic(self, u: int, v: int) -> float:
        """
        Heuristic attractiveness  η(u, v) = 1 / distance(u, v).
        Incorporates the extra-cost c<w,x> concept from Section 5.6.1
        by including the pickup-to-destination distance as a penalty.
        """
        d = self.dist[u][v]
        if d < 0.01:
            return 100.0
        # Add a small penalty proportional to how far the pickup is from dest
        # (approximation of the c<w,x> extra cost described in Section 5.6.1)
        if self.m <= v < self.m + self.n:
            extra = self.dist[v][self.dest_node] * 0.1
        else:
            extra = 0.0
        return 1.0 / (d + extra)

    # ==================================================================
    # Solution construction (ACOO – Optimisation)
    # ==================================================================

    def _construct_solution(self) -> Tuple[Dict[int, List[int]], List[int]]:
        """
        A single ant builds a complete assignment.

        Returns
        -------
        assignment : dict  driver_local_idx → list of passenger_local_indices (order)
        unassigned : list  passenger_local_indices not assigned
        """
        assignment: Dict[int, List[int]] = {d: [] for d in range(self.m)}
        remaining_cap = list(self.capacity)
        unassigned = list(range(self.n))

        # One random driver with free seats extends its route per step. The paper's
        # round-robin order forced every ant onto the most balanced split, which hid the
        # km-optimal plans whenever passengers cluster around one driver.
        while unassigned:
            open_drivers = [d for d in range(self.m) if remaining_cap[d] > 0]
            if not open_drivers:
                break
            for d in (random.choice(open_drivers),):
                # Current node for this driver
                if assignment[d]:
                    cur = self.m + assignment[d][-1]
                else:
                    cur = d  # driver start

                feasible = list(unassigned)

                # Probability calculation  (Section 5.6.2)
                weights = []
                for p in feasible:
                    p_node = self.m + p
                    t = max(self.tau[d][cur][p_node], 1e-12)
                    h = self._heuristic(cur, p_node)
                    if self.plan_preference == "detour_aware":
                        marginal_cost, best_sequence = self._marginal_insertion_cost(d, assignment[d], p)
                        best_other_cost = min(
                            self._marginal_insertion_cost(other_driver, assignment[other_driver], p)[0]
                            for other_driver in range(self.m)
                            if remaining_cap[other_driver] > 0
                        )
                        candidate_cost = max(marginal_cost, 0.0)
                        alternative_cost = max(best_other_cost, 0.0)
                        insertion_affinity = (alternative_cost + 1.0) / (candidate_cost + 1.0)
                        route_load_penalty = self._detour_aware_route_load_penalty(d, best_sequence)
                        load_affinity = 1.0 / (1.0 + route_load_penalty)
                        h *= (0.22 + (0.58 * insertion_affinity) + (0.20 * load_affinity))
                    w = (t ** self.alpha) * (h ** self.beta)
                    weights.append(w)

                total_w = sum(weights)
                if total_w <= 0:
                    chosen = random.choice(feasible)
                else:
                    # Roulette-wheel selection
                    r = random.random() * total_w
                    cumul = 0.0
                    chosen = feasible[-1]
                    for p, w in zip(feasible, weights):
                        cumul += w
                        if cumul >= r:
                            chosen = p
                            break

                if self.plan_preference == "detour_aware":
                    _chosen_cost, chosen_sequence = self._best_insertion(d, assignment[d], chosen)
                    assignment[d] = chosen_sequence
                else:
                    assignment[d].append(chosen)
                remaining_cap[d] -= 1
                unassigned.remove(chosen)

        return assignment, unassigned

    # ==================================================================
    # Solution evaluation (Section 5.6.3)
    # ==================================================================

    def _evaluate(
        self,
        assignment: Dict[int, List[int]],
        unassigned: List[int],
    ) -> float:
        """
        Objective function  f(s)  (Section 5.6.3).

        f(s) = Σ_i |M_i|  +  Σ_i Σ_j U_i(n_j)  +  Σ_i φ_i

        • |M_i|  : passengers matched to driver i
        • U_i    : seat-usage rate at each pickup node
        • φ_i    : Gaussian-transformed temporal cost → [0, 1]
        """
        total_matched = 0
        total_sur = 0.0
        total_phi = 0.0
        total_route_distance = 0.0
        route_distances: List[float] = []
        route_durations: List[float] = []
        active_drivers = 0
        total_time_window_violation = 0.0

        for d in range(self.m):
            passengers = assignment[d]
            n_pass = len(passengers)
            total_matched += n_pass

            # Seat-usage rate (cumulative over nodes visited)
            if self.capacity[d] > 0:
                for k in range(1, n_pass + 1):
                    total_sur += k / self.capacity[d]

            # Route distance (temporal cost proxy)
            route_dist = self._route_distance(d, passengers)
            total_route_distance += route_dist
            route_distances.append(route_dist)
            if passengers:
                active_drivers += 1
            analysis = self._route_time_analysis(d, passengers)
            route_durations.append(analysis["outbound_duration_min"])
            total_time_window_violation += (
                analysis["outbound_violation_min"]
                + (analysis["return_violation_min"] * self.return_window_preference_weight)
            )
            total_time_window_violation += self._pickup_order_penalty(passengers)

            # Gaussian transform  φ = exp(-d² / 2σ²)
            phi = math.exp(-(route_dist ** 2) / (2.0 * self.sigma ** 2))
            total_phi += phi

        # Penalty for unassigned passengers
        penalty = len(unassigned) * 10.0
        ride_together_penalty = self._ride_together_violation_count(assignment, unassigned) * 1000.0
        distance_penalty = total_route_distance / max(self.reference_total_distance, 1.0)
        # The paper's seat-usage reward, active-driver penalty and Gaussian per-route term only
        # make sense when drivers may stay home and routes are short; here every driver drives
        # anyway and routes run 20-150 km, so in efficiency mode all three rewarded cramming
        # everyone into one car. Efficiency mode is km plus the constraint penalties; the
        # detour-aware branch below keeps its own weights.
        driver_penalty = 0.0
        time_window_penalty = total_time_window_violation * self.time_window_penalty_per_minute
        occupancy_reward = 0.0

        if self.plan_preference == "detour_aware":
            occupancy_reward = total_sur * 0.03
            driver_penalty = 0.04 * active_drivers
            active_route_distances = [distance for distance in route_distances if distance > 0.0]
            active_route_durations = [duration for duration in route_durations if duration > 0.0]
            distance_reference = max(
                self.reference_total_distance / max(active_drivers, 1),
                1.0,
            )
            duration_reference = max(
                (self.reference_total_distance / max(self.average_speed_kmh, 1.0)) * 60.0,
                1.0,
            )
            longest_route_km_penalty = (
                max(active_route_distances, default=0.0) / distance_reference
            )
            route_spread_km_penalty = (
                (max(active_route_distances) - min(active_route_distances)) / distance_reference
                if len(active_route_distances) >= 2
                else 0.0
            )
            detour_penalty = 0.0
            nearest_driver_penalty = 0.0
            relative_duration_penalty = 0.0
            route_load_penalty = 0.0
            for d in range(self.m):
                passengers = assignment[d]
                if not passengers:
                    continue
                driver_to_destination = self.dist[d][self.dest_node]
                detour_penalty += self._route_distance(d, passengers) / max(driver_to_destination, 1.0)
                direct_duration = self._leg_duration_minutes(d, self.dest_node)
                route_duration = self._route_time_analysis(d, passengers)["outbound_duration_min"]
                relative_duration_penalty += max(
                    (route_duration / max(direct_duration, 1.0)) - 1.0,
                    0.0,
                )
                route_load_penalty += self._detour_aware_route_load_penalty(d, passengers)
                for passenger_local_idx in passengers:
                    assigned_distance = self._assigned_passenger_cost(d, passengers, passenger_local_idx)
                    nearest_driver_distance = min(
                        self._marginal_insertion_cost(driver_local_idx, assignment[driver_local_idx], passenger_local_idx)[0]
                        if driver_local_idx != d
                        else assigned_distance
                        for driver_local_idx in range(self.m)
                        if driver_local_idx == d or len(assignment[driver_local_idx]) < self.capacity[driver_local_idx]
                    )
                    nearest_driver_penalty += max(
                        assigned_distance - nearest_driver_distance,
                        0.0,
                    ) / max(nearest_driver_distance, 1.0)
            distance_penalty = distance_penalty * 1.35
            return (
                total_matched
                + occupancy_reward
                + total_phi
                - penalty
                - ride_together_penalty
                - distance_penalty
                - driver_penalty
                - time_window_penalty
                - (1.7 * longest_route_km_penalty)
                - (1.2 * route_spread_km_penalty)
                - (0.95 * detour_penalty)
                - (1.55 * route_load_penalty)
                - (1.45 * nearest_driver_penalty)
                - (0.22 * relative_duration_penalty)
            )

        return (
            total_matched
            - penalty
            - ride_together_penalty
            - distance_penalty
            - time_window_penalty
        )

    def _route_distance(self, d: int, passengers: List[int]) -> float:
        """Total km for driver d picking up *passengers* then going to dest."""
        dist_total = 0.0
        cur = d  # driver-start node
        for p in passengers:
            p_node = self.m + p
            dist_total += self.dist[cur][p_node]
            cur = p_node
        dist_total += self.dist[cur][self.dest_node]
        return dist_total

    def _route_distance_for_sequence(self, d: int, passengers: List[int]) -> float:
        """Return the full route distance for an explicit passenger order."""
        return self._route_distance(d, passengers)

    def _best_insertion(self, d: int, passengers: List[int], passenger_local_idx: int) -> tuple[float, List[int]]:
        """Return the cheapest route obtained by inserting one passenger into an existing route."""
        best_sequence: List[int] | None = None
        best_distance: float | None = None
        for insert_at in range(len(passengers) + 1):
            candidate = list(passengers)
            candidate.insert(insert_at, passenger_local_idx)
            candidate_distance = self._route_distance_for_sequence(d, candidate)
            if best_distance is None or candidate_distance < best_distance:
                best_distance = candidate_distance
                best_sequence = candidate
        return best_distance or 0.0, best_sequence or list(passengers)

    def _marginal_insertion_cost(self, d: int, passengers: List[int], passenger_local_idx: int) -> tuple[float, List[int]]:
        """Return the cheapest marginal distance increase for adding one passenger."""
        current_distance = self._route_distance_for_sequence(d, passengers)
        best_distance, best_sequence = self._best_insertion(d, passengers, passenger_local_idx)
        return best_distance - current_distance, best_sequence

    def _detour_aware_route_load_penalty(self, d: int, passengers: List[int]) -> float:
        """Penalize assigning many riders to a driver whose route is already detour-heavy."""
        if not passengers:
            return 0.0
        direct_distance = max(self.dist[d][self.dest_node], 1.0)
        route_distance = self._route_distance_for_sequence(d, passengers)
        distance_overhead = max((route_distance / direct_distance) - 1.0, 0.0)
        direct_duration = max(self._leg_duration_minutes(d, self.dest_node), 1.0)
        route_duration = self._route_time_analysis(d, passengers)["outbound_duration_min"]
        duration_overhead = max((route_duration / direct_duration) - 1.0, 0.0)
        return len(passengers) * ((1.8 * distance_overhead) + (0.65 * duration_overhead))

    def _assigned_passenger_cost(
        self,
        d: int,
        passengers: List[int],
        passenger_local_idx: int,
    ) -> float:
        """Estimate how much route distance one assigned passenger is adding to a driver's plan."""
        without_passenger = [p for p in passengers if p != passenger_local_idx]
        current_distance = self._route_distance_for_sequence(d, passengers)
        without_distance = self._route_distance_for_sequence(d, without_passenger)
        return current_distance - without_distance

    def _repair_detour_aware_assignment(
        self,
        assignment: Dict[int, List[int]],
        unassigned: List[int],
    ) -> tuple[Dict[int, List[int]], List[int], float]:
        """Greedily move passengers across active cars when it improves the detour-aware objective."""
        if self.plan_preference != "detour_aware" or unassigned:
            repaired_fitness = self._evaluate(assignment, unassigned)
            return assignment, unassigned, repaired_fitness

        improved = True
        current_assignment = {driver_idx: list(passengers) for driver_idx, passengers in assignment.items()}
        current_fitness = self._evaluate(current_assignment, unassigned)

        while improved:
            improved = False
            best_candidate_assignment = current_assignment
            best_candidate_fitness = current_fitness
            best_candidate_sort_key = self._solution_sort_key(current_assignment, unassigned, current_fitness)

            for source_driver in range(self.m):
                source_passengers = current_assignment[source_driver]
                if not source_passengers:
                    continue
                for passenger_local_idx in list(source_passengers):
                    reduced_source = [p for p in source_passengers if p != passenger_local_idx]
                    for target_driver in range(self.m):
                        if target_driver == source_driver:
                            continue
                        if len(current_assignment[target_driver]) >= self.capacity[target_driver]:
                            continue
                        _added_cost, target_sequence = self._marginal_insertion_cost(
                            target_driver,
                            current_assignment[target_driver],
                            passenger_local_idx,
                        )
                        candidate_assignment = {
                            driver_idx: list(passengers)
                            for driver_idx, passengers in current_assignment.items()
                        }
                        candidate_assignment[source_driver] = reduced_source
                        candidate_assignment[target_driver] = target_sequence
                        candidate_fitness = self._evaluate(candidate_assignment, unassigned)
                        candidate_sort_key = self._solution_sort_key(
                            candidate_assignment,
                            unassigned,
                            candidate_fitness,
                        )
                        if candidate_sort_key > best_candidate_sort_key:
                            best_candidate_assignment = candidate_assignment
                            best_candidate_fitness = candidate_fitness
                            best_candidate_sort_key = candidate_sort_key
                            improved = True

            current_assignment = {
                driver_idx: list(passengers)
                for driver_idx, passengers in best_candidate_assignment.items()
            }
            current_fitness = best_candidate_fitness

        return current_assignment, unassigned, current_fitness

    def _leg_duration_minutes(self, start_node: int, end_node: int) -> float:
        """Estimate leg duration from straight-line distance using a simple average speed."""
        if (
            self.road_duration_matrix_min
            and start_node < len(self.road_duration_matrix_min)
            and end_node < len(self.road_duration_matrix_min[start_node])
            and self.road_duration_matrix_min[start_node][end_node] is not None
        ):
            return float(self.road_duration_matrix_min[start_node][end_node])
        return (self.dist[start_node][end_node] / self.average_speed_kmh) * 60.0

    @staticmethod
    def _window_violation_minutes(
        actual_time_min: float,
        earliest_min: Optional[int],
        latest_min: Optional[int],
    ) -> float:
        """Return minutes outside the preferred time window."""
        violation = 0.0
        if earliest_min is not None and actual_time_min < earliest_min:
            violation += earliest_min - actual_time_min
        if latest_min is not None and actual_time_min > latest_min:
            violation += actual_time_min - latest_min
        return violation

    @staticmethod
    def _best_shift(candidates: List[float], scorer: Callable[[float], float]) -> Tuple[Optional[float], float]:
        """Pick the shift that minimizes time-window violation."""
        if not candidates:
            return None, 0.0
        unique_candidates = sorted({max(0.0, min(1439.0, candidate)) for candidate in candidates})
        best_shift = unique_candidates[0]
        best_score = scorer(best_shift)
        for candidate in unique_candidates[1:]:
            score = scorer(candidate)
            if score < best_score - 1e-6 or (abs(score - best_score) < 1e-6 and candidate < best_shift):
                best_shift = candidate
                best_score = score
        return best_shift, best_score

    def _route_time_analysis(self, d: int, passengers: List[int]) -> dict:
        """Estimate the best outbound and return schedule for one route under participant windows."""
        cache_key = (d, tuple(passengers))
        if cache_key in self._route_time_cache:
            return self._route_time_cache[cache_key]

        driver_participant_idx = self.driver_indices[d]
        cur = d
        elapsed = 0.0
        pickup_offsets: List[Tuple[int, str, float]] = []
        for passenger_local_idx in passengers:
            pickup_node = self.m + passenger_local_idx
            elapsed += self._leg_duration_minutes(cur, pickup_node)
            participant_idx = self.passenger_indices[passenger_local_idx]
            pickup_offsets.append(
                (participant_idx, self.participants[participant_idx].name, elapsed)
            )
            elapsed += self.pickup_stop_minutes
            cur = pickup_node
        total_outbound_duration = elapsed + self._leg_duration_minutes(cur, self.dest_node)
        destination_target_arrival = getattr(self.destination, "target_arrival_time_min", None)

        outbound_candidates = []
        driver = self.participants[driver_participant_idx]
        for bound in (driver.outbound_earliest_time_min, driver.outbound_latest_time_min):
            if bound is not None:
                outbound_candidates.append(float(bound))
        for participant_idx, _name, offset in pickup_offsets:
            participant = self.participants[participant_idx]
            if participant.outbound_earliest_time_min is not None:
                outbound_candidates.append(participant.outbound_earliest_time_min - offset)
            if participant.outbound_latest_time_min is not None:
                outbound_candidates.append(participant.outbound_latest_time_min - offset)
        if destination_target_arrival is not None:
            outbound_candidates.append(destination_target_arrival - total_outbound_duration)

        flexible_member_count = 0
        if driver.outbound_earliest_time_min is None and driver.outbound_latest_time_min is None:
            flexible_member_count += 1
        for participant_idx, _name, _offset in pickup_offsets:
            participant = self.participants[participant_idx]
            if participant.outbound_earliest_time_min is None and participant.outbound_latest_time_min is None:
                flexible_member_count += 1

        def outbound_score(start_min: float) -> float:
            score = self._window_violation_minutes(
                start_min,
                driver.outbound_earliest_time_min,
                driver.outbound_latest_time_min,
            )
            for participant_idx, _name, offset in pickup_offsets:
                participant = self.participants[participant_idx]
                score += self._window_violation_minutes(
                    start_min + offset,
                    participant.outbound_earliest_time_min,
                    participant.outbound_latest_time_min,
                )
            if destination_target_arrival is not None and flexible_member_count > 0:
                arrival_min = start_min + total_outbound_duration
                score += abs(arrival_min - destination_target_arrival) * (0.2 * flexible_member_count)
            return score

        outbound_departure_min, outbound_violation = self._best_shift(outbound_candidates, outbound_score)
        destination_arrival_min = (
            outbound_departure_min + total_outbound_duration
            if outbound_departure_min is not None
            else None
        )
        pickup_schedule = []
        if outbound_departure_min is not None:
            for _participant_idx, participant_name, offset in pickup_offsets:
                pickup_schedule.append(
                    f"{participant_name} at {format_minutes_as_time(outbound_departure_min + offset)}"
                )

        route_member_indices = [driver_participant_idx] + [self.passenger_indices[p] for p in passengers]
        return_candidates = []
        for participant_idx in route_member_indices:
            participant = self.participants[participant_idx]
            for bound in (participant.return_earliest_time_min, participant.return_latest_time_min):
                if bound is not None:
                    return_candidates.append(float(bound))

        def return_score(return_departure_min: float) -> float:
            return sum(
                self._window_violation_minutes(
                    return_departure_min,
                    self.participants[participant_idx].return_earliest_time_min,
                    self.participants[participant_idx].return_latest_time_min,
                )
                for participant_idx in route_member_indices
            )

        return_departure_min, return_violation = self._best_shift(return_candidates, return_score)
        analysis = {
            "outbound_departure_time": format_minutes_as_time(outbound_departure_min),
            "destination_arrival_time": format_minutes_as_time(destination_arrival_min),
            "return_departure_time": format_minutes_as_time(return_departure_min),
            "pickup_schedule": pickup_schedule,
            "outbound_duration_min": total_outbound_duration,
            "outbound_violation_min": outbound_violation,
            "return_violation_min": return_violation,
            "violation_min": outbound_violation + return_violation,
        }
        self._route_time_cache[cache_key] = analysis
        return analysis

    def _pickup_order_penalty(self, passengers: List[int]) -> float:
        """Apply a soft penalty when a same-car pickup precedence rule is reversed."""
        if not passengers or not self.pickup_order_rules or self.pickup_order_rule_penalty <= 0.0:
            return 0.0
        passenger_positions = {
            self.passenger_indices[passenger_local_idx]: position
            for position, passenger_local_idx in enumerate(passengers)
        }
        total_penalty = 0.0
        for before_index, after_index in self.pickup_order_rules:
            if before_index in passenger_positions and after_index in passenger_positions:
                if passenger_positions[before_index] > passenger_positions[after_index]:
                    total_penalty += self.pickup_order_rule_penalty
        return total_penalty

    def _ride_together_violation_count(self, assignment: Dict[int, List[int]], unassigned: List[int]) -> int:
        """Count same-car rules that are not satisfied by the current assignment."""
        if not self.ride_together_rules:
            return 0
        driver_owner: dict[int, int] = {}
        for driver_local_idx, participant_idx in enumerate(self.driver_indices):
            driver_owner[participant_idx] = driver_local_idx
        for passenger_local_idx, driver_local_idx in (
            (passenger_local_idx, owner)
            for owner, passengers in assignment.items()
            for passenger_local_idx in passengers
        ):
            driver_owner[self.passenger_indices[passenger_local_idx]] = driver_local_idx
        unassigned_participants = {self.passenger_indices[passenger_local_idx] for passenger_local_idx in unassigned}
        violations = 0
        for first_index, second_index in self.ride_together_rules:
            if first_index in unassigned_participants or second_index in unassigned_participants:
                violations += 1
                continue
            if driver_owner.get(first_index) != driver_owner.get(second_index):
                violations += 1
        return violations

    def _reference_total_distance(self) -> float:
        """Return a baseline trip distance so route penalties scale sensibly."""
        baseline = 0.0
        for driver_local_idx in range(self.m):
            baseline += self.dist[driver_local_idx][self.dest_node]
        for passenger_local_idx in range(self.n):
            baseline += self.dist[self.m + passenger_local_idx][self.dest_node]
        return max(baseline, 1.0)

    def _assignment_detour_metrics(
        self,
        assignment: Dict[int, List[int]],
    ) -> tuple[float, float, float, float, float, float, float, float]:
        """Compute km-first summary metrics for one assignment."""
        route_distances: list[float] = []
        route_durations: list[float] = []
        detour_ratios: list[float] = []
        total_route_distance = 0.0
        total_violation = 0.0
        route_load_penalty = 0.0

        for d in range(self.m):
            passengers = assignment[d]
            route_distance = self._route_distance(d, passengers)
            analysis = self._route_time_analysis(d, passengers)
            route_distances.append(route_distance)
            route_durations.append(analysis["outbound_duration_min"])
            total_route_distance += route_distance
            total_violation += (
                analysis["outbound_violation_min"]
                + (analysis["return_violation_min"] * self.return_window_preference_weight)
                + self._pickup_order_penalty(passengers)
            )
            route_load_penalty += self._detour_aware_route_load_penalty(d, passengers)
            if passengers:
                driver_to_destination = self.dist[d][self.dest_node]
                detour_ratios.append(route_distance / max(driver_to_destination, 1.0))

        max_route_distance = max(route_distances, default=0.0)
        route_distance_spread = (
            max(route_distances) - min(route_distances)
            if len(route_distances) >= 2
            else 0.0
        )
        max_route_duration = max(route_durations, default=0.0)
        route_duration_spread = (
            max(route_durations) - min(route_durations)
            if len(route_durations) >= 2
            else 0.0
        )
        max_detour_ratio = max(detour_ratios, default=0.0)
        return (
            total_route_distance,
            total_violation,
            max_route_distance,
            route_distance_spread,
            max_route_duration,
            route_duration_spread,
            max_detour_ratio,
            route_load_penalty,
        )

    def _solution_sort_key(
        self,
        assignment: Dict[int, List[int]],
        unassigned: List[int],
        fitness: float,
    ) -> Tuple[float, ...]:
        """Prefer higher fitness, then compare solutions using the active optimization intent."""
        (
            total_route_distance,
            total_violation,
            max_route_distance,
            route_distance_spread,
            max_route_duration,
            route_duration_spread,
            max_detour_ratio,
            route_load_penalty,
        ) = self._assignment_detour_metrics(assignment)
        if self.plan_preference == "detour_aware":
            return (
                -len(unassigned),
                -total_violation,
                -max_route_distance,
                -route_distance_spread,
                -max_detour_ratio,
                -route_load_penalty,
                -total_route_distance,
                -max_route_duration,
                -route_duration_spread,
                fitness,
            )
        return (fitness, -len(unassigned), -total_violation, -total_route_distance)

    # ==================================================================
    # Pheromone update (Section 5.6.4)
    # ==================================================================

    def _update_pheromones(
        self,
        solutions: List[Tuple[Dict[int, List[int]], List[int], float]],
        global_best: Optional[Tuple[Dict[int, List[int]], List[int], float]] = None,
    ):
        """
        Evaporate, then let only the iteration-best and global-best ants deposit.

        Depositing from every ant in proportion to fitness (the paper's rule) gave near-uniform
        deposits here because fitness values differ by a few percent, so the trail tracked
        choice frequency rather than quality. Elitist Ant System fixes that in two lines.
        """
        # Evaporation
        for d in range(self.m):
            for u in range(self.total_nodes):
                for v in range(self.total_nodes):
                    self.tau[d][u][v] *= (1.0 - self.rho)

        deposits = [max(solutions, key=lambda solution: solution[2])]
        if global_best is not None:
            deposits.append(global_best)
        for assignment, _unassigned, _fitness in deposits:
            delta = self.Q
            for d in range(self.m):
                if not assignment[d]:
                    continue
                cur = d
                for p in assignment[d]:
                    p_node = self.m + p
                    self.tau[d][cur][p_node] += delta
                    cur = p_node
                self.tau[d][cur][self.dest_node] += delta

    # ==================================================================
    # Main solver loop
    # ==================================================================

    def solve(
        self,
        progress_callback: Optional[Callable[[int, int, float], None]] = None,
    ) -> TripResult:
        """
        Run the selected search strategy, then the shared repair and pickup-order polish.

        Parameters
        ----------
        progress_callback : callable(iteration, total_iterations, best_fitness)
            Optional callback for GUI progress updates (ant colony only).
        """
        self.solver_used = self.solver
        search = {
            "ant_colony": lambda: self._search_ant_colony(progress_callback),
            "annealing": self._search_annealing,
            "exhaustive": self._search_exhaustive,
        }.get(self.solver, self._search_local)
        LOG_INFO(f"solver '{self.solver}' started - {self.m} drivers, {self.n} passengers")
        best_assignment, best_unassigned, best_fitness = search()
        best_sort_key = self._solution_sort_key(best_assignment, best_unassigned, best_fitness)

        repaired_assignment, repaired_unassigned, repaired_fitness = self._repair_detour_aware_assignment(
            best_assignment or {d: [] for d in range(self.m)},
            best_unassigned,
        )
        repaired_sort_key = self._solution_sort_key(
            repaired_assignment,
            repaired_unassigned,
            repaired_fitness,
        )
        if repaired_sort_key > best_sort_key:
            best_assignment = repaired_assignment
            best_unassigned = repaired_unassigned
            best_fitness = repaired_fitness

        best_assignment, best_fitness = self._refine_pickup_orders(
            best_assignment or {d: [] for d in range(self.m)},
            best_unassigned,
            best_fitness,
        )

        LOG_INFO(f"solver '{self.solver_used}' finished - best fitness={best_fitness:.4f}, unassigned={len(best_unassigned)}")
        return self._build_trip_result(
            best_assignment or {d: [] for d in range(self.m)},
            best_unassigned,
            best_fitness,
        )

    # ==================================================================
    # Search strategies
    # ==================================================================

    def _search_ant_colony(self, progress_callback=None):
        """The original APCA loop: ants construct, evaluate, reinforce (elitist deposit)."""
        self._init_pheromones()
        best_assignment: Optional[Dict[int, List[int]]] = None
        best_unassigned: List[int] = list(range(self.n))
        best_fitness = -float("inf")
        best_sort_key: Tuple[float, ...] = (-float("inf"),)

        for it in range(self.num_iterations):
            iter_solutions: List[Tuple[Dict, List, float]] = []
            for _ant in range(self.num_ants):
                assignment, unassigned = self._construct_solution()
                fitness = self._evaluate(assignment, unassigned)
                iter_solutions.append((assignment, unassigned, fitness))
                sort_key = self._solution_sort_key(assignment, unassigned, fitness)
                if sort_key > best_sort_key:
                    best_sort_key = sort_key
                    best_fitness = fitness
                    best_assignment = {d: list(ps) for d, ps in assignment.items()}
                    best_unassigned = list(unassigned)
            self._update_pheromones(iter_solutions, (best_assignment, best_unassigned, best_fitness))
            if progress_callback:
                progress_callback(it + 1, self.num_iterations, best_fitness)

        return best_assignment or {d: [] for d in range(self.m)}, best_unassigned, best_fitness

    def _key(self, assignment: Dict[int, List[int]], unassigned: List[int]) -> Tuple[Tuple[float, ...], float]:
        """Return (sort key, fitness) for one solution; the sort key is what every search compares."""
        fitness = self._evaluate(assignment, unassigned)
        return self._solution_sort_key(assignment, unassigned, fitness), fitness

    def _ride_groups(self) -> Dict[int, List[int]]:
        """Map each passenger-local index to the sorted passengers it must ride with (itself included)."""
        local_of = {participant_idx: local for local, participant_idx in enumerate(self.passenger_indices)}
        groups: Dict[int, set] = {p: {p} for p in range(self.n)}
        for first, second in self.ride_together_rules:
            a, b = local_of.get(first), local_of.get(second)
            if a is None or b is None:
                continue
            merged = groups[a] | groups[b]
            for p in merged:
                groups[p] = merged
        return {p: sorted(members) for p, members in groups.items()}

    def _units(self, assignment: Dict[int, List[int]], groups: Dict[int, List[int]]) -> List[Tuple[int, List[int]]]:
        """List (driver, ride group) pairs; moves shift whole groups so ride-together rules survive."""
        units = []
        for d in range(self.m):
            seen: set = set()
            for p in assignment[d]:
                if p in seen:
                    continue
                unit = [q for q in groups[p] if q in assignment[d]]
                seen.update(unit)
                units.append((d, unit))
        return units

    def _reinsert(
        self,
        assignment: Dict[int, List[int]],
        removals: Dict[int, List[int]],
        insertions: Dict[int, List[int]],
    ) -> Dict[int, List[int]]:
        """Copy the assignment, drop *removals* per driver, insert each new passenger at its cheapest spot."""
        trial = {d: [p for p in ps if p not in removals.get(d, ())] for d, ps in assignment.items()}
        for d, unit in insertions.items():
            for q in unit:
                _cost, trial[d] = self._best_insertion(d, trial[d], q)
        return trial

    def _km_optimal_order(self, d: int, passengers: List[int]) -> List[int]:
        """Shortest pickup order for one car by enumeration, cached per passenger set."""
        key = (d, tuple(sorted(passengers)))
        if key not in self._order_cache:
            self._order_cache[key] = min(
                (list(order) for order in permutations(passengers)),
                key=lambda order: self._route_distance(d, order),
                default=[],
            )
        return self._order_cache[key]

    def _neighbours(self, assignment: Dict[int, List[int]], groups: Dict[int, List[int]]):
        """Yield every capacity-feasible relocate and swap of ride groups between cars."""
        units = self._units(assignment, groups)
        for source, unit in units:
            for target in range(self.m):
                if target != source and len(assignment[target]) + len(unit) <= self.capacity[target]:
                    yield self._reinsert(assignment, {source: unit}, {target: unit})
        for index, (d1, u1) in enumerate(units):
            for d2, u2 in units[index + 1:]:
                if d1 == d2:
                    continue
                if (len(assignment[d1]) - len(u1) + len(u2) <= self.capacity[d1]
                        and len(assignment[d2]) - len(u2) + len(u1) <= self.capacity[d2]):
                    yield self._reinsert(assignment, {d1: u1, d2: u2}, {d1: u2, d2: u1})

    def _random_neighbour(self, assignment, groups, rng=random):
        """One random relocate or swap, or None when nothing can move."""
        units = self._units(assignment, groups)
        if not units:
            return None
        source, unit = rng.choice(units)
        others = [(d, u) for d, u in units if d != source]
        if others and rng.random() < 0.5:
            d2, u2 = rng.choice(others)
            if (len(assignment[source]) - len(unit) + len(u2) <= self.capacity[source]
                    and len(assignment[d2]) - len(u2) + len(unit) <= self.capacity[d2]):
                return self._reinsert(assignment, {source: unit, d2: u2}, {source: u2, d2: unit})
        targets = [d for d in range(self.m) if d != source and len(assignment[d]) + len(unit) <= self.capacity[d]]
        if not targets:
            return None
        return self._reinsert(assignment, {source: unit}, {rng.choice(targets): unit})

    def _seed_insertion(self) -> Tuple[Dict[int, List[int]], List[int]]:
        """Cheapest insertion: place the hardest-to-reach ride groups first, each at its cheapest car and spot."""
        assignment: Dict[int, List[int]] = {d: [] for d in range(self.m)}
        groups = self._ride_groups()
        units, seen = [], set()
        for p in range(self.n):
            if p not in seen:
                units.append(groups[p])
                seen.update(groups[p])
        units.sort(key=lambda unit: -min(self.dist[d][self.m + p] for d in range(self.m) for p in unit))
        unassigned: List[int] = []
        for unit in units:
            best = self._cheapest_car(assignment, unit)
            if best is None:
                unassigned.extend(unit)
            else:
                assignment[best[0]] = best[1]
        return assignment, unassigned

    def _cheapest_car(self, assignment, unit: List[int]):
        """Return (driver, new route) adding *unit* where it costs the fewest km, or None if no car has room."""
        best = None
        for d in range(self.m):
            if len(assignment[d]) + len(unit) > self.capacity[d]:
                continue
            route = self._reinsert(assignment, {}, {d: unit})[d]
            added = self._route_distance(d, route) - self._route_distance(d, assignment[d])
            if best is None or added < best[0]:
                best = (added, d, route)
        return None if best is None else (best[1], best[2])

    def _kick(self, assignment, groups, rng):
        """Perturbation: a few random relocate/swap moves (greedy ruin-and-recreate just rebuilt the same plan)."""
        trial = assignment
        for _move in range(rng.randint(*LOCAL_SEARCH_KICKS)):
            trial = self._random_neighbour(trial, groups, rng) or trial
        return trial

    def _descend(self, assignment, unassigned):
        """First-improvement relocate/swap descent on the full sort key until no move improves."""
        groups = self._ride_groups()
        current = {d: list(ps) for d, ps in assignment.items()}
        key, fitness = self._key(current, unassigned)
        while True:
            for trial in self._neighbours(current, groups):
                trial_key, trial_fitness = self._key(trial, unassigned)
                if trial_key > key:
                    current, key, fitness = trial, trial_key, trial_fitness
                    break
            else:
                return current, key, fitness

    def _search_local(self):
        """Cheapest insertion + descent, restarted from seeded kicks (iterated local search). Deterministic."""
        assignment, unassigned = self._seed_insertion()
        best, best_key, best_fitness = self._descend(assignment, unassigned)
        rng = random.Random(0)  # fixed seed: the same people give the same plan on every click
        groups = self._ride_groups()
        for _restart in range(LOCAL_SEARCH_RESTARTS[self.plan_preference]):
            trial, trial_key, trial_fitness = self._descend(self._kick(best, groups, rng), unassigned)
            if trial_key > best_key:
                best, best_key, best_fitness = trial, trial_key, trial_fitness
        return best, unassigned, best_fitness

    def _search_annealing(self):
        """Simulated annealing on fitness (Kirkpatrick et al. 1983), then the same descent as local search."""
        groups = self._ride_groups()
        current, unassigned = self._seed_insertion()
        current_key, current_fitness = self._key(current, unassigned)
        best, best_key, best_fitness = current, current_key, current_fitness
        steps = max(self.num_iterations * 30, 500)  # ~ the ant colony's sample count by default
        temperature = 0.5  # fitness units: a typical relocate changes fitness by 0.05-0.5
        cooling = (0.005 / temperature) ** (1.0 / steps)
        for _step in range(steps):
            trial = self._random_neighbour(current, groups)
            if trial is None:  # e.g. every other car is full: try another random move
                continue
            trial_key, trial_fitness = self._key(trial, unassigned)
            delta = trial_fitness - current_fitness
            if delta >= 0 or random.random() < math.exp(delta / temperature):
                current, current_key, current_fitness = trial, trial_key, trial_fitness
                if current_key > best_key:
                    best, best_key, best_fitness = current, current_key, current_fitness
            temperature *= cooling
        best, _best_key, best_fitness = self._descend(best, unassigned)
        return best, unassigned, best_fitness

    def _search_exhaustive(self):
        """Every capacity-feasible assignment with the km-optimal pickup order per car (exact for km)."""
        if self.m ** self.n > EXHAUSTIVE_MAX_COMBINATIONS:
            LOG_INFO(f"exhaustive search skipped ({self.m}^{self.n} assignments), using local search")
            self.solver_used = "local_search"
            return self._search_local()
        best = best_key = best_fitness = None
        for labels in product(range(self.m), repeat=self.n):
            members = {d: tuple(p for p in range(self.n) if labels[p] == d) for d in range(self.m)}
            if any(len(members[d]) > self.capacity[d] for d in range(self.m)):
                continue
            assignment = {d: list(self._km_optimal_order(d, list(members[d]))) for d in range(self.m)}
            key, fitness = self._key(assignment, [])
            if best_key is None or key > best_key:
                best, best_key, best_fitness = assignment, key, fitness
        if best is None:  # not enough seats for everyone: let local search leave people out
            self.solver_used = "local_search"
            return self._search_local()
        return best, [], best_fitness

    def _refine_pickup_orders(
        self,
        assignment: Dict[int, List[int]],
        unassigned: List[int],
        fitness: float,
    ) -> Tuple[Dict[int, List[int]], float]:
        """
        Deterministic local search on each driver's pickup sequence.

        Short routes (≤5 passengers) are enumerated exhaustively; longer ones
        use repeated 2-opt segment reversals. A reordering is only accepted
        when it improves the full solution sort key, so time windows, pickup
        rules, and plan-preference penalties all stay respected.
        """
        best_assignment = {d: list(ps) for d, ps in assignment.items()}
        best_fitness = fitness
        best_key = self._solution_sort_key(best_assignment, unassigned, best_fitness)
        improved_any = False

        for d in range(self.m):
            for _pass in range(3):
                passengers = best_assignment[d]
                if len(passengers) < 2:
                    break
                if len(passengers) <= 5:
                    candidates = [list(p) for p in permutations(passengers)]
                else:
                    candidates = [
                        passengers[:i] + passengers[i:j + 1][::-1] + passengers[j + 1:]
                        for i in range(len(passengers) - 1)
                        for j in range(i + 1, len(passengers))
                    ]
                improved_this_pass = False
                for candidate in candidates:
                    if candidate == best_assignment[d]:
                        continue
                    trial = {dd: list(ps) for dd, ps in best_assignment.items()}
                    trial[d] = candidate
                    trial_fitness = self._evaluate(trial, unassigned)
                    trial_key = self._solution_sort_key(trial, unassigned, trial_fitness)
                    if trial_key > best_key:
                        best_assignment = trial
                        best_fitness = trial_fitness
                        best_key = trial_key
                        improved_this_pass = True
                        improved_any = True
                if not improved_this_pass or len(passengers) <= 5:
                    break

        if improved_any:
            LOG_DEBUG(f"pickup-order refinement improved fitness to {best_fitness:.4f}")
        return best_assignment, best_fitness

    # ==================================================================
    # Result builder
    # ==================================================================

    def _build_trip_result(
        self,
        assignment: Dict[int, List[int]],
        unassigned: List[int],
        fitness: float,
    ) -> TripResult:
        """Package raw assignment into a user-friendly TripResult."""
        assignments: List[RouteAssignment] = []
        total_dist = 0.0
        total_cost = 0.0
        total_time_window_violation = 0.0

        for d in range(self.m):
            drv_part_idx = self.driver_indices[d]
            pass_part_idxs = [self.passenger_indices[p] for p in assignment[d]]
            time_analysis = self._route_time_analysis(d, assignment[d])
            total_time_window_violation += time_analysis["violation_min"]

            route_dist = self._route_distance(d, assignment[d])
            total_dist += route_dist

            fuel = self.participants[drv_part_idx].car.fuel_type
            cost = route_dist * COST_PER_KM.get(fuel, 0.10)
            total_cost += cost

            n_people = 1 + len(pass_part_idxs)  # driver + passengers
            cost_pp = cost / n_people if n_people else 0.0

            # Build human-readable route
            route_nodes = [self.participants[drv_part_idx].location.name]
            for p in assignment[d]:
                ppi = self.passenger_indices[p]
                route_nodes.append(
                    f"📍 Pick up {self.participants[ppi].name} "
                    f"({self.participants[ppi].location.name})"
                )
            route_nodes.append(f"🏁 {self.destination.name}")

            assignments.append(RouteAssignment(
                driver_index=drv_part_idx,
                passenger_indices=pass_part_idxs,
                route_nodes=route_nodes,
                route_distance_km=route_dist,
                route_cost_eur=cost,
                cost_per_person_eur=cost_pp,
                route_duration_min=time_analysis["outbound_duration_min"],
                fuel_cost_eur=cost,
                toll_cost_eur=0.0,
                outbound_departure_time=time_analysis["outbound_departure_time"],
                destination_arrival_time=time_analysis["destination_arrival_time"],
                return_departure_time=time_analysis["return_departure_time"],
                pickup_schedule=time_analysis["pickup_schedule"],
                time_window_violation_min=time_analysis["violation_min"],
            ))

        unassigned_idxs = [self.passenger_indices[p] for p in unassigned]

        return TripResult(
            driver_set_name="",   # filled in by caller
            assignments=assignments,
            unassigned_passenger_indices=unassigned_idxs,
            fitness=fitness,
            total_distance_km=total_dist,
            total_cost_eur=total_cost,
            total_time_window_violation_min=total_time_window_violation,
            solver_used=self.solver_used,
        )
