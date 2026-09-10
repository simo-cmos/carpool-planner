"""Participant persistence, validation, and CSV operations."""

from __future__ import annotations

import csv
import io
from typing import Any

from core.logging_config import LOG_INFO, LOG_DEBUG
from app.database import execute, execute_insert, fetch_all, fetch_one
from app.time_utils import normalize_time_text, summarize_participant_time_preferences
from core.config import VEHICLE_TYPES


def _mark_dataset_customized() -> None:
    from app.services.common import mark_current_dataset_context_customized

    mark_current_dataset_context_customized()


def _clear_dataset_context() -> None:
    from app.services.common import clear_current_dataset_context

    clear_current_dataset_context()


def list_participants(include_inactive: bool = True) -> list[dict[str, Any]]:
    """Return participants stored in the local database."""
    LOG_DEBUG(f"listing participants — include_inactive={include_inactive}")
    where_clause = "" if include_inactive else "WHERE active_in_trip = 1"
    rows = fetch_all(
        f"""
        SELECT id, name, address_text, location_name, latitude, longitude, has_car, vehicle_type,
               pickup_mode, pickup_location_name, pickup_address_text, pickup_latitude, pickup_longitude,
               fuel_type, consumption_l_per_100km, total_seats, habit_score,
               role_tag, availability_tag, pickup_flexible, priority_rank, active_in_trip,
               outbound_earliest_time, outbound_latest_time, return_earliest_time, return_latest_time,
               force_drive_alone
        FROM participants
        {where_clause}
        ORDER BY priority_rank ASC, id ASC
        """
    )
    participants = [dict(row) for row in rows]
    for participant in participants:
        participant["time_preferences_summary"] = summarize_participant_time_preferences(participant)
    return participants


def get_participant(participant_id: int) -> dict[str, Any] | None:
    """Return a single participant row."""
    row = fetch_one(
        """
        SELECT id, name, address_text, location_name, latitude, longitude, has_car, vehicle_type,
               pickup_mode, pickup_location_name, pickup_address_text, pickup_latitude, pickup_longitude,
               fuel_type, consumption_l_per_100km, total_seats, habit_score,
               role_tag, availability_tag, pickup_flexible, priority_rank, active_in_trip,
               outbound_earliest_time, outbound_latest_time, return_earliest_time, return_latest_time,
               force_drive_alone
        FROM participants
        WHERE id = ?
        """,
        (participant_id,),
    )
    if row is None:
        return None
    participant = dict(row)
    participant["time_preferences_summary"] = summarize_participant_time_preferences(participant)
    return participant


