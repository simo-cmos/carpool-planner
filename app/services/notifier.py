"""Queue and delivery helpers for local desktop notifications."""

from __future__ import annotations

from typing import Any

from core.logging_config import LOG_DEBUG, LOG_INFO

from app.database import execute, execute_insert, fetch_all, fetch_one
from app.services.settings import get_app_settings


DEFAULT_NOTIFIER_SETTINGS = {
    "enabled": True,
    "notify_guest_visits": True,
    "notify_guest_submissions": True,
    "poll_seconds": 5,
}


def get_notifier_settings() -> dict[str, Any]:
    """Return notification preferences with defaults applied."""
    return DEFAULT_NOTIFIER_SETTINGS | get_app_settings().get("notifications", {})


def queue_notification_event(
    *,
    event_type: str,
    title: str,
    message: str,
    dedupe_key: str | None = None,
    dedupe_window_seconds: int = 0,
) -> int | None:
    """Store a notification event for the background watcher to deliver."""
    settings = get_notifier_settings()
    if not settings.get("enabled", True):
        LOG_DEBUG(f"notification suppressed because notifications are disabled: {event_type}")
        return None
    if dedupe_key and dedupe_window_seconds > 0:
        duplicate = fetch_one(
            """
            SELECT id
            FROM notification_events
            WHERE dedupe_key = ?
              AND created_at >= datetime('now', ?)
            ORDER BY id DESC
            LIMIT 1
            """,
            (dedupe_key, f"-{max(int(dedupe_window_seconds), 1)} seconds"),
        )
        if duplicate is not None:
            LOG_DEBUG(f"notification deduplicated: {event_type} key={dedupe_key}")
            return None
    event_id = execute_insert(
        """
        INSERT INTO notification_events(event_type, title, message, dedupe_key)
        VALUES(?, ?, ?, ?)
        """,
        (event_type, title.strip(), message.strip(), (dedupe_key or "").strip() or None),
    )
    LOG_INFO(f"queued notification event id={event_id} type={event_type}")
    return event_id


def list_pending_notification_events(limit: int = 20) -> list[dict[str, Any]]:
    """Return queued notification events that have not been delivered yet."""
    rows = fetch_all(
        """
        SELECT id, event_type, title, message, dedupe_key, delivery_error, created_at, delivered_at
        FROM notification_events
        WHERE delivered_at IS NULL
        ORDER BY id ASC
        LIMIT ?
        """,
        (limit,),
    )
    return [dict(row) for row in rows]


def mark_notification_delivered(event_id: int) -> None:
    """Mark one queued notification as delivered."""
    execute(
        """
        UPDATE notification_events
        SET delivered_at = CURRENT_TIMESTAMP,
            delivery_error = NULL
        WHERE id = ?
        """,
        (event_id,),
    )


def mark_notification_delivery_error(event_id: int, error_message: str) -> None:
    """Persist the last delivery error without removing the pending event."""
    execute(
        """
        UPDATE notification_events
        SET delivery_error = ?
        WHERE id = ?
        """,
        (error_message[:500], event_id),
    )
