"""Trip history persistence and restore logic."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from core.logging_config import LOG_INFO
from app.database import execute, fetch_all, fetch_one
from app.services.common import current_workspace_snapshot, get_cached_optimization_result, serialize_optimization
from app.services.planner import run_optimization


def save_trip_history(
    *, trip_name: str, trip_date: str | None, notes: str | None, preset_name: str | None = None
) -> None:
    """Persist the current optimization result and workspace snapshot."""
    LOG_INFO(f"saving trip to history — '{trip_name}'")
    optimization = get_cached_optimization_result()
    if optimization is None:
        optimization = serialize_optimization(run_optimization())
    execute(
        """
        INSERT INTO trip_history(trip_name, trip_date, preset_name, notes, snapshot_json, result_json)
        VALUES(?, ?, ?, ?, ?, ?)
        """,
        (
            trip_name.strip() or "Untitled Trip",
            trip_date,
            preset_name,
            notes,
            json.dumps(current_workspace_snapshot()),
            json.dumps(optimization | {"saved_at": datetime.now(timezone.utc).isoformat()}),
        ),
    )


def list_trip_history() -> list[dict[str, Any]]:
    """Return previous optimization runs."""
    return [
        dict(row)
        for row in fetch_all(
            """
            SELECT id, trip_name, trip_date, preset_name, notes, created_at
            FROM trip_history
            ORDER BY created_at DESC, id DESC
            """
        )
    ]


def get_trip_history_entry(trip_id: int) -> dict[str, Any] | None:
    """Return one historical trip entry with serialized result."""
    row = fetch_one(
        """
        SELECT id, trip_name, trip_date, preset_name, notes, snapshot_json, result_json, created_at
        FROM trip_history
        WHERE id = ?
        """,
        (trip_id,),
    )
    if row is None:
        return None
    data = dict(row)
    data["snapshot"] = json.loads(data.pop("snapshot_json"))
    data["result"] = json.loads(data.pop("result_json"))
    return data


def delete_trip_history_entry(trip_id: int) -> bool:
    """Delete one saved trip-history snapshot."""
    existing = fetch_one("SELECT id FROM trip_history WHERE id = ?", (trip_id,))
    if existing is None:
        return False
    LOG_INFO(f"deleting trip history entry id={trip_id}")
    execute("DELETE FROM trip_history WHERE id = ?", (trip_id,))
    return True
