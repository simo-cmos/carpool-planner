"""Workspace export and import helpers."""

from __future__ import annotations

import json
from typing import Any

from core.logging_config import LOG_INFO
from app.database import execute, fetch_all
from app.services.common import (
    backup_current_workspace,
    current_workspace_snapshot,
    load_workspace_snapshot,
)
from app.services.destinations import list_saved_destinations, save_destination_favorite

BACKUP_FORMAT = "dmproject-workspace"
BACKUP_VERSION = 1


def export_workspace_backup() -> str:
    """Serialize the whole local workspace into a restorable JSON backup."""
    LOG_INFO("exporting full workspace backup")
    return json.dumps(
        {
            "format": BACKUP_FORMAT,
            "version": BACKUP_VERSION,
            "workspace": current_workspace_snapshot(),
            "destinations": list_saved_destinations(),
            "groups": [
                dict(row)
                for row in fetch_all("SELECT name, snapshot_json FROM saved_groups ORDER BY id ASC")
            ],
            "history": [
                dict(row)
                for row in fetch_all(
                    """
                    SELECT trip_name, trip_date, preset_name, notes, snapshot_json, result_json, created_at
                    FROM trip_history
                    ORDER BY id ASC
                    """
                )
            ],
        },
        indent=2,
    )


def import_workspace_backup(payload_json: str) -> dict[str, int]:
    """Restore a backup produced by export_workspace_backup()."""
    try:
        payload = json.loads(payload_json)
    except json.JSONDecodeError as error:
        raise ValueError("That file is not valid JSON.") from error
    workspace = payload.get("workspace") if isinstance(payload, dict) else None
    if not isinstance(workspace, dict) or "participants" not in workspace:
        raise ValueError("That file is not a Drivers Manager workspace backup.")

    LOG_INFO("importing workspace backup")
    backup_current_workspace("import_workspace_backup")
    load_workspace_snapshot(workspace)

    destinations: list[dict[str, Any]] = payload.get("destinations") or []
    for destination in destinations:
        save_destination_favorite(
            name=destination["name"],
            address_text=destination.get("address_text"),
            latitude=float(destination["latitude"]),
            longitude=float(destination["longitude"]),
            target_arrival_time=destination.get("target_arrival_time"),
        )

    groups: list[dict[str, Any]] = payload.get("groups") or []
    for group in groups:
        execute(
            """
            INSERT INTO saved_groups(name, snapshot_json)
            VALUES(?, ?)
            ON CONFLICT(name) DO UPDATE SET snapshot_json = excluded.snapshot_json
            """,
            (group["name"], group["snapshot_json"]),
        )

    history: list[dict[str, Any]] = payload.get("history") or []
    for entry in history:
        execute(
            """
            INSERT INTO trip_history(trip_name, trip_date, preset_name, notes, snapshot_json, result_json, created_at)
            VALUES(?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry["trip_name"],
                entry.get("trip_date"),
                entry.get("preset_name"),
                entry.get("notes"),
                entry["snapshot_json"],
                entry["result_json"],
                entry.get("created_at"),
            ),
        )

    return {
        "participants": len(workspace.get("participants", [])),
        "destinations": len(destinations),
        "groups": len(groups),
        "history": len(history),
    }
