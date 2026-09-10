"""Guest invite and trip-response persistence for the lightweight intake MVP."""

from __future__ import annotations

import secrets
from typing import Any

from core.logging_config import LOG_INFO

from app.database import execute, execute_insert, fetch_all, fetch_one
from app.services.common import backup_current_workspace, load_workspace_snapshot, get_destination, set_current_dataset_context
from app.services.participants import list_participants, save_participant
from core.config import FUEL_TYPE_DEFAULT_CONSUMPTION, VEHICLE_TYPES


def _generate_token(length: int = 9) -> str:
    """Return a short URL-safe token."""
    return secrets.token_urlsafe(length).replace("-", "").replace("_", "")[: max(length, 6)]


def create_trip_invite(*, trip_name: str, deadline_at: str | None = None, notes: str | None = None) -> dict[str, Any]:
    """Create a shareable guest invite using the current destination snapshot."""
    destination = get_destination()
    if destination is None:
        raise ValueError("Save a destination before creating an invite.")
    token = _generate_token(12)
    invite_id = execute_insert(
        """
        INSERT INTO trip_invites(
            token, trip_name, destination_name, destination_address_text, destination_latitude,
            destination_longitude, target_arrival_time, deadline_at, notes, status
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, 'open')
        """,
        (
            token,
            trip_name.strip(),
            destination.get("name"),
            destination.get("address_text"),
            destination.get("latitude"),
            destination.get("longitude"),
            destination.get("target_arrival_time"),
            deadline_at,
            notes,
        ),
    )
    LOG_INFO(f"created trip invite id={invite_id} token={token}")
    return get_trip_invite(invite_id) or {}


def list_trip_invites() -> list[dict[str, Any]]:
    """Return invite summaries with response counts."""
    rows = fetch_all(
        """
        SELECT i.id, i.token, i.trip_name, i.destination_name, i.destination_address_text,
               i.target_arrival_time, i.deadline_at, i.notes, i.status, i.created_at,
               COUNT(r.id) AS response_count,
               SUM(CASE WHEN r.status = 'imported' THEN 1 ELSE 0 END) AS imported_count,
               MAX(r.updated_at) AS last_response_at
        FROM trip_invites i
        LEFT JOIN trip_responses r ON r.invite_id = i.id
        GROUP BY i.id
        ORDER BY i.created_at DESC, i.id DESC
        """
    )
    return [dict(row) for row in rows]


def get_trip_invite(invite_id: int) -> dict[str, Any] | None:
    """Return one invite by numeric id."""
    row = fetch_one(
        """
        SELECT id, token, trip_name, destination_name, destination_address_text,
               destination_latitude, destination_longitude, target_arrival_time,
               deadline_at, notes, status, created_at
        FROM trip_invites
        WHERE id = ?
        """,
        (invite_id,),
    )
    return dict(row) if row is not None else None


def get_trip_invite_by_token(token: str) -> dict[str, Any] | None:
    """Return one invite by public token."""
    row = fetch_one(
        """
        SELECT id, token, trip_name, destination_name, destination_address_text,
               destination_latitude, destination_longitude, target_arrival_time,
               deadline_at, notes, status, created_at
        FROM trip_invites
        WHERE token = ?
        """,
        (token,),
    )
    return dict(row) if row is not None else None


def list_trip_responses(invite_id: int) -> list[dict[str, Any]]:
    """Return all guest responses for an invite."""
    rows = fetch_all(
        """
        SELECT id, invite_id, response_token, status, name, address_text, location_name,
               latitude, longitude, pickup_mode, pickup_location_name, pickup_address_text,
               pickup_latitude, pickup_longitude, pickup_flexible, has_car, vehicle_type, fuel_type,
               consumption_l_per_100km, total_seats, role_tag, outbound_earliest_time,
               outbound_latest_time, return_earliest_time, return_latest_time,
               food_stop_vote, imported_participant_id, created_at, updated_at
        FROM trip_responses
        WHERE invite_id = ?
        ORDER BY created_at ASC, id ASC
        """,
        (invite_id,),
    )
    return [dict(row) for row in rows]


