"""Solver strategies in core/apca.py: brute-force parity, the clustered optimum, fallbacks, rules."""

import itertools
import random
import unittest

from core.apca import APCAAlgorithm, EXHAUSTIVE_MAX_COMBINATIONS
from core.models import Car, Location, Participant


def _instance(seed: int, drivers: int, passengers: int, seats: int = 5):
    rng = random.Random(seed)

    def loc(name: str) -> Location:
        return Location(name, 44.6471 + rng.uniform(-0.25, 0.25), 10.9252 + rng.uniform(-0.35, 0.35))

    people = [Participant(f"D{i}", loc(f"d{i}"), car=Car("gasoline", 6.5, seats)) for i in range(drivers)]
    people += [Participant(f"P{i}", loc(f"p{i}")) for i in range(passengers)]
    return people, Location("dest", 44.6471, 10.9252)


def _clustered():
    """Five passengers beside driver 0, one beside driver 1, both cars seat five: optimum is 5+1."""
    people = [
        Participant("D0", Location("d0", 44.60, 10.60), car=Car("gasoline", 6.5, 6)),
        Participant("D1", Location("d1", 44.90, 11.20), car=Car("gasoline", 6.5, 6)),
    ]
    people += [Participant(f"P{i}", Location(f"p{i}", 44.60 + 0.01 * i, 10.62 + 0.03 * i)) for i in range(5)]
    people.append(Participant("P5", Location("p5", 44.85, 11.20)))
    return people, Location("dest", 44.60, 11.20)


def _min_km(algo: APCAAlgorithm) -> float:
    """Brute-force shortest total km over every capacity-feasible assignment and pickup order."""
    best = float("inf")
    for labels in itertools.product(range(algo.m), repeat=algo.n):
        groups = {d: [p for p, label in enumerate(labels) if label == d] for d in range(algo.m)}
        if any(len(groups[d]) > algo.capacity[d] for d in range(algo.m)):
            continue
        best = min(best, sum(
            min(algo._route_distance(d, list(order)) for order in itertools.permutations(groups[d]))
            for d in range(algo.m)
        ))
    return best


class LocalSearchTests(unittest.TestCase):
    def test_matches_brute_force_km_on_random_instances(self) -> None:
        for seed in range(4):
            people, dest = _instance(seed, 2, 6)
            algo = APCAAlgorithm(people, [0, 1], dest)
            result = algo.solve()
            self.assertEqual(result.unassigned_passenger_indices, [])
            self.assertAlmostEqual(result.total_distance_km, _min_km(algo), delta=0.01, msg=f"seed {seed}")

    def test_is_deterministic(self) -> None:
        people, dest = _instance(7, 3, 12)
        first = APCAAlgorithm(people, [0, 1, 2], dest).solve()
        second = APCAAlgorithm(people, [0, 1, 2], dest).solve()
        self.assertEqual(
            [a.passenger_indices for a in first.assignments],
            [a.passenger_indices for a in second.assignments],
        )

    def test_ride_together_pair_stays_in_one_car(self) -> None:
        people, dest = _clustered()
        # P5 (index 7) lives beside D1; forcing it to ride with P0 (index 2) must keep the pair together.
        result = APCAAlgorithm(people, [0, 1], dest, ride_together_rules=[(2, 7)]).solve()
        self.assertTrue(any({2, 7} <= set(a.passenger_indices) for a in result.assignments))
        self.assertEqual(result.unassigned_passenger_indices, [])


class EverySolverTests(unittest.TestCase):
    def test_all_solvers_find_the_unbalanced_optimum(self) -> None:
        for solver in ("local_search", "annealing", "ant_colony", "exhaustive"):
            people, dest = _clustered()
            random.seed(1)
            result = APCAAlgorithm(people, [0, 1], dest, solver=solver).solve()
            self.assertEqual(result.solver_used, solver)
            self.assertLess(result.total_distance_km, 83.0, solver)
            self.assertEqual(sorted(len(a.passenger_indices) for a in result.assignments), [1, 5], solver)

    def test_exhaustive_falls_back_to_local_search_on_big_groups(self) -> None:
        people, dest = _instance(2, 4, 8)
        self.assertGreater(4 ** 8, EXHAUSTIVE_MAX_COMBINATIONS)
        result = APCAAlgorithm(people, [0, 1, 2, 3], dest, solver="exhaustive").solve()
        self.assertEqual(result.solver_used, "local_search")
        self.assertEqual(result.unassigned_passenger_indices, [])

    def test_unknown_solver_uses_default(self) -> None:
        people, dest = _instance(3, 2, 4)
        self.assertEqual(APCAAlgorithm(people, [0, 1], dest, solver="bogus").solver, "local_search")


class ObjectiveTests(unittest.TestCase):
    def test_efficiency_mode_prefers_spread_over_cramming(self) -> None:
        # Two drivers with two neighbours each; cramming all four into one car costs about 24 km more.
        dest = Location("dest", 44.60, 11.00)
        people = [
            Participant("D0", Location("d0", 44.60, 10.70), car=Car("gasoline", 6.5, 5)),
            Participant("D1", Location("d1", 44.80, 11.00), car=Car("gasoline", 6.5, 5)),
            Participant("P0", Location("p0", 44.60, 10.75)),
            Participant("P1", Location("p1", 44.60, 10.80)),
            Participant("P2", Location("p2", 44.75, 11.00)),
            Participant("P3", Location("p3", 44.70, 11.00)),
        ]
        algo = APCAAlgorithm(people, [0, 1], dest)
        self.assertGreater(algo._evaluate({0: [0, 1], 1: [2, 3]}, []), algo._evaluate({0: [0, 1, 2, 3], 1: []}, []))


if __name__ == "__main__":
    unittest.main()
