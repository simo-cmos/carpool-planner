"""
Quick syntax and import check for all project modules.
Run with: python -m tests.test_check
"""

import py_compile
import os
import sys
from pathlib import Path

files = [
    "core/__init__.py",
    "core/config.py",
    "core/models.py",
    "core/utils.py",
    "core/driver_selection.py",
    "core/apca.py",
    "core/logging_config.py",
    "legacy/__init__.py",
    "legacy/gui.py",
    "legacy/main.py",
    "app/__init__.py",
    "app/database.py",
    "app/geocoding.py",
    "app/mapping.py",
    "app/services/__init__.py",
    "app/services/common.py",
    "app/services/destinations.py",
    "app/services/groups.py",
    "app/services/history.py",
    "app/services/participants.py",
    "app/services/planner.py",
    "app/services/settings.py",
    "app/services/workspace.py",
    "app/main.py",
]

ok = True
for file_path in files:
    try:
        py_compile.compile(file_path, doraise=True)
        print(f"  OK  {file_path}")
    except py_compile.PyCompileError as error:
        print(f"  ERR  {file_path}: {error}")
        ok = False

if ok:
    smoke_data_dir = Path(".tmp-smoke-db")
    smoke_data_dir.mkdir(exist_ok=True)
    os.environ["DMPROJECT_DATA_DIR"] = str(smoke_data_dir.resolve())
    from core.apca import APCAAlgorithm
    from app.database import init_db
    from app.main import app
    from core.driver_selection import DriverSelector
    from core.utils import get_sample_data

    print("\nAll files compile OK.")
    participants, destination = get_sample_data()
    init_db()
    print(f"\nSample data: {len(participants)} participants, destination = {destination.name}")
    print(f"FastAPI app loaded: {app.title}")

    selector = DriverSelector(participants, destination)
    driver_sets = selector.select_driver_sets()
    print(f"Driver sets generated: {len(driver_sets)}")
    for driver_set in driver_sets:
        names = [participants[index].name for index in driver_set.driver_indices]
        print(f"  {driver_set.name}: {names} (seats={driver_set.total_seats})")

    if driver_sets:
        driver_set = driver_sets[0]
        apca = APCAAlgorithm(
            participants,
            driver_set.driver_indices,
            destination,
            num_ants=10,
            num_iterations=20,
        )
        result = apca.solve()
        result.driver_set_name = driver_set.name
        print(f"\nAPCA result for '{driver_set.name}':")
        print(f"  Fitness: {result.fitness:.4f}")
        print(f"  Total distance: {result.total_distance_km:.2f} km")
        print(f"  Total cost: EUR {result.total_cost_eur:.2f}")
        for assignment in result.assignments:
            driver = participants[assignment.driver_index]
            passengers = [participants[index].name for index in assignment.passenger_indices]
            print(
                f"  Driver {driver.name} -> {passengers} "
                f"({assignment.route_distance_km:.1f} km, "
                f"EUR {assignment.cost_per_person_eur:.2f}/person)"
            )
        if result.unassigned_passenger_indices:
            unassigned = [participants[index].name for index in result.unassigned_passenger_indices]
            print(f"  Unassigned: {unassigned}")

    print("\n=== ALL TESTS PASSED ===")
else:
    print("\nFix the errors above before running.")
    sys.exit(1)
