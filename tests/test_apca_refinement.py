"""Tests for the deterministic pickup-order refinement shared by every route solver."""

import random
import unittest

from core.apca import APCAAlgorithm
from core.models import Car, Location, Participant


def _build_line_scenario():
    """One driver west of three passengers laid out on a straight line to the destination.

    The only sensible pickup order is nearest-first (local indices 0, 1, 2).
    """
    driver = Participant(
        name="Driver",
        location=Location("Driver home", 45.0, 7.0),
        car=Car(fuel_type="gasoline", consumption_l_per_100km=6.5, total_seats=5),
    )
    passengers = [
        Participant(name="P1", location=Location("P1 home", 45.0, 7.2)),
        Participant(name="P2", location=Location("P2 home", 45.0, 7.4)),
        Participant(name="P3", location=Location("P3 home", 45.0, 7.6)),
    ]
    destination = Location("Destination", 45.0, 7.8)
    return [driver, *passengers], destination


class PickupOrderRefinementTests(unittest.TestCase):
    def _make_algorithm(self):
        participants, destination = _build_line_scenario()
        return APCAAlgorithm(
            participants,
            driver_indices=[0],
            destination=destination,
            num_ants=5,
            num_iterations=5,
        )

    def test_refinement_fixes_reversed_pickup_order(self):
        algorithm = self._make_algorithm()
        bad_assignment = {0: [2, 1, 0]}  # farthest passenger first
        bad_fitness = algorithm._evaluate(bad_assignment, [])

        refined_assignment, refined_fitness = algorithm._refine_pickup_orders(
            bad_assignment, [], bad_fitness
        )

        self.assertEqual(refined_assignment[0], [0, 1, 2])
        self.assertGreater(refined_fitness, bad_fitness)
        self.assertLess(
            algorithm._route_distance(0, refined_assignment[0]),
            algorithm._route_distance(0, bad_assignment[0]),
        )

    def test_refinement_keeps_already_optimal_order(self):
        algorithm = self._make_algorithm()
        good_assignment = {0: [0, 1, 2]}
        good_fitness = algorithm._evaluate(good_assignment, [])

        refined_assignment, refined_fitness = algorithm._refine_pickup_orders(
            good_assignment, [], good_fitness
        )

        self.assertEqual(refined_assignment[0], [0, 1, 2])
        self.assertEqual(refined_fitness, good_fitness)

    def test_solve_returns_optimal_order_end_to_end(self):
        algorithm = self._make_algorithm()
        result = algorithm.solve()

        self.assertEqual(result.unassigned_passenger_indices, [])
        self.assertEqual(len(result.assignments), 1)
        # Participant indices 1..3 picked up nearest-first along the line.
        self.assertEqual(result.assignments[0].passenger_indices, [1, 2, 3])


class SolverBenchmarkTests(unittest.TestCase):
    """Seeded regression guard: solution quality must not silently degrade.

    Bounds are set with margin below the solver's current results
    (fitness 5.62, distance 71.7 km with the km-first efficiency objective)
    so genuine improvements pass and regressions fail.
    """

    def test_two_driver_benchmark_scenario_quality_floor(self):
        participants = [
            Participant("D1", Location("d1", 44.60, 10.80), car=Car("gasoline", 6.5, 5)),
            Participant("D2", Location("d2", 44.75, 11.05), car=Car("diesel", 5.0, 4)),
            Participant("P1", Location("p1", 44.62, 10.85)),
            Participant("P2", Location("p2", 44.66, 10.90)),
            Participant("P3", Location("p3", 44.70, 10.95)),
            Participant("P4", Location("p4", 44.72, 11.00)),
            Participant("P5", Location("p5", 44.78, 11.10)),
            Participant("P6", Location("p6", 44.64, 10.99)),
        ]
        destination = Location("dest", 44.80, 11.20)

        random.seed(42)
        algorithm = APCAAlgorithm(
            participants, [0, 1], destination, num_ants=20, num_iterations=60
        )
        result = algorithm.solve()

        self.assertEqual(result.unassigned_passenger_indices, [])
        self.assertGreaterEqual(result.fitness, 5.3)
        self.assertLessEqual(result.total_distance_km, 75.0)


if __name__ == "__main__":
    unittest.main()
