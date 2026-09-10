"""
Drivers Manager Project - Utility Functions
=============================================
Geographic distance calculations.
"""

import math

from core.models import Location


def haversine_distance(loc1: Location, loc2: Location) -> float:
    """
    Calculate the great-circle distance between two geographic points
    using the Haversine formula.

    Parameters
    ----------
    loc1, loc2 : Location
        The two points.

    Returns
    -------
    float
        Distance in kilometres.
    """
    R = 6_371.0  # Earth's mean radius in km

    lat1 = math.radians(loc1.latitude)
    lon1 = math.radians(loc1.longitude)
    lat2 = math.radians(loc2.latitude)
    lon2 = math.radians(loc2.longitude)

    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = (math.sin(dlat / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return R * c


def get_sample_data():
    """
    Return sample participants and destination around Modena, Italy,
    useful for quick testing of the application.
    """
    from core.models import Car, Participant
    from core.logging_config import LOG_INFO

    LOG_INFO("loading sample dataset — 8 participants around Modena")
    destination = Location("Modena Centro", 44.6471, 10.9252)

    participants = [
        Participant(
            name="Alice",
            location=Location("Reggio Emilia", 44.6989, 10.6310),
            car=Car(fuel_type="gasoline", consumption_l_per_100km=7.0, total_seats=5),
            habit_score=0.0,
        ),
        Participant(
            name="Bob",
            location=Location("Bologna", 44.4949, 11.3426),
            car=Car(fuel_type="diesel", consumption_l_per_100km=5.0, total_seats=5),
            habit_score=-2.0,
        ),
        Participant(
            name="Carla",
            location=Location("Carpi", 44.7839, 10.8853),
            car=Car(fuel_type="electric", consumption_l_per_100km=0, total_seats=5),
            habit_score=1.0,
        ),
        Participant(
            name="Davide",
            location=Location("Sassuolo", 44.5432, 10.7849),
            car=None,
            habit_score=3.0,
        ),
        Participant(
            name="Elena",
            location=Location("Formigine", 44.5724, 10.8443),
            car=None,
            habit_score=0.5,
        ),
        Participant(
            name="Fabio",
            location=Location("Maranello", 44.5295, 10.8680),
            car=None,
            habit_score=-1.0,
        ),
        Participant(
            name="Giulia",
            location=Location("Vignola", 44.4830, 10.9818),
            car=Car(fuel_type="hybrid", consumption_l_per_100km=4.0, total_seats=5),
            habit_score=0.0,
        ),
        Participant(
            name="Hasan",
            location=Location("Castelfranco E.", 44.5983, 11.0547),
            car=None,
            habit_score=2.0,
        ),
    ]
    return participants, destination
