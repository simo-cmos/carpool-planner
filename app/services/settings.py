"""Settings and defaults for the local web app."""

from __future__ import annotations

from typing import Any

from core.logging_config import LOG_INFO, LOG_DEBUG
from core.config import APCA_ALPHA, APCA_BETA, APCA_NUM_ANTS, APCA_NUM_ITERATIONS, APCA_RHO, DEFAULT_SOLVER, SOLVER_CHOICES

from app.database import get_setting, set_setting


DEFAULT_APP_SETTINGS = {
    "map_center": {"latitude": 44.6471, "longitude": 10.9252, "zoom": 9},
    "optimization": {
        "algorithm": DEFAULT_SOLVER,
        "num_ants": APCA_NUM_ANTS,
        "num_iterations": APCA_NUM_ITERATIONS,
        "alpha": APCA_ALPHA,
        "beta": APCA_BETA,
        "rho": APCA_RHO,
        "plan_return_separately": False,
        "allow_meetup_pooling": True,
        "max_meetup_self_transfer_km": 3.0,
    },
    "sharing": {
        "guest_public_base_url": "",
        "admin_public_base_url": "",
    },
    "notifications": {
        "enabled": True,
        "notify_guest_visits": True,
        "notify_guest_submissions": True,
        "poll_seconds": 5,
    },
}


# Copy for the Settings drop-down and its hint cards. Ratings are 1-3 (meter elements).
SOLVER_CATALOG = {
    "local_search": {
        "name": "Local search",
        "speed": 3, "quality": 3, "consistency": 3,
        "pro": "Fast and repeatable: the same people always get the same plan, usually the shortest one.",
        "con": "Can settle on a slightly longer plan when many rules pull against each other.",
    },
    "annealing": {
        "name": "Simulated annealing",
        "speed": 2, "quality": 3, "consistency": 1,
        "pro": "Explores widely, so unusual fairness or time-window trade-offs get a fair look.",
        "con": "Results vary from run to run and it takes a few seconds on big groups.",
    },
    "ant_colony": {
        "name": "Ant colony",
        "speed": 1, "quality": 2, "consistency": 1,
        "pro": "The original research method (Huang et al. 2019); the ants, iterations and alpha/beta/rho knobs apply here.",
        "con": "Slowest for the quality it reaches, random, and it needs tuning to beat local search.",
    },
    "exhaustive": {
        "name": "Exhaustive",
        "speed": 1, "quality": 3, "consistency": 3,
        "pro": "Checks every possible split, so the km are provably the shortest.",
        "con": "Only up to about 8 passengers; bigger groups silently use local search instead.",
    },
}


def get_app_settings() -> dict[str, Any]:
    """Return application defaults, falling back to sane defaults."""
    LOG_DEBUG("loading app settings")
    current = get_setting("app_defaults", None)
    if current is None:
        set_setting("app_defaults", DEFAULT_APP_SETTINGS)
        return DEFAULT_APP_SETTINGS
    current_sharing = current.get("sharing", {})
    legacy_public_base_url = current_sharing.get("public_base_url", "")
    return {
        "map_center": DEFAULT_APP_SETTINGS["map_center"] | current.get("map_center", {}),
        "optimization": DEFAULT_APP_SETTINGS["optimization"] | current.get("optimization", {}),
        "sharing": DEFAULT_APP_SETTINGS["sharing"] | current_sharing | {
            "guest_public_base_url": current_sharing.get("guest_public_base_url", legacy_public_base_url),
            "admin_public_base_url": current_sharing.get("admin_public_base_url", ""),
        },
        "notifications": DEFAULT_APP_SETTINGS["notifications"] | current.get("notifications", {}),
    }


def save_app_settings(
    *,
    num_ants: int,
    num_iterations: int,
    alpha: float,
    beta: float,
    rho: float,
    map_latitude: float,
    map_longitude: float,
    map_zoom: int,
    plan_return_separately: bool,
    allow_meetup_pooling: bool = True,
    max_meetup_self_transfer_km: float = 3.0,
    notifications_enabled: bool = True,
    algorithm: str = DEFAULT_SOLVER,
) -> None:
    """Persist app defaults for map and optimization settings."""
    LOG_INFO(f"saving app settings - solver={algorithm}, ants={num_ants}, iter={num_iterations}")
    set_setting(
        "app_defaults",
        {
            "optimization": {
                "algorithm": algorithm if algorithm in SOLVER_CHOICES else DEFAULT_SOLVER,
                "num_ants": num_ants,
                "num_iterations": num_iterations,
                "alpha": alpha,
                "beta": beta,
                "rho": rho,
                "plan_return_separately": plan_return_separately,
                "allow_meetup_pooling": allow_meetup_pooling,
                "max_meetup_self_transfer_km": max_meetup_self_transfer_km,
            },
            "map_center": {
                "latitude": map_latitude,
                "longitude": map_longitude,
                "zoom": map_zoom,
            },
            "sharing": get_app_settings()["sharing"],
            "notifications": get_app_settings()["notifications"] | {
                "enabled": notifications_enabled,
            },
        },
    )


def save_public_access_urls(*, guest_public_base_url: str, admin_public_base_url: str) -> None:
    """Persist separate guest/admin public URLs for tunnel access."""
    settings = get_app_settings()
    set_setting(
        "app_defaults",
        {
            "map_center": settings["map_center"],
            "optimization": settings["optimization"],
            "sharing": {
                "guest_public_base_url": guest_public_base_url.strip().rstrip("/"),
                "admin_public_base_url": admin_public_base_url.strip().rstrip("/"),
            },
            "notifications": settings["notifications"],
        },
    )


def save_public_access_url(*, mode: str, public_base_url: str) -> None:
    """Persist a single guest/admin public URL while preserving the other value."""
    settings = get_app_settings()
    sharing = settings["sharing"]
    normalized = public_base_url.strip().rstrip("/")
    normalized_mode = mode.strip().lower()
    if normalized_mode == "guest":
        guest_public_base_url = normalized
        admin_public_base_url = sharing.get("admin_public_base_url", "")
    elif normalized_mode == "admin":
        guest_public_base_url = sharing.get("guest_public_base_url", "")
        admin_public_base_url = normalized
    else:
        raise ValueError("mode must be 'guest' or 'admin'")
    save_public_access_urls(
        guest_public_base_url=guest_public_base_url,
        admin_public_base_url=admin_public_base_url,
    )