def save_participant(
    *,
    participant_id: int | None,
    name: str,
    address_text: str | None,
    location_name: str,
    latitude: float,
    longitude: float,
    has_car: bool,
    fuel_type: str | None,
    consumption_l_per_100km: float | None,
    total_seats: int | None,
    habit_score: float,
    vehicle_type: str = "car",
    pickup_mode: str = "home",
    pickup_location_name: str | None = None,
    pickup_address_text: str | None = None,
    pickup_latitude: float | None = None,
    pickup_longitude: float | None = None,
    role_tag: str = "standard",
    availability_tag: str = "available",
    pickup_flexible: bool = True,
    priority_rank: int = 100,
    active_in_trip: bool = True,
    force_drive_alone: bool = False,
    outbound_earliest_time: str | None = None,
    outbound_latest_time: str | None = None,
    return_earliest_time: str | None = None,
    return_latest_time: str | None = None,
) -> int:
    """Insert or update a participant."""
    LOG_DEBUG(f"saving participant — id={participant_id}, name={name!r}, has_car={has_car}")
    # Normalised here rather than per caller: the web form, guest import, CSV
    # import and snapshot restore all land on this one function.
    if vehicle_type not in VEHICLE_TYPES:
        vehicle_type = "car"
    if vehicle_type == "motorbike" and total_seats is not None:
        total_seats = min(total_seats, 2)
    values = (
        name,
        address_text,
        location_name,
        latitude,
        longitude,
        pickup_mode,
        pickup_location_name,
        pickup_address_text,
        pickup_latitude,
        pickup_longitude,
        int(has_car),
        vehicle_type,
        fuel_type,
        consumption_l_per_100km,
        total_seats,
        habit_score,
        role_tag,
        availability_tag,
        int(pickup_flexible),
        priority_rank,
        int(active_in_trip),
        int(force_drive_alone),
        outbound_earliest_time,
        outbound_latest_time,
        return_earliest_time,
        return_latest_time,
    )
    if participant_id is None:
        new_participant_id = execute_insert(
            """
            INSERT INTO participants(
                name, address_text, location_name, latitude, longitude,
                pickup_mode, pickup_location_name, pickup_address_text, pickup_latitude, pickup_longitude,
                has_car, vehicle_type, fuel_type,
                consumption_l_per_100km, total_seats, habit_score, role_tag, availability_tag,
                pickup_flexible, priority_rank, active_in_trip, force_drive_alone,
                outbound_earliest_time, outbound_latest_time, return_earliest_time, return_latest_time
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            values,
        )
        _mark_dataset_customized()
        return new_participant_id

    execute(
        """
        UPDATE participants
        SET name = ?, address_text = ?, location_name = ?, latitude = ?, longitude = ?,
            pickup_mode = ?, pickup_location_name = ?, pickup_address_text = ?, pickup_latitude = ?, pickup_longitude = ?,
            has_car = ?, vehicle_type = ?, fuel_type = ?, consumption_l_per_100km = ?, total_seats = ?,
            habit_score = ?, role_tag = ?, availability_tag = ?, pickup_flexible = ?,
            priority_rank = ?, active_in_trip = ?, force_drive_alone = ?, outbound_earliest_time = ?,
            outbound_latest_time = ?, return_earliest_time = ?, return_latest_time = ?
        WHERE id = ?
        """,
        values + (participant_id,),
    )
    _mark_dataset_customized()
    return participant_id


def delete_participant(participant_id: int) -> None:
    """Remove a participant from the local dataset."""
    LOG_INFO(f"deleting participant id={participant_id}")
    execute("DELETE FROM participants WHERE id = ?", (participant_id,))
    _mark_dataset_customized()


def clear_all_data() -> None:
    """Reset the local dataset."""
    LOG_INFO("clearing all participant data")
    execute("DELETE FROM participants")
    _clear_dataset_context()


def set_participant_trip_active(participant_id: int, active_in_trip: bool) -> None:
    """Toggle whether a participant is included in the current trip workspace."""
    execute(
        "UPDATE participants SET active_in_trip = ? WHERE id = ?",
        (int(active_in_trip), participant_id),
    )
    _mark_dataset_customized()


def reorder_participants(participant_ids: list[int]) -> None:
    """Persist a display-only ordering for the participant list."""
    for order, participant_id in enumerate(participant_ids, start=1):
        execute(
            "UPDATE participants SET priority_rank = ? WHERE id = ?",
            (order, participant_id),
        )
    if participant_ids:
        _mark_dataset_customized()


def export_participants_csv() -> str:
    """Serialize participants to CSV."""
    LOG_INFO("exporting participants to CSV")
    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer,
        fieldnames=[
            "name",
            "address_text",
            "location_name",
            "latitude",
            "longitude",
            "pickup_mode",
            "pickup_location_name",
            "pickup_address_text",
            "pickup_latitude",
            "pickup_longitude",
            "has_car",
            "vehicle_type",
            "fuel_type",
            "consumption_l_per_100km",
            "total_seats",
            "habit_score",
            "role_tag",
            "availability_tag",
            "pickup_flexible",
            "priority_rank",
            "active_in_trip",
            "force_drive_alone",
            "outbound_earliest_time",
            "outbound_latest_time",
            "return_earliest_time",
            "return_latest_time",
        ],
    )
    writer.writeheader()
    fieldnames = set(writer.fieldnames or [])
    for participant in list_participants():
        participant.pop("id", None)
        writer.writerow({key: value for key, value in participant.items() if key in fieldnames})
    return buffer.getvalue()


def import_participants_csv(csv_text: str, replace_existing: bool = False) -> dict[str, Any]:
    """Import participants from CSV text with row-level validation details."""
    LOG_INFO(f"importing participants from CSV — replace_existing={replace_existing}")
    reader = csv.DictReader(io.StringIO(csv_text))
    if reader.fieldnames is None:
        raise ValueError("CSV file is empty.")
    missing = {"name", "latitude", "longitude"} - set(reader.fieldnames)
    if missing:
        raise ValueError(f"CSV is missing required columns: {', '.join(sorted(missing))}")

    rows = list(reader)
    errors: list[str] = []
    valid_rows: list[dict[str, Any]] = []
    for idx, row in enumerate(rows, start=2):
        try:
            if not (row.get("name") or "").strip():
                raise ValueError("name is required")
            valid_rows.append(
                {
                    "name": row["name"].strip(),
                    "address_text": (row.get("address_text") or "").strip() or None,
                    "location_name": (row.get("location_name") or row["name"]).strip(),
                    "latitude": float(row["latitude"]),
                    "longitude": float(row["longitude"]),
                    "pickup_mode": (row.get("pickup_mode") or "home").strip() or "home",
                    "pickup_location_name": (row.get("pickup_location_name") or "").strip() or None,
                    "pickup_address_text": (row.get("pickup_address_text") or "").strip() or None,
                    "pickup_latitude": float(row["pickup_latitude"]) if (row.get("pickup_latitude") or "").strip() else None,
                    "pickup_longitude": float(row["pickup_longitude"]) if (row.get("pickup_longitude") or "").strip() else None,
                    "has_car": str(row.get("has_car", "")).lower() in {"1", "true", "yes", "on"},
                    "vehicle_type": (row.get("vehicle_type") or "car").strip() or "car",
                    "fuel_type": (row.get("fuel_type") or "").strip() or None,
                    "consumption_l_per_100km": float(row["consumption_l_per_100km"]) if (row.get("consumption_l_per_100km") or "").strip() else None,
                    "total_seats": int(row["total_seats"]) if (row.get("total_seats") or "").strip() else None,
                    "habit_score": float((row.get("habit_score") or "0").strip() or 0.0),
                    "role_tag": (row.get("role_tag") or "standard").strip() or "standard",
                    "availability_tag": (row.get("availability_tag") or "available").strip() or "available",
                    "pickup_flexible": str(row.get("pickup_flexible", "true")).lower() in {"1", "true", "yes", "on"},
                    "priority_rank": int((row.get("priority_rank") or "100").strip() or 100),
                    "active_in_trip": str(row.get("active_in_trip", "true")).lower() in {"1", "true", "yes", "on"},
                    "force_drive_alone": str(row.get("force_drive_alone", "")).lower() in {"1", "true", "yes", "on"},
                    "outbound_earliest_time": normalize_time_text(row.get("outbound_earliest_time")),
                    "outbound_latest_time": normalize_time_text(row.get("outbound_latest_time")),
                    "return_earliest_time": normalize_time_text(row.get("return_earliest_time")),
                    "return_latest_time": normalize_time_text(row.get("return_latest_time")),
                }
            )
        except Exception as error:
            errors.append(f"Row {idx}: {error}")

    if errors:
        raise ValueError("CSV import failed:\n" + "\n".join(errors[:10]))
    if replace_existing:
        execute("DELETE FROM participants")
        _clear_dataset_context()
    for row in valid_rows:
        save_participant(participant_id=None, **row)
    return {"count": len(valid_rows), "errors": errors}
