"""
Drivers Manager Project configuration.
"""

# Environmental impact factors by fuel type.
FUEL_TYPE_FACTORS = {
    "electric": 5.0,
    "hybrid": 4.0,
    "methane": 3.5,
    "lpg": 3.0,
    "gasoline": 2.0,
    "diesel": 1.0,
}

# Electric cars get a fixed high environmental score (no liquid fuel consumption).
ELECTRIC_FIXED_SCORE = 10.0

# APCA algorithm defaults.
APCA_NUM_ANTS = 30
APCA_NUM_ITERATIONS = 150
APCA_ALPHA = 1.0
APCA_BETA = 2.5
APCA_RHO = 0.15
APCA_Q = 1.0
APCA_TAU0 = 0.1
APCA_SIGMA = 50.0

# Route solver selection (see docs/superpowers/specs/2026-09-10-route-solver-design.md).
DEFAULT_SOLVER = "local_search"
SOLVER_CHOICES = ("local_search", "annealing", "ant_colony", "exhaustive")

# Cost estimation in EUR/km by fuel type.
COST_PER_KM = {
    "electric": 0.04,
    "hybrid": 0.06,
    "methane": 0.05,
    "lpg": 0.06,
    "gasoline": 0.10,
    "diesel": 0.08,
}

# GUI and app settings.
APP_TITLE = "Drivers Manager Project"
APP_WIDTH = 1100
APP_HEIGHT = 750

FUEL_TYPES = ["electric", "hybrid", "methane", "lpg", "gasoline", "diesel"]
VEHICLE_TYPES = {"car": "Car", "motorbike": "Motorbike"}
FUEL_TYPE_DEFAULT_CONSUMPTION = {
    "electric": 0.0,
    "hybrid": 4.3,
    "methane": 3.8,
    "lpg": 7.9,
    "gasoline": 6.7,
    "diesel": 4.9,
}
FUEL_TYPE_LABELS = {
    "electric": "Electric",
    "hybrid": "Hybrid",
    "methane": "Methane/CNG",
    "lpg": "LPG",
    "gasoline": "Gasoline",
    "diesel": "Diesel",
}

# Average passenger-car emissions used for the shared-ride impact estimate.
CO2_KG_PER_KM = 0.12

# Time-constraint estimation defaults for the local planner.
ESTIMATED_AVERAGE_SPEED_KMH = 42.0
PICKUP_STOP_MINUTES = 3.0
TIME_WINDOW_PENALTY_PER_MINUTE = 0.08
RETURN_WINDOW_PREFERENCE_WEIGHT = 0.4
RETURN_PLAN_DISTANCE_WEIGHT = 0.02
PICKUP_ORDER_RULE_PENALTY = 1.8
