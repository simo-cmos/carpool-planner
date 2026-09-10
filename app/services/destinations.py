"""Destination favorites persistence."""

from __future__ import annotations

from typing import Any

from core.logging_config import LOG_INFO
from app.database import execute, fetch_all, fetch_one
from app.services.common import save_destination


def save_destination_favorite(
    *,
    name: str,
    latitude: float,
    longitude: float,
    address_text: str | None,
    target_arrival_time: str | None = None,
) -> None:
    """Save the current destination as a reusable favorite."""
    LOG_INFO(f"saving destination favorite — {name}")
    normalized_name = name.strip()
    normalized_address = (address_text or "").strip() or None
    normalized_target_arrival = (target_arrival_time or "").strip() or None
    existing = fetch_one(
        """
        SELECT id
        FROM saved_destinations
        WHERE lower(name) = lower(?)
          AND abs(latitude - ?) < 0.00001
          AND abs(longitude - ?) < 0.00001
          AND coalesce(lower(address_text), '') = coalesce(lower(?), '')
          AND coalesce(target_arrival_time, '') = coalesce(?, '')
        """,
        (normalized_name, latitude, longitude, normalized_address, normalized_target_arrival),
    )
    if existing is not None:
        return
    execute(
        """
        INSERT INTO saved_destinations(name, address_text, latitude, longitude, target_arrival_time)
        VALUES(?, ?, ?, ?, ?)
        """,
        (normalized_name, normalized_address, latitude, longitude, normalized_target_arrival),
    )


def list_saved_destinations() -> list[dict[str, Any]]:
    """Return saved destination favorites."""
    return [
        dict(row)
        for row in fetch_all(
            """
            SELECT id, name, address_text, latitude, longitude, target_arrival_time, created_at
            FROM saved_destinations
            ORDER BY created_at DESC, id DESC
            """
        )
    ]


def apply_saved_destination(destination_id: int) -> None:
    """Load a saved destination into the active workspace."""
    LOG_INFO(f"applying saved destination id={destination_id}")
    row = fetch_one(
        """
        SELECT name, address_text, latitude, longitude, target_arrival_time
        FROM saved_destinations
        WHERE id = ?
        """,
        (destination_id,),
    )
    if row is None:
        raise ValueError("Saved destination not found.")
    save_destination(
        name=row["name"],
        address_text=row["address_text"],
        latitude=row["latitude"],
        longitude=row["longitude"],
        target_arrival_time=row["target_arrival_time"],
    )


def delete_saved_destination(destination_id: int) -> None:
    """Delete a saved destination."""
    execute("DELETE FROM saved_destinations WHERE id = ?", (destination_id,))
