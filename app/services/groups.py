"""Saved participant groups."""

from __future__ import annotations

import json
from typing import Any

from core.logging_config import LOG_INFO
from app.database import execute, fetch_all, fetch_one
from app.services.common import (
    clear_current_group_context,
    current_workspace_snapshot,
    get_current_group_context,
    load_workspace_snapshot,
    set_current_group_context,
)


def save_group(name: str) -> None:
    """Store the current participant list as a reusable group."""
    LOG_INFO(f"saving group '{name}'")
    cleaned_name = name.strip()
    snapshot = current_workspace_snapshot()
    execute(
        """
        INSERT INTO saved_groups(name, snapshot_json)
        VALUES(?, ?)
        ON CONFLICT(name) DO UPDATE SET snapshot_json = excluded.snapshot_json
        """,
        (cleaned_name, json.dumps({"participants": snapshot["participants"]})),
    )
    row = fetch_one("SELECT id FROM saved_groups WHERE name = ?", (cleaned_name,))
    if row is not None:
        set_current_group_context(group_id=int(row["id"]), group_name=cleaned_name)


def list_groups() -> list[dict[str, Any]]:
    """Return saved participant groups."""
    return [
        dict(row)
        for row in fetch_all(
            """
            SELECT id, name, created_at
            FROM saved_groups
            ORDER BY created_at DESC, id DESC
            """
        )
    ]


def apply_group(group_id: int) -> None:
    """Replace current participants with the saved group snapshot."""
    LOG_INFO(f"applying saved group id={group_id}")
    row = fetch_one("SELECT name, snapshot_json FROM saved_groups WHERE id = ?", (group_id,))
    if row is None:
        raise ValueError("Saved group not found.")
    load_workspace_snapshot(json.loads(row["snapshot_json"]) | {"destination": None})
    set_current_group_context(group_id=group_id, group_name=row["name"])


def delete_group(group_id: int) -> None:
    """Delete a saved group."""
    current_context = get_current_group_context()
    execute("DELETE FROM saved_groups WHERE id = ?", (group_id,))
    if current_context and current_context.get("group_id") == group_id:
        clear_current_group_context()
