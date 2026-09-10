"""Reusable meetup spot persistence."""

from __future__ import annotations

from typing import Any

from core.logging_config import LOG_INFO
from app.database import execute, fetch_all, fetch_one


def save_meetup_spot(
    *,
    name: str,
    latitude: float,
    longitude: float,
    address_text: str | None,
    is_free_parking: bool = True,
    has_ev_charging: bool = False,
    notes: str | None = None,
) -> None:
    """Save a reusable meetup spot if it is not already present."""

    normalized_name = name.strip()
    normalized_address = (address_text or "").strip() or None
    normalized_notes = (notes or "").strip() or None
    LOG_INFO(f"saving meetup spot - {normalized_name}")
    existing = fetch_one(
        """
        SELECT id
        FROM saved_meetup_spots
        WHERE lower(name) = lower(?)
          AND abs(latitude - ?) < 0.00001
          AND abs(longitude - ?) < 0.00001
          AND coalesce(lower(address_text), '') = coalesce(lower(?), '')
        """,
        (normalized_name, latitude, longitude, normalized_address),
    )
    if existing is not None:
        return
    execute(
        """
        INSERT INTO saved_meetup_spots(
            name, address_text, latitude, longitude, is_free_parking, has_ev_charging, notes
        )
        VALUES(?, ?, ?, ?, ?, ?, ?)
        """,
        (
            normalized_name,
            normalized_address,
            latitude,
            longitude,
            int(is_free_parking),
            int(has_ev_charging),
            normalized_notes,
        ),
    )


def list_saved_meetup_spots() -> list[dict[str, Any]]:
    """Return reusable meetup spots."""

    return [
        dict(row)
        for row in fetch_all(
            """
            SELECT id, name, address_text, latitude, longitude, is_free_parking, has_ev_charging, notes, created_at
            FROM saved_meetup_spots
            ORDER BY created_at DESC, id DESC
            """
        )
    ]


def get_meetup_spot(spot_id: int) -> dict[str, Any] | None:
    """Return one saved meetup spot."""

    row = fetch_one(
        """
        SELECT id, name, address_text, latitude, longitude, is_free_parking, has_ev_charging, notes, created_at
        FROM saved_meetup_spots
        WHERE id = ?
        """,
        (spot_id,),
    )
    return dict(row) if row is not None else None


def delete_meetup_spot(spot_id: int) -> None:
    """Delete a saved meetup spot."""

    execute("DELETE FROM saved_meetup_spots WHERE id = ?", (spot_id,))
