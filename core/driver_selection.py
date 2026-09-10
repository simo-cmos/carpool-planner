"""
Drivers Manager Project - Driver Selection
============================================
Implements the 5-criteria heuristic scoring system and the generation
of candidate driver sets as described in Sections 4.1–4.2 of the thesis.

Scoring criteria (each normalised to 0–1 except Habituality which is -1–1):
    1. SDP  – Distance Drivers ↔ Passengers
    2. SEI  – Environmental Impact
    3. SDD  – Distance Drivers ↔ Destination
    4. SAS  – Available Seats
    5. SH   – Habituality (past credit/debit)

Driver sets:
    • Best Score        – top drivers by raw total score
    • Long Distance     – favours drivers spread apart
    • Short Distance    – favours clustered drivers
    • Balanced Load     – allows one extra car to keep routes fairer
"""

from itertools import combinations
from typing import List
from core.logging_config import LOG_INFO, LOG_DEBUG
from core.models import Participant, Location, DriverScore, DriverSet
from core.utils import haversine_distance
from core.config import FUEL_TYPE_FACTORS, ELECTRIC_FIXED_SCORE


class DriverSelector:
    """Evaluate potential drivers and select candidate driver sets."""

    def __init__(self, participants: List[Participant], destination: Location):
        self.participants = participants
        self.destination = destination
        self.potential_drivers: List[int] = [
            i for i, p in enumerate(participants) if p.can_drive
        ]
        self.mandatory_drivers: List[int] = [
            i for i, p in enumerate(participants) if p.can_drive and p.force_drive_alone
        ]
        self.scores: dict = {}  # participant_index → DriverScore

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def calculate_all_scores(self) -> List[DriverScore]:
        """Run all five scoring algorithms and return sorted scores (best first)."""
        LOG_INFO(f"calculating driver scores — {len(self.potential_drivers)} potential drivers out of {len(self.participants)} participants")
        self.scores = {
            idx: DriverScore(participant_index=idx, name=self.participants[idx].name)
            for idx in self.potential_drivers
        }

        self._score_distance_passengers()   # SDP
        self._score_environmental_impact()  # SEI
        self._score_distance_destination()  # SDD
        self._score_available_seats()       # SAS
        self._score_habit()                 # SH
        LOG_DEBUG("all 5 scoring criteria computed (SDP, SEI, SDD, SAS, SH)")

        # Clamp any negative partial score to 0 (except habit which allows -1)
        for s in self.scores.values():
            s.score_distance_passengers = max(s.score_distance_passengers, 0.0)
            s.score_environmental = max(s.score_environmental, 0.0)
            s.score_distance_destination = max(s.score_distance_destination, 0.0)
            s.score_available_seats = max(s.score_available_seats, 0.0)
            s.score_habit = max(s.score_habit, -1.0)

        return sorted(self.scores.values(), key=lambda s: s.total_score, reverse=True)

    def select_driver_sets(self) -> List[DriverSet]:
        """
        Calculate scores then produce up to 4 distinct candidate driver sets:
            1. Best Score
            2. Long Distance  (inter-driver distance bonus)
            3. Short Distance (inter-driver closeness bonus)
            4. Detour-Aware  (tries one extra car when it avoids unfair detours)
        """
        LOG_INFO("generating candidate driver sets")
        self.calculate_all_scores()
        total_people = len(self.participants)
        sets: List[DriverSet] = []

        # ---- Set 1: Best total score -----------------------------------------
        ranked = sorted(
            self.potential_drivers,
            key=lambda i: self.scores[i].total_score,
            reverse=True,
        )
        set1 = self._pick_enough_drivers(ranked, total_people)
        if set1:
            sets.append(self._make_driver_set(
                "Best Score", set1,
                {i: self.scores[i].total_score for i in set1},
            ))
        minimum_driver_count = len(set1)

        # ---- Inter-driver distance scores ------------------------------------
        if len(self.potential_drivers) < 2:
            return sets

        drv_dist_raw: dict = {}
        for d1 in self.potential_drivers:
            total_d = sum(
                haversine_distance(
                    self.participants[d1].location,
                    self.participants[d2].location,
                )
                for d2 in self.potential_drivers if d2 != d1
            )
            drv_dist_raw[d1] = total_d

        max_dist = max(drv_dist_raw.values()) if drv_dist_raw else 1.0
        if max_dist == 0:
            max_dist = 1.0

        # ---- Set 2: Long Distance bonus (spread-out drivers favoured) --------
        long_scores: dict = {}
        for d in self.potential_drivers:
            norm = drv_dist_raw[d] / max_dist * 5.0
            long_scores[d] = self.scores[d].total_score + norm

        ranked_long = sorted(
            self.potential_drivers, key=lambda i: long_scores[i], reverse=True
        )
        set2 = self._pick_enough_drivers(ranked_long, total_people)
        if set2 and (not set1 or set(set2) != set(set1)):
            sets.append(self._make_driver_set(
                "Long Distance", set2,
                {i: long_scores[i] for i in set2},
            ))

        # ---- Set 3: Short Distance bonus (clustered drivers favoured) --------
        short_scores: dict = {}
        for d in self.potential_drivers:
            norm_long = drv_dist_raw[d] / max_dist * 5.0
            short_scores[d] = self.scores[d].total_score + (5.0 - norm_long)

        ranked_short = sorted(
            self.potential_drivers, key=lambda i: short_scores[i], reverse=True
        )
        set3 = self._pick_enough_drivers(ranked_short, total_people)
        existing = [set(s.driver_indices) for s in sets]
        if set3 and set(set3) not in existing:
            sets.append(self._make_driver_set(
                "Short Distance", set3,
                {i: short_scores[i] for i in set3},
            ))

        # ---- Set 4: Detour-Aware (search extra-driver combinations) ----------
        if minimum_driver_count and minimum_driver_count < len(self.potential_drivers):
            target_driver_count = min(minimum_driver_count + 1, len(self.potential_drivers))
            set4, detour_scores = self._pick_detour_aware_set(
                total_people,
                target_driver_count=target_driver_count,
            )
            existing = [set(s.driver_indices) for s in sets]
            if set4 and set(set4) not in existing:
                sets.append(self._make_driver_set(
                    "Detour-Aware", set4,
                    detour_scores,
                ))

        LOG_INFO(f"driver set selection complete — {len(sets)} sets generated")
        for ds in sets:
            LOG_DEBUG(f"  set '{ds.name}' — drivers={ds.driver_indices}, seats={ds.total_seats}")
        return sets

    # ------------------------------------------------------------------
    # Scoring algorithms (Algorithms 1–5 in the thesis)
    # ------------------------------------------------------------------

    def _score_distance_passengers(self):
        """Algorithm 1 – SDP: sum of 1/dist² to every other participant."""
        raw: dict = {}
        for d_idx in self.potential_drivers:
            d_loc = self.participants[d_idx].location
            score = 0.0
            for p_idx, p in enumerate(self.participants):
                if p_idx == d_idx:
                    continue
                dist = haversine_distance(d_loc, p.location)
                if dist > 0.001:
                    score += 1.0 / (dist ** 2)
            raw[d_idx] = score
        self._normalise(raw, "score_distance_passengers")

    def _score_environmental_impact(self):
        """Algorithm 2 – SEI: fuel_type_factor × km/L  (electric gets fixed max)."""
        raw: dict = {}
        for d_idx in self.potential_drivers:
            car = self.participants[d_idx].car
            factor = FUEL_TYPE_FACTORS.get(car.fuel_type, 1.0)
            if car.fuel_type == "electric":
                raw[d_idx] = ELECTRIC_FIXED_SCORE
            elif car.consumption_l_per_100km > 0:
                km_per_l = 100.0 / car.consumption_l_per_100km
                raw[d_idx] = factor * km_per_l
            else:
                raw[d_idx] = factor
        self._normalise(raw, "score_environmental")

    def _score_distance_destination(self):
        """Algorithm 3 – SDD: distance from driver to destination (farther = better)."""
        raw: dict = {}
        for d_idx in self.potential_drivers:
            raw[d_idx] = haversine_distance(
                self.participants[d_idx].location, self.destination
            )
        self._normalise(raw, "score_distance_destination")

    def _score_available_seats(self):
        """Algorithm 4 – SAS: number of available passenger seats."""
        raw: dict = {}
        for d_idx in self.potential_drivers:
            raw[d_idx] = float(self.participants[d_idx].car.available_seats)
        self._normalise(raw, "score_available_seats")

    def _score_habit(self):
        """Algorithm 5 – SH: habituality score (debits-credits)."""
        raw: dict = {}
        for d_idx in self.potential_drivers:
            raw[d_idx] = self.participants[d_idx].habit_score
        max_abs = max((abs(v) for v in raw.values()), default=1.0)
        if max_abs == 0:
            max_abs = 1.0
        for d_idx in self.potential_drivers:
            normalised = raw[d_idx] / max_abs
            self.scores[d_idx].score_habit = max(normalised, -1.0)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _normalise(self, raw: dict, attr: str):
        """Divide every value in *raw* by its maximum and store in self.scores."""
        if not raw:
            return
        max_val = max(raw.values())
        if max_val <= 0:
            max_val = 1.0
        for d_idx, val in raw.items():
            setattr(self.scores[d_idx], attr, val / max_val)

    def _pick_enough_drivers(
        self, sorted_indices: List[int], total_people: int
    ) -> List[int]:
        """
        Walk through *sorted_indices* (best first) and collect drivers until
        the sum of their available seats ≥ number of remaining passengers.
        """
        selected: List[int] = list(self.mandatory_drivers)
        total_seats = sum(self.participants[d_idx].car.available_seats for d_idx in selected)
        passengers_remaining = total_people - len(selected)
        if total_seats >= passengers_remaining:
            return selected
        for d_idx in sorted_indices:
            if d_idx in selected:
                continue
            selected.append(d_idx)
            total_seats += self.participants[d_idx].car.available_seats
            passengers_remaining = total_people - len(selected)
            if total_seats >= passengers_remaining:
                return selected
        return selected   # all drivers, still not enough → return them all

    def _pick_target_driver_count(
        self, sorted_indices: List[int], total_people: int, target_driver_count: int
    ) -> List[int]:
        """Pick at least *target_driver_count* drivers while still satisfying seat needs."""
        selected: List[int] = list(self.mandatory_drivers)
        total_seats = sum(self.participants[d_idx].car.available_seats for d_idx in selected)
        target_driver_count = max(target_driver_count, len(selected))
        passengers_remaining = total_people - len(selected)
        if len(selected) >= target_driver_count and total_seats >= passengers_remaining:
            return selected
        for d_idx in sorted_indices:
            if d_idx in selected:
                continue
            selected.append(d_idx)
            total_seats += self.participants[d_idx].car.available_seats
            passengers_remaining = total_people - len(selected)
            if len(selected) >= target_driver_count and total_seats >= passengers_remaining:
                return selected
        return selected

    def _pick_detour_aware_set(
        self,
        total_people: int,
        target_driver_count: int,
    ) -> tuple[List[int], dict[int, float]]:
        """Search feasible driver combinations and keep the one with the fairest geography."""
        best_combo: tuple[int, ...] | None = None
        best_score: tuple[float, float, float, float, float] | None = None
        best_breakdown: dict[int, float] = {}

        optional_drivers = [driver_index for driver_index in self.potential_drivers if driver_index not in self.mandatory_drivers]
        optional_slots = max(target_driver_count - len(self.mandatory_drivers), 0)
        candidate_combos = (
            [tuple(self.mandatory_drivers)]
            if optional_slots == 0
            else [tuple(self.mandatory_drivers) + combo for combo in combinations(optional_drivers, optional_slots)]
        )

        for combo in candidate_combos:
            total_seats = sum(self.participants[index].car.available_seats for index in combo)
            passengers_remaining = total_people - len(combo)
            if total_seats < passengers_remaining:
                continue
            combo_score, breakdown = self._detour_aware_combo_score(combo)
            if best_score is None or combo_score > best_score:
                best_combo = combo
                best_score = combo_score
                best_breakdown = breakdown

        return list(best_combo or []), best_breakdown

    def _detour_aware_combo_score(
        self,
        combo: tuple[int, ...],
    ) -> tuple[tuple[float, float, float, float, float], dict[int, float]]:
        """Estimate whether this driver set avoids forcing a local driver into a remote detour."""
        capacities = {
            driver_index: self.participants[driver_index].car.available_seats
            for driver_index in combo
        }
        assigned_passengers: dict[int, List[int]] = {driver_index: [] for driver_index in combo}
        mismatch_penalty = 0.0

        passengers = [index for index in range(len(self.participants)) if index not in combo]
        passenger_priorities: list[tuple[float, float, int, list[tuple[float, int]]]] = []
        for passenger_index in passengers:
            distances = sorted(
                (
                    haversine_distance(
                        self.participants[driver_index].location,
                        self.participants[passenger_index].location,
                    ),
                    driver_index,
                )
                for driver_index in combo
            )
            nearest_distance = distances[0][0]
            second_distance = distances[1][0] if len(distances) > 1 else nearest_distance
            passenger_priorities.append(
                (
                    second_distance - nearest_distance,
                    second_distance,
                    passenger_index,
                    distances,
                )
            )
        passenger_priorities.sort(reverse=True)

        for _advantage, _second_distance, passenger_index, distances in passenger_priorities:
            nearest_distance = distances[0][0]
            for distance, driver_index in distances:
                if capacities[driver_index] <= 0:
                    continue
                capacities[driver_index] -= 1
                assigned_passengers[driver_index].append(passenger_index)
                mismatch_penalty += max(distance - nearest_distance, 0.0)
                break

        route_estimates = []
        occupancy_levels = []
        for driver_index in combo:
            direct_to_destination = haversine_distance(
                self.participants[driver_index].location,
                self.destination,
            )
            passenger_distances = [
                haversine_distance(
                    self.participants[driver_index].location,
                    self.participants[passenger_index].location,
                )
                for passenger_index in assigned_passengers[driver_index]
            ]
            proxy_route = direct_to_destination
            if passenger_distances:
                proxy_route += max(passenger_distances) + (sum(passenger_distances) * 0.35)
            route_estimates.append(proxy_route)
            occupancy_levels.append(len(assigned_passengers[driver_index]))

        longest_route = max(route_estimates, default=0.0)
        route_spread = (
            max(route_estimates) - min(route_estimates)
            if len(route_estimates) >= 2
            else 0.0
        )
        total_detour = sum(route_estimates)
        occupancy_spread = (
            max(occupancy_levels) - min(occupancy_levels)
            if len(occupancy_levels) >= 2
            else 0.0
        )
        close_to_destination_bias = sum(
            1.0 - self.scores[driver_index].score_distance_destination
            for driver_index in combo
        ) / max(len(combo), 1)

        score = (
            -longest_route,
            -route_spread,
            -mismatch_penalty,
            -total_detour,
            -occupancy_spread + (close_to_destination_bias * 0.01),
        )
        breakdown = {
            driver_index: round(route_estimates[position], 4)
            for position, driver_index in enumerate(combo)
        }
        return score, breakdown

    def _make_driver_set(
        self, name: str, indices: List[int], scores: dict
    ) -> DriverSet:
        return DriverSet(
            name=name,
            driver_indices=list(indices),
            total_seats=sum(
                self.participants[i].car.available_seats for i in indices
            ),
            scores=scores,
        )