def _build_participant_snapshot_from_response(response: dict[str, Any], priority_rank: int) -> dict[str, Any]:
    """Translate one guest response into the workspace snapshot format."""
    has_car = bool(response["has_car"])
    seats_value = int(response["total_seats"]) if response["total_seats"] is not None else None
    if has_car and (seats_value is None or seats_value < 2):
        seats_value = max(seats_value or 0, 2)
    return {
        "name": response["name"],
        "address_text": response["address_text"],
        "location_name": response["location_name"],
        "latitude": float(response["latitude"]),
        "longitude": float(response["longitude"]),
        "pickup_mode": (response.get("pickup_mode") or "home").strip() or "home",
        "pickup_location_name": response.get("pickup_location_name"),
        "pickup_address_text": response.get("pickup_address_text"),
        "pickup_latitude": (
            float(response["pickup_latitude"])
            if response.get("pickup_latitude") is not None
            else None
        ),
        "pickup_longitude": (
            float(response["pickup_longitude"])
            if response.get("pickup_longitude") is not None
            else None
        ),
        "has_car": has_car,
        "vehicle_type": response.get("vehicle_type") or "car",
        "fuel_type": response["fuel_type"] if has_car else None,
        "consumption_l_per_100km": (
            float(response["consumption_l_per_100km"])
            if response["consumption_l_per_100km"] is not None
            else FUEL_TYPE_DEFAULT_CONSUMPTION.get((response["fuel_type"] or "").strip(), None)
            if has_car
            else None
        ),
        "total_seats": seats_value if has_car else None,
        "habit_score": 0.0,
        "role_tag": response["role_tag"] or "standard",
        "availability_tag": "available",
        "pickup_flexible": bool(response.get("pickup_flexible", 1)),
        "priority_rank": priority_rank,
        "active_in_trip": True,
        "force_drive_alone": False,
        "outbound_earliest_time": response["outbound_earliest_time"],
        "outbound_latest_time": response["outbound_latest_time"],
        "return_earliest_time": response["return_earliest_time"],
        "return_latest_time": response["return_latest_time"],
    }


def get_trip_response(response_id: int) -> dict[str, Any] | None:
    """Return one response by id."""
    row = fetch_one(
        """
        SELECT id, invite_id, response_token, status, name, address_text, location_name,
               latitude, longitude, pickup_mode, pickup_location_name, pickup_address_text,
               pickup_latitude, pickup_longitude, pickup_flexible, has_car, vehicle_type, fuel_type,
               consumption_l_per_100km, total_seats, role_tag, outbound_earliest_time,
               outbound_latest_time, return_earliest_time, return_latest_time,
               food_stop_vote, imported_participant_id, created_at, updated_at
        FROM trip_responses
        WHERE id = ?
        """,
        (response_id,),
    )
    return dict(row) if row is not None else None


def get_trip_response_by_token(response_token: str) -> dict[str, Any] | None:
    """Return one response by guest-facing token."""
    row = fetch_one(
        """
        SELECT id, invite_id, response_token, status, name, address_text, location_name,
               latitude, longitude, pickup_mode, pickup_location_name, pickup_address_text,
               pickup_latitude, pickup_longitude, pickup_flexible, has_car, vehicle_type, fuel_type,
               consumption_l_per_100km, total_seats, role_tag, outbound_earliest_time,
               outbound_latest_time, return_earliest_time, return_latest_time,
               food_stop_vote, imported_participant_id, created_at, updated_at
        FROM trip_responses
        WHERE response_token = ?
        """,
        (response_token,),
    )
    return dict(row) if row is not None else None


