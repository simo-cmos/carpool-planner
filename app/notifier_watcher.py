"""Background watcher that turns queued DB events into Windows desktop notifications."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from app.database import init_db
from app.services.notifier import (
    get_notifier_settings,
    list_pending_notification_events,
    mark_notification_delivered,
    mark_notification_delivery_error,
)
from core.logging_config import LOG_DEBUG, LOG_INFO, init_logging


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _notification_script() -> Path:
    return _repo_root() / "scripts" / "show_notification.ps1"


def _show_windows_notification(title: str, message: str) -> None:
    script_path = _notification_script()
    command = [
        "powershell",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script_path),
        "-Title",
        title[:80],
        "-Message",
        message[:240],
        "-TimeoutMs",
        "9000",
    ]
    subprocess.run(
        command,
        check=True,
        timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def run_notifier_loop() -> None:
    """Continuously poll the DB for pending notification events."""
    init_logging()
    init_db()
    LOG_INFO("desktop notifier watcher started")
    while True:
        settings = get_notifier_settings()
        poll_seconds = max(int(settings.get("poll_seconds", 5) or 5), 2)
        if not settings.get("enabled", True):
            time.sleep(poll_seconds)
            continue
        pending_events = list_pending_notification_events(limit=10)
        if not pending_events:
            time.sleep(poll_seconds)
            continue
        for event in pending_events:
            try:
                _show_windows_notification(str(event["title"]), str(event["message"]))
            except Exception as error:  # pragma: no cover - desktop delivery is platform-specific
                LOG_INFO(f"notification delivery failed for event {event['id']}: {error}")
                mark_notification_delivery_error(int(event["id"]), str(error))
                time.sleep(2)
                continue
            mark_notification_delivered(int(event["id"]))
            LOG_INFO(f"notification delivered for event {event['id']}")
            time.sleep(1)


if __name__ == "__main__":
    if os.name != "nt":
        raise SystemExit("Desktop notifications are only supported on Windows in this watcher.")
    run_notifier_loop()