def save_trip_response(
    *,
    invite_id: int,
    response_token: str | None,
    name: str,
    address_text: str | None,
    location_name: str,
    latitude: float,
    longitude: float,
    pickup_mode: str,
    pickup_location_name: str | None,
    pickup_address_text: str | None,
    pickup_latitude: float | None,
    pickup_longitude: float | None,
    pickup_flexible: bool,
    has_car: bool,
    vehicle_type: str,
    fuel_type: str | None,
    consumption_l_per_100km: float | None,
    total_seats: int | None,
    role_tag: str,
    outbound_earliest_time: str | None,
    outbound_latest_time: str | None,
    return_earliest_time: str | None,
    return_latest_time: str | None,
    food_stop_vote: str | None = None,
) -> dict[str, Any]:
    """Insert or update one guest response."""
    values = (
        invite_id,
        name.strip(),
        address_text,
        location_name,
        latitude,
        longitude,
        pickup_mode,
        pickup_location_name,
        pickup_address_text,
        pickup_latitude,
        pickup_longitude,
        int(pickup_flexible),
        int(has_car),
        vehicle_type if vehicle_type in VEHICLE_TYPES else "car",
        fuel_type,
        consumption_l_per_100km,
        total_seats,
        role_tag,
        outbound_earliest_time,
        outbound_latest_time,
        return_earliest_time,
        return_latest_time,
        food_stop_vote,
    )
    existing = get_trip_response_by_token(response_token) if response_token else None
    if existing is None:
        guest_token = _generate_token(16)
        response_id = execute_insert(
            """
            INSERT INTO trip_responses(
                invite_id, response_token, status, name, address_text, location_name, latitude,
                longitude, pickup_mode, pickup_location_name, pickup_address_text, pickup_latitude,
                pickup_longitude, pickup_flexible, has_car, vehicle_type, fuel_type, consumption_l_per_100km,
                total_seats, role_tag, outbound_earliest_time, outbound_latest_time,
                return_earliest_time, return_latest_time, food_stop_vote
            )
            VALUES(?, ?, 'submitted', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (invite_id, guest_token) + values[1:],
        )
        return get_trip_response(response_id) or {}
    execute(
        """
        UPDATE trip_responses
        SET invite_id = ?, name = ?, address_text = ?, location_name = ?, latitude = ?, longitude = ?,
            pickup_mode = ?, pickup_location_name = ?, pickup_address_text = ?, pickup_latitude = ?,
            pickup_longitude = ?, pickup_flexible = ?, has_car = ?, vehicle_type = ?, fuel_type = ?,
            consumption_l_per_100km = ?, total_seats = ?, role_tag = ?, outbound_earliest_time = ?,
            outbound_latest_time = ?, return_earliest_time = ?, return_latest_time = ?,
            food_stop_vote = ?,
            status = CASE WHEN status = 'imported' THEN status ELSE 'submitted' END,
            updated_at = CURRENT_TIMESTAMP
        WHERE response_token = ?
        """,
        values + (existing["response_token"],),
    )
    return get_trip_response_by_token(existing["response_token"]) or {}


def import_trip_response(response_id: int) -> dict[str, Any]:
    """Import one guest response into the current participant workspace."""
    response = get_trip_response(response_id)
    if response is None:
        raise ValueError("Response not found.")
    if response.get("imported_participant_id"):
        raise ValueError("This response was already imported.")
    participant_snapshot = _build_participant_snapshot_from_response(response, len(list_participants()) + 1)
    participant_id = save_participant(participant_id=None, **participant_snapshot)
    execute(
        """
        UPDATE trip_responses
        SET status = 'imported', imported_participant_id = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (participant_id, response_id),
    )
    return get_trip_response(response_id) or {}


def import_all_trip_responses(invite_id: int) -> dict[str, int]:
    """Import every submitted response for one invite."""
    imported = 0
    skipped = 0
    for response in list_trip_responses(invite_id):
        if response.get("imported_participant_id"):
            skipped += 1
            continue
        import_trip_response(int(response["id"]))
        imported += 1
    return {"imported": imported, "skipped": skipped}


def load_invite_session_into_workspace(invite_id: int) -> dict[str, Any]:
    """Replace the live workspace with the latest responses from one invite session."""
    invite = get_trip_invite(invite_id)
    if invite is None:
        raise ValueError("Invite not found.")
    responses = list_trip_responses(invite_id)
    if not responses:
        raise ValueError("This invite has no guest responses yet.")
    backup_current_workspace(f"load_invite_session:{invite_id}")
    load_workspace_snapshot(
        {
            "participants": [
                _build_participant_snapshot_from_response(response, index)
                for index, response in enumerate(responses, start=1)
            ],
            "destination": {
                "name": invite["destination_name"],
                "address_text": invite.get("destination_address_text"),
                "latitude": float(invite["destination_latitude"]),
                "longitude": float(invite["destination_longitude"]),
                "target_arrival_time": invite.get("target_arrival_time"),
            },
            "pickup_order_rules": [],
            "ride_together_rules": [],
        }
    )
    set_current_dataset_context(
        dataset_type="invite",
        dataset_id=invite_id,
        dataset_name=str(invite["trip_name"]),
        response_count=len(responses),
    )
    return invite | {"response_count": len(responses)}
